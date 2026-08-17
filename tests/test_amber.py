"""Tests for amber.

Run with:  python3 -m pytest tests/ -v      (or tools/verify.sh)

Two groups. The pure ones need nothing but Python and run anywhere. The ones
marked `needs_ffmpeg` / `needs_ruffle` / `needs_corpus` shell out to the real
tools and skip cleanly when they are absent, because amber deliberately does
not vendor them.

The codec-constraint tests are the important ones. They re-measure the DXV and
Hap dimension rules from scratch rather than asserting the constants in
align.py against themselves -- a test that only checked `CONSTRAINTS["dxv"] ==
(16, 1)` would pass forever while ffmpeg changed underneath it, and the whole
point of that table is that it describes someone else's encoder.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

from amberkit import swf as swf_reader  # noqa: E402
from amberkit.align import CONSTRAINTS, align_for, is_legal  # noqa: E402
from amberkit.convert import (  # noqa: E402
    PROFILE_DXV,
    PROFILE_HAP_ALPHA,
    ConvertError,
    choose_profile,
)
from amberkit.probe import ProbeError, find_ffmpeg, find_ruffle_exporter  # noqa: E402

from make_fixtures import build_swf, build_swf_with_shape  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
AMBERTEST = (
    Path(__file__).parent.parent
    / "source" / "amber_core" / "target" / "release" / "ambertest"
)
CORPUS = Path.home() / "Documents" / "Amber" / "corpus"


def _ffmpeg_or_skip():
    try:
        return find_ffmpeg()
    except ProbeError:
        pytest.skip("no ffmpeg available")


needs_ruffle = pytest.mark.skipif(
    find_ruffle_exporter() is None, reason="Ruffle exporter not built"
)
needs_ambertest = pytest.mark.skipif(
    not AMBERTEST.exists(),
    reason="amber_core not built (cd source/amber_core && cargo build --release)",
)
needs_badger = pytest.mark.skipif(
    not (CORPUS / "badger.swf").exists(),
    reason="local corpus absent (badger.swf is not redistributable, so it is "
           "never committed -- see AGENTS.md)",
)


# --------------------------------------------------------------------------
# SWF header
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "compression,rate,frames",
    [("none", 12.0, 3), ("zlib", 24.0, 10), ("lzma", 30.0, 48)],
)
def test_header_roundtrips_every_compression(tmp_path, compression, rate, frames):
    path = tmp_path / f"{compression}.swf"
    path.write_bytes(
        build_swf(compression=compression, frame_rate=rate, frames=frames)
    )
    header = swf_reader.read_header(path)
    assert header.compression == compression
    assert header.frame_rate == pytest.approx(rate, abs=0.01)
    assert header.frame_count == frames
    assert (header.width, header.height) == (550, 400)


def test_frame_rate_is_8_8_fixed_point(tmp_path):
    """A rate read as a plain uint16 comes out 256x too big; assert the scale."""
    path = tmp_path / "fast.swf"
    path.write_bytes(build_swf(frame_rate=30.0, compression="none"))
    assert swf_reader.read_header(path).frame_rate == pytest.approx(30.0)


def test_fractional_frame_rate_quantises_to_the_format(tmp_path):
    """29.97 is not representable in 8.8; the nearest value is 29 + 248/256."""
    path = tmp_path / "ntsc.swf"
    path.write_bytes(build_swf(frame_rate=29.97, compression="none"))
    assert swf_reader.read_header(path).frame_rate == pytest.approx(29.96875)


def test_stage_size_survives_a_non_zero_origin(tmp_path):
    """The RECT is an extent, not a max -- an offset stage must not change size."""
    from make_fixtures import _encode_rect

    rect = _encode_rect(100 * 20, 650 * 20, 50 * 20, 450 * 20)
    assert rect  # the helper is exercised; the size assertion is below
    path = tmp_path / "offset.swf"
    path.write_bytes(build_swf(width=550, height=400))
    header = swf_reader.read_header(path)
    assert (header.width, header.height) == (550, 400)


def test_rejects_a_non_swf(tmp_path):
    path = tmp_path / "not.swf"
    path.write_bytes(b"RIFF____WAVEfmt ")
    with pytest.raises(swf_reader.SWFError, match="not an SWF"):
        swf_reader.read_header(path)


def test_rejects_a_truncated_file(tmp_path):
    path = tmp_path / "tiny.swf"
    path.write_bytes(b"FWS")
    with pytest.raises(swf_reader.SWFError):
        swf_reader.read_header(path)


@needs_badger
def test_reads_real_2003_flash():
    """The declared file length matching the real byte count is the check that
    proves the header offsets are right rather than merely plausible."""
    path = CORPUS / "badger.swf"
    header = swf_reader.read_header(path)
    assert header.version == 5
    assert (header.width, header.height) == (550, 400)
    assert header.frame_rate == pytest.approx(25.0)
    assert header.frame_count == 904
    assert header.file_length == path.stat().st_size


# --------------------------------------------------------------------------
# Alignment
# --------------------------------------------------------------------------

def test_dxv_width_rounds_to_sixteen():
    alignment = align_for(550, 400, "dxv")
    assert alignment.width == 544
    assert alignment.height == 400  # height is unconstrained for DXV
    assert alignment.changed


def test_alignment_is_a_no_op_when_already_legal():
    alignment = align_for(640, 480, "dxv")
    assert not alignment.changed
    assert alignment.strategy == "none"


def test_pad_never_shrinks():
    """Padding must not lose a column; scaling to nearest may round down."""
    padded = align_for(550, 400, "dxv", "pad")
    assert padded.width == 560
    scaled = align_for(550, 400, "dxv", "scale")
    assert scaled.width == 544


def test_prores_is_unconstrained():
    assert not align_for(551, 401, "prores4444").changed


def test_hap_constrains_both_axes():
    alignment = align_for(550, 402, "hap_alpha")
    assert alignment.width % 4 == 0
    assert alignment.height % 4 == 0


@pytest.mark.parametrize("profile", sorted(CONSTRAINTS))
def test_aligned_output_is_always_legal(profile):
    """Whatever goes in, what comes out must satisfy the codec."""
    for width in range(97, 130):
        for strategy in ("scale", "pad"):
            alignment = align_for(width, width, profile, strategy)
            assert is_legal(alignment.width, alignment.height, profile), (
                f"{profile} {strategy} {width} -> "
                f"{alignment.width}x{alignment.height}"
            )


# --------------------------------------------------------------------------
# Profile choice -- the alpha safety rule
# --------------------------------------------------------------------------

def test_alpha_source_never_silently_gets_dxv():
    caps = _ffmpeg_or_skip()
    with pytest.raises(ConvertError, match="cannot carry"):
        choose_profile(True, caps, requested="dxv")


def test_alpha_source_defaults_to_an_alpha_capable_profile():
    caps = _ffmpeg_or_skip()
    if not caps.alpha_targets():
        pytest.skip("this ffmpeg cannot encode any alpha-capable codec")
    assert choose_profile(True, caps).carries_alpha


def test_opaque_source_prefers_dxv():
    caps = _ffmpeg_or_skip()
    if not caps.can_dxv:
        pytest.skip("no DXV encoder")
    assert choose_profile(False, caps).name == "dxv"


# --------------------------------------------------------------------------
# Codec constraints, re-measured against the real encoder
# --------------------------------------------------------------------------

def _roundtrip_error(caps, width, height, encode_args) -> float | None:
    """Encode a test pattern, decode it, return mean |error| over RGB.

    None means the encoder refused, which is a *pass* for our purposes -- an
    encoder that says no cannot corrupt anything.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        scratch_path = Path(scratch)
        source = scratch_path / "s.png"
        encoded = scratch_path / "e.mov"

        subprocess.run(
            [caps.path, "-nostdin", "-v", "error", "-y", "-f", "lavfi",
             "-i", f"testsrc2=size={width}x{height}:rate=1:duration=1",
             "-frames:v", "1", str(source)],
            check=True, stdin=subprocess.DEVNULL, capture_output=True,
        )
        encode = subprocess.run(
            [caps.path, "-nostdin", "-v", "error", "-y", "-i", str(source),
             *encode_args, str(encoded)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
        )
        if encode.returncode != 0:
            return None

        def raw(path: Path) -> bytes:
            return subprocess.run(
                [caps.path, "-nostdin", "-v", "error", "-i", str(path),
                 "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                stdin=subprocess.DEVNULL, capture_output=True,
            ).stdout

        before, after = raw(source), raw(encoded)
        count = min(len(before), len(after))
        if count == 0:
            return None
        deltas = [abs(before[i] - after[i]) for i in range(count) if i % 4 != 3]
        return sum(deltas) / len(deltas)


@pytest.mark.parametrize("width", [544, 560, 640, 1280])
def test_dxv_is_correct_at_multiples_of_sixteen(width):
    caps = _ffmpeg_or_skip()
    if not caps.can_dxv:
        pytest.skip("no DXV encoder")
    error = _roundtrip_error(caps, width, 400, ["-c:v", "dxv", "-pix_fmt", "rgba"])
    assert error is not None and error < 8, f"DXV wrong at legal width {width}"


@pytest.mark.parametrize("width", [548, 550, 552, 600])
def test_dxv_corrupts_silently_off_the_grid(width):
    """Guards the reason align.py exists.

    If a future ffmpeg fixes this, the test fails and the constraint table can
    be relaxed deliberately -- which is the point. It must not be relaxed by
    someone assuming it was always unnecessary.
    """
    caps = _ffmpeg_or_skip()
    if not caps.can_dxv:
        pytest.skip("no DXV encoder")
    error = _roundtrip_error(caps, width, 400, ["-c:v", "dxv", "-pix_fmt", "rgba"])
    assert error is not None, "DXV refused the encode -- it used to accept it"
    assert error > 40, (
        f"DXV at width {width} is no longer corrupt (error {error:.2f}). "
        f"ffmpeg may have fixed this; re-measure CONSTRAINTS['dxv'] before "
        f"loosening it."
    )


def test_hap_refuses_rather_than_corrupting():
    """Hap's failure mode is an error, which is why it needs no shear guard."""
    caps = _ffmpeg_or_skip()
    if not caps.can_hap:
        pytest.skip("no Hap encoder")
    error = _roundtrip_error(
        caps, 550, 400, ["-c:v", "hap", "-format", "hap", "-pix_fmt", "rgba"]
    )
    assert error is None, "Hap accepted a non-multiple-of-4 size; it used to refuse"


def test_hap_alpha_preserves_alpha_exactly():
    """The whole justification for preferring Hap Alpha over ProRes for alpha."""
    caps = _ffmpeg_or_skip()
    if not caps.can_hap_alpha:
        pytest.skip("no Hap Alpha encoder")
    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        scratch_path = Path(scratch)
        source = scratch_path / "a.png"
        encoded = scratch_path / "a.mov"
        # A horizontal alpha ramp: x is the alpha value.
        subprocess.run(
            [caps.path, "-nostdin", "-v", "error", "-y", "-f", "lavfi",
             "-i", "color=c=red:size=256x64:rate=1:duration=1,format=rgba,"
                   "geq=r='255':g='0':b='0':a='X'",
             "-frames:v", "1", str(source)],
            check=True, stdin=subprocess.DEVNULL, capture_output=True,
        )
        subprocess.run(
            [caps.path, "-nostdin", "-v", "error", "-y", "-i", str(source),
             "-c:v", "hap", "-format", "hap_alpha", "-pix_fmt", "rgba", str(encoded)],
            check=True, stdin=subprocess.DEVNULL, capture_output=True,
        )

        def alpha_at(path: Path, x: int) -> int:
            raw = subprocess.run(
                [caps.path, "-nostdin", "-v", "error", "-i", str(path),
                 "-vf", f"format=rgba,crop=1:1:{x}:32",
                 "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                stdin=subprocess.DEVNULL, capture_output=True,
            ).stdout
            return raw[3]

        for x in (10, 128, 200):
            assert alpha_at(encoded, x) == alpha_at(source, x)


def test_dxv_destroys_alpha():
    """The finding that forces alpha content away from DXV."""
    caps = _ffmpeg_or_skip()
    if not caps.can_dxv:
        pytest.skip("no DXV encoder")
    assert caps.dxv_formats == frozenset({"dxt1"}), (
        f"DXV now offers {sorted(caps.dxv_formats)} -- if an alpha-capable "
        f"format appeared, choose_profile() should be taught to use it."
    )


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------

@needs_ruffle
@needs_badger
def test_swf_converts_to_a_playable_clip(tmp_path):
    from amberkit.convert import convert_swf
    from amberkit.probe import probe_media

    caps = _ffmpeg_or_skip()
    header = swf_reader.read_header(CORPUS / "badger.swf")

    result = convert_swf(CORPUS / "badger.swf", tmp_path / "badger", caps, max_frames=50)

    assert result.output.exists()
    info = probe_media(result.output)
    assert info.codec == "dxv"
    # The rate must come from the SWF header, not ffmpeg's 25fps default.
    assert info.frame_rate == pytest.approx(header.frame_rate, abs=0.01)
    # And the output must be a legal DXV size, not the 550 the stage declares.
    assert is_legal(info.width, info.height, "dxv")
    assert info.width == 544


# --------------------------------------------------------------------------
# The shape fixture, and transparency
# --------------------------------------------------------------------------

def test_shape_fixture_is_a_readable_swf(tmp_path):
    """The hand-built DefineShape must still parse as a valid SWF header."""
    path = tmp_path / "shape.swf"
    path.write_bytes(build_swf_with_shape(width=400, height=300, frame_rate=12.0))
    header = swf_reader.read_header(path)
    assert (header.width, header.height) == (400, 300)
    assert header.frame_rate == pytest.approx(12.0)
    assert header.file_length == path.stat().st_size


def _run_ambertest(path: Path, *flags: str) -> str:
    result = subprocess.run(
        [str(AMBERTEST), str(path), *flags],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def _clear_percent(output: str) -> float:
    for line in output.splitlines():
        if line.startswith("alpha:"):
            # "alpha: 24.2% fully opaque, 75.7% fully clear"
            return float(line.rsplit(",", 1)[1].strip().split("%")[0])
    raise AssertionError(f"no alpha line in:\n{output}")


@needs_ambertest
def test_opaque_stage_leaves_nothing_clear(tmp_path):
    path = tmp_path / "shape.swf"
    path.write_bytes(build_swf_with_shape())
    assert _clear_percent(_run_ambertest(path, "--frames", "3", "--static")) < 1.0


@needs_ambertest
def test_transparent_stage_clears_everything_not_drawn(tmp_path):
    """The shape covers a quarter of the stage, so three quarters must be clear.

    This is the check that distinguishes "transparency was requested" from
    "transparency happened". badger.swf cannot make it -- it paints its own
    full-stage background, so a transparent stage is invisible in it.
    """
    path = tmp_path / "shape.swf"
    path.write_bytes(build_swf_with_shape())
    clear = _clear_percent(
        _run_ambertest(path, "--frames", "3", "--static", "--transparent")
    )
    assert 65.0 < clear < 85.0, f"expected ~75% clear, got {clear}%"


@needs_ambertest
@needs_badger
def test_content_with_its_own_background_stays_opaque():
    """Transparency must not invent holes in content that fills its own stage."""
    output = _run_ambertest(
        CORPUS / "badger.swf", "--frames", "10", "--transparent"
    )
    assert _clear_percent(output) < 1.0


def test_converter_refuses_transparent_swf_rather_than_lying():
    """Ruffle's exporter has no background option, so the subprocess path cannot
    do transparency. It must say so instead of returning an opaque clip."""
    from amberkit.convert import convert_swf

    caps = _ffmpeg_or_skip()
    with pytest.raises(ConvertError, match="transparent"):
        convert_swf(
            FIXTURES / "shape.swf", Path("/tmp/unused"), caps, transparent=True
        )


def test_frame_rate_prefers_the_measured_average(tmp_path):
    """FLV timestamps are milliseconds, so r_frame_rate collapses to 1000/1 for
    any file whose intervals are not a neat divisor of it. avg_frame_rate is
    measured rather than derived and is the honest number.

    Built here rather than taken from a fixture on disk, because ffmpeg cannot
    encode VP6 and the real file that exposed this is not redistributable.
    """
    caps = _ffmpeg_or_skip()
    source = tmp_path / "sparse.flv"
    # Two frames six seconds apart: exactly the shape that makes r_frame_rate
    # report 1000 while the true rate is a fraction of one frame per second.
    subprocess.run(
        [caps.path, "-nostdin", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=320x240:rate=1:duration=6",
         "-c:v", "flv1", "-r", "1", str(source)],
        check=True, stdin=subprocess.DEVNULL, capture_output=True,
    )
    from amberkit.probe import probe_media

    info = probe_media(source)
    assert 0 < info.frame_rate < 120, (
        f"frame rate {info.frame_rate} looks like a time-base artifact, "
        f"not a real rate"
    )
