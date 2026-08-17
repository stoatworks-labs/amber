#pragma once

#include "AmberCore.h"

#include <FFGLSDK.h>

#include <string>
#include <vector>

/**
	Flash, playing live on a Resolume layer.

	An `FF_SOURCE` that hosts a Ruffle player (see `amber_core`, Rust) and draws
	its output as a textured quad. The SWF runs for real -- its timeline, its
	ActionScript, its nested clips -- rather than being a pre-rendered clip, so
	it can be triggered, speed-changed and restarted from the composition.

	## What it costs, stated plainly

	**Ruffle runs in this process, so content that makes it panic takes
	Resolume with it.** Every call into `amber_core` is `catch_unwind`-guarded
	on the Rust side, which converts the great majority of those into a returned
	failure and a black layer -- but a guard is not a process boundary. The
	out-of-process helper that cartridge grew for exactly this reason does not
	exist here yet, and until it does this is the honest description.

	## Traps this class is shaped by

	**No FBO is allocated anywhere.** `FFGLFBO::Initialise` allocates under a
	`ScopedTextureBinding` whose destructor *clears* the binding rather than
	restoring it -- which silently unbinds the input texture on exactly the
	frames that allocate -- and `FFGLFBO::Release` leaks its colour texture.
	A source drawing one quad needs neither.

	**No `ffglex::Scoped*` binding is used.** They all clear to 0 on scope exit
	instead of restoring what was there, so GL state is saved and put back by
	hand, as orrery and cartridge both do.

	**`SetTextParameter` is overridden even though the About line is display
	only.** The SDK's `instantiateGL` sets every parameter's default on a fresh
	instance and deletes the instance if any set returns FF_FAIL; the base
	`SetTextParameter` is a stub returning FF_FAIL. A display-only text
	parameter without this override therefore makes the whole plugin
	un-instantiable in any real host, while remaining invisible to a harness
	that constructs the class directly.

	**Ranged `FF_TYPE_STANDARD` defaults are clamped to 0..1** before
	`SetParamRange` can widen them, so Speed is stored 0..1 and mapped to a
	real multiplier in `SpeedMultiplier()`.

	**The host may render the same instant more than once** -- to the preview,
	to the program output, to a clip thumbnail. A player is stateful, so a
	second render of the same instant must not advance the movie. See
	`ProcessOpenGL`.
*/
namespace amber
{

class AmberPlugin : public CFFGLPlugin
{
public:
	AmberPlugin();
	~AmberPlugin() override;

	FFResult InitGL( const FFGLViewportStruct* vp ) override;
	FFResult DeInitGL() override;
	FFResult ProcessOpenGL( ProcessOpenGLStruct* pGL ) override;

	FFResult SetFloatParameter( unsigned int index, float value ) override;
	float GetFloatParameter( unsigned int index ) override;

	FFResult SetTextParameter( unsigned int index, const char* value ) override;
	char* GetTextParameter( unsigned int index ) override;

private:
	enum ParamId : unsigned int
	{
		PT_FILE = 0,
		PT_RUN,
		PT_RESTART,
		PT_SPEED,
		PT_SCALING,
		PT_SMOOTHING,
		PT_ABOUT,
		PT_COUNT
	};

	enum class Scaling
	{
		Fit,     ///< whole stage visible, letterboxed
		Fill,    ///< fills the output, edges cropped
		Stretch  ///< ignores aspect ratio
	};

	/// Open the movie named by `mMoviePath`, replacing any current one.
	void OpenMovie();
	/// Release the player and its buffer.
	void CloseMovie();

	/// Convert the host's clock into seconds, whatever units it hands over.
	void UpdateClock();

	/// Upload `mPixels` into `mTexture`.
	void UploadFrame();

	/// Draw the movie quad with the current scaling mode.
	void DrawQuad( int viewportWidth, int viewportHeight );

	float SpeedMultiplier() const;

	// --- host parameters ---------------------------------------------------
	std::string mMoviePath;
	bool mRun = true;
	float mSpeed = 0.5f;  ///< 0..1 as the host sees it; 0.5 is 1.0x
	Scaling mScaling = Scaling::Fit;
	bool mSmoothing = true;

	// --- player ------------------------------------------------------------
	// Declared by AmberCore.h, at global scope -- forward-declaring it inside
	// this namespace would silently create a different, incomplete type.
	::AmberHandle* mPlayer = nullptr;
	std::vector< unsigned char > mPixels;
	unsigned int mMovieWidth = 0;
	unsigned int mMovieHeight = 0;
	/// Set when opening failed, so the failure is not retried every frame.
	bool mOpenFailed = false;
	std::string mLastError;

	// --- clock -------------------------------------------------------------
	/// Multiplier turning the host's `hostTime` into seconds. Zero until a
	/// plausible delta has been seen; hosts differ on whether this is seconds
	/// or milliseconds and the header does not say.
	double mClockScale = 0.0;
	double mLastRawTime = -1.0;
	double mHostSeconds = 0.0;
	double mLastHostSeconds = -1.0;

	// --- GL ------------------------------------------------------------------
	GLuint mTexture = 0;
	bool mTextureAllocated = false;

	ffglex::FFGLShader mShader;
	ffglex::FFGLScreenQuad mQuad;
};

}  // namespace amber
