# Attributions

amber bundles no third-party code. Everything below is invoked as a separate
process, installed and owned by the operator.

## ffmpeg
<https://ffmpeg.org> — LGPL-2.1-or-later / GPL-2.0-or-later depending on build.

amber spawns the `ffmpeg` and `ffprobe` binaries. It does not link against
libavcodec or any other ffmpeg library, and ships no part of ffmpeg. A separate
process is not a derived work, so amber's MIT licence stands independently of
how the operator's ffmpeg was configured.

## Ruffle
<https://ruffle.rs> — MIT OR Apache-2.0.

amber spawns Ruffle's `exporter` binary to rasterise SWF timelines. It ships no
part of Ruffle and does not link against `ruffle_core`. The operator builds the
exporter themselves; Ruffle does not distribute it in releases.

## Resolume DXV and Hap
DXV is Resolume's codec (<https://resolume.com>). Hap is an open codec by
Vidvox (<https://hap.video>), BSD-licensed. amber implements neither — both are
encoded by the operator's ffmpeg. No Resolume SDK, header or library is used.

## Test content
No Flash content is committed to this repository. See AGENTS.md.
