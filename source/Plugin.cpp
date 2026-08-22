#include "Plugin.h"

#include "AmberCore.h"

#include <chrono>
#include <algorithm>
#include <cmath>
#include <cstring>

namespace amber
{

namespace
{

/// Frames that must agree before the host's clock unit is settled.
constexpr int kClockVotes = 4;

/// Wall clock, to calibrate the host's against. Steady rather than system, so
/// nothing here moves if the machine's clock is corrected.
double wallSeconds()
{
	using namespace std::chrono;
	static const steady_clock::time_point start = steady_clock::now();
	return duration_cast< duration< double > >( steady_clock::now() - start ).count();
}

/// Passthrough vertex shader. The screen quad supplies position and UV.
const char* kVertexShader = R"(#version 410 core
layout( location = 0 ) in vec4 vPosition;
layout( location = 1 ) in vec2 vUV;

out vec2 uv;

void main()
{
	gl_Position = vPosition;
	uv = vUV;
}
)";

/**
	Samples the movie with a UV transform, so Fit / Fill / Stretch cost no
	geometry changes.

	`uvScale` and `uvOffset` map the output's 0..1 into the movie's 0..1. Fit
	produces a scale greater than one on the letterboxed axis, which pushes UVs
	outside 0..1, and those samples are discarded rather than clamped -- a
	CLAMP_TO_EDGE smear along the letterbox is a classic and ugly artifact.

	The output is premultiplied because Resolume composites premultiplied, and
	the frame arriving from Ruffle is straight alpha.
*/
const char* kFragmentShader = R"(#version 410 core
uniform sampler2D movie;
uniform vec2 uvScale;
uniform vec2 uvOffset;

in vec2 uv;
out vec4 fragColor;

void main()
{
	// Flip V. Ruffle hands back RGBA with row 0 at the TOP of the picture, and
	// glTexSubImage2D treats the first row it is given as v=0, which in GL is
	// the BOTTOM. Uploading it straight through therefore renders the movie
	// upside down -- correct in every other respect, which is what makes it easy
	// to miss in a structural check: the frame is the right size, fully opaque,
	// changes over time, and passes every assertion a harness can make about it
	// without looking. Only rendering a frame and looking at it catches this.
	//
	// The flip happens before the scaling transform so the letterbox arithmetic
	// below is unaffected.
	vec2 sourceUV = vec2( uv.x, 1.0 - uv.y );
	vec2 sampleUV = sourceUV * uvScale + uvOffset;

	if( sampleUV.x < 0.0 || sampleUV.x > 1.0 ||
	    sampleUV.y < 0.0 || sampleUV.y > 1.0 )
	{
		fragColor = vec4( 0.0 );
		return;
	}

	vec4 texel = texture( movie, sampleUV );
	fragColor = vec4( texel.rgb * texel.a, texel.a );
}
)";

/// The shared line -- name, version, licence, maker -- then the facts that are
/// amber's alone. The version comes from the build, not from the generated
/// header's fallback, so it is substituted rather than taken as read.
std::string BuildAboutText()
{
	std::string line = stoatworks::about::textParam( 0 );

	const std::string stale = std::string( stoatworks::about::name ) + " " +
	                          stoatworks::about::versionFallback;
	if( line.rfind( stale, 0 ) == 0 )
		line = std::string( stoatworks::about::name ) + " " AMBER_VERSION +
		       line.substr( stale.size() );

	return line + "\n"
	       "Flash content as a live source.\n"
	       "Powered by Ruffle (ruffle.rs), MIT/Apache-2.0.\n"
	       "Transparent uses Flash's own wmode=transparent.\n"
	       "No audio: FFGL provides no audio path.";
}

}  // namespace

