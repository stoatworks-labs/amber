# amber

> This is an AI-assisted project — the code was written with [Claude Code](https://claude.com/claude-code).
> The converter has been run end to end against real 2003-era Flash and against
> synthesised FLV files, and its output has been verified frame-accurate by
> decoding it again and comparing pixels. **Nothing here has yet been loaded
> into Resolume**, and the FFGL plugin described under "What comes next" does
> not exist yet.

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

## What it handles

| Input | Path |
|---|---|
| `.flv` — Sorenson Spark, VP6, VP6-with-alpha, H.264, Screen Video | decoded by ffmpeg, transcoded directly |
| `.swf` — vector Flash, ActionScript 1/2/3 | rendered by [Ruffle](https://ruffle.rs), then muxed |

Output is chosen from what the source needs:

| Profile | When | Notes |
|---|---|---|
| `dxv` | default for opaque content | Resolume's own codec, GPU-decoded |
| `hap_alpha` | default when the source has alpha | GPU-decoded, alpha byte-exact |
| `hap_q` | on request | higher quality than DXV, bigger files |
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

## What comes next

The converter is phase one. The intended phase two is an FFGL source plugin
that plays this content live inside Resolume — the same shape as
[cartridge](../cartridge), including its split between an in-process bundle and
an out-of-process helper so a decoder crash cannot take Resolume down with it.
That plugin does not exist yet. See `AGENTS.md` for the design constraints
already established, including the one that matters most: **FFGL has no audio
path at all**, so anything amber ever plays live will be silent.

## Verification

```bash
tools/verify.sh
```

34 tests. The codec-constraint tests re-derive the DXV and Hap dimension rules
from the real encoder rather than asserting a table against itself.

## Licence

MIT — see [LICENSE](LICENSE). amber bundles no Flash content, no ffmpeg, and no
part of Ruffle; see [ATTRIBUTIONS.md](ATTRIBUTIONS.md).
