# AGENTS.md — amber

Onboarding for an LLM or a newcomer. `README.md` is the user-facing document;
this is the *why*, plus what is genuinely verified and what is not.

## What this is

Legacy Flash (`.swf`) and Flash Video (`.flv`) converted into codecs Resolume
plays natively. Started 2026-08-17. Intended **PUBLIC MIT**.

Two phases, both now exist:

1. **The converter** — a Python CLI in `tools/`, driving the operator's own
   ffmpeg and Ruffle exporter as subprocesses.
2. **The FFGL plugin** — an `FF_SOURCE` that embeds Ruffle and plays SWFs live
   on a Resolume layer. **Confirmed working in Resolume Arena on Apple Silicon**
   (2026-08-17). The Intel slice and the Windows build have not been run in a
   host.

## The architectural decision everything else follows from

**amber vendors nothing and links nothing.** ffmpeg and Ruffle are invoked as
*subprocesses*.

This is not squeamishness. The fleet ships MIT and takes Patreon income, and
the useful FLV decoders (VP6 especially) live in ffmpeg under LGPL. Linking
`libavcodec` would put an LGPL obligation on distribution; spawning the
operator's own `ffmpeg` binary does not, because a separate process is not a
derived work. It also sidesteps the ABI problem entirely — the dev machine
carries `libavcodec.62.28.102`, and a plugin that `dlopen`s a versioned soname
breaks on every ffmpeg upgrade.

The cost is that capability becomes a runtime question, which is why
`probe.py` interrogates ffmpeg rather than assuming, and why `amber doctor`
exists.

Ruffle is MIT-OR-Apache-2.0 and could safely be vendored. It still is not,
because its library API carries no stability guarantee and the exporter binary
is a clean, stable-enough boundary.

## The traps, all measured on this machine

**DXV silently corrupts any width that is not a multiple of 16.**
The encoder returns success, writes a file of plausible size, records the
correct dimensions in the header, and the picture inside is sheared diagonally
— rows written at the padded stride and read back at the requested one. Nothing
at any ffmpeg log level mentions it.

Measured by sweeping 20 widths and comparing raw RGBA through a roundtrip:
every `w % 16 == 0` gave mean error ~0.65, every other width gave ~125. Height
is entirely unconstrained — even odd heights are fine.

It matters because **550×400 is the default Flash stage size**. The badger.swf
used to develop this is 550×400, and the first working version of the converter
produced 904 frames of diagonally sheared badgers while reporting complete
success. It was caught by *looking at a frame*, not by any check.

**Hap refuses instead of corrupting.** `hap`, `hap_alpha` and `hap_q` all
reject a size that is not a multiple of 4 with a clear error, and are correct at
every legal size including widths divisible by 4 but not 16. An encoder that
says no cannot ship a broken show; DXV is the dangerous one.

**DXV cannot carry alpha.** One format, `dxt1`, self-described "No Alpha". An
RGBA input comes back with every alpha byte 0xff and no warning. Since `vp6a`
exists specifically for transparent overlay loops, defaulting alpha content to
DXV would destroy the exact property that made converting it worthwhile.
`choose_profile()` therefore raises rather than warns.

Hap Alpha was measured **byte-exact** through a roundtrip. ProRes 4444 was off
by one 8-bit step in places (the trip through 10-bit YUV) and is the fallback
only.

**Plain `hap` is strictly worse than DXV and should never be chosen over it.**
Measured on four seconds of gradient-heavy 720p footage: `dxv` and `hap` scored
an *identical* 44.96 dB with an identical maximum error of 44 -- they are both
DXT1 -- while `hap` was **27% larger** (14MB against 11MB). `hap_q` reached
46.76 dB and more than halved the worst-case error to 19, for 20MB. So the
profile order in `choose_profile` is not a guess: DXV for opaque content by
default, `hap_q` when gradients block up, and plain `hap` never on merit -- it
exists only as an alpha-less sibling of the Hap family.