AmberPlugin::AmberPlugin()
{
	// A source produces a picture and consumes none.
	SetMinInputs( 0 );
	SetMaxInputs( 0 );

	SetParamInfo( PT_FILE, "Movie", FF_TYPE_FILE, "" );
	SetParamInfo( PT_RUN, "Run", FF_TYPE_BOOLEAN, true );
	SetParamInfo( PT_RESTART, "Restart", FF_TYPE_EVENT, false );

	// Stored 0..1 because SetParamInfo clamps a ranged FF_TYPE_STANDARD default
	// before SetParamRange could widen it -- there is no SetParamDefault. 0.5
	// is 1.0x; see SpeedMultiplier().
	SetParamInfo( PT_SPEED, "Speed", FF_TYPE_STANDARD, 0.5f );

	SetOptionParamInfo( PT_SCALING, "Scaling", 3, 0 );
	SetParamElementInfo( PT_SCALING, 0, "Fit", 0.0f );
	SetParamElementInfo( PT_SCALING, 1, "Fill", 1.0f );
	SetParamElementInfo( PT_SCALING, 2, "Stretch", 2.0f );

	SetParamInfo( PT_SMOOTHING, "Smoothing", FF_TYPE_BOOLEAN, true );
	SetParamInfo( PT_TRANSPARENT, "Transparent", FF_TYPE_BOOLEAN, true );

	mAboutText = BuildAboutText();
	SetParamInfo( PT_ABOUT, "About", FF_TYPE_TEXT, mAboutText.c_str() );
	{
		// Inline rather than through a helper: SetParamInfo is protected on
		// CFFGLPlugin, so nothing outside the class can call it.
		FFUInt32 aboutId = PT_ABOUT + 1;
		for( const auto& b : stoatworks::about::buttons() )
			SetParamInfo( aboutId++, b.label, FF_TYPE_EVENT, false );
	}
	for( unsigned int id = PT_ABOUT; id < PT_COUNT; ++id )
		SetParamGroup( id, "About" );
}

AmberPlugin::~AmberPlugin()
{
	CloseMovie();
}

FFResult AmberPlugin::InitGL( const FFGLViewportStruct* vp )
{
	if( !mShader.Compile( kVertexShader, kFragmentShader ) )
	{
		DeInitGL();
		return FF_FAIL;
	}
	if( !mQuad.Initialise() )
	{
		DeInitGL();
		return FF_FAIL;
	}

	glGenTextures( 1, &mTexture );
	if( mTexture == 0 )
	{
		DeInitGL();
		return FF_FAIL;
	}

	// The movie is opened lazily in ProcessOpenGL, because the host may set the
	// file parameter either before or after InitGL and there is no ordering
	// guarantee either way.
	return CFFGLPlugin::InitGL( vp );
}

FFResult AmberPlugin::DeInitGL()
{
	CloseMovie();

	if( mTexture != 0 )
	{
		glDeleteTextures( 1, &mTexture );
		mTexture = 0;
	}
	mTextureAllocated = false;

	mQuad.Release();
	mShader.FreeGLResources();
	return FF_SUCCESS;
}

void AmberPlugin::OpenMovie()
{
	CloseMovie();

	if( mMoviePath.empty() )
		return;

	char error[ 512 ] = { 0 };

	// Ruffle lays the stage out against the size it is given, so the movie is
	// rendered at its OWN declared size rather than the host viewport: a Flash
	// stage has a fixed size, and stretching it to the output is the scaling
	// parameter's job, not the player's. Rendering a 550x400 stage into a 4K
	// viewport would also mean a 4K readback every frame for no added detail.
	unsigned int width  = 0;
	unsigned int height = 0;
	if( !amber_probe_size( mMoviePath.c_str(), &width, &height ) || width == 0 || height == 0 )
	{
		mOpenFailed = true;
		mLastError  = "could not read the movie's stage size";
		return;
	}

	mPlayer = amber_open( mMoviePath.c_str(), width, height, error, sizeof( error ) );
	if( mPlayer == nullptr )
	{
		mOpenFailed = true;
		mLastError  = error;
		return;
	}

	// Applied before the first advance so frame one is already correct; a movie
	// that flashed its opaque stage colour for one frame on every trigger would
	// be very visible on a layer.
	amber_set_transparent( mPlayer, mTransparent );

	mMovieWidth  = amber_width( mPlayer );
	mMovieHeight = amber_height( mPlayer );
	mPixels.assign( static_cast< size_t >( mMovieWidth ) * mMovieHeight * 4, 0 );

	mOpenFailed       = false;
	mLastError.clear();
	mTextureAllocated = false;
}

