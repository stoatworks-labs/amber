/**
	ambergl -- the plugin class, in a real GL context, with no Resolume.

	Resolume's GUI must not be driven with synthesized input in this fleet: two
	clicks on its custom-drawn UI simply do not register, and synthesized
	keystrokes reach the composition as clip triggers, which has modified a live
	project before. So everything that can be proven without a host is proven
	here.

	This exists in addition to `oxbow probe/selftest` because oxbow's `--set`
	routes every assignment through `setParamFloat`, including `FF_TYPE_FILE`
	ones. amber's whole job is behind a file parameter, so oxbow can load and
	enumerate the plugin -- which is genuinely useful and is how the registration
	was confirmed -- but it cannot hand it a movie. (That limitation also stops
	oxbow testing cartridge's Core/Content parameters, and is worth reporting
	upstream.)

	What it checks:
	  - the plugin constructs, InitGL succeeds, DeInitGL cleans up
	  - a movie set through SetTextParameter actually loads and renders
	  - the picture is not blank, and it CHANGES over time
	  - the double-render guard holds at the plugin level: rendering the same
	    host instant twice must produce the same picture
	  - both aspect branches of the scaling code, since a sign error in the
	    letterbox arithmetic is invisible at one aspect ratio

	Usage: ambergl <file.swf> [--size WxH] [--frames N] [--out PREFIX]
*/

#include "Plugin.h"