**Ruffle's exporter pads filenames to the frame count.** 904 frames are written
`000.png`; 60 frames are written `00.png`. A hardcoded `%03d` works on long
content and silently matches nothing on short. `_frame_pattern()` reads the
width from the files that exist, and refuses a directory holding mixed widths.

**The exporter never reports a frame rate.** It writes numbered PNGs and stops.
The rate comes from the SWF header (`swf.py`), and left to ffmpeg's 25fps
default a 12fps animation plays at slightly over double speed with nothing in
any log to say so.

**The exporter has no background or transparency option**, so the *converter*
cannot produce transparent output. This is a gap in the exporter's argument
list, **not a limitation of Ruffle** — see "Transparency" below. `convert_swf`
raises on `transparent=True` rather than quietly returning an opaque clip.

**SWF frame rate is 8.8 fixed point, little-endian.** Low byte is the fraction.
Read as a plain uint16 it comes out 256× too large. 29.97 is not representable
and quantises to 29.96875; that is the format, not a bug.

**ZWS (LZMA) SWFs are not plain `.lzma` files.** Bytes 8–11 are the compressed
length and 12–16 the LZMA properties, so the stream starts at 17, and Python's
`FORMAT_ALONE` needs a synthesised 8-byte size field that the SWF header does
not provide in the right width. Rebuilt from the declared file length.

**FLV's `r_frame_rate` is routinely a lie, and `avg_frame_rate` is the answer.**
FLV timestamps are milliseconds, so the container time base is 1/1000, and
ffprobe defines `r_frame_rate` as the lowest rate representing every timestamp
exactly -- which collapses to **1000/1** whenever the frame intervals are not a
neat divisor of it. Measured on a real bars-and-tone card: `r_frame_rate`
1000/1, `avg_frame_rate` 10/1. `probe_media` prefers avg and falls back to r
only when ffprobe cannot compute it (0/0 on a stream with no duration).

**A `vp6a` fixture can be ASSEMBLED without a VP6 encoder** — `tools/make_vp6a.py`.
This closed the last hole in the converter's verification. ffmpeg decodes VP6 but
cannot encode it and no free VP6 encoder exists, so `vp6a` looked untestable. It
is not: FLV codec 4 is VP6 and codec 5 is VP6-with-alpha, and the only difference
is structural —

    codec 4:  [frametype|4] [adjustment] <VP6 stream>
    codec 5:  [frametype|5] [adjustment] [UI24 offset] <VP6 colour> <VP6 alpha>

— because **the alpha plane is itself an ordinary VP6 stream**, decoded as
greyscale. So a genuine `vp6a` file can be built from VP6 bitstreams that already
exist, reusing each frame's colour stream as its own alpha plane. ffmpeg reports
the result as `vp6a` / `yuva420p`, and it decodes to **132 distinct alpha values
spanning 11–234**. Through `hap_alpha` the range is preserved exactly with a mean
error of 0.05 (max 11, DXT5 block interpolation at a boundary).

The alpha is correlated with luma rather than independent, and the file is not a
sample of what a period authoring tool emitted. For "does the pipeline detect
alpha, refuse DXV, and route to Hap Alpha without losing it", neither matters.

**A sparse FLV is a real thing, not a conversion failure.** `barsandtone.flv` is
six seconds long and contains exactly **two** video frames -- a static card held
for the duration. A converter reporting "2 frames" for a 6s clip looks broken
and is correct; check `ffprobe -count_frames` on the *source* before chasing it.

**Homebrew Python is PEP 668 managed** — `pip install pytest` fails. `verify.sh`
creates `.venv` itself.

## The plugin

`source/amber_core/` is a Rust staticlib embedding `ruffle_core` +
`ruffle_render_wgpu`, **pinned to git rev `ae0ba6d`** — Ruffle offers no API
stability whatsoever, so this is the same discipline the fleet applies to the
FFGL SDK at `b1afaf9`. It exposes a small C ABI (`source/AmberCore.h`,
hand-written; change one side and you must change the other). `source/Plugin.cpp`
is the FFGL side.

