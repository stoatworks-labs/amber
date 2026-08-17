"""Synthesise a VP6-with-alpha (`vp6a`) FLV from an ordinary VP6 (`vp6f`) one.

## Why this exists

`vp6a` is the codec that transparent Flash overlay loops were made in, and it
is the single case amber's whole alpha-routing path exists to serve. It was
also, for a long time, the only thing in the project that could not be tested:
**ffmpeg can decode VP6 but cannot encode it**, and no free VP6 encoder exists,
so a `vp6a` fixture could not be generated the way every other fixture here is.

It turns out no encoder is needed. In FLV, codec ID 4 is VP6 and codec ID 5 is
VP6-with-alpha, and the difference between their packets is purely structural:

    codec 4 (VP6):        [frametype|4] [adjustment] <VP6 stream>
    codec 5 (VP6ALPHA):   [frametype|5] [adjustment] [UI24 offset]
                          <VP6 stream: colour> <VP6 stream: alpha>

The alpha plane is *itself an ordinary VP6 stream*, decoded as greyscale, whose
luma becomes the alpha channel. So a valid `vp6a` file can be assembled entirely
from VP6 bitstreams that already exist -- reusing each frame's colour stream as
its own alpha plane. The resulting alpha is the luma of the picture, which is
not artistically meaningful but is exactly what a test needs: a real, varying,
per-pixel alpha channel produced by a real VP6 decode path.

`OffsetToAlpha` is the distance from the start of the colour data to the start
of the alpha data, i.e. the length of the colour stream.

## What it does NOT prove

The file is genuine `vp6a` and exercises the real two-plane decode. It is not a
sample of what a Flash authoring tool of the period emitted, and its alpha is
correlated with its luma rather than independent. For amber's purposes -- does
the pipeline detect alpha, refuse DXV, and route to Hap Alpha without losing it
-- that distinction does not matter.

Usage:
    python3 tools/make_vp6a.py input.flv output.flv
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

TAG_AUDIO = 8
TAG_VIDEO = 9
TAG_SCRIPT = 18

CODEC_VP6 = 4
CODEC_VP6_ALPHA = 5


class FLVError(Exception):
    """The input is not an FLV, or holds no VP6 video."""


def _read_tags(data: bytes):
    """Yield (tag_type, timestamp, body) for each tag in an FLV."""
    if data[:3] != b"FLV":
        raise FLVError("not an FLV file")

    offset = struct.unpack(">I", data[5:9])[0]
    position = offset

    while position + 15 <= len(data):
        # Each tag is preceded by the size of the previous one.
        position += 4
        if position + 11 > len(data):
            break

        tag_type = data[position] & 0x1F
        size = int.from_bytes(data[position + 1 : position + 4], "big")
        timestamp = int.from_bytes(data[position + 4 : position + 7], "big")
        # The 4th timestamp byte is the high-order extension, not padding.
        timestamp |= data[position + 7] << 24

        body = data[position + 11 : position + 11 + size]
        if len(body) != size:
            break  # truncated final tag

        yield tag_type, timestamp, body
        position += 11 + size


def _write_tag(out: bytearray, tag_type: int, timestamp: int, body: bytes) -> None:
    """Append a tag, with the PreviousTagSize word that precedes the NEXT one.

    The caller is responsible for having written the initial PreviousTagSize of
    zero after the file header.
    """
    out.append(tag_type)
    out += len(body).to_bytes(3, "big")
    out += (timestamp & 0xFFFFFF).to_bytes(3, "big")
    out.append((timestamp >> 24) & 0xFF)
    out += b"\x00\x00\x00"  # StreamID, always zero
    out += body
    out += struct.pack(">I", 11 + len(body))


def convert(data: bytes) -> bytes:
    """Return a vp6a FLV built from the vp6f one in `data`."""
    out = bytearray()
    # Header: signature, version, flags (video only -- the audio is dropped
    # below, and a flags byte claiming audio that is not there confuses probes),
    # then the data offset.
    out += b"FLV" + bytes([1, 0x01]) + struct.pack(">I", 9)
    out += struct.pack(">I", 0)  # PreviousTagSize0

    video_tags = 0

    for tag_type, timestamp, body in _read_tags(data):
        # Audio is dropped: FFGL has no audio path, so amber never carries it,
        # and a video-only fixture keeps the test honest about what is measured.
        # The script tag is dropped rather than patched -- its onMetaData
        # declares videocodecid 4, and a metadata block disagreeing with the
        # actual packets is worse than none at all.
        if tag_type != TAG_VIDEO or not body:
            continue

        frame_type = body[0] >> 4
        codec_id = body[0] & 0x0F
        if codec_id != CODEC_VP6:
            raise FLVError(
                f"expected VP6 (codec 4) video, found codec {codec_id}"
            )

        adjustment = body[1]
        colour = body[2:]

        if len(colour) >= 1 << 24:
            raise FLVError("VP6 frame too large for a 24-bit alpha offset")

        # Reuse the colour stream as the alpha plane. A VP6 alpha plane is an
        # ordinary VP6 stream decoded as greyscale, so this is structurally
        # valid and gives a real per-pixel alpha channel.
        alpha = colour

        packet = bytearray()
        packet.append((frame_type << 4) | CODEC_VP6_ALPHA)
        packet.append(adjustment)
        packet += len(colour).to_bytes(3, "big")  # OffsetToAlpha
        packet += colour
        packet += alpha

        _write_tag(out, TAG_VIDEO, timestamp, bytes(packet))
        video_tags += 1

    if video_tags == 0:
        raise FLVError("input contained no VP6 video tags")

    return bytes(out)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        print("usage: make_vp6a.py <input.flv> <output.flv>", file=sys.stderr)
        return 2

    source, destination = Path(argv[1]), Path(argv[2])
    try:
        result = convert(source.read_bytes())
    except FLVError as exc:
        print(f"make_vp6a: {source.name}: {exc}", file=sys.stderr)
        return 1

    destination.write_bytes(result)
    print(f"{source.name} -> {destination.name}  {len(result)} bytes (vp6a)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
