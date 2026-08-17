# AGENTS.md — amber

Onboarding for an LLM or a newcomer. `README.md` is the user-facing document;
this is the *why*, plus what is genuinely verified and what is not.

## What this is

Legacy Flash (`.swf`) and Flash Video (`.flv`) converted into codecs Resolume
plays natively. Started 2026-08-17. Intended **PUBLIC MIT**.

Two phases, and only the first exists:

1. **The converter** (done) — a Python CLI in `tools/`, driving the operator's
   own ffmpeg and Ruffle exporter as subprocesses.
2. **The FFGL plugin** (not started) — live playback inside Resolume.

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

**Ruffle's exporter pads filenames to the frame count.** 904 frames are written
`000.png`; 60 frames are written `00.png`. A hardcoded `%03d` works on long
content and silently matches nothing on short. `_frame_pattern()` reads the
width from the files that exist, and refuses a directory holding mixed widths.

**The exporter never reports a frame rate.** It writes numbered PNGs and stops.
The rate comes from the SWF header (`swf.py`), and left to ffmpeg's 25fps
default a 12fps animation plays at slightly over double speed with nothing in
any log to say so.

**The exporter has no background or transparency option.** Its frames are
always opaque, so an SWF is never treated as an alpha source. Transparent Flash
stages are therefore *not* currently supported end to end — see "Not done".

**SWF frame rate is 8.8 fixed point, little-endian.** Low byte is the fraction.
Read as a plain uint16 it comes out 256× too large. 29.97 is not representable
and quantises to 29.96875; that is the format, not a bug.

**ZWS (LZMA) SWFs are not plain `.lzma` files.** Bytes 8–11 are the compressed
length and 12–16 the LZMA properties, so the stream starts at 17, and Python's
`FORMAT_ALONE` needs a synthesised 8-byte size field that the SWF header does
not provide in the right width. Rebuilt from the declared file length.

**Homebrew Python is PEP 668 managed** — `pip install pytest` fails. `verify.sh`
creates `.venv` itself.

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
```

`source/` and `docs/` are empty and reserved for the FFGL plugin.

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
- **Nothing has been loaded into Resolume.** Every claim about what Resolume
  plays comes from the codec choice, not from Resolume having opened a file.
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

- **Transparent SWF stages.** Ruffle's exporter renders opaque and has no
  background option, so alpha Flash cannot currently round-trip. Needs either a
  patch to the exporter or a post-pass keying the known stage colour — the
  latter is fragile and has not been attempted.
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