**The frame crosses from Metal to OpenGL through system memory.** Ruffle renders
via wgpu, FFGL is OpenGL, and there is no cheap way to hand a Metal texture to a
GL context the host owns. Flash is small by construction, so a 550x400 readback
is under a megabyte — the same shape as cartridge's `pixels` path. An IOSurface
would buy little and be macOS-only.

**Every call into Rust is `catch_unwind`-guarded.** Ruffle panics are a normal
failure mode for content it dislikes, and a panic crossing `extern "C"` is
undefined behaviour — in-process that means taking Resolume down mid-show. A
guard is not a process boundary, though: **content that hard-crashes Ruffle still
takes Resolume with it.** cartridge's out-of-process helper is the answer and
does not exist here yet.

### Four things that each produced a frozen or wrong picture while every check passed

**`preload()` must run before every frame, not once.** SWF tags load
progressively; without it the timeline advances a few frames and stops forever.

**Play must be re-asserted every advance.** Flash content stops itself
constantly — a `stop()` on the root, a preloader waiting on a byte count that
never moves because the file came off local disk in one go, a click-to-play gate
expecting a mouse the plugin cannot deliver. Forcing play once at open advanced
badger.swf 7 frames and then froze on a single badger.

**`Player::tick()` is the WRONG API here, and this is the subtle one.** It ends
by correcting its own accumulator against the audio clock:

    frame_accumulator += audio_manager.audio_skew_time(..)

amber has no audio by construction — FFGL provides no audio path at all, so the
player runs on a null audio backend. For content whose timeline is synced to a
stream sound, which badger.swf and most music-driven Flash is, that correction is
computed against a clock that never advances and **cancels the time just added**.
Measured: 120 ticks, 7 distinct frames, then a permanently frozen picture. amber
owns the accumulator and calls `run_frame()` directly, so playback depends only
on wall time. Ruffle's own exporter does the same.

**Ruffle's rows are top-first; GL's are bottom-first.** Uploading straight
through renders the movie upside down and correct in every other respect — right
size, fully opaque, changing over time, passing every assertion a harness can
make without looking at it. `Plugin.cpp`'s fragment shader flips V. This is the
third conflicting idea of row 0 in the fleet; cartridge documents the same
collision.

### Transparency

**`wmode=transparent` is fully implemented in Ruffle and needed no patch.**
`Player::render` already clears to `Color::from_rgba(0)` instead of the stage
colour whenever the window mode is Transparent and the stage is not fullscreen,
and `Player::set_window_mode(&str)` is public. amber calls it through
`amber_set_transparent`, and the plugin exposes it as **Transparent**, default
**on** — a source on a Resolume layer is far more often wanted as an overlay
than as a backdrop, and an unwanted transparent background is trivially fixed by
putting something underneath while an unwanted opaque one hides every layer
below.

It is worth being explicit that this was a false alarm. Ruffle's `exporter` CLI
has no background flag, and it is easy to conclude from that alone that Ruffle
cannot do transparency at all. The gap is in the exporter's arguments; the
engine has supported it the whole time. The **converter** still cannot do it,
because it drives the exporter binary; the **plugin** can, because it embeds
the engine directly.

**Testing it needs content that does not fill its own stage.** badger.swf paints
sky and grass across the entire stage, so a transparent stage is completely
invisible in it — zero clear pixels is the *correct* answer there, and asserting
otherwise is a bug (one that briefly lived in `ambertest`). `tests/fixtures/
shape.swf` exists for this: a hand-built `DefineShape` covering exactly a
quarter of the stage, so transparency must leave ~75% clear. Measured: **75.7%
clear** through the Rust player and **74.9%** through the plugin's full GL path.

