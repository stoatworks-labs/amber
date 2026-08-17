"""Command line for amber's converter."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .convert import PROFILES, ConvertError, convert
from .probe import ProbeError, find_ffmpeg, find_ruffle_exporter
from .swf import SWFError, read_header

INPUT_SUFFIXES = (".swf", ".flv", ".f4v")


def _collect_inputs(paths: list[str]) -> list[Path]:
    """Expand directories into the convertible files they contain."""
    found: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix.lower() in INPUT_SUFFIXES:
                    found.append(child)
        elif path.is_file():
            found.append(path)
        else:
            print(f"amber: {raw}: no such file or directory", file=sys.stderr)
    return found


def _command_doctor(_args: argparse.Namespace) -> int:
    """Report what this machine can and cannot do, before anything is converted."""
    status = 0
    try:
        caps = find_ffmpeg()
    except ProbeError as exc:
        print(f"ffmpeg   : NOT FOUND\n           {exc}", file=sys.stderr)
        return 1

    print(f"ffmpeg   : {caps.path}")
    print(f"           {caps.version}")

    missing = caps.missing_flv_decoders()
    if missing:
        print(f"FLV      : INCOMPLETE -- missing decoders: {', '.join(missing)}")
        status = 1
    else:
        print("FLV      : all decoders present (Spark, VP6, VP6-alpha, H.264, ScreenVideo)")

    print(f"DXV      : {'yes' if caps.can_dxv else 'NO'}"
          f"{'  (formats: ' + ', '.join(sorted(caps.dxv_formats)) + ')' if caps.dxv_formats else ''}")

    if caps.can_hap:
        print(f"Hap      : yes  (formats: {', '.join(sorted(caps.hap_formats))})")
    else:
        print("Hap      : NO -- transparent output will fall back to ProRes 4444.")
        print("           `brew install ffmpeg-full` provides the Hap encoder.")

    alpha = caps.alpha_targets()
    if alpha:
        print(f"alpha    : {', '.join(alpha)}")
    else:
        print("alpha    : NONE -- transparency cannot be preserved by this ffmpeg.")
        status = 1

    exporter = find_ruffle_exporter()
    if exporter:
        print(f"SWF      : {exporter}")
    else:
        print("SWF      : NO exporter -- .swf conversion unavailable, .flv still works.")
        print("           Build it: git clone --depth 1 https://github.com/ruffle-rs/ruffle")
        print("                     cd ruffle && cargo build --release --package=exporter")

    return status


def _command_info(args: argparse.Namespace) -> int:
    """Print what amber can tell about each input without converting it."""
    status = 0
    for path in _collect_inputs(args.inputs):
        if path.suffix.lower() == ".swf":
            try:
                header = read_header(path)
            except SWFError as exc:
                print(f"{path.name}: {exc}", file=sys.stderr)
                status = 1
                continue
            print(
                f"{path.name}: SWF v{header.version} ({header.compression}) "
                f"{header.width}x{header.height} @ {header.frame_rate:g}fps "
                f"{header.frame_count} frames ~{header.duration:.1f}s"
            )
        else:
            try:
                from .probe import probe_media

                info = probe_media(path)
            except ProbeError as exc:
                print(f"{path.name}: {exc}", file=sys.stderr)
                status = 1
                continue
            alpha = " +alpha" if info.has_alpha else ""
            # 0 means the container gave nothing trustworthy -- see probe_media.
            rate = f"{info.frame_rate:g}fps" if info.frame_rate > 0 else "unknown fps"
            print(
                f"{path.name}: {info.codec}{alpha} {info.width}x{info.height} "
                f"@ {rate} {info.duration:.1f}s ({info.pix_fmt})"
            )
    return status


def _command_convert(args: argparse.Namespace) -> int:
    try:
        caps = find_ffmpeg()
    except ProbeError as exc:
        print(f"amber: {exc}", file=sys.stderr)
        return 1

    inputs = _collect_inputs(args.inputs)
    if not inputs:
        print("amber: nothing to convert", file=sys.stderr)
        return 1

    out_dir = Path(args.output).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    for source in inputs:
        destination = out_dir / source.stem
        try:
            result = convert(
                source,
                destination,
                caps,
                requested_profile=args.profile,
                flatten=args.flatten,
                scale=args.scale,
                max_frames=args.frames,
                fit=args.fit,
            )
        except (ConvertError, SWFError, ProbeError) as exc:
            print(f"amber: {source.name}: {exc}", file=sys.stderr)
            failures += 1
            continue

        size_mb = result.output.stat().st_size / (1024 * 1024)
        notes = []
        if result.had_alpha and not result.profile.carries_alpha:
            notes.append("ALPHA FLATTENED")
        if result.alignment is not None and result.alignment.changed:
            # Say so every time. The dimensions in the output no longer match
            # the source, and a silent resize is exactly the kind of thing that
            # is noticed for the first time on a wall.
            notes.append(f"resized {result.alignment.describe()}")
        suffix = ("  [" + "; ".join(notes) + "]") if notes else ""
        rate = f"{result.frame_rate:g}fps" if result.frame_rate > 0 else "unknown fps"
        print(
            f"{source.name} -> {result.output.name}  "
            f"{result.width}x{result.height} @ {rate}  "
            f"{result.frames} frames  {size_mb:.1f}MB  [{result.profile.name}]{suffix}"
        )

    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amber",
        description="Convert legacy Flash content (.swf, .flv) into codecs "
                    "Resolume plays natively.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="report what this machine can convert, and what it cannot"
    )
    doctor.set_defaults(func=_command_doctor)

    info = subparsers.add_parser("info", help="describe inputs without converting")
    info.add_argument("inputs", nargs="+")
    info.set_defaults(func=_command_info)

    convert_parser = subparsers.add_parser("convert", help="convert files or directories")
    convert_parser.add_argument("inputs", nargs="+")
    convert_parser.add_argument(
        "-o", "--output", default="./converted", help="output directory"
    )
    convert_parser.add_argument(
        "-p", "--profile", choices=sorted(PROFILES), default=None,
        help="force an encode profile (default: chosen from whether the source has alpha)",
    )
    convert_parser.add_argument(
        "--flatten", action="store_true",
        help="allow an alpha channel to be discarded (required to send alpha content to DXV)",
    )
    convert_parser.add_argument(
        "--scale", type=float, default=1.0,
        help="scale factor for SWF rendering (ignored for FLV)",
    )
    convert_parser.add_argument(
        "--frames", type=int, default=None,
        help="cap the number of SWF frames rendered (default: the whole timeline)",
    )
    convert_parser.add_argument(
        "--fit", choices=("scale", "pad"), default="scale",
        help="how to satisfy the codec's dimension constraints -- 'scale' resamples "
             "to the nearest legal size (default), 'pad' keeps every original pixel "
             "and grows the canvas, which bakes an opaque border into a DXV clip",
    )
    convert_parser.set_defaults(func=_command_convert)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
