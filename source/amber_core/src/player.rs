//! A Ruffle player driven in real time, rendering to an off-screen texture and
//! handing back RGBA on the CPU.
//!
//! ## Why the frame comes back through system memory
//!
//! Ruffle renders through wgpu, which on macOS means Metal. FFGL's plugin
//! interface is OpenGL. There is no cheap way to hand a Metal texture to a GL
//! context inside a host process that owns the GL state, so the frame is read
//! back to the CPU and uploaded as a normal GL texture on the C++ side.
//!
//! That sounds wasteful and is not, at these sizes. Flash content is small by
//! construction -- the default stage is 550x400 -- so a readback is under a
//! megabyte, and the same shape is already proven in cartridge's `pixels`
//! path. Optimising it to an IOSurface would buy little and would be macOS
//! only.
//!
//! ## Timing
//!
//! `Player::tick(dt)` carries its own frame accumulator and its own
//! `max_frames_per_tick` cap, so Ruffle already converts elapsed wall time into
//! the right number of Flash frames and refuses to spiral after a long gap.
//! What it cannot know is that the host sometimes renders the same instant
//! twice -- see `AmberPlayer::advance`.

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;
use std::sync::{Arc, Mutex};

use ruffle_core::limits::ExecutionLimit;
use ruffle_core::tag_utils::movie_from_path;
use ruffle_core::{FloatDuration, Player, PlayerBuilder};
use ruffle_render::backend::ViewportDimensions;
use ruffle_render_wgpu::backend::{
    create_wgpu_instance, request_adapter_and_device, WgpuRenderBackend,
};
use ruffle_render_wgpu::descriptors::Descriptors;
use ruffle_render_wgpu::target::TextureTarget;
use ruffle_render_wgpu::wgpu;

/// Longest gap the player will honour in one advance.
///
/// Beyond this the movie simply skips ahead. When a Resolume layer is bypassed,
/// its deck hidden, or its clip stopped, rendering stops being called at all;
/// coming back forty seconds later and honestly owing forty seconds of Flash
/// would run the whole timeline in a single frame before a pixel reached the
/// screen. The content loses time nobody was watching, which is the right
/// answer. Quarter of a second is about twelve host frames -- long enough to
/// ride out a heavy composition change, short enough not to lurch.
const MAX_ELAPSED_SECONDS: f64 = 0.25;

pub struct AmberPlayer {
    player: Arc<Mutex<Player>>,
    width: u32,
    height: u32,
    /// Cached because reading it needs the lock and the C side asks often.
    frame_rate: f64,
    total_frames: u32,
    /// Re-assert play on every advance. See `set_force_play`.
    force_play_always: std::sync::atomic::AtomicBool,
    /// Unpaid frame time, in seconds. See `advance`.
    accumulator: Mutex<f64>,
}

/// Read a movie's declared stage size without building a player.
///
/// The plugin needs this before it can size a render target, and building a
/// throwaway player to ask would mean creating a wgpu device per open. Parsing
/// the header is essentially free by comparison.
pub fn probe_stage_size(path: &Path) -> Result<(u32, u32), String> {
    catch_unwind(AssertUnwindSafe(|| {
        let movie = movie_from_path(path, None).map_err(|e| format!("could not read SWF: {e}"))?;
        // Ruffle reports the stage in twips; a stage smaller than a pixel is
        // not usable, so both axes are floored at 1.
        let width = movie.width().to_pixels().round().max(1.0) as u32;
        let height = movie.height().to_pixels().round().max(1.0) as u32;
        Ok((width, height))
    }))
    .unwrap_or_else(|_| Err(format!("Ruffle panicked while probing {}", path.display())))
}