Two traps in hand-building that fixture, both of which produce a valid file that
draws nothing at all:
- **`NumBits` in a StraightEdgeRecord is a FOUR-bit field holding (actual - 2)**,
  so the widest edge expressible is 17 bits. Asking for 20 writes 18 into four
  bits, truncating and corrupting every edge after it.
- **A path traced clockwise in SWF coordinates (y down) has its interior on the
  RIGHT**, so the fill belongs to `fillStyle1`. Setting `fillStyle0` gives a
  valid shape enclosing nothing.

### The double-render guard

Resolume renders the same instant more than once — preview, program output, clip
thumbnail. A stateful player that stepped on every render would run at double or
triple speed **depending on which windows the operator has open**, which is
miserable to diagnose from a bug report. Guarded twice: the plugin only computes
a positive elapsed time from a moving host clock, and `amber_advance` refuses a
non-positive dt again on its own side. `ambergl` asserts it. The same defence is
documented at length in coinop's `Sim.h`.

`hostTime`'s units are not specified by FFGL and hosts disagree, so the scale is
inferred once from the first plausible delta — same approach as coinop.

## Verifying the plugin without Resolume

**Resolume's GUI must not be driven with synthesized input.** Clicks on its
custom-drawn UI do not register, and synthesized keystrokes reach the composition
as clip triggers — this has modified a live project before. Two harnesses
instead, and they cover different halves:

- `./build/ambergl <file.swf>` — links the plugin class directly into a real CGL
  4.1 context. Covers the render path, the double-render guard, and **both aspect
  branches** (a sign error in letterbox arithmetic is invisible at one aspect
  ratio only). Cannot see registration.
- `oxbow probe build/Amber.bundle` — loads the real bundle through a real FFGL
  host and enumerates it. This is what proves `plugMain` is exported and the
  plugin actually registers, which a directly-linked harness cannot.

**`oxbow selftest` cannot drive amber**, because its `--set` routes every
assignment through `setParamFloat`, including `FF_TYPE_FILE` ones — so the movie
path never arrives and the layer is correctly transparent. That is an oxbow
limitation worth reporting upstream; it also stops oxbow testing cartridge's
Core/Content parameters.

Check the bundle by hand after any link change:

    nm -gU build/Amber.bundle/Contents/MacOS/Amber | grep plugMain
    lipo -archs build/Amber.bundle/Contents/MacOS/Amber

## Layout

```
tools/amber          entry script
tools/amberkit/
  swf.py             SWF header only -- never a tag, never an opcode
  probe.py           locate ffmpeg/ffprobe/exporter, interrogate capabilities
  align.py           the measured codec dimension constraints
  convert.py         the pipelines
  cli.py             doctor / info / convert
tests/               34 tests; make_fixtures.py synthesises real SWF bytes
tools/verify.sh      the whole pass

source/amber_core/   Rust: Ruffle embedded behind a C ABI (pinned ae0ba6d)
source/AmberCore.h   the C ABI, hand-written -- keep in step with lib.rs
source/Plugin.cpp    the FFGL source plugin
source/SourcePlugin.cpp  registration ONLY -- see below
tools/ambergl/       the GL harness
external/ffgl/       Resolume FFGL SDK, pinned b1afaf9
```

**`FFGLSDK.cpp` is an umbrella that `#include`s every other SDK .cpp.** Compiling
the rest alongside it defines every symbol twice and fails the link with several
hundred duplicates. It is the only SDK source in CMakeLists.

**`SourcePlugin.cpp` holds the registration and nothing else.**
`CFFGLPluginInfo` registers from a file-scope constructor nothing references by
name, so from a STATIC archive the linker may drop the whole translation unit,
giving a bundle that loads, exports `plugMain`, and reports that it contains no
plugins. Everything is compiled into one target here, which removes the archive
and with it the trap.

## Test corpus — deliberately not in the repo

Legacy Flash is third-party content whose licence is almost never clear.
Following [cartridge]'s "ships no ROMs, ever" rule, amber commits **none** of
it. The corpus lives at `~/Documents/Amber/corpus/` and `.gitignore` blocks
`*.swf` and `*.flv` outright.

