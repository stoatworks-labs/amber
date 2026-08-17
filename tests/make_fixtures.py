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


def build_swf_showcase(
    width: int = 960,
    height: int = 540,
    frames: int = 48,
    frame_rate: float = 24.0,
) -> bytes:
    """Several overlapping rectangles on a transparent stage.

    Exists so the project has demonstration content of its OWN. Every real Flash
    file that would show amber off is somebody else's copyrighted work -- fine as
    a local test file, not fine as promotional artwork on a public site, and the
    fleet ships no third-party content by rule. This is generated, so it can go
    anywhere.

    No background colour tag is emitted at all, which combined with
    `wmode=transparent` makes everything the shapes do not cover genuinely clear.
    """
    body = bytearray()
    body += _encode_rect(0, width * TWIPS_PER_PIXEL, 0, height * TWIPS_PER_PIXEL)

    integer = int(frame_rate)
    fraction = int(round((frame_rate - integer) * 256)) & 0xFF
    body += bytes([fraction, integer])
    body += struct.pack("<H", frames)

    # A stepped diagonal of blocks, each its own shape at its own depth.
    palette = [
        (0xE8, 0x3A, 0x3A), (0xE8, 0x8B, 0x3A), (0xE8, 0xD0, 0x3A),
        (0x5A, 0xC8, 0x5A), (0x3A, 0xB0, 0xE8), (0x6A, 0x5A, 0xE8),
        (0xC0, 0x4A, 0xC8),
    ]
    count = len(palette)
    block_w = width // (count + 2)
    block_h = height // 3

    for index, colour in enumerate(palette):
        x = (block_w + block_w // 3) * index + block_w // 2
        y = (height - block_h) // 2 + int((index - count / 2) * block_h / 5)
        body += _define_shape_rect(
            index + 1,
            x * TWIPS_PER_PIXEL, y * TWIPS_PER_PIXEL,
            block_w * TWIPS_PER_PIXEL, block_h * TWIPS_PER_PIXEL,
            colour,
        )
        body += _place_object2(index + 1, index + 1)

    for _ in range(frames):
        body += _tag(TAG_SHOW_FRAME)
    body += _tag(TAG_END)

    file_length = 8 + len(body)
    return b"FWS" + bytes([6]) + struct.pack("<I", file_length) + bytes(body)


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

    # The only fixture that draws anything, and therefore the only one against
    # which transparency is measurable: a rectangle over a quarter of the stage,
    # so `wmode=transparent` must leave the other three quarters clear.
    shape = directory / "shape.swf"
    shape.write_bytes(build_swf_with_shape())
    written["shape.swf"] = shape

    # Demonstration content the project owns outright -- see build_swf_showcase.
    showcase = directory / "showcase.swf"
    showcase.write_bytes(build_swf_showcase())
    written["showcase.swf"] = showcase
    return written


class _BitWriter:
    """MSB-first bit writer. SWF shape records are bit-packed and unaligned."""

    def __init__(self) -> None:
        self._bits: list[int] = []

    def write(self, value: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            self._bits.append((value >> shift) & 1)

    def write_signed(self, value: int, width: int) -> None:
        self.write(value & ((1 << width) - 1), width)

    def align(self) -> bytes:
        bits = list(self._bits)
        while len(bits) % 8:
            bits.append(0)
        out = bytearray()
        for index in range(0, len(bits), 8):
            byte = 0
            for bit in bits[index : index + 8]:
                byte = (byte << 1) | bit
            out.append(byte)
        return bytes(out)


def _define_shape_rect(shape_id: int, x: int, y: int, w: int, h: int,
                       colour: tuple[int, int, int]) -> bytes:
    """A DefineShape tag holding one solid rectangle, in twips.

    Hand-built rather than taken from a real file so the fixture stays
    redistributable -- no third-party Flash is committed to this repo. It is a
    real DefineShape, not a stub: correct bit-packed RECT, a real fill style
    array, and four genuine StraightEdgeRecords.
    """
    body = bytearray()
    body += struct.pack("<H", shape_id)
    # ShapeBounds.
    body += _encode_rect(x, x + w, y, y + h)

    # FillStyleArray: one solid RGB fill. LineStyleArray: none.
    body += bytes([1])          # fill style count
    body += bytes([0x00])       # type 0x00 = solid
    body += bytes(colour)       # RGB (DefineShape 1/2 use RGB, not RGBA)
    body += bytes([0])          # line style count

    writer = _BitWriter()
    fill_bits, line_bits = 1, 0
    writer.write(fill_bits, 4)
    writer.write(line_bits, 4)

    # StyleChangeRecord: move to the origin and select the fill.
    #
    # The path below is traced clockwise in SWF coordinates (y increases
    # downward), which puts the interior on the RIGHT of each edge -- so the
    # fill belongs to fillStyle1, not fillStyle0. Setting the wrong side gives a
    # perfectly valid shape enclosing nothing, which renders as an empty stage
    # with no error anywhere.
    writer.write(0, 1)          # TypeFlag: 0 = non-edge record
    writer.write(0, 1)          # StateNewStyles
    writer.write(0, 1)          # StateLineStyle
    writer.write(1, 1)          # StateFillStyle1
    writer.write(0, 1)          # StateFillStyle0
    writer.write(1, 1)          # StateMoveTo

    # Field order after the flags is fixed: MoveTo, then FillStyle0, then
    # FillStyle1, then LineStyle.
    move_bits = 20              # a 5-bit field, so up to 31 is legal
    writer.write(move_bits, 5)
    writer.write_signed(x, move_bits)
    writer.write_signed(y, move_bits)
    writer.write(1, fill_bits)  # fillStyle1 -> index 1

    # Four StraightEdgeRecords tracing the rectangle.
    #
    # NumBits is a FOUR-bit field holding (actual bits - 2), so the widest edge
    # it can describe is 17 bits. Asking for 20 writes 18 into four bits, which
    # silently truncates and corrupts every edge that follows -- the shape then
    # parses without complaint and draws nothing. 15 bits is signed +/-16383
    # twips, comfortably past the 4000 a half-stage needs here.
    edge_bits = 15
    assert 2 <= edge_bits - 2 <= 15, "NumBits must fit in four bits"
    for delta_x, delta_y in ((w, 0), (0, h), (-w, 0), (0, -h)):
        writer.write(1, 1)                  # TypeFlag: edge
        writer.write(1, 1)                  # StraightFlag
        writer.write(edge_bits - 2, 4)      # NumBits, stored as actual-2
        writer.write(1, 1)                  # GeneralLineFlag
        writer.write_signed(delta_x, edge_bits)
        writer.write_signed(delta_y, edge_bits)

    # EndShapeRecord: a non-edge record with all five state flags clear.
    writer.write(0, 6)

    body += writer.align()
    return _tag(2, bytes(body))  # tag 2 = DefineShape


def _place_object2(shape_id: int, depth: int) -> bytes:
    """PlaceObject2 with an identity matrix -- the shape carries its own
    coordinates, so nothing needs transforming."""
    flags = 0b0000_0110  # HasMatrix | HasCharacter
    body = bytes([flags]) + struct.pack("<H", depth) + struct.pack("<H", shape_id)
    # MATRIX: HasScale=0, HasRotate=0, TranslateBits=0 -> seven zero bits.
    body += bytes([0x00])
    return _tag(26, body)


def build_swf_with_shape(
    width: int = 400,
    height: int = 300,
    frame_rate: float = 12.0,
    frames: int = 4,
    background: tuple[int, int, int] = (0xFF, 0x00, 0x00),
    fill: tuple[int, int, int] = (0x00, 0xFF, 0x00),
) -> bytes:
    """An SWF drawing one rectangle over a QUARTER of the stage.

    This is the fixture transparency is actually testable against. A file that
    draws nothing proves only that the clear colour changed, and real content
    like badger.swf paints its own full-stage background so it can never show
    transparency at all. Here three quarters of the stage are genuinely
    untouched, so `wmode=transparent` must leave exactly that much clear.
    """
    body = bytearray()
    body += _encode_rect(0, width * TWIPS_PER_PIXEL, 0, height * TWIPS_PER_PIXEL)

    integer = int(frame_rate)
    fraction = int(round((frame_rate - integer) * 256)) & 0xFF
    body += bytes([fraction, integer])
    body += struct.pack("<H", frames)

    body += _tag(TAG_SET_BACKGROUND_COLOR, bytes(background))
    body += _define_shape_rect(
        1,
        0, 0,
        (width // 2) * TWIPS_PER_PIXEL,
        (height // 2) * TWIPS_PER_PIXEL,
        fill,
    )
    body += _place_object2(1, 1)
    for _ in range(frames):
        body += _tag(TAG_SHOW_FRAME)
    body += _tag(TAG_END)

    file_length = 8 + len(body)
    return b"FWS" + bytes([6]) + struct.pack("<I", file_length) + bytes(body)


if __name__ == "__main__":
    here = Path(__file__).parent / "fixtures"
    for name, path in write_all(here).items():
        print(f"{name}: {path.stat().st_size} bytes")
