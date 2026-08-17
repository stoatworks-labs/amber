"""Find the tools amber depends on, and ask them what they can actually do.

amber deliberately vendors nothing. It drives the operator's own ffmpeg as a
subprocess and their own build of Ruffle's exporter. That keeps this repo
cleanly MIT with no LGPL linking question to answer -- a separate process is
not a derived work -- but it moves a whole class of failure to runtime, so
every capability is *probed* rather than assumed.

The probing is not defensive padding. Two facts found on the development
machine make it necessary:

  * Homebrew's `ffmpeg` has the DXV encoder but NOT the Hap encoder, while
    `ffmpeg-full` has both. Which binary is first on PATH therefore decides
    whether transparent output is possible at all.
  * ffmpeg's DXV encoder supports exactly one format, `dxt1`, described by its
    own help as "Normal Quality, No Alpha". It does not fail on RGBA input --
    it silently flattens every alpha value to 0xff.

That second one is the reason this file exists. A converter that trusted DXV
with a transparent Flash stage would produce a clip with a black box behind it
and no error anywhere in the log.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# Searched in order. ffmpeg-full comes first because it is the superset: on the
# development machine it is the only one of the two carrying the Hap encoder,
# and Hap Alpha is the only format tested here that survives an alpha channel
# byte-exact.
FFMPEG_CANDIDATES = (
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
    "/opt/homebrew/bin/ffmpeg",
    "ffmpeg",
)

FFPROBE_CANDIDATES = (
    "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe",
    "/opt/homebrew/bin/ffprobe",
    "ffprobe",
)

# The FLV video codecs worth naming. Anything else in an FLV is either audio
# (which FFGL cannot carry anyway) or so rare it is not worth a special case.
FLV_VIDEO_DECODERS = ("flv", "vp6", "vp6f", "vp6a", "h264", "flashsv", "flashsv2")

# Codecs whose FLV form carries an alpha channel. vp6a is the only one in the
# wild, and it is exactly the codec used for the transparent-background overlay
# loops that a VJ library is full of.
FLV_ALPHA_CODECS = frozenset({"vp6a"})


class ProbeError(Exception):
    """A required external tool is missing or unusable."""


def _run(argv: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a tool and capture it.

    stdin is closed rather than inherited. ffmpeg reads stdin for interactive
    keys and will consume the parent's, which in a terminal makes an otherwise
    working batch run appear to hang; `-nostdin` covers the ffmpeg case but
    DEVNULL covers every tool uniformly.
    """
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _first_working(candidates: tuple[str, ...], flag: str = "-version") -> str | None:
    for candidate in candidates:
        path = candidate if Path(candidate).is_absolute() else shutil.which(candidate)
        if not path or not Path(path).exists():
            continue
        try:
            if _run([path, flag], timeout=15).returncode == 0:
                return path
        except (OSError, subprocess.SubprocessError):
            continue
    return None


@dataclass(frozen=True)
class FFmpegCapabilities:
    path: str
    version: str
    encoders: frozenset[str]
    decoders: frozenset[str]
    dxv_formats: frozenset[str] = field(default_factory=frozenset)
    hap_formats: frozenset[str] = field(default_factory=frozenset)

    @property
    def can_dxv(self) -> bool:
        return "dxv" in self.encoders

    @property
    def can_hap(self) -> bool:
        return "hap" in self.encoders

    @property
    def can_hap_alpha(self) -> bool:
        return "hap_alpha" in self.hap_formats

    @property
    def can_prores(self) -> bool:
        return "prores_ks" in self.encoders

    def missing_flv_decoders(self) -> list[str]:
        return [name for name in FLV_VIDEO_DECODERS if name not in self.decoders]

    def alpha_targets(self) -> list[str]:
        """Output codecs available here that genuinely preserve alpha.

        Ordered best-first for Resolume: Hap Alpha is GPU-decoded like DXV and
        measured byte-exact through a roundtrip; ProRes 4444 also preserves
        alpha but is CPU-decoded and was measured off by one 8-bit step in
        places, from the trip through 10-bit YUV.
        """
        targets = []
        if self.can_hap_alpha:
            targets.append("hap_alpha")
        if self.can_prores:
            targets.append("prores4444")
        return targets


def _parse_codec_list(output: str) -> frozenset[str]:
    """Pull codec names out of `-encoders` / `-decoders` output.

    The listing is a flags column then the name then a description. Splitting
    on whitespace and taking field 1 is enough, but only after the header,
    which is separated from the body by a line of dashes.
    """
    names: set[str] = set()
    body = output.split("------", 1)[-1]
    for line in body.splitlines():
        parts = line.split()
        # A codec line is "FLAGS name Description..." -- at least 2 fields, and
        # the flags column never contains a space.
        if len(parts) >= 2 and parts[0] and not parts[0].startswith("-"):
            names.add(parts[1])
    return frozenset(names)