#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace
{

CGLContextObj MakeContext()
{
	const CGLPixelFormatAttribute accelerated[] = {
		kCGLPFAOpenGLProfile, CGLPixelFormatAttribute( kCGLOGLPVersion_GL4_Core ),
		kCGLPFAAccelerated,
		kCGLPFAColorSize, CGLPixelFormatAttribute( 24 ),
		kCGLPFAAlphaSize, CGLPixelFormatAttribute( 8 ),
		CGLPixelFormatAttribute( 0 )
	};
	const CGLPixelFormatAttribute software[] = {
		kCGLPFAOpenGLProfile, CGLPixelFormatAttribute( kCGLOGLPVersion_GL4_Core ),
		kCGLPFAColorSize, CGLPixelFormatAttribute( 24 ),
		kCGLPFAAlphaSize, CGLPixelFormatAttribute( 8 ),
		CGLPixelFormatAttribute( 0 )
	};

	CGLPixelFormatObj format = nullptr;
	GLint count              = 0;
	if( CGLChoosePixelFormat( accelerated, &format, &count ) != kCGLNoError || format == nullptr )
		CGLChoosePixelFormat( software, &format, &count );
	if( format == nullptr )
		return nullptr;

	CGLContextObj context = nullptr;
	CGLCreateContext( format, nullptr, &context );
	CGLDestroyPixelFormat( format );
	if( context )
		CGLSetCurrentContext( context );
	return context;
}

struct Target
{
	GLuint fbo     = 0;
	GLuint texture = 0;
	int width      = 0;
	int height     = 0;

	bool Create( int w, int h )
	{
		width  = w;
		height = h;
		glGenTextures( 1, &texture );
		glBindTexture( GL_TEXTURE_2D, texture );
		glTexImage2D( GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr );
		glTexParameteri( GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR );
		glTexParameteri( GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR );

		glGenFramebuffers( 1, &fbo );
		glBindFramebuffer( GL_FRAMEBUFFER, fbo );
		glFramebufferTexture2D( GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0 );
		return glCheckFramebufferStatus( GL_FRAMEBUFFER ) == GL_FRAMEBUFFER_COMPLETE;
	}

	void Destroy()
	{
		if( fbo )     glDeleteFramebuffers( 1, &fbo );
		if( texture ) glDeleteTextures( 1, &texture );
	}
};

std::vector< unsigned char > ReadBack( const Target& target )
{
	std::vector< unsigned char > pixels( static_cast< size_t >( target.width ) * target.height * 4 );
	glBindFramebuffer( GL_FRAMEBUFFER, target.fbo );
	glReadPixels( 0, 0, target.width, target.height, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data() );
	return pixels;
}

/// Fraction of pixels with any alpha at all.
double LitFraction( const std::vector< unsigned char >& pixels )
{
	size_t lit = 0;
	for( size_t i = 3; i < pixels.size(); i += 4 )
		if( pixels[ i ] != 0 )
			++lit;
	return pixels.empty() ? 0.0 : static_cast< double >( lit ) / ( pixels.size() / 4 );
}

void WritePPM( const std::string& path, const std::vector< unsigned char >& pixels, int w, int h )
{
	FILE* file = std::fopen( path.c_str(), "wb" );
	if( !file )
		return;
	std::fprintf( file, "P6\n%d %d\n255\n", w, h );
	// glReadPixels gives bottom-up; PPM is top-down.
	for( int y = h - 1; y >= 0; --y )
		for( int x = 0; x < w; ++x )
		{
			const size_t index = ( static_cast< size_t >( y ) * w + x ) * 4;
			std::fputc( pixels[ index + 0 ], file );
			std::fputc( pixels[ index + 1 ], file );
			std::fputc( pixels[ index + 2 ], file );
		}
	std::fclose( file );
}

int failures = 0;

void Check( bool condition, const char* what )
{
	std::printf( "  %-56s %s\n", what, condition ? "ok" : "FAIL" );
	if( !condition )
		++failures;
}

/// Render `frames` frames, stepping the host clock, and report what happened.
struct RunResult
{
	int nonBlank = 0;
	int changed  = 0;
	std::vector< unsigned char > last;
};

RunResult Run( amber::AmberPlugin& plugin, Target& target, int frames, double startTime,
               const std::string& dumpPrefix )
{
	RunResult result;
	std::vector< unsigned char > previous;

	for( int frame = 0; frame < frames; ++frame )
	{
		glBindFramebuffer( GL_FRAMEBUFFER, target.fbo );
		glViewport( 0, 0, target.width, target.height );
		glClearColor( 0.0f, 0.0f, 0.0f, 0.0f );
		glClear( GL_COLOR_BUFFER_BIT );

		// A host advances its clock between frames; 1/60s is a normal output rate
		// and deliberately not the movie's own, so the accumulator is exercised.
		plugin.SetTime( startTime + frame / 60.0 );

		ProcessOpenGLStruct gl = {};
		gl.numInputTextures    = 0;
		gl.inputTextures       = nullptr;
		gl.HostFBO             = target.fbo;
		plugin.ProcessOpenGL( &gl );

		std::vector< unsigned char > pixels = ReadBack( target );
		if( LitFraction( pixels ) > 0.01 )
			++result.nonBlank;
		if( !previous.empty() && pixels != previous )
			++result.changed;

		if( !dumpPrefix.empty() && frame % 20 == 0 )
		{
			char name[ 512 ];
			std::snprintf( name, sizeof( name ), "%s%04d.ppm", dumpPrefix.c_str(), frame );
			WritePPM( name, pixels, target.width, target.height );
		}

		previous = pixels;
	}

	result.last = previous;
	return result;
}

}  // namespace

