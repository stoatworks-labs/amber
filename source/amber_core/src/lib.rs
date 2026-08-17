//! C ABI for amber's Flash player, consumed by the FFGL plugin.
//!
//! Every entry point is `catch_unwind`-guarded inside `player.rs`, and every
//! one of them is also null-checked here. A panic unwinding across an `extern
//! "C"` boundary is undefined behaviour; in an in-process FFGL plugin that
//! means taking Resolume down mid-show, so the rule is absolute: nothing
//! escapes.
//!
//! Ownership is the usual opaque-handle arrangement. `amber_open` returns a
//! pointer the caller owns and must return to `amber_close` exactly once.

pub mod player;

use std::ffi::{c_char, c_void, CStr};
use std::path::PathBuf;
use std::ptr;

pub use player::AmberPlayer;

/// Opaque handle. The C side never sees the layout.
pub struct AmberHandle {
    player: AmberPlayer,
    /// Last error, kept alive so `amber_last_error` can hand out a pointer.
    last_error: std::ffi::CString,
}

/// Open an SWF. Returns null on failure; pass a buffer to learn why.
///
/// # Safety
/// `path` must be a valid NUL-terminated UTF-8 string. `error_out`, if not
/// null, must point to at least `error_len` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn amber_open(
    path: *const c_char,
    width: u32,
    height: u32,
    error_out: *mut c_char,
    error_len: usize,
) -> *mut AmberHandle {
    let write_error = |message: &str| {
        if error_out.is_null() || error_len == 0 {
            return;
        }
        let bytes = message.as_bytes();
        let count = bytes.len().min(error_len - 1);
        ptr::copy_nonoverlapping(bytes.as_ptr(), error_out as *mut u8, count);
        *error_out.add(count) = 0;
    };

    if path.is_null() {
        write_error("path is null");
        return ptr::null_mut();
    }

    let path = match CStr::from_ptr(path).to_str() {
        Ok(text) => PathBuf::from(text),
        Err(_) => {
            write_error("path is not valid UTF-8");
            return ptr::null_mut();
        }
    };

    match AmberPlayer::open(&path, width, height) {
        Ok(player) => Box::into_raw(Box::new(AmberHandle {
            player,
            last_error: std::ffi::CString::new("").unwrap(),
        })),
        Err(message) => {
            write_error(&message);
            ptr::null_mut()
        }
    }
}

/// # Safety
/// `handle` must have come from `amber_open` and not been closed already.
#[no_mangle]
pub unsafe extern "C" fn amber_close(handle: *mut AmberHandle) {
    if !handle.is_null() {
        drop(Box::from_raw(handle));
    }
}

macro_rules! handle_ref {
    ($handle:expr, $fallback:expr) => {
        match $handle.as_ref() {
            Some(reference) => reference,
            None => return $fallback,
        }
    };
}

/// Advance by `elapsed_seconds` of wall time. A non-positive value steps
/// nothing -- see `AmberPlayer::advance` for why that matters in a host.
///
/// # Safety
/// `handle` must be live.
#[no_mangle]
pub unsafe extern "C" fn amber_advance(handle: *mut AmberHandle, elapsed_seconds: f64) -> bool {
    handle_ref!(handle, false).player.advance(elapsed_seconds)
}

/// Render into a tightly packed RGBA buffer of exactly `width * height * 4`
/// bytes.
///
/// # Safety
/// `out` must point to at least `len` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn amber_render(
    handle: *mut AmberHandle,
    out: *mut c_void,
    len: usize,
) -> bool {
    if out.is_null() {
        return false;
    }
    let handle = match handle.as_mut() {
        Some(reference) => reference,
        None => return false,
    };

    let buffer = std::slice::from_raw_parts_mut(out as *mut u8, len);
    match handle.player.render_into(buffer) {
        Ok(()) => true,
        Err(message) => {
            handle.last_error =
                std::ffi::CString::new(message).unwrap_or_else(|_| std::ffi::CString::new("").unwrap());
            false
        }
    }
}

/// # Safety
/// `handle` must be live. The returned pointer is owned by the handle and is
/// valid only until the next failing call.
#[no_mangle]
pub unsafe extern "C" fn amber_last_error(handle: *mut AmberHandle) -> *const c_char {
    match handle.as_ref() {
        Some(reference) => reference.last_error.as_ptr(),
        None => c"invalid handle".as_ptr(),
    }
}

/// # Safety
/// `handle` must be live.
#[no_mangle]
pub unsafe extern "C" fn amber_width(handle: *mut AmberHandle) -> u32 {
    handle_ref!(handle, 0).player.width()
}

/// # Safety
/// `handle` must be live.
#[no_mangle]
pub unsafe extern "C" fn amber_height(handle: *mut AmberHandle) -> u32 {
    handle_ref!(handle, 0).player.height()
}

/// The movie's own frame rate, which is NOT the host's. The plugin needs this
/// only for display; `amber_advance` already converts wall time correctly.
///
/// # Safety
/// `handle` must be live.
#[no_mangle]
pub unsafe extern "C" fn amber_frame_rate(handle: *mut AmberHandle) -> f64 {
    handle_ref!(handle, 0.0).player.frame_rate()
}

/// # Safety
/// `handle` must be live.
#[no_mangle]
pub unsafe extern "C" fn amber_total_frames(handle: *mut AmberHandle) -> u32 {
    handle_ref!(handle, 0).player.total_frames()
}

/// # Safety
/// `handle` must be live.
#[no_mangle]
pub unsafe extern "C" fn amber_force_play(handle: *mut AmberHandle) -> bool {
    handle_ref!(handle, false).player.force_play()
}

/// # Safety
/// `handle` must be live.
#[no_mangle]
pub unsafe extern "C" fn amber_set_playing(handle: *mut AmberHandle, playing: bool) -> bool {
    handle_ref!(handle, false).player.set_playing(playing)
}

/// # Safety
/// `handle` must be live.
#[no_mangle]
pub unsafe extern "C" fn amber_set_viewport(
    handle: *mut AmberHandle,
    width: u32,
    height: u32,
) -> bool {
    match handle.as_mut() {
        Some(reference) => reference.player.set_viewport(width, height),
        None => false,
    }
}