void AmberPlugin::CloseMovie()
{
	if( mPlayer != nullptr )
	{
		amber_close( mPlayer );
		mPlayer = nullptr;
	}
	mPixels.clear();
	mMovieWidth  = 0;
	mMovieHeight = 0;
}

void AmberPlugin::UpdateClock()
{
	// FFGL never says what unit SetTime arrives in, and hosts disagree:
	// Resolume sends MILLISECONDS (measured live at 20.0 per frame at its
	// 50 fps, and the SDK's own Particles sample divides by 1000), while the
	// offline harness sends seconds. Reading it raw is a thousand times fast
	// on the one host that matters and exactly right on the one that gets
	// tested, which is how it stays hidden.
	//
	// This used to guess the unit from the magnitude of a single frame delta
	// and then lock. That had three holes: a delta between 0.5 and 2.0 decided
	// nothing, a burst of sub-0.5 ms frames at load -- a thumbnail render on a
	// quick GPU -- locked it to "seconds" for the rest of the session, and
	// while undecided it assumed seconds, which is precisely the millisecond
	// host's wrong answer.
	//
	// So measure instead of guessing. steady_clock says how much real time
	// passed, the host says how much host time passed, and the ratio names the
	// unit outright. Nothing plausible sits between 1 and 1000, so both bands
	// are wide and a frame fitting neither simply does not vote.
	const double wallNow = wallSeconds();
	if( mWallStart < 0.0 )
		mWallStart = wallNow;

	// Never read `hostTime` before the host has set it: CFFGLPlugin's
	// constructor initialises bpm and barPhase and leaves hostTime
	// uninitialised, so until SetTime lands it is whatever was in that memory.
	const double raw = mHostTimeSeen ? hostTime : -1.0;

	if( mClockScale == 0.0 && raw >= 0.0 && mLastRawTime >= 0.0 && mLastWallTime >= 0.0 )
	{
		const double hostDelta = raw - mLastRawTime;
		const double wallDelta = wallNow - mLastWallTime;

		// A paused host, a looping clip or a stalled frame tells us nothing.
		if( hostDelta > 0.0 && wallDelta >= 0.0005 )
		{
			const double ratio = hostDelta / wallDelta;
			if( ratio > 0.1 && ratio < 10.0 )
				++mSecondsVotes;
			else if( ratio > 100.0 && ratio < 10000.0 )
				++mMillisVotes;

			// Several frames rather than one, so a single odd frame -- the
			// first after a seek, say -- cannot decide it on its own.
			if( mSecondsVotes >= kClockVotes || mMillisVotes >= kClockVotes )
			{
				mClockScale = mMillisVotes > mSecondsVotes ? 0.001 : 1.0;
			}
		}
	}

	if( raw >= 0.0 )
		mLastRawTime = raw;
	mLastWallTime = wallNow;

	// Until the unit is settled -- and for a host that never calls SetTime at
	// all -- run on the real clock. Wrong in origin but right in rate, where
	// assuming seconds would be a thousand times fast on Resolume.
	mHostSeconds = ( raw >= 0.0 && mClockScale != 0.0 ) ? raw * mClockScale : wallNow - mWallStart;
}

FFResult AmberPlugin::SetTime( double time )
{
	mHostTimeSeen = true;
	return CFFGLPlugin::SetTime( time );
}

void AmberPlugin::SetClockScaleForTest( double scale )
{
	mClockScale = scale;
}

void AmberPlugin::TickClockForTest()
{
	UpdateClock();
}

double AmberPlugin::ClockScaleForTest() const
{
	return mClockScale;
}

double AmberPlugin::HostSecondsForTest() const
{
	return mHostSeconds;
}

float AmberPlugin::SpeedMultiplier() const
{
	// 0.5 is 1.0x. Below that it eases to a stop; above, up to 4x. Squaring the
	// upper half keeps the useful 1x-2x range across most of the slider instead
	// of crushing it against the left.
	if( mSpeed <= 0.5f )
		return mSpeed * 2.0f;
	const float t = ( mSpeed - 0.5f ) * 2.0f;
	return 1.0f + t * t * 3.0f;
}