The development file is `badger.swf` (444,807 bytes, SHA-256
`d1137e52…75dbbc`), from archive.org item `flash_badger` — "Badger" by
AlbinoBlackSheep (John Picking). Tests that need it skip cleanly when it is
absent.

Fixtures in `tests/` are *synthesised* — real SWF bytes with correct
signatures, bit-packed RECTs, genuine zlib/LZMA compression — because a fixture
generated by the code under test proves nothing.

## Verified vs assumed

**Verified by measurement:**
- The SWF header reader against real 2003 Flash. The badger's declared file
  length matches its actual byte count exactly, which is what proves the
  offsets are right rather than merely plausible.
- All three SWF compressions, including LZMA.
- Full FLV decoder coverage present in ffmpeg 8.1.2.
- FLV → DXV and SWF → DXV end to end, output re-decoded and compared to source
  pixels; badger renders 904 frames at exactly 25fps / 36.160000s.
- The DXV width-16 rule and the Hap multiple-of-4 rule, both swept.
- Hap Alpha preserving alpha byte-exact; DXV destroying it.

**Assumed, or simply not done:**
- **The plugin IS confirmed working in Resolume Arena on Apple Silicon**
  (2026-08-17). What is still assumed: that the **Intel slice** of the universal
  bundle works in an Intel Resolume, and that the **Windows** build works at all
  in a host — neither has been run. Converter output has never been opened in
  Resolume either; every claim about what it plays comes from the codec choice.
- **Whether Resolume's own DXV decoder reproduces ffmpeg's shear is unknown.**
  It may read the padded stride correctly and play a 550-wide file fine. amber
  aligns anyway — ffmpeg's decoder is the only one testable here, and shipping
  footage whose correctness depends on an untested disagreement between two
  decoders is not worth the six pixels.
- **Only one real SWF has ever been converted.** The badger is AS1/AS2 vector
  with a fixed timeline. Nothing with AS3, external assets, embedded video,
  runtime-loaded content, or a stage that resizes has been tried.
- **No FLV in the wild has been converted** — the FLV tests use files
  synthesised by ffmpeg, so Spark and H.264 are exercised but **VP6 and VP6-alpha
  are not**, because ffmpeg can decode but not encode them. VP6-alpha is exactly
  the case the alpha logic exists for, and it is the largest untested gap.
- Windows and Linux: the ffmpeg discovery paths in `probe.py` are macOS
  Homebrew paths with a bare-name PATH fallback. Never run elsewhere.

## Not done

- **Transparent SWF *conversion*.** The plugin does transparency; the converter
  cannot, because it drives the exporter binary and the exporter has no
  background flag. The fix is a small patch to `exporter/src/cli.rs` adding one,
  which would be worth sending upstream — Ruffle's engine already supports it.
- **Audio.** Nothing carries it, and FFGL has no audio path at all, so the
  eventual plugin will be silent regardless.
- **The FFGL plugin.** Design constraints established: follow cartridge's
  in-process/out-of-process split; Ruffle renders through wgpu (Metal on macOS)
  while FFGL is OpenGL, so the frame most likely comes back via CPU readback,
  which is cheap at Flash resolutions; a Rust↔C++ boundary is needed, the same
  shape as cartridge's libretro C boundary; and Ruffle offers no stable library
  API, so a pinned git rev is required — the same discipline already used for
  the FFGL SDK at `b1afaf9`.

## Related

`cartridge` (the architectural template), `old-cathode`, `plugin-bench`.
The fleet's FFGL trap list applies in full to phase two.

## Notes

`docs/NOTES.md` carries this repo's working notes — current status, decisions
already made, and the traps that have actually bitten. Read it before changing
anything non-obvious. Cross-cutting fleet knowledge lives in
[fleet-notes](https://github.com/stoatworks-labs/fleet-notes).