impl AmberPlayer {
    /// Open an SWF and build a player rendering at `width` x `height`.
    ///
    /// The wgpu device is created per player rather than shared. Two Resolume
    /// layers each running a movie therefore each own a device, which costs
    /// memory but keeps one instance's GPU error from reaching the other -- the
    /// same isolation argument cartridge makes for copying a libretro core to a
    /// unique path so two layers cannot share globals.
    pub fn open(path: &Path, width: u32, height: u32) -> Result<Self, String> {
        if width == 0 || height == 0 {
            return Err("viewport must be at least 1x1".into());
        }

        // Everything below can panic on malformed content -- Ruffle is a
        // reimplementation of a proprietary format and panics are a normal
        // failure mode for a file it does not like. A panic crossing the C ABI
        // is undefined behaviour, and in-process that means taking Resolume
        // down, so nothing is allowed past this boundary uncaught.
        let built = catch_unwind(AssertUnwindSafe(|| -> Result<Self, String> {
            let instance =
                create_wgpu_instance(Default::default(), wgpu::BackendOptions::default());

            let (adapter, device, queue) =
                futures::executor::block_on(request_adapter_and_device(
                    Default::default(),
                    &instance,
                    None,
                    Default::default(),
                ))
                .map_err(|e| format!("no usable GPU adapter: {e}"))?;

            let descriptors = Arc::new(Descriptors::new(instance, adapter, device, queue));

            let movie = movie_from_path(path, None)
                .map_err(|e| format!("could not read SWF: {e}"))?;

            // num_frames() is the u16 from the SWF header; widened here so the
            // C ABI does not have to care that the format caps at 65535.
            let total_frames = u32::from(movie.num_frames());

            let target = TextureTarget::new(&descriptors.device, (width, height))
                .map_err(|e| format!("could not create render target: {e}"))?;

            let renderer = WgpuRenderBackend::new(descriptors, target)
                .map_err(|e| format!("could not create renderer: {e}"))?;

            let player = PlayerBuilder::new()
                .with_renderer(renderer)
                .with_movie(movie)
                .with_viewport_dimensions(width, height, 1.0)
                .with_autoplay(true)
                .build();

            let frame_rate = player.lock().unwrap().frame_rate();

            Ok(Self {
                player,
                width,
                height,
                frame_rate,
                total_frames,
                // On by default. A VJ dropping a clip on a layer wants it to
                // play; content that sits waiting for a click it will never
                // receive is indistinguishable from a broken plugin.
                force_play_always: std::sync::atomic::AtomicBool::new(true),
                accumulator: Mutex::new(0.0),
            })
        }));

        match built {
            Ok(result) => result,
            Err(_) => Err(format!(
                "Ruffle panicked while opening {}",
                path.display()
            )),
        }
    }

    pub fn width(&self) -> u32 {
        self.width
    }

    pub fn height(&self) -> u32 {
        self.height
    }

    pub fn frame_rate(&self) -> f64 {
        self.frame_rate
    }

    pub fn total_frames(&self) -> u32 {
        self.total_frames
    }

    /// Advance the movie by `elapsed_seconds` of wall time.
    ///
    /// **A zero or negative elapsed time steps nothing.** This is the defence
    /// that matters most in a host: Resolume renders the same instant more than
    /// once -- to the preview, to the program output, to a clip thumbnail --
    /// and a stateful player that ticks on every render runs at double or
    /// triple speed depending on which windows the operator happens to have
    /// open. That is a bug whose reproduction depends on the shape of somebody
    /// else's screen, and it is miserable to diagnose from a report. The same
    /// defence is documented at length in coinop's Sim.h.
    ///
    /// Returns false if Ruffle panicked, after which the player should be
    /// considered dead.
    pub fn advance(&self, elapsed_seconds: f64) -> bool {
        if !(elapsed_seconds > 0.0) {
            return true; // NaN-safe: only a genuinely positive dt steps.
        }
        let dt = elapsed_seconds.min(MAX_ELAPSED_SECONDS);

        // How many whole Flash frames this advance has paid for.
        //
        // This deliberately does NOT use `Player::tick`, which would be the
        // obvious choice. tick() ends by correcting its accumulator against
        // the audio clock:
        //
        //     frame_accumulator += audio_manager.audio_skew_time(..)
        //
        // and amber has no audio by construction -- FFGL provides no audio path
        // at all, so the player is built on a null audio backend. For content
        // whose timeline is synced to a stream sound, which badger.swf and most
        // music-driven Flash is, that correction is computed against a clock
        // that never advances and cancels the time just added. Measured: 120
        // ticks produced 7 distinct frames and then a permanently frozen
        // picture, with every structural check still passing.
        //
        // Driving run_frame() from an accumulator here is what Ruffle's own
        // exporter does, and it makes playback depend only on wall time.
        let frames_due = {
            let frame_duration = 1.0 / self.frame_rate.max(1.0);
            let mut accumulator = self.accumulator.lock().unwrap();
            *accumulator += dt;
            let mut due = 0u32;
            // Defence 3 from coinop's Sim.h: cap catch-up. A layer that was
            // bypassed for a minute must not try to run a minute of Flash in
            // one host frame -- nobody was watching, so that time is forfeit.
            const MAX_FRAMES_PER_ADVANCE: u32 = 8;
            while *accumulator >= frame_duration && due < MAX_FRAMES_PER_ADVANCE {
                *accumulator -= frame_duration;
                due += 1;
            }
            if due == MAX_FRAMES_PER_ADVANCE {
                *accumulator = 0.0;
            }
            due
        };

        if frames_due == 0 {
            return true;
        }

        catch_unwind(AssertUnwindSafe(|| {
            let mut player = self.player.lock().unwrap();

            // Preload before every tick, not once at open.
            //
            // An SWF is a *stream*: tags arrive progressively and Ruffle only
            // makes available what it has processed. Without this the root
            // timeline advances a handful of frames, reaches the end of what
            // was loaded, and simply stops -- rendering a still picture
            // forever while every structural check still passes. On badger.swf
            // that looked like 120 successful renders with 7 distinct frames,
            // which reads as "the animation is nearly static" rather than as a
            // stall. The exporter preloads on every frame for the same reason.
            //
            // ExecutionLimit::none() preloads without budget. That is right for
            // a VJ clip -- a hitch at load is far better than content that
            // silently refuses to start mid-show.
            player.preload(&mut ExecutionLimit::none());

            // Re-assert play on EVERY advance, not once at open.
            //
            // Flash content stops itself constantly and for many reasons: a
            // `stop()` on the root, a preloader waiting on a byte count that
            // will never move because the file came off local disk in one go,
            // a "click to play" gate expecting a mouse this plugin has no way
            // to deliver. Any one of them freezes the timeline permanently.
            //
            // Measured on badger.swf: force-playing once at open advanced 7
            // frames and then stalled on a single badger for the rest of the
            // run, while every check reported success -- 120 renders, all
            // non-blank. Ruffle's own exporter re-applies this before every
            // frame, which is what its --force-play flag exists to do.
            if self
                .force_play_always
                .load(std::sync::atomic::Ordering::Relaxed)
            {
                if !player.is_playing() {
                    player.set_is_playing(true);
                }
                player.mutate_with_update_context(|ctx| {
                    if let Some(root) = ctx.stage.root_clip() {
                        if let Some(clip) = root.as_movie_clip() {
                            if !clip.playing() {
                                clip.play();
                            }
                        }
                    }
                });
            }

            for _ in 0..frames_due {
                player.run_frame();
            }

            // Timers, sockets and stream state still want real elapsed time
            // even though the frames were stepped explicitly above.
            player.update_timers(FloatDuration::from_secs(dt));
        }))
        .is_ok()
    }

