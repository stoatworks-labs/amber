"""Read the header of an SWF file.

Only the header is parsed. amber never interprets a tag, let alone an
ActionScript opcode -- Ruffle does that. What is needed here is the handful of
facts the exporter does not report and that the reassembly step cannot work
without: the stage size, the frame rate, and the declared frame count.

The frame rate matters most. `ruffle_exporter` writes a numbered PNG per frame
and says nothing about how fast they were meant to run, so muxing them without
reading it here produces a clip at ffmpeg's default 25fps. Flash content is
overwhelmingly 12, 24 or 30, and 12fps content played at 25 is a little over
twice as fast with no error anywhere to say so.
"""

from __future__ import annotations

import io
import lzma
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

# The three container signatures. The byte after them is the SWF version, which
# is the *language/feature* version and has nothing to do with the compression:
# a CWS file is zlib-compressed whatever its version says.
SIG_UNCOMPRESSED = b"FWS"
SIG_ZLIB = b"CWS"
SIG_LZMA = b"ZWS"

TWIPS_PER_PIXEL = 20

# Enough decompressed bytes to be certain the header is complete. The largest a
# header can be is 4 bytes of RECT nbits+fields at the maximum 31 bits each
# (~17 bytes) plus 4 for rate and count. 64 is comfortable and avoids
# decompressing an entire 40MB animation to read nine bytes of it.
_HEADER_SLACK = 64


class SWFError(Exception):
    """The file is not an SWF, or its header is malformed."""


@dataclass(frozen=True)
class SWFHeader:
    version: int
    compression: str  # "none" | "zlib" | "lzma"
    width: int  # pixels, rounded up
    height: int  # pixels, rounded up
    frame_rate: float
    frame_count: int
    file_length: int  # uncompressed length as *declared* by the header

    @property
    def duration(self) -> float:
        """Declared duration in seconds, or 0.0 if the rate is nonsense.

        This is what the header claims, not what Ruffle will actually play.
        A timeline that loops, stops, or is driven by ActionScript can run for
        any length of time regardless of what frame_count says.
        """
        if self.frame_rate <= 0:
            return 0.0
        return self.frame_count / self.frame_rate


class _BitReader:
    """MSB-first bit reader. SWF packs RECT fields without byte alignment."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0  # in bits

    def read_unsigned(self, nbits: int) -> int:
        value = 0
        for _ in range(nbits):
            byte_index = self._pos >> 3
            if byte_index >= len(self._data):
                raise SWFError("header truncated while reading a bit field")
            bit = (self._data[byte_index] >> (7 - (self._pos & 7))) & 1
            value = (value << 1) | bit
            self._pos += 1
        return value

    def read_signed(self, nbits: int) -> int:
        if nbits == 0:
            return 0
        value = self.read_unsigned(nbits)
        # Two's complement within nbits.
        if value & (1 << (nbits - 1)):
            value -= 1 << nbits
        return value

    @property
    def bytes_consumed(self) -> int:
        return (self._pos + 7) // 8


def _decompress_body(signature: bytes, raw: bytes, declared_length: int) -> bytes:
    """Return the header body (everything after the first 8 bytes), decompressed.

    Only enough bytes to cover the header are needed, so both branches tolerate
    a truncated stream: zlib via a decompressobj that is simply not fed the
    rest, LZMA by catching the EOF that decompressing a partial stream raises.
    """
    body = raw[8:]

    if signature == SIG_UNCOMPRESSED:
        return body

    if signature == SIG_ZLIB:
        try:
            return zlib.decompressobj().decompress(body, _HEADER_SLACK)
        except zlib.error as exc:
            raise SWFError(f"zlib body would not decompress: {exc}") from exc

    if signature == SIG_LZMA:
        # ZWS is not a plain LZMA stream. Bytes 8..11 are the compressed length
        # and 12..16 are the 5 LZMA properties; the actual stream starts at 17.
        # Python's lzma wants an ALONE-format header: 5 property bytes followed
        # by an 8-byte uncompressed size. The SWF header does not carry that
        # size in the right place or width, so it is rebuilt here from the
        # declared file length. Getting this wrong is not detectable from the
        # first few bytes, which is why it is spelled out.
        if len(raw) < 17:
            raise SWFError("LZMA SWF too short to contain its properties")
        props = raw[12:17]
        stream = raw[17:]
        # The declared length counts the whole uncompressed file including the
        # 8-byte prefix, which is not part of the LZMA payload.
        payload_length = max(declared_length - 8, 0)
        alone = props + struct.pack("<Q", payload_length) + stream
        decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
        try:
            return decompressor.decompress(alone, _HEADER_SLACK)
        except lzma.LZMAError as exc:
            raise SWFError(f"LZMA body would not decompress: {exc}") from exc

    raise SWFError(f"unknown signature {signature!r}")


def read_header(path: str | Path) -> SWFHeader:
    """Parse the header of the SWF at `path`.

    Raises SWFError if the file is not an SWF or the header does not parse.
    """
    path = Path(path)
    # A compressed header needs the first 8 bytes plus some compressed body;
    # 4KB is far more than any header needs and still one small read.
    with open(path, "rb") as handle:
        raw = handle.read(4096)

    if len(raw) < 8:
        raise SWFError(f"{path.name}: too short to be an SWF")

    signature = raw[:3]
    if signature not in (SIG_UNCOMPRESSED, SIG_ZLIB, SIG_LZMA):
        raise SWFError(
            f"{path.name}: not an SWF (signature {signature!r}, expected FWS/CWS/ZWS)"
        )

    version = raw[3]
    (file_length,) = struct.unpack("<I", raw[4:8])

    compression = {
        SIG_UNCOMPRESSED: "none",
        SIG_ZLIB: "zlib",
        SIG_LZMA: "lzma",
    }[signature]

    body = _decompress_body(signature, raw, file_length)
    if len(body) < 5:
        raise SWFError(f"{path.name}: header body truncated")

    reader = _BitReader(body)
    nbits = reader.read_unsigned(5)
    x_min = reader.read_signed(nbits)
    x_max = reader.read_signed(nbits)
    y_min = reader.read_signed(nbits)
    y_max = reader.read_signed(nbits)

    offset = reader.bytes_consumed
    if len(body) < offset + 4:
        raise SWFError(f"{path.name}: header ends before frame rate and count")

    # FrameRate is 8.8 fixed point stored little-endian, so the low byte is the
    # fraction and the high byte the integer part -- reading it as a plain
    # uint16 gives a number 256x too large.
    rate_fraction = body[offset]
    rate_integer = body[offset + 1]
    frame_rate = rate_integer + rate_fraction / 256.0

    (frame_count,) = struct.unpack("<H", body[offset + 2 : offset + 4])

    # The stage RECT is in twips and may have a non-zero origin; the size is the
    # extent, not the max. -(-x // y) is a ceiling division that keeps a stage
    # of 550.5px from silently losing its last column.
    width = -(-(x_max - x_min) // TWIPS_PER_PIXEL)
    height = -(-(y_max - y_min) // TWIPS_PER_PIXEL)

    return SWFHeader(
        version=version,
        compression=compression,
        width=int(width),
        height=int(height),
        frame_rate=frame_rate,
        frame_count=frame_count,
        file_length=file_length,
    )
