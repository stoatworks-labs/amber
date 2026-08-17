"""Turn legacy Flash content into something Resolume plays natively.

The shape of the pipeline is decided by two measured facts, both of which are
counter-intuitive enough to be worth stating before the code:

**DXV cannot carry alpha here.** ffmpeg's DXV encoder exposes exactly one
format, `dxt1`, whose own help string reads "Normal Quality, No Alpha". Handed
RGBA it does not complain, does not warn, and returns every alpha value as
0xff. Since transparent overlay loops are a large fraction of why anyone still
has an FLV library -- vp6a exists for precisely that -- routing alpha content to
DXV would quietly destroy the thing that made it worth converting. Content with
alpha therefore goes to Hap Alpha, which was measured byte-exact through a full
roundtrip, and falls back to ProRes 4444 where Hap is unavailable.

**Ruffle's exporter pads its filenames to fit the frame count.** 904 frames are
written as `000.png`, but 60 frames are written as `00.png`. A fixed `%03d`
input pattern works on a long SWF and silently matches nothing on a short one,
so the width is computed from the files that actually exist.

**And the exporter never reports a frame rate.** It writes a numbered PNG per
frame and stops. The rate comes from the SWF header instead (see swf.py); left
to ffmpeg's 25fps default, a 12fps animation plays at slightly over double
speed with nothing in any log to say so.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import swf as swf_reader
from .align import Alignment, align_for
from .probe import FFmpegCapabilities, ProbeError, find_ruffle_exporter, probe_media

FLV_SUFFIXES = frozenset({".flv", ".f4v"})
SWF_SUFFIXES = frozenset({".swf"})


class ConvertError(Exception):
    """A conversion could not be performed or produced nothing usable."""


@dataclass(frozen=True)
class EncodeProfile:
    """How to encode, and what that choice costs."""

    name: str
    args: list[str]
    container: str
    carries_alpha: bool
    note: str


# Resolume decodes DXV and Hap on the GPU; ProRes it decodes on the CPU. So the
# ordering below is not arbitrary -- it is "cheapest for Resolume to play that
# does not lose information".
PROFILE_DXV = EncodeProfile(
    name="dxv",
    args=["-c:v", "dxv", "-pix_fmt", "rgba"],
    container=".mov",
    carries_alpha=False,
    note="DXV (DXT1) -- Resolume's native codec, GPU-decoded, no alpha",
)

PROFILE_HAP_ALPHA = EncodeProfile(
    name="hap_alpha",
    args=["-c:v", "hap", "-format", "hap_alpha", "-pix_fmt", "rgba"],
    container=".mov",
    carries_alpha=True,
    note="Hap Alpha (DXT5) -- GPU-decoded, alpha preserved",
)

PROFILE_HAP_Q = EncodeProfile(
    name="hap_q",
    args=["-c:v", "hap", "-format", "hap_q", "-pix_fmt", "rgba"],
    container=".mov",
    carries_alpha=False,
    note="Hap Q (DXT5-YCoCg) -- higher quality than DXV, larger files, no alpha",
)

PROFILE_PRORES_4444 = EncodeProfile(
    name="prores4444",
    args=["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"],
    container=".mov",
    carries_alpha=True,
    note="ProRes 4444 -- alpha preserved, but CPU-decoded and much larger",
)

PROFILES = {
    profile.name: profile
    for profile in (PROFILE_DXV, PROFILE_HAP_ALPHA, PROFILE_HAP_Q, PROFILE_PRORES_4444)
}


def choose_profile(
    has_alpha: bool,
    caps: FFmpegCapabilities,
    requested: str | None = None,
) -> EncodeProfile:
    """Pick an encode profile, refusing to silently discard an alpha channel.

    An explicit request is honoured, but requesting a non-alpha profile for
    alpha content is an error rather than a warning: it is not recoverable
    after the fact and the output looks plausible, so it must not be possible
    to do by accident.
    """
    if requested is not None:
        profile = PROFILES.get(requested)
        if profile is None:
            raise ConvertError(
                f"unknown profile {requested!r}; choose from {', '.join(sorted(PROFILES))}"
            )
        if has_alpha and not profile.carries_alpha:
            raise ConvertError(
                f"source has an alpha channel but profile {requested!r} cannot carry "
                f"one -- it would be flattened to opaque with no error. Use "
                f"'hap_alpha' or 'prores4444', or pass --flatten to accept the loss."
            )
        _require_profile(profile, caps)
        return profile

    if has_alpha:
        for candidate in (PROFILE_HAP_ALPHA, PROFILE_PRORES_4444):
            if _profile_available(candidate, caps):
                return candidate
        raise ConvertError(
            "source has an alpha channel but this ffmpeg can encode neither Hap "
            "Alpha nor ProRes 4444. Install ffmpeg-full (`brew install ffmpeg-full`) "
            "or pass --flatten to convert to DXV and lose transparency."
        )

    if _profile_available(PROFILE_DXV, caps):
        return PROFILE_DXV
    if _profile_available(PROFILE_HAP_Q, caps):
        return PROFILE_HAP_Q
    raise ConvertError("this ffmpeg has neither a DXV nor a Hap encoder.")


def _profile_available(profile: EncodeProfile, caps: FFmpegCapabilities) -> bool:
    if profile.name == "dxv":
        return caps.can_dxv
    if profile.name == "hap_alpha":
        return caps.can_hap_alpha
    if profile.name == "hap_q":
        return "hap_q" in caps.hap_formats
    if profile.name == "prores4444":
        return caps.can_prores
    return False


def _require_profile(profile: EncodeProfile, caps: FFmpegCapabilities) -> None:
    if not _profile_available(profile, caps):
        raise ConvertError(
            f"profile {profile.name!r} is not available in {caps.path}. "
            f"`brew install ffmpeg-full` provides the widest set."
        )


def _run_ffmpeg(caps: FFmpegCapabilities, args: list[str], what: str) -> None:
    """Invoke ffmpeg, raising with its own stderr if it fails.

    `-nostdin` is not optional. ffmpeg reads stdin for interactive keypresses
    and will happily consume the parent's, which turns a batch run into an
    apparent hang with no output.
    """
    argv = [caps.path, "-nostdin", "-y", "-v", "error", *args]
    result = subprocess.run(
        argv, stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "(no stderr)"
        raise ConvertError(f"{what}: ffmpeg failed\n  {' '.join(argv)}\n  {detail}")


@dataclass(frozen=True)
class ConversionResult:
    source: Path
    output: Path
    profile: EncodeProfile
    width: int
    height: int
    frame_rate: float
    frames: int
    had_alpha: bool
    alignment: Alignment | None = None


def _count_frames(caps: FFmpegCapabilities, path: Path) -> int:
    """Count the frames actually written.

    FLV carries no frame count, so ffprobe reports N/A for the source and the
    only honest number comes from the output. The count is read from the
    container's own metadata, which the MOV muxer does fill in.
    """
    from .probe import probe_media as _probe

    try:
        return _probe(path).nb_frames or 0
    except ProbeError:
        return 0


def convert_flv(
    source: Path,
    destination: Path,
    caps: FFmpegCapabilities,
    requested_profile: str | None = None,
    flatten: bool = False,
    fit: str = "scale",
) -> ConversionResult:
    """Transcode an FLV straight to a Resolume-native codec."""
    info = probe_media(source)

    if info.width == 0 or info.height == 0:
        raise ConvertError(f"{source.name}: video stream reports no dimensions")

    has_alpha = info.has_alpha and not flatten
    profile = choose_profile(has_alpha, caps, requested_profile)

    output = destination.with_suffix(profile.container)
    output.parent.mkdir(parents=True, exist_ok=True)

    alignment = align_for(info.width, info.height, profile.name, fit)

    args = ["-i", str(source)]
    if alignment.changed:
        if alignment.strategy == "pad":
            # Keep the original pixels at the top-left and grow the canvas.
            args += ["-vf", f"pad={alignment.width}:{alignment.height}:0:0"]
        else:
            # A raster source has to be resampled; lanczos keeps the hard edges
            # of 2000s-era animation crisper than the bilinear default.
            args += ["-vf", f"scale={alignment.width}:{alignment.height}:flags=lanczos"]

    args += [*profile.args, "-an", str(output)]
    _run_ffmpeg(caps, args, f"converting {source.name}")

    if not output.exists() or output.stat().st_size == 0:
        raise ConvertError(f"{source.name}: produced no output")

    return ConversionResult(
        source=source,
        output=output,
        profile=profile,
        width=alignment.width,
        height=alignment.height,
        frame_rate=info.frame_rate,
        frames=_count_frames(caps, output),
        had_alpha=info.has_alpha,
        alignment=alignment,
    )


def _frame_pattern(directory: Path) -> tuple[str, int]:
    """Work out the printf pattern matching the exporter's PNG sequence.

    The width is read from the files rather than assumed, because the exporter
    pads to the frame count: `00.png` for 60 frames, `000.png` for 904.
    """
    frames = sorted(p for p in directory.glob("*.png") if re.fullmatch(r"\d+", p.stem))
    if not frames:
        raise ConvertError(f"no numbered PNG frames were written to {directory}")

    widths = {len(p.stem) for p in frames}
    if len(widths) != 1:
        # Ruffle pads uniformly; mixed widths would mean a partial or mixed
        # directory, and picking either width would drop frames silently.
        raise ConvertError(
            f"{directory} holds frames of mixed numbering widths {sorted(widths)} "
            f"-- it may contain output from more than one run"
        )

    width = widths.pop()
    return f"%0{width}d.png", len(frames)


def convert_swf(
    source: Path,
    destination: Path,
    caps: FFmpegCapabilities,
    requested_profile: str | None = None,
    flatten: bool = False,
    scale: float = 1.0,
    max_frames: int | None = None,
    exporter: str | None = None,
    fit: str = "scale",
) -> ConversionResult:
    """Render an SWF through Ruffle, then mux the frames at the SWF's own rate."""
    exporter_path = exporter or find_ruffle_exporter()
    if exporter_path is None:
        raise ConvertError(
            "no Ruffle exporter found, so SWF conversion is unavailable. Ruffle "
            "does not ship it in releases; build it with:\n"
            "  git clone --depth 1 https://github.com/ruffle-rs/ruffle\n"
            "  cd ruffle && cargo build --release --package=exporter"
        )

    header = swf_reader.read_header(source)
    if header.frame_rate <= 0:
        raise ConvertError(f"{source.name}: header declares a frame rate of 0")

    # The exporter renders opaque frames -- it has no background or
    # transparency option -- so an SWF is never treated as having alpha.
    profile = choose_profile(False, caps, requested_profile)
    output = destination.with_suffix(profile.container)
    output.parent.mkdir(parents=True, exist_ok=True)

    frames_argument = "all" if max_frames is None else str(max_frames)

    # Flash is vector, so the codec's dimension constraint costs nothing here:
    # rather than rendering at the stage size and resampling afterwards, ask
    # Ruffle to rasterise straight to the legal size. The result is a clean
    # re-render at the target resolution, not a scaled bitmap -- which is why
    # the SWF path can satisfy DXV's multiple-of-16 width with no quality loss
    # at all, while the FLV path has no choice but to resample.
    target_width = int(header.width * scale)
    target_height = int(header.height * scale)
    alignment = align_for(target_width, target_height, profile.name, fit)

    with tempfile.TemporaryDirectory(prefix="amber-frames-") as scratch:
        frame_dir = Path(scratch)
        export_argv = [
            exporter_path,
            str(source),
            str(frame_dir),
            "--frames", frames_argument,
            "--force-play",
            "--silent",
            "--width", str(alignment.width),
            "--height", str(alignment.height),
        ]

        result = subprocess.run(
            export_argv, stdin=subprocess.DEVNULL, capture_output=True, text=True
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "(no output)"
            raise ConvertError(f"{source.name}: Ruffle exporter failed\n  {detail}")

        pattern, frame_count = _frame_pattern(frame_dir)

        args = [
            "-framerate", f"{header.frame_rate:.6f}",
            "-i", str(frame_dir / pattern),
            *profile.args,
            str(output),
        ]
        _run_ffmpeg(caps, args, f"muxing {source.name}")

    if not output.exists() or output.stat().st_size == 0:
        raise ConvertError(f"{source.name}: produced no output")

    return ConversionResult(
        source=source,
        output=output,
        profile=profile,
        width=alignment.width,
        height=alignment.height,
        frame_rate=header.frame_rate,
        frames=frame_count,
        had_alpha=False,
        alignment=alignment,
    )


def convert(
    source: Path,
    destination: Path,
    caps: FFmpegCapabilities,
    **kwargs,
) -> ConversionResult:
    """Dispatch on file type."""
    suffix = source.suffix.lower()
    if suffix in SWF_SUFFIXES:
        return convert_swf(source, destination, caps, **kwargs)
    if suffix in FLV_SUFFIXES:
        # The SWF-only options do not apply to a straight transcode.
        for swf_only in ("scale", "max_frames", "exporter"):
            kwargs.pop(swf_only, None)
        return convert_flv(source, destination, caps, **kwargs)
    raise ConvertError(f"{source.name}: not an .swf or .flv file")