void AmberPlugin::UploadFrame()
{
	glBindTexture( GL_TEXTURE_2D, mTexture );

	if( !mTextureAllocated )
	{
		glTexParameteri( GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE );
		glTexParameteri( GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE );
		glTexImage2D( GL_TEXTURE_2D, 0, GL_RGBA8,
		              static_cast< GLsizei >( mMovieWidth ),
		              static_cast< GLsizei >( mMovieHeight ),
		              0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr );
		mTextureAllocated = true;
	}

	// Filtering is set every frame because Smoothing is a live parameter.
	const GLint filter = mSmoothing ? GL_LINEAR : GL_NEAREST;
	glTexParameteri( GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, filter );
	glTexParameteri( GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, filter );

	// RGBA8 is four bytes per texel so the default 4-byte unpack alignment is
	// already correct, and the rows are tightly packed by construction.
	glTexSubImage2D( GL_TEXTURE_2D, 0, 0, 0,
	                 static_cast< GLsizei >( mMovieWidth ),
	                 static_cast< GLsizei >( mMovieHeight ),
	                 GL_RGBA, GL_UNSIGNED_BYTE, mPixels.data() );
}

void AmberPlugin::DrawQuad( int viewportWidth, int viewportHeight )
{
	float scaleX  = 1.0f;
	float scaleY  = 1.0f;

	if( mScaling != Scaling::Stretch && viewportWidth > 0 && viewportHeight > 0 &&
	    mMovieWidth > 0 && mMovieHeight > 0 )
	{
		const float outputAspect = static_cast< float >( viewportWidth ) / static_cast< float >( viewportHeight );
		const float movieAspect  = static_cast< float >( mMovieWidth ) / static_cast< float >( mMovieHeight );
		const float ratio        = outputAspect / movieAspect;

		// Fit shows the whole stage and letterboxes; Fill covers the output and
		// crops. The two differ only in which way the comparison goes.
		const bool wider = ( mScaling == Scaling::Fit ) ? ( ratio > 1.0f ) : ( ratio < 1.0f );
		if( wider )
			scaleX = ratio;
		else
			scaleY = 1.0f / ratio;
	}

	// Centre whatever scaling produced.
	const float offsetX = ( 1.0f - scaleX ) * 0.5f;
	const float offsetY = ( 1.0f - scaleY ) * 0.5f;

	glUseProgram( mShader.GetGLID() );
	mShader.Set( "movie", 0 );
	mShader.Set( "uvScale", scaleX, scaleY );
	mShader.Set( "uvOffset", offsetX, offsetY );

	glActiveTexture( GL_TEXTURE0 );
	glBindTexture( GL_TEXTURE_2D, mTexture );

	mQuad.Draw();

	// Put the state back by hand. ffglex::Scoped* helpers clear to 0 on scope
	// exit rather than restoring, which is worse than doing nothing.
	glBindTexture( GL_TEXTURE_2D, 0 );
	glUseProgram( 0 );
}

