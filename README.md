# amber

> This is an AI-assisted project — the code was written with [Claude Code](https://claude.com/claude-code).
> The converter has been run end to end against real 2003-era Flash and against
> real-world FLV in all three of its common codecs, with the output verified
> frame-accurate by decoding it again and comparing pixels. **The plugin has been
> confirmed working in Resolume Arena on Apple Silicon.** Its Intel slice has
> never been run in an Intel Resolume, and the Windows build is new and untested
> in a host.

Legacy Flash content, made playable in Resolume.

Resolume opens neither `.swf` nor `.flv`. A decade and a half of VJ loops, web
animation and motion tests is therefore sitting in formats nothing on the
machine will play. `amber` converts them into DXV or Hap — the codecs Resolume
decodes on the GPU — preserving frame rate, transparency and dimensions.

```bash
tools/amber doctor                          # what can this machine do?
tools/amber info  ~/clips/badger.swf        # describe without converting
tools/amber convert ~/clips -o ~/converted  # convert a whole directory
```

```
badger.swf -> badger.mov  544x400 @ 25fps  904 frames  15.2MB  [dxv]  [resized 550x400 -> 544x400 (scale)]
```

<!-- downloads:start -->

## Download

**[v0.1.0](https://github.com/stoatworks-labs/amber/releases/tag/v0.1.0)** — prebuilt for macOS and Windows. Pick your platform:

<details>
<summary><b>macOS</b> — Universal (Apple Silicon + Intel)</summary>

| Build | Download | Size |
| --- | --- | --- |
| Universal (Apple Silicon + Intel) · .dmg disk image | [`amber-0.1.0-macos-universal.dmg`](https://github.com/stoatworks-labs/amber/releases/download/v0.1.0/amber-0.1.0-macos-universal.dmg) | 25 MB |
| Universal (Apple Silicon + Intel) · .zip archive | [`amber-0.1.0-macos-universal.zip`](https://github.com/stoatworks-labs/amber/releases/download/v0.1.0/amber-0.1.0-macos-universal.zip) | 22 MB |

</details>

<details>
<summary><b>Windows</b> — x64</summary>

| Build | Download | Size |
| --- | --- | --- |
| x64 · .zip archive | [`amber-0.1.0-windows-x86_64.zip`](https://github.com/stoatworks-labs/amber/releases/download/v0.1.0/amber-0.1.0-windows-x86_64.zip) | 6.6 MB |

</details>

All builds, checksums and release notes: [github.com/stoatworks-labs/amber/releases](https://github.com/stoatworks-labs/amber/releases).

macOS builds are signed and notarised and open normally. The Windows builds are unsigned, so SmartScreen warns once.

<!-- downloads:end -->

## What it handles

| Input | Path |
|---|---|
| `.flv` — Sorenson Spark, VP6, VP6-with-alpha, H.264, Screen Video | decoded by ffmpeg, transcoded directly |
| a `vp6a` test file, if you need one | `tools/make_vp6a.py in.flv out.flv` |
| `.swf` — vector Flash, ActionScript 1/2/3 | rendered by [Ruffle](https://ruffle.rs), then muxed |

Output is chosen from what the source needs:

| Profile | When | Notes |
|---|---|---|
| `dxv` | default for opaque content | Resolume's own codec, GPU-decoded |
| `hap_alpha` | default when the source has alpha | GPU-decoded, alpha byte-exact |
| `hap_q` | on request | 46.8 dB vs DXV's 45.0, worst-case error halved, ~80% larger |
| `prores4444` | alpha fallback where Hap is unavailable | CPU-decoded, much larger |

## Installing

amber **vendors nothing**. It drives your own ffmpeg and your own build of
Ruffle's exporter as subprocesses, which is what keeps this repo cleanly MIT
with no LGPL linking question to answer.

```bash
brew install ffmpeg-full
```

`ffmpeg-full` rather than `ffmpeg`: plain Homebrew ffmpeg has the DXV encoder
but **not** the Hap encoder, so transparent content would have nowhere to go.
`tools/amber doctor` tells you which one it found and what it can do.

For `.swf` support you also need Ruffle's exporter, which Ruffle does not ship
in its releases:

```bash
git clone --depth 1 https://github.com/ruffle-rs/ruffle ~/Documents/Amber/ruffle-src
cd ~/Documents/Amber/ruffle-src && cargo build --release --package=exporter
```

amber finds it there automatically. `.flv` conversion works without it.

## Two things worth knowing before you convert a library

**DXV cannot carry transparency.** ffmpeg's DXV encoder offers exactly one
format, `dxt1`, whose own help text reads "Normal Quality, No Alpha". Handed an
image with alpha it does not warn — it returns every pixel opaque. Since VP6-alpha
exists precisely for transparent overlay loops, amber refuses to send alpha
content to DXV and routes it to Hap Alpha instead. `--flatten` overrides this
if you genuinely want the alpha gone.

**DXV silently corrupts any width that is not a multiple of 16.** The encoder
returns success, writes a plausible file, and records the right dimensions —
and the picture inside is sheared diagonally. This is not a corner case:
**550×400 is the default Flash stage size**, so the naive pipeline mangles a
large fraction of all Flash ever made. amber measures the constraint and works
around it, and `tests/test_amber.py` re-measures it on every run so the day
ffmpeg fixes it is noticed rather than assumed.

For `.swf` this costs nothing at all — Flash is vector, so Ruffle simply
rasterises straight to a legal size. For `.flv` the frames are raster and have
to be resampled (`--fit scale`, the default) or padded (`--fit pad`).

**DXV blocks up on smooth gradients.** It is DXT1 at a fixed 4:1, so skies and
soft falloffs show visible 4x4 blocking. Measured against a lossless reference
on gradient-heavy 720p footage: DXV 45.0 dB with a worst-case error of 44,
`hap_q` 46.8 dB with a worst-case error of 19 — for about 80% more file. Reach
for `--profile hap_q` when the content is gradient-heavy and the disk can take
it. Plain `--profile hap` is pointless: identical quality to DXV, 27% bigger.

**DXV files are large.** 28 seconds of 3840x2160 came out at 1.4GB. That is the
codec working as intended — it trades size for GPU-decodable playback — but plan
disk accordingly before converting a library.

## The live plugin

`.swf` files can also be played **live on a Resolume layer**, with the timeline
and ActionScript actually running, rather than converted ahead of time. amber
builds an FFGL `FF_SOURCE` that embeds [Ruffle](https://ruffle.rs).

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
cmake --install build     # -> ~/Library/Graphics/FreeFrame Plug-Ins
```

Parameters: **Movie** (the .swf), **Run**, **Restart**, **Speed**, **Scaling**
(Fit / Fill / Stretch), **Smoothing**, **Transparent**.

**Transparent is on by default**, and it is what makes Flash usable as an
overlay: Ruffle implements Flash's own `wmode=transparent`, so everything the
movie does not draw comes through clear and the layers underneath show. Turn it
off to get the movie's declared stage colour behind it instead.

**Confirmed working in Resolume Arena on Apple Silicon.** Three limits are worth
knowing before you rely on it:

- **There is no audio.** FFGL provides no audio path at all, so a live Flash
  clip is silent no matter what it contains. The converter has the same limit.
- **Ruffle runs in Resolume's process.** Every call into it is guarded, which
  turns most bad content into a black layer rather than a crash — but a guard is
  not a process boundary, and content that hard-crashes Ruffle takes Resolume
  with it. An out-of-process helper, like the one
  [cartridge](https://github.com/stoatworks-labs/cartridge) grew for exactly this
  reason, does not exist here yet.
- **Only the Apple Silicon slice has been run in a host.** The bundle is
  universal and the Windows build exists, but neither the Intel slice nor the
  Windows DLL has been loaded into a Resolume yet.

Live playback is `.swf` only; `.flv` goes through the converter.

**Only the plugin can do transparency, not the converter.** The converter drives
Ruffle's `exporter` binary, which has no background option and always renders
opaque, so `.swf` files needing a transparent background have to be played live
rather than converted. That is a gap in the exporter's CLI rather than in
Ruffle, and the converter says so instead of quietly handing back an opaque clip.

## Verification

```bash
tools/verify.sh                                  # converter + fixtures: 39 tests
./build/ambergl ~/clips/badger.swf               # plugin, in a real GL context
oxbow probe build/Amber.bundle                   # plugin, in a real FFGL host
```

The codec-constraint tests re-derive the DXV and Hap dimension rules from the
real encoder rather than asserting a table against itself. `ambergl` covers the
render path and the double-render guard; `oxbow probe` covers registration,
which a directly-linked harness cannot see.

## Credits

The demonstration footage shows short clips of **"Badgers"** by **Jonti Picking
— Weebl** ([the original](https://www.youtube.com/watch?v=EIyixC9NsLI),
[his channel](https://www.youtube.com/@MrWeebl)), used to show what amber does
with real period Flash. It is not included here and not redistributed.

## Licence

MIT — see [LICENSE](LICENSE). amber bundles no Flash content, no ffmpeg, and no
part of Ruffle; see [ATTRIBUTIONS.md](ATTRIBUTIONS.md).
