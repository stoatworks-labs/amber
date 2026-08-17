"""Dimension constraints of the GPU texture codecs, and how to satisfy them.

These numbers were measured on ffmpeg 8.1.2 by encoding a known test pattern,
decoding it again and comparing raw RGBA, not read from documentation. The
sweep is reproduced by tests/test_align.py.

    codec         width          height         what happens if you ignore it
    ----------------------------------------------------------------------
    DXV           multiple of 16 unconstrained  SILENT CORRUPTION
    Hap (all)     multiple of 4  multiple of 4  refuses, with a clear error
    ProRes 4444   unconstrained  unconstrained  --

The DXV row is the reason this module exists, and it is worth being blunt about
it. At a width that is not a multiple of 16 the encoder returns success, writes
a file of a plausible size, and reports the right dimensions in its header; the
picture inside is sheared diagonally, because the rows are written at a stride
of the padded width and read back at the requested one. Nothing in the ffmpeg
output at any log level mentions it.

It is not an obscure corner. **550x400 is the default Flash stage size**, so a
naive .swf -> DXV pipeline corrupts a large fraction of all Flash content ever
made, and the badger.swf used to develop amber is exactly 550x400. Hap by
contrast refuses the encode outright, which is a far better failure -- a build
that stops is fixable; one that quietly ships sheared footage to a show is not.

Whether Resolume's own DXV decoder reproduces ffmpeg's shear is UNVERIFIED --
it may well read the padded stride correctly and play the file fine. amber
aligns anyway: the roundtrip through ffmpeg is the only decoder available to
test against here, and shipping footage whose correctness depends on an
untested disagreement between two decoders is not worth the few pixels saved.
"""

from __future__ import annotations

from dataclasses import dataclass

# Per-profile (width_multiple, height_multiple). 1 means unconstrained.
CONSTRAINTS: dict[str, tuple[int, int]] = {
    "dxv": (16, 1),
    "hap": (4, 4),
    "hap_alpha": (4, 4),
    "hap_q": (4, 4),
    "prores4444": (1, 1),
}


@dataclass(frozen=True)
class Alignment:
    width: int
    height: int
    original_width: int
    original_height: int
    strategy: str  # "none" | "scale" | "pad"

    @property
    def changed(self) -> bool:
        return (self.width, self.height) != (self.original_width, self.original_height)

    def describe(self) -> str:
        if not self.changed:
            return ""
        return (
            f"{self.original_width}x{self.original_height} -> "
            f"{self.width}x{self.height} ({self.strategy})"
        )


def _round_to(value: int, multiple: int) -> int:
    """Round to the NEAREST multiple, never below it.

    Nearest rather than up, because rounding 550 up to 560 stretches by 1.8%
    while rounding to 544 shrinks by 1.1%; over a whole library the nearest
    rule keeps the average distortion smaller. The max() floor stops a tiny
    source from rounding away to zero.
    """
    if multiple <= 1:
        return value
    return max(multiple, int(round(value / multiple)) * multiple)


def _round_up(value: int, multiple: int) -> int:
    if multiple <= 1:
        return value
    return max(multiple, -(-value // multiple) * multiple)


def align_for(
    width: int,
    height: int,
    profile: str,
    strategy: str = "scale",
) -> Alignment:
    """Return the dimensions `profile` will accept.

    `strategy` is "scale" (resample to the nearest legal size, changing the
    aspect ratio by a fraction of a percent) or "pad" (grow to the next legal
    size, keeping every original pixel untouched and filling the remainder).

    Padding is pixel-exact but bakes a border into the clip, and since DXV
    carries no alpha that border is opaque black -- visible on any layer
    composited over something else. Scaling is therefore the default: Resolume
    rescales clips to the composition anyway, so a 1% aspect change is
    invisible in use where a black edge strip is not.
    """
    width_multiple, height_multiple = CONSTRAINTS.get(profile, (1, 1))

    if strategy == "pad":
        new_width = _round_up(width, width_multiple)
        new_height = _round_up(height, height_multiple)
    elif strategy == "scale":
        new_width = _round_to(width, width_multiple)
        new_height = _round_to(height, height_multiple)
    else:
        raise ValueError(f"unknown alignment strategy {strategy!r}")

    used = "none" if (new_width, new_height) == (width, height) else strategy
    return Alignment(
        width=new_width,
        height=new_height,
        original_width=width,
        original_height=height,
        strategy=used,
    )


def is_legal(width: int, height: int, profile: str) -> bool:
    width_multiple, height_multiple = CONSTRAINTS.get(profile, (1, 1))
    return width % width_multiple == 0 and height % height_multiple == 0