FFResult AmberPlugin::ProcessOpenGL( ProcessOpenGLStruct* pGL )
{
	UpdateClock();

	if( mPlayer == nullptr && !mOpenFailed && !mMoviePath.empty() )
		OpenMovie();

	if( mPlayer == nullptr )
	{
		// Nothing loaded, or loading failed. A transparent layer is the right
		// answer -- a source that cannot produce a picture should not paint
		// over whatever is beneath it.
		glClearColor( 0.0f, 0.0f, 0.0f, 0.0f );
		glClear( GL_COLOR_BUFFER_BIT );
		return FF_SUCCESS;
	}

	// --- advance ------------------------------------------------------------
	// The elapsed time, and only ever a genuinely positive one.
	//
	// Resolume renders the same instant more than once: to the preview, to the
	// program output, and to a clip thumbnail. A stateful player that stepped
	// on every render would run at double or triple speed depending purely on
	// which windows the operator has open -- a bug whose reproduction depends
	// on the shape of somebody else's screen. `mLastHostSeconds` not moving
	// means no time has passed, so nothing is owed. amber_advance applies the
	// same rule again on its own side.
	double elapsed = 0.0;
	if( mLastHostSeconds >= 0.0 && mHostSeconds > mLastHostSeconds )
		elapsed = mHostSeconds - mLastHostSeconds;
	mLastHostSeconds = mHostSeconds;

	if( mRun && elapsed > 0.0 )
		amber_advance( mPlayer, elapsed * SpeedMultiplier() );

	// --- render -------------------------------------------------------------
	if( !amber_render( mPlayer, mPixels.data(), mPixels.size() ) )
	{
		// The player failed or panicked. Keep the last good picture rather than
		// flashing black mid-show.
		const char* message = amber_last_error( mPlayer );
		mLastError          = message != nullptr ? message : "render failed";
	}

	UploadFrame();
	DrawQuad( pGL != nullptr ? currentViewport.width : 0,
	          pGL != nullptr ? currentViewport.height : 0 );

	return FF_SUCCESS;
}

FFResult AmberPlugin::SetFloatParameter( unsigned int index, float value )
{
	switch( index )
	{
	case PT_RUN:
		mRun = value > 0.5f;
		if( mPlayer != nullptr )
			amber_set_playing( mPlayer, mRun );
		return FF_SUCCESS;

	case PT_RESTART:
		// An event parameter arrives as a rising edge.
		if( value > 0.5f && !mMoviePath.empty() )
			OpenMovie();
		return FF_SUCCESS;

	case PT_SPEED:
		mSpeed = value;
		return FF_SUCCESS;

	case PT_SCALING:
	{
		const int option = static_cast< int >( std::lround( value ) );
		mScaling = option == 1 ? Scaling::Fill
		         : option == 2 ? Scaling::Stretch
		                       : Scaling::Fit;
		return FF_SUCCESS;
	}

	case PT_SMOOTHING:
		mSmoothing = value > 0.5f;
		return FF_SUCCESS;

	case PT_TRANSPARENT:
		mTransparent = value > 0.5f;
		if( mPlayer != nullptr )
			amber_set_transparent( mPlayer, mTransparent );
		return FF_SUCCESS;

	default:
		// The About buttons open a browser and store nothing.
		if( index > PT_ABOUT && index < PT_COUNT )
			return stoatworks::about::handleParam( index - PT_ABOUT, value ) ? FF_SUCCESS : FF_FAIL;
		return FF_FAIL;
	}
}

float AmberPlugin::GetFloatParameter( unsigned int index )
{
	switch( index )
	{
	case PT_RUN:       return mRun ? 1.0f : 0.0f;
	case PT_RESTART:   return 0.0f;
	case PT_SPEED:     return mSpeed;
	case PT_SCALING:   return static_cast< float >( static_cast< int >( mScaling ) );
	case PT_SMOOTHING:   return mSmoothing ? 1.0f : 0.0f;
	case PT_TRANSPARENT: return mTransparent ? 1.0f : 0.0f;
	default:           return 0.0f;
	}
}

FFResult AmberPlugin::SetTextParameter( unsigned int index, const char* value )
{
	// This override must exist and must succeed for the About line, or the
	// SDK's instantiateGL deletes every fresh instance and the plugin cannot be
	// loaded by any real host at all.
	switch( index )
	{
	case PT_FILE:
	{
		const std::string path = value != nullptr ? value : "";
		if( path != mMoviePath )
		{
			mMoviePath  = path;
			mOpenFailed = false;
			CloseMovie();  // reopened lazily on the next ProcessOpenGL
		}
		return FF_SUCCESS;
	}

	case PT_ABOUT:
		return FF_SUCCESS;  // display only, but must not fail

	default:
		return FF_FAIL;
	}
}

char* AmberPlugin::GetTextParameter( unsigned int index )
{
	switch( index )
	{
	case PT_FILE:  return const_cast< char* >( mMoviePath.c_str() );
	case PT_ABOUT: return const_cast< char* >( mAboutText.c_str() );
	default:       return nullptr;
	}
}

}  // namespace amber
