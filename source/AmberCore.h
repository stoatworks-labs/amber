#pragma once

/**
	C ABI of `amber_core`, the Rust crate that embeds Ruffle.

	Hand-written rather than generated. The surface is small enough that a
	bindgen step would add a build dependency and a chance of drift for no gain,
	and every declaration here has a counterpart marked `#[no_mangle] extern
	"C"` in `source/amber_core/src/lib.rs`. **Change one and you must change the
	other** -- a mismatch links cleanly and misbehaves at runtime.

	Every function is safe to call with a null handle and returns a benign
	value; nothing here can unwind, because a Rust panic crossing this boundary
	would be undefined behaviour and the Rust side catches all of them.
*/

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/// Opaque player. Created by amber_open, released by amber_close, exactly once.
typedef struct AmberHandle AmberHandle;

/// Read a movie's declared stage size without building a player. Cheap: it
/// parses the header and creates no GPU resources. Returns false on failure.
bool amber_probe_size( const char* path, unsigned int* width_out, unsigned int* height_out );

/// Open an SWF and build a player rendering at width x height.
/// Returns NULL on failure, writing a NUL-terminated reason into error_out.
AmberHandle* amber_open( const char* path,
                         unsigned int width,
                         unsigned int height,
                         char* error_out,
                         size_t error_len );

void amber_close( AmberHandle* handle );

/// Advance by elapsed_seconds of wall time.
///
/// **A non-positive value steps nothing**, which is deliberate and load
/// bearing: the host renders the same instant more than once and a stateful
/// player must not advance on the repeats.
bool amber_advance( AmberHandle* handle, double elapsed_seconds );

/// Render the current state into a tightly packed RGBA buffer, which must be
/// exactly amber_width * amber_height * 4 bytes.
bool amber_render( AmberHandle* handle, void* out, size_t len );

/// Reason the last failing call failed. Owned by the handle.
const char* amber_last_error( AmberHandle* handle );

unsigned int amber_width( AmberHandle* handle );
unsigned int amber_height( AmberHandle* handle );

/// The movie's own frame rate. Not the host's, and not needed for pacing --
/// amber_advance already converts wall time correctly.
double amber_frame_rate( AmberHandle* handle );
unsigned int amber_total_frames( AmberHandle* handle );

/// Re-assert play before every advance. On by default: Flash content stops
/// itself for many reasons and content that never starts looks like a broken
/// plugin.
bool amber_force_play( AmberHandle* handle );

/// Render the stage with a transparent background -- Flash's own
/// `wmode=transparent`, which Ruffle implements fully. Without it, content
/// authored on a transparent stage arrives with an opaque rectangle behind it
/// and covers every layer underneath.
bool amber_set_transparent( AmberHandle* handle, bool transparent );
bool amber_set_playing( AmberHandle* handle, bool playing );
bool amber_set_viewport( AmberHandle* handle, unsigned int width, unsigned int height );

#ifdef __cplusplus
}  // extern "C"
#endif