    /// Whether to re-assert play before every advance.
    ///
    /// Ruffle warns that forcing play "may break or alter content that expects
    /// user interaction", so it is exposed rather than hardcoded -- but it
    /// defaults on, because content that never starts is the worse failure.
    pub fn set_force_play(&self, always: bool) {
        self.force_play_always
            .store(always, std::sync::atomic::Ordering::Relaxed);
    }

    /// Render the current state and copy it into `out` as tightly packed RGBA.
    ///
    /// `out` must be exactly `width * height * 4` bytes.
    pub fn render_into(&self, out: &mut [u8]) -> Result<(), String> {
        let expected = (self.width as usize) * (self.height as usize) * 4;
        if out.len() != expected {
            return Err(format!(
                "buffer is {} bytes, expected {expected}",
                out.len()
            ));
        }

        let captured = catch_unwind(AssertUnwindSafe(|| {
            let mut player = self.player.lock().unwrap();
            player.render();
            drop(player);

            let mut player = self.player.lock().unwrap();
            let renderer = <dyn std::any::Any>::downcast_mut::<WgpuRenderBackend<TextureTarget>>(
                player.renderer_mut(),
            )?;
            renderer.capture_frame()
        }));

        match captured {
            Ok(Some(image)) => {
                let raw = image.as_raw();
                if raw.len() != expected {
                    return Err(format!(
                        "Ruffle returned {} bytes, expected {expected}",
                        raw.len()
                    ));
                }
                out.copy_from_slice(raw);
                Ok(())
            }
            Ok(None) => Err("no frame captured".into()),
            Err(_) => Err("Ruffle panicked while rendering".into()),
        }
    }

    /// Resize the stage. Rebuilding the render target is the caller's business
    /// -- this only updates the viewport Ruffle lays out against.
    pub fn set_viewport(&mut self, width: u32, height: u32) -> bool {
        if width == 0 || height == 0 {
            return false;
        }
        let ok = catch_unwind(AssertUnwindSafe(|| {
            let mut player = self.player.lock().unwrap();
            player.set_viewport_dimensions(ViewportDimensions {
                width,
                height,
                scale_factor: 1.0,
            });
        }))
        .is_ok();
        if ok {
            self.width = width;
            self.height = height;
        }
        ok
    }

    /// Force the root clip to play, for content that opens on a stopped frame
    /// or waits for a click. Many Flash pieces do; without this they render a
    /// single still frame forever and look like a broken plugin.
    pub fn force_play(&self) -> bool {
        catch_unwind(AssertUnwindSafe(|| {
            let mut player = self.player.lock().unwrap();
            if !player.is_playing() {
                player.set_is_playing(true);
            }
            player.mutate_with_update_context(|ctx| {
                if let Some(root) = ctx.stage.root_clip() {
                    if let Some(clip) = root.as_movie_clip() {
                        if !clip.playing() {
                            clip.play();
                        }
                    }
                }
            });
        }))
        .is_ok()
    }

    pub fn set_playing(&self, playing: bool) -> bool {
        catch_unwind(AssertUnwindSafe(|| {
            self.player.lock().unwrap().set_is_playing(playing);
        }))
        .is_ok()
    }
}