def _parse_encoder_formats(output: str) -> frozenset[str]:
    """Read the named values of a `-format` AVOption out of encoder help.

    The option's values are indented lines of the shape
    `     dxt1            1146639409   E..V....... DXT1 (Normal Quality, No Alpha)`
    """
    formats: set[str] = set()
    in_format_block = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("-format"):
            in_format_block = True
            continue
        if in_format_block:
            # Value lines are more deeply indented than the option line; any
            # line starting a new option (or a blank line) ends the block.
            if not stripped or stripped.startswith("-"):
                in_format_block = False
                continue
            parts = stripped.split()
            if parts:
                formats.add(parts[0])
    return frozenset(formats)


@lru_cache(maxsize=1)
def find_ffmpeg() -> FFmpegCapabilities:
    """Locate ffmpeg and interrogate it. Cached -- it cannot change mid-run."""
    path = _first_working(FFMPEG_CANDIDATES)
    if path is None:
        raise ProbeError(
            "no usable ffmpeg found. amber drives the operator's own ffmpeg "
            "rather than bundling one; install it with `brew install ffmpeg-full` "
            "(ffmpeg-full is preferred -- plain ffmpeg has no Hap encoder, so "
            "transparent output would be unavailable)."
        )

    version_line = _run([path, "-version"]).stdout.splitlines()
    version = version_line[0] if version_line else "unknown"

    encoders = _parse_codec_list(_run([path, "-hide_banner", "-encoders"]).stdout)
    decoders = _parse_codec_list(_run([path, "-hide_banner", "-decoders"]).stdout)

    dxv_formats: frozenset[str] = frozenset()
    if "dxv" in encoders:
        dxv_formats = _parse_encoder_formats(
            _run([path, "-hide_banner", "-h", "encoder=dxv"]).stdout
        )

    hap_formats: frozenset[str] = frozenset()
    if "hap" in encoders:
        hap_formats = _parse_encoder_formats(
            _run([path, "-hide_banner", "-h", "encoder=hap"]).stdout
        )

    return FFmpegCapabilities(
        path=path,
        version=version,
        encoders=encoders,
        decoders=decoders,
        dxv_formats=dxv_formats,
        hap_formats=hap_formats,
    )


@lru_cache(maxsize=1)
def find_ffprobe() -> str:
    path = _first_working(FFPROBE_CANDIDATES)
    if path is None:
        raise ProbeError("no usable ffprobe found (it ships alongside ffmpeg).")
    return path


@dataclass(frozen=True)
class MediaInfo:
    codec: str
    width: int
    height: int
    frame_rate: float
    duration: float
    pix_fmt: str
    nb_frames: int | None

    @property
    def has_alpha(self) -> bool:
        """Whether this source carries transparency worth preserving.

        Both halves matter: vp6a is the Flash alpha codec, and a pix_fmt ending
        in `a` covers everything else ffmpeg might report.
        """
        return self.codec in FLV_ALPHA_CODECS or self.pix_fmt.endswith("a")


def _parse_rate(value: str) -> float:
    """ffprobe reports frame rates as the fraction `num/den`."""
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        try:
            denominator_value = float(denominator)
            if denominator_value == 0:
                return 0.0
            return float(numerator) / denominator_value
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def probe_media(path: str | Path) -> MediaInfo:
    """Read the first video stream of a media file."""
    path = Path(path)
    result = _run(
        [
            find_ffprobe(),
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate,pix_fmt,nb_frames:format=duration",
            "-of", "json",
            str(path),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise ProbeError(f"{path.name}: ffprobe failed: {result.stderr.strip()}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"{path.name}: could not parse ffprobe output: {exc}") from exc

    streams = payload.get("streams") or []
    if not streams:
        raise ProbeError(f"{path.name}: contains no video stream")
    stream = streams[0]

    nb_frames_raw = stream.get("nb_frames")
    try:
        nb_frames = int(nb_frames_raw) if nb_frames_raw not in (None, "N/A") else None
    except (TypeError, ValueError):
        nb_frames = None

    try:
        duration = float(payload.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0

    return MediaInfo(
        codec=stream.get("codec_name", "?"),
        width=int(stream.get("width") or 0),
        height=int(stream.get("height") or 0),
        frame_rate=_parse_rate(stream.get("r_frame_rate", "")),
        duration=duration,
        pix_fmt=stream.get("pix_fmt", ""),
        nb_frames=nb_frames,
    )


@lru_cache(maxsize=1)
def find_ruffle_exporter() -> str | None:
    """Locate a built `exporter` binary, or None if SWF support is unavailable.

    Ruffle does not ship the exporter in its releases, so this is always a
    local build. Returning None rather than raising lets the FLV half of amber
    work on a machine that has never built it.
    """
    explicit = shutil.which("ruffle-exporter") or shutil.which("exporter")
    if explicit:
        return explicit

    for base in (
        Path.home() / "Documents" / "Amber" / "ruffle-src",
        Path.home() / "Documents" / "Amber" / "ruffle",
    ):
        candidate = base / "target" / "release" / "exporter"
        if candidate.exists():
            return str(candidate)
    return None
