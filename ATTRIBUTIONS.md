# Attributions

amber is two things, and they owe different debts. The plugin (`Amber.bundle`)
compiles in the code listed under the first heading below. The converter
(`tools/amber`) bundles no third-party code: everything it uses is invoked as a
separate process, installed and owned by the operator.

## Compiled into the plugin

### Resolume FFGL SDK
<https://github.com/resolume/ffgl> — BSD-3-Clause, copyright FreeFrame.

A git submodule at `external/ffgl`, pinned to `b1afaf9`. Its sources are
compiled straight into the bundle (`FFGLSDK.cpp` is an umbrella over the rest of
the SDK). The plugin ABI itself: an FFGL source is defined by these headers, and
there is no other way to be loadable by Resolume Arena and Avenue.

### Ruffle
<https://ruffle.rs> — MIT OR Apache-2.0, copyright Ruffle LLC and Ruffle
contributors.

The plugin embeds Ruffle to play `.swf` live: `ruffle_core`, `ruffle_render` and
`ruffle_render_wgpu`, at a pinned git revision (`ae0ba6d`), linked into the
bundle through `source/amber_core` and run in Resolume's process. Their crate
dependency tree, wgpu included, is pinned in `source/amber_core/Cargo.lock`,
which is the authoritative list.

Ruffle's `default_font` feature is enabled, so the bundle also carries Ruffle's
fallback device font: a subset of **Noto Sans**
(<https://github.com/notofonts/latin-greek-cyrillic>), SIL Open Font License 1.1.

### GLEW
<https://github.com/nigels-com/glew> — BSD-3-Clause (with Mesa 3-D and Khronos
components).

Off macOS only, from vcpkg (`vcpkg.json` guards it with `!osx`). macOS uses the
system OpenGL framework instead.

## Invoked by the converter, never bundled

### ffmpeg
<https://ffmpeg.org> — LGPL-2.1-or-later / GPL-2.0-or-later depending on build.

The converter spawns the `ffmpeg` and `ffprobe` binaries. amber does not link
against libavcodec or any other ffmpeg library, and ships no part of ffmpeg. A
separate process is not a derived work, so amber's MIT licence stands
independently of how the operator's ffmpeg was configured.

### Ruffle's exporter
<https://ruffle.rs> — MIT OR Apache-2.0.

The converter spawns Ruffle's `exporter` binary to rasterise SWF timelines. It
ships no part of the exporter. The operator builds it themselves; Ruffle does
not distribute it in releases.

### Resolume DXV and Hap
DXV is Resolume's codec (<https://resolume.com>). Hap is an open codec by
Vidvox (<https://hap.video>), BSD-licensed. amber implements neither — both are
encoded by the operator's ffmpeg. No DXV or Hap SDK, header or library is used;
the FFGL SDK above is the only Resolume code in amber.

## Test content
No Flash content is committed to this repository. See AGENTS.md.

## Demonstration footage

The project video shows short clips of **"Badgers"** (widely known as *Badger
Badger Badger*), a Flash animation by **Jonti Picking — Weebl**, used briefly to
demonstrate what amber does with real period Flash. It is not included in this
repository, is not redistributed with any release, and no ownership is claimed.

- The original: <https://www.youtube.com/watch?v=EIyixC9NsLI>
- Weebl's channel: <https://www.youtube.com/@MrWeebl>

Note that amber carries no audio at all — FFGL provides no audio path — so the
footage is silent and the song does not appear in it.
