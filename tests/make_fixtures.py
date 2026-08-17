"""Build minimal but genuinely valid SWF files to test the header reader.

There is no SWF corpus on this machine and legacy Flash content cannot be
downloaded into a test suite, so the fixtures are synthesised. They are real
files -- correct signature, correct bit-packed RECT, correct 8.8 frame rate,
real tag stream, and for CWS/ZWS really compressed -- not recordings of what the
parser happens to emit. A fixture built by the code under test proves nothing.

Each file is a solid background colour for a few frames, which is also the
smallest thing Ruffle can be asked to render once there is a Ruffle to ask.
"""

from __future__ import annotations

import lzma
import struct
import zlib
from pathlib import Path

TWIPS_PER_PIXEL = 20

TAG_END = 0
TAG_SHOW_FRAME = 1
TAG_SET_BACKGROUND_COLOR = 9


def _encode_rect(x_min: int, x_max: int, y_min: int, y_max: int) -> bytes:
    """Bit-pack a RECT the way the SWF spec defines it: 5-bit width, then four
    signed fields of that width, MSB first, zero-padded to a byte boundary."""
    values = [x_min, x_max, y_min, y_max]

    # Every value must fit as a *signed* field of nbits, so the width is driven
    # by the largest magnitude plus its sign bit.
    nbits = 1
    for value in values:
        needed = value.bit_length() + 1  # +1 for the sign bit
        if value < 0:
            # Two's complement holds -2^(n-1) exactly, so a negative power of
            # two needs one bit fewer than the +1 rule would suggest.
            needed = (value + 1).bit_length() + 1 if value != -1 else 1
        nbits = max(nbits, needed)

    bits: list[int] = []

    def push(value: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            bits.append((value >> shift) & 1)

    push(nbits, 5)
    for value in values:
        push(value & ((1 << nbits) - 1), nbits)

    while len(bits) % 8:
        bits.append(0)

    out = bytearray()
    for index in range(0, len(bits), 8):
        byte = 0
        for bit in bits[index : index + 8]:
            byte = (byte << 1) | bit
        out.append(byte)
    return bytes(out)


def _tag(code: int, payload: bytes = b"") -> bytes:
    """A SWF tag: 10 bits of code and 6 bits of length, long form past 62."""
    if len(payload) < 0x3F:
        return struct.pack("<H", (code << 6) | len(payload)) + payload
    header = struct.pack("<H", (code << 6) | 0x3F)
    return header + struct.pack("<I", len(payload)) + payload


def build_swf(
    width: int = 550,
    height: int = 400,
    frame_rate: float = 12.0,
    frames: int = 3,
    version: int = 6,
    compression: str = "none",
    background: tuple[int, int, int] = (0x33, 0x66, 0x99),
) -> bytes:
    """Return the bytes of a complete, valid SWF."""
    body = bytearray()
    body += _encode_rect(0, width * TWIPS_PER_PIXEL, 0, height * TWIPS_PER_PIXEL)

    # 8.8 fixed point, little-endian: fraction byte first, then integer.
    integer = int(frame_rate)
    fraction = int(round((frame_rate - integer) * 256)) & 0xFF
    body += bytes([fraction, integer])
    body += struct.pack("<H", frames)

    body += _tag(TAG_SET_BACKGROUND_COLOR, bytes(background))
    for _ in range(frames):
        body += _tag(TAG_SHOW_FRAME)
    body += _tag(TAG_END)

    signature = {"none": b"FWS", "zlib": b"CWS", "lzma": b"ZWS"}[compression]

    # FileLength is the length of the whole *uncompressed* file, prefix included.
    file_length = 8 + len(body)
    prefix = signature + bytes([version]) + struct.pack("<I", file_length)

    if compression == "none":
        return prefix + bytes(body)

    if compression == "zlib":
        return prefix + zlib.compress(bytes(body))

    # ZWS layout: 8-byte prefix, uint32 compressed length, 5 property bytes,
    # then the raw LZMA stream -- note the compressed length sits *before* the
    # properties, which is the detail that makes ZWS not a plain .lzma file.
    filters = [{"id": lzma.FILTER_LZMA1, "preset": 6}]
    compressor = lzma.LZMACompressor(format=lzma.FORMAT_ALONE, filters=filters)
    packed = compressor.compress(bytes(body)) + compressor.flush()
    props, stream = packed[:5], packed[13:]  # skip the 8-byte ALONE size field
    return prefix + struct.pack("<I", len(stream)) + props + stream


def write_all(directory: Path) -> dict[str, Path]:
    """Write one fixture per compression plus a fractional-rate case."""
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    cases = {
        "plain.swf": dict(compression="none", frame_rate=12.0, frames=3),
        "zlib.swf": dict(compression="zlib", frame_rate=24.0, frames=10, version=8),
        "lzma.swf": dict(compression="lzma", frame_rate=30.0, frames=48, version=13),
        # 29.97 exercises the 8.8 fixed point path, where an off-by-256 or a
        # byte-order slip is obvious rather than plausible.
        "fractional.swf": dict(
            compression="none", frame_rate=29.97, frames=5, width=1280, height=720
        ),
    }

    for name, kwargs in cases.items():
        path = directory / name
        path.write_bytes(build_swf(**kwargs))
        written[name] = path
    return written


if __name__ == "__main__":
    here = Path(__file__).parent / "fixtures"
    for name, path in write_all(here).items():
        print(f"{name}: {path.stat().st_size} bytes")
