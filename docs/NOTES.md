# Notes

Working notes for this repo: status, decisions, and the traps that have actually bitten.
Migrated out of Claude Code's memory on 2026-08-24, so they are written in the first
person and dated by when each thing was learned — that date is usually the useful part.

Cross-cutting notes that are not specific to this repo live in
[fleet-notes](https://github.com/stoatworks-labs/fleet-notes).

*amber — legacy Flash (.swf/.flv) converted to Resolume-native DXV/Hap; converter DONE and committed local-only, FFGL plugin NOT started; vendors nothing, never loaded into Resolume*

`~/Projects/resolume/amber` — started **2026-08-17**. Makes a decade and a half
of stranded VJ content playable: Resolume opens neither `.swf` nor `.flv`.
**PUBLIC MIT, LIVE**: `github.com/stoatworks-labs/amber`, released **v0.1.0**
2026-08-17, site page at `stoatworks-labs.com/software/amber/`. Branch `main`.

Named `amber` (content fossilised and still visible). "Flash"/"Shockwave" are
Adobe marks and were avoided deliberately, same rule as [coinop](https://github.com/stoatworks-labs/coinop/blob/main/docs/NOTES.md) (`coinop`).

**Two phases, BOTH now exist.**

1. **Converter — DONE.** Python CLI, `tools/amber` with `doctor` / `info` /
   `convert`. 34 tests, `tools/verify.sh` green.
2. **FFGL plugin — CONFIRMED WORKING IN RESOLUME ARENA** (Allan verified
   2026-08-17, Apple Silicon). Params: Movie / Run / Restart / Speed / Scaling /
   Smoothing / Transparent. **macOS universal + Windows x64 both built.** Still
   `.swf` only, no seek/scrub, no out-of-process helper; the **Intel slice and
   the Windows DLL have never been loaded into a host**.

Scope was confirmed with Allan up front (both FLV *and* SWF, converter first
then plugin, user-supplied ffmpeg) — see
**confirm scope before researching** (working-practice note, kept in Claude memory).

## The decision everything follows from: vendors nothing

ffmpeg and Ruffle are spawned as **subprocesses**. The fleet ships MIT and takes
Patreon income, and the useful FLV decoders (VP6) are LGPL inside ffmpeg —
linking libavcodec would create a distribution obligation, spawning the
operator's own binary does not. It also kills the ABI problem (`libavcodec.62.28.102`).
Same instinct as never baking in libndi. Cost: capability is a runtime question,
hence `probe.py` and `amber doctor`.

Ruffle is MIT-OR-Apache-2.0 and *could* be vendored; it is not, because its
library API has no stability guarantee and the exporter binary is a stable
boundary.

## What was learned, in priority order

**The DXV and Hap encoder traps are in their own note —
[dxv hap encoder traps](https://github.com/stoatworks-labs/fleet-notes/blob/main/notes/reference_dxv_hap_encoder_traps.md) — and they are severe** (silent diagonal
shear at any width not divisible by 16; no alpha at all; only `ffmpeg-full` has
Hap). Read that before touching any DXV pipeline anywhere in the fleet.

**Ruffle's exporter is the SWF renderer and it works well.** Builds in ~43s
(`cargo build --release --package=exporter`), needs Java for AS3 builtins
(Temurin 25 present). Renders 904 frames of real Flash in 2.4s via Metal.
Ruffle does **not** ship it in releases. Clone at
`~/Documents/Amber/ruffle-src`, pinned nowhere yet — HEAD was `ae0ba6d`
(2026-08-16).

Three exporter behaviours that each break a naive pipeline:
- **Filenames pad to the frame count** — `000.png` for 904 frames, `00.png` for
  60. A hardcoded `%03d` works on long content and matches nothing on short.
- **It never reports a frame rate.** Comes from the SWF header instead; ffmpeg's
  25fps default silently doubles the speed of 12fps content.
- **No background/transparency option**, so its frames are always opaque —
  transparent Flash stages do **not** round-trip today.

**SWF is vector, so the DXV width constraint costs nothing there** — Ruffle
rasterises straight to the legal size via `--width/--height`, a clean re-render
rather than a resample. FLV has no such luxury and is scaled (lanczos) or padded.

**SWF header details**: frame rate is 8.8 fixed LE (low byte = fraction; read as
uint16 it is 256× too big). 29.97 quantises to 29.96875 — the format, not a bug.
**ZWS/LZMA is not a plain `.lzma` file**: compressed length at 8–11, properties
at 12–16, stream from 17, and Python's `FORMAT_ALONE` needs a synthesised 8-byte
size field rebuilt from the declared file length.

**Homebrew Python is PEP 668 managed** — `verify.sh` makes its own `.venv`.

## Release v0.1.0 — what shipped and what is still owed

Assets: `amber-0.1.0-macos-universal.{dmg,zip}` (signed + notarised, verified by
downloading the published files with `verify-signing.sh`) and
`amber-0.1.0-windows-x86_64.zip`. Autosign state was **seeded by hand** because
the assets were signed locally before upload — otherwise the agent re-signs and
the download block goes stale within 15 minutes.

**SIX of the seven release homes are done**: repo docs, the release, downloads
(`gen-downloads.py`, clean 129-insert diff), `projects.json` + thumbnail, the site
deployed and verified at the Worker origin, and the **video — published
2026-08-17 at `vNWyO8A20Fk`**, with both embeds (README + `projects.json`
`youtube`/`videoDate`) landed in the same pass. **Instagram is the only one
outstanding**; `make_social.py amber` cuts it and it is SLOW (the 9x16 reel takes
many minutes). Post with `publish_instagram.py reel --only amber` — **never
without `--only`, which posts every cut in the project**.

## The third-party-content rule, and where the line was drawn

amber's appeal is *real Flash*, and every real Flash file that shows it off is
someone else's copyright. Allan's call, and it is a good one: **short clips for
demonstration, with attribution, are fine; promotional artwork is not.**

- The **video** uses short `badger.swf` clips, credited in the description, on
  the website (a `credits` block added to `src/pages/software/[slug].astro`,
  new — it renders `detail.credits.{text,links}`), and in the repo's README and
  ATTRIBUTIONS.md. Links to the original (`EIyixC9NsLI`) and Weebl's channel
  (`@MrWeebl`).
- The **thumbnails** — website and YouTube both — use
  `build_swf_showcase()` output instead, which the project owns outright.
  badger.swf was caught *on its way to becoming the website thumbnail*.
- **amber has no audio at all**, so the footage is silent and the song never
  appears — which is what would otherwise attract a Content ID claim.

`tools/ambergl` grew `--every N`, `--transparent/--opaque`, `--scaling` and
`--speed` so footage can be rendered from the real plugin class. **Do not render
1080p PAMs for a long take** — they are 8MB a frame and 780 frames was 6GB.
Encode each beat to mp4 and delete the frames as you go.

**A co-session committed the amber `projects.json` entry** inside its own "Add
vectrix" commit while this session was still working — **cosession shared checkout** (working-practice note, kept in Claude memory)
happening live. Harmless here, but check `git log` rather than assuming your
website edits are still uncommitted.

## Cross-platform build traps (2026-08-17)

**macOS universal:** cargo cannot emit a universal staticlib. Build each target
separately and `lipo -create` the two archives, then let CMake build universal
C++ against the combined one. `-DAMBER_UNIVERSAL=OFF` for a fast dev build.

**Windows needed three things macOS did not**, each failing differently:
1. **Java** — Ruffle builds its AS3 `playerglobal` with it. The VM had none;
   installed Temurin 21 **aarch64** (the VM is ARM64 Windows) at `C:\jdk`.
2. **GLEW** — FFGL's headers `#include <GL/glew.h>` on Windows. From vcpkg as
   `x64-windows-static-md`; it was in `C:\vcpkg\packages` but **not installed**,
   so `find_package(GLEW)` failed until `vcpkg install glew:x64-windows-static-md`.
3. **An explicit cargo target triple.** The VM is ARM64 Windows, so a bare
   `cargo build` gives an ARM64 staticlib against an x64 DLL — `LNK4272` plus ten
   unresolved externals, which **reads like a broken C ABI rather than the wrong
   architecture**. `-DAMBER_CARGO_TARGET=x86_64-pc-windows-msvc` fixes both the
   flag and the `target/<triple>/` path.

Also: **VS Build Tools lives under `Program Files (x86)` even on ARM64**, and
CMake ships inside it rather than on PATH — find it with `vswhere`, not a guess.

## Real-FLV findings (2026-08-17)

**FLV's `r_frame_rate` is routinely a LIE.** FLV timestamps are milliseconds, so
the time base is 1/1000 and ffprobe's `r_frame_rate` collapses to **1000/1**
whenever frame intervals are not a neat divisor of it. barsandtone reported
`r_frame_rate` 1000/1 against `avg_frame_rate` 10/1 — amber said "1000fps" for a
10fps file. **Use `avg_frame_rate`** (measured, not derived); fall back to `r`
only when ffprobe gives 0/0.

**A sparse FLV is real, not a failure.** barsandtone genuinely holds **two**
video frames across six seconds — a static card. "2 frames" from a 6s clip looks
broken and is correct; check `ffprobe -count_frames` on the SOURCE first.

**Plain `hap` is strictly worse than DXV — never choose it on merit.** Measured
on gradient-heavy 720p against a lossless reference: `dxv` and `hap` both scored
**44.96 dB with an identical max error of 44** (both are DXT1), but `hap` was
**27% larger**. `hap_q` gave 46.76 dB and **halved max error to 19** for ~80%
more size. So: DXV by default, `hap_q` when gradients block up, plain `hap` only
as the alpha-less sibling of the Hap family.

**DXV files are big:** 28s of 3840x2160 → **1.4GB**.

**A `vp6a` fixture can be ASSEMBLED with no VP6 encoder** — `tools/make_vp6a.py`.
ffmpeg decodes VP6 but cannot encode it and no free encoder exists, so `vp6a`
looked untestable. It is not: FLV codec 4 is VP6, codec 5 is VP6-with-alpha, and
codec 5 is just **two VP6 bitstreams back to back with a UI24 offset** —
`[frametype|5][adjustment][UI24 offset]<colour><alpha>` — because **the alpha
plane is itself an ordinary VP6 stream decoded as greyscale**. So reuse each
frame's colour stream as its own alpha plane. ffmpeg then reports `vp6a` /
`yuva420p` with **132 distinct alpha values spanning 11–234**; through
`hap_alpha` the range survives exactly at mean error 0.05 (max 11 = DXT5 block
interpolation). Alpha correlates with luma and it is not a period-authentic
sample — irrelevant for testing the routing.

**Hap needs ÷4, DXV needs ÷16**, so 360x288 survives intact through `hap_alpha`
while DXV forces 360→352. The per-profile constraint table is load-bearing.

## Test corpus — deliberately outside the repo

Legacy Flash is third-party with unclear licence. Following cartridge's "ships
no ROMs, ever", **nothing is committed**: `.gitignore` blocks `*.swf`/`*.flv`
and the corpus lives at `~/Documents/Amber/corpus/`.

Development file is **`badger.swf`** (444,807 bytes, 550x400, 25fps, 904 frames,
SWF v5) — archive.org item `flash_badger`, "Badger" by AlbinoBlackSheep (John
Picking). **Allan supplied this link himself mid-session**, which is what
unblocked real-content testing; cartridge stalled for want of exactly this.

`tests/make_fixtures.py` **synthesises** real SWF bytes (correct signatures,
bit-packed RECTs, genuine zlib/LZMA) — a fixture built by the code under test
proves nothing.

## Verified vs assumed

**Verified:** header reader against real 2003 Flash (the badger's declared file
length matches its actual byte count exactly — that is what proves the offsets);
all three compressions; FLV→DXV and SWF→DXV end to end with output re-decoded
and compared to source pixels; badger gives 904 frames at exactly 25fps /
36.160000s; the width-16 and multiple-of-4 rules swept; Hap Alpha byte-exact.

**NOT verified — the real gaps:**
- **Nothing has ever been loaded into Resolume.** Every claim about playability
  comes from the codec choice.
- **Only ONE real SWF has been converted**, and it is AS1/AS2 with a fixed
  timeline. No AS3, external assets, embedded video, or resizing stage.
- **VP6 is now VERIFIED** (2026-08-17) against real files Allan supplied:
  `barsandtone.flv` (vp6f 360x288, now in the corpus at 87K) decodes to DXV
  pixel-correct, plus two Sorenson Spark files including one at **3840x2160**
  (677 frames @ 23.976) and a 183s 720p (4389 frames).
- **`vp6a` (VP6 with alpha) is now VERIFIED too** — see the assembly trick below.
  The alpha path is exercised end to end: detected, auto-routed to `hap_alpha`,
  DXV refused, `--flatten` honoured, alpha preserved at mean error 0.05.
- macOS only; `probe.py` uses Homebrew paths with a bare-PATH fallback.

## Phase two, as built — the four traps that each froze or flipped the picture

All four passed every structural check. See [ruffle embedding traps](https://github.com/stoatworks-labs/fleet-notes/blob/main/notes/reference_ruffle_embedding_traps.md)
for the detail; the headline is that **`Player::tick()` is the wrong API** when
there is no audio backend, because it corrects its accumulator against an audio
clock that never advances and cancels the time just added.

`source/amber_core` is a Rust staticlib (ruffle_core + ruffle_render_wgpu,
**pinned git rev `ae0ba6d`**) behind a hand-written C ABI in `source/AmberCore.h`.
Frame crosses Metal->OpenGL via **CPU readback** — Flash is small, 550x400 is
under a megabyte, same shape as cartridge's `pixels`. Every call is
`catch_unwind`-guarded, but **a guard is not a process boundary**: content that
hard-crashes Ruffle still takes Resolume down.

**Verification needs BOTH harnesses, and neither is sufficient alone:**
- `./build/ambergl <file.swf>` — plugin class linked directly into a real CGL 4.1
  context. Render path, double-render guard, both aspect branches. Cannot see
  registration.
- `oxbow probe build/Amber.bundle` — real FFGL host loading the real bundle.
  This is what proves `plugMain` is exported and the plugin registers.

**`oxbow selftest` CANNOT drive amber**: its `--set` routes every assignment
through `setParamFloat`, including `FF_TYPE_FILE`. So the movie path never
arrives and the layer is (correctly) transparent. **This also blocks oxbow
testing cartridge's Core/Content params** — worth fixing in oxbow.

**Build traps:** `FFGLSDK.cpp` is an UMBRELLA that `#include`s every other SDK
`.cpp` — compiling the rest alongside it fails the link with several hundred
duplicate symbols. FFGL SDK is a **submodule** pinned at `b1afaf9` (an embedded
git repo would not survive a clone). Registration lives alone in
`SourcePlugin.cpp`.

## Original phase two design notes

Follow [cartridge](https://github.com/stoatworks-labs/cartridge/blob/main/docs/NOTES.md) (`cartridge`)'s split (in-process bundle + out-of-process helper
over POSIX shm) — more warranted here, since Ruffle would run untrusted AS3
inside Resolume. Ruffle renders through **wgpu (Metal)** while FFGL is
**OpenGL**, so the frame most likely returns via CPU readback (cheap at Flash
resolutions, and cartridge already has that shape in `pixels::Convert`). Needs a
Rust↔C++ C ABI boundary like cartridge's libretro one. Ruffle has no stable
library API, so **pin a git rev**, same discipline as the FFGL SDK at `b1afaf9`.
**FFGL has no audio path at all** — whatever amber ever plays live is silent,
and Flash content is often audio-driven.

Related: [dxv hap encoder traps](https://github.com/stoatworks-labs/fleet-notes/blob/main/notes/reference_dxv_hap_encoder_traps.md), [cartridge](https://github.com/stoatworks-labs/cartridge/blob/main/docs/NOTES.md) (`cartridge`),
[ffgl sdk bugs](https://github.com/stoatworks-labs/fleet-notes/blob/main/notes/reference_ffgl_sdk_bugs.md), [licence gaps](https://github.com/stoatworks-labs/fleet-notes/blob/main/notes/project_licence_gaps.md),
**disclaimer scope** (working-practice note, kept in Claude memory) (disclaimer present, top of README).
