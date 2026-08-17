# Changelog

## v0.1.0 — 2026-08-17

First release.

### The converter

`tools/amber` turns legacy Flash into codecs Resolume plays natively.

- `.flv` — Sorenson Spark, VP6, VP6-with-alpha, H.264 and Screen Video, decoded
  by the operator's own ffmpeg and transcoded directly.
- `.swf` — rendered through [Ruffle](https://ruffle.rs), then muxed at the frame
  rate read from the SWF header.
- Output profiles: DXV by default, **Hap Alpha whenever the source carries
  transparency**, Hap Q on request, ProRes 4444 as an alpha fallback.
- `amber doctor` reports what the machine can actually do before anything is
  converted.

Two measured ffmpeg behaviours shape the whole pipeline, and both are silent:

- **DXV corrupts any width that is not a multiple of 16**, reporting success and
  writing a diagonally sheared picture. 550x400 is the default Flash stage size,
  so this affects a large fraction of all Flash ever made. amber aligns around it.
- **DXV cannot carry alpha** (`dxt1` only, self-described "No Alpha") and
  flattens it without warning, so alpha content is routed to Hap Alpha instead.

### The plugin

`Amber.bundle` — an FFGL source that embeds Ruffle and plays `.swf` files live
on a Resolume layer, with the timeline and ActionScript actually running.

- Parameters: Movie, Run, Restart, Speed, Scaling (Fit / Fill / Stretch),
  Smoothing, Transparent.
- **Transparent is on by default**, using Flash's own `wmode=transparent`, so
  Flash works as an overlay rather than a backdrop.
- Confirmed working in Resolume Arena on Apple Silicon.

### Known limits

- **No audio, anywhere.** FFGL provides no audio path at all, so live Flash is
  silent whatever it contains. The converter drops audio for the same reason.
- **Ruffle runs in Resolume's process.** Calls into it are guarded, which turns
  most bad content into a black layer rather than a crash, but a guard is not a
  process boundary. There is no out-of-process helper yet.
- **Live playback is `.swf` only.** `.flv` goes through the converter.
- **The converter cannot produce transparent output from `.swf`** — it drives
  Ruffle's `exporter` binary, which has no background option. The plugin can.
- **Only the Apple Silicon slice has been run in a host.** The macOS bundle is
  universal and there is a Windows build, but neither the Intel slice nor the
  Windows DLL has been loaded into a Resolume.
- No seek or scrub parameter on the plugin.