int main( int argc, char** argv )
{
	if( argc < 2 )
	{
		std::fprintf( stderr, "usage: ambergl <file.swf> [--size WxH] [--frames N] [--out PREFIX]\n" );
		return 2;
	}

	const std::string movie = argv[ 1 ];
	int width               = 1280;
	int height              = 720;
	int frames              = 120;
	std::string dumpPrefix;

	for( int i = 2; i < argc; ++i )
	{
		if( std::strcmp( argv[ i ], "--size" ) == 0 && i + 1 < argc )
			std::sscanf( argv[ ++i ], "%dx%d", &width, &height );
		else if( std::strcmp( argv[ i ], "--frames" ) == 0 && i + 1 < argc )
			frames = std::atoi( argv[ ++i ] );
		else if( std::strcmp( argv[ i ], "--out" ) == 0 && i + 1 < argc )
			dumpPrefix = argv[ ++i ];
	}

	CGLContextObj context = MakeContext();
	if( context == nullptr )
	{
		std::fprintf( stderr, "ambergl: could not create a GL context\n" );
		return 1;
	}
	std::printf( "gl: %s\n", glGetString( GL_VERSION ) );

	Target target;
	if( !target.Create( width, height ) )
	{
		std::fprintf( stderr, "ambergl: incomplete framebuffer\n" );
		return 1;
	}

	std::printf( "\nplugin lifecycle\n" );
	{
		amber::AmberPlugin plugin;
		FFGLViewportStruct viewport = { 0, 0, static_cast< FFUInt32 >( width ),
		                                static_cast< FFUInt32 >( height ) };
		Check( plugin.InitGL( &viewport ) == FF_SUCCESS, "InitGL succeeds" );

		// The file parameter is the whole point, and is the one thing oxbow
		// cannot set.
		Check( plugin.SetTextParameter( 0, movie.c_str() ) == FF_SUCCESS,
		       "SetTextParameter accepts the movie path" );
		// The About line is display only and MUST still succeed, or the SDK's
		// instantiateGL deletes every fresh instance in a real host.
		Check( plugin.SetTextParameter( 6, "" ) == FF_SUCCESS,
		       "SetTextParameter succeeds for the display-only About line" );

		std::printf( "\nplayback at %dx%d\n", width, height );
		RunResult run = Run( plugin, target, frames, 0.0, dumpPrefix );
		std::printf( "  %d of %d frames non-blank, %d changed\n", run.nonBlank, frames, run.changed );
		Check( run.nonBlank > frames / 2, "most frames carry a picture" );
		Check( run.changed > frames / 4, "the picture changes over time" );

		// --- double-render guard -------------------------------------------
		// Render the same host instant twice. A stateful player that stepped on
		// every render would run at double speed whenever Resolume's preview
		// monitor is open -- a bug that depends on the shape of the operator's
		// screen.
		std::printf( "\ndouble-render guard\n" );
		const double frozen = 99.0;
		plugin.SetTime( frozen );
		ProcessOpenGLStruct gl = {};
		gl.HostFBO             = target.fbo;

		glBindFramebuffer( GL_FRAMEBUFFER, target.fbo );
		glViewport( 0, 0, target.width, target.height );
		plugin.ProcessOpenGL( &gl );
		const std::vector< unsigned char > once = ReadBack( target );

		plugin.SetTime( frozen );  // same instant again
		glBindFramebuffer( GL_FRAMEBUFFER, target.fbo );
		plugin.ProcessOpenGL( &gl );
		const std::vector< unsigned char > twice = ReadBack( target );

		Check( once == twice, "re-rendering the same host instant does not advance" );

		Check( plugin.DeInitGL() == FF_SUCCESS, "DeInitGL succeeds" );
	}

	// --- the other aspect branch --------------------------------------------
	// A sign error in the letterbox arithmetic is invisible at one aspect ratio
	// only; cartridge found exactly that bug this way.
	std::printf( "\nthe other aspect branch (720x720)\n" );
	{
		target.Destroy();
		Target square;
		square.Create( 720, 720 );

		amber::AmberPlugin plugin;
		FFGLViewportStruct viewport = { 0, 0, 720, 720 };
		plugin.InitGL( &viewport );
		plugin.SetTextParameter( 0, movie.c_str() );

		RunResult run = Run( plugin, square, 30, 0.0, "" );
		std::printf( "  %d of 30 frames non-blank, %d changed\n", run.nonBlank, run.changed );
		Check( run.nonBlank > 15, "square output still carries a picture" );

		plugin.DeInitGL();
		square.Destroy();
	}

	const GLenum error = glGetError();
	std::printf( "\ngl error: 0x%x\n", error );
	Check( error == GL_NO_ERROR, "no GL error was left behind" );

	CGLSetCurrentContext( nullptr );
	CGLDestroyContext( context );

	std::printf( "\nambergl: %s\n", failures == 0 ? "OK" : "FAIL" );
	return failures == 0 ? 0 : 1;
}
