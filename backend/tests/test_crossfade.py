"""Unit tests for the pure crossfade filter/feasibility/chapter helpers.

These cover only the pure logic (filtergraph strings, offset math, feasibility,
encoder selection, chapter-offset correction) — no ffmpeg is invoked. Run with:

    py -3 -m pytest backend/tests/test_crossfade.py
    # or, without pytest:
    py -3 backend/tests/test_crossfade.py
"""
import os
import tempfile

from backend.concatenation_engine import (
    _crossfade_feasible,
    _audio_encoder_args,
    _build_acrossfade_filter,
    _build_xfade_filter,
)
from backend.ffmpeg_chapters_handler import generate_ffmpeg_metadata


# ── _crossfade_feasible ────────────────────────────────────────────────────────

def test_feasible_normal():
    assert _crossfade_feasible([10, 10, 10], 2.0, 50) == 2.0


def test_feasible_clamped_to_shortest():
    # shortest track 1.5s → clamp to 0.9 * 1.5 = 1.35
    assert _crossfade_feasible([1.5, 10, 10], 2.0, 50) == 1.35


def test_feasible_too_few_tracks():
    assert _crossfade_feasible([10], 2.0, 50) is None


def test_feasible_too_many_tracks():
    assert _crossfade_feasible([10] * 51, 2.0, 50) is None


def test_feasible_zero_or_missing_duration():
    assert _crossfade_feasible([10, 0, 10], 2.0, 50) is None
    assert _crossfade_feasible([10, None, 10], 2.0, 50) is None


def test_feasible_tracks_too_short():
    # 0.9 * 0.05 = 0.045 < 0.1 minimum → not feasible
    assert _crossfade_feasible([0.05, 0.05], 2.0, 50) is None


# ── _audio_encoder_args ────────────────────────────────────────────────────────

def test_encoder_mp3_with_bareword_bitrate():
    assert _audio_encoder_args('.mp3', '320') == ['-c:a', 'libmp3lame', '-b:a', '320k']


def test_encoder_flac_ignores_bitrate():
    assert _audio_encoder_args('flac', '320') == ['-c:a', 'flac']


def test_encoder_aac_keeps_k_suffix():
    assert _audio_encoder_args('.m4a', '256k') == ['-c:a', 'aac', '-b:a', '256k']


def test_encoder_webm_uses_opus():
    assert _audio_encoder_args('.webm', '') == ['-c:a', 'libopus', '-b:a', '192k']


# ── _build_acrossfade_filter ───────────────────────────────────────────────────

def test_acrossfade_two_inputs():
    filt, out = _build_acrossfade_filter(2, 2.0, 'qsin', 44100)
    assert out == 'aout'
    assert '[0:a]aformat=sample_rates=44100:channel_layouts=stereo[a0]' in filt
    assert '[1:a]aformat=sample_rates=44100:channel_layouts=stereo[a1]' in filt
    assert '[a0][a1]acrossfade=d=2.0:c1=qsin:c2=qsin[aout]' in filt


def test_acrossfade_three_inputs_chains_intermediate():
    filt, out = _build_acrossfade_filter(3, 2.0, 'qsin', 44100)
    assert '[a0][a1]acrossfade=d=2.0:c1=qsin:c2=qsin[ax1]' in filt
    assert '[ax1][a2]acrossfade=d=2.0:c1=qsin:c2=qsin[aout]' in filt


# ── _build_xfade_filter (offset math) ──────────────────────────────────────────

def test_xfade_offsets_follow_formula():
    # offset_k = (Σ_{j<k} d_j) − k·D ; durations [10,20,15], D=2
    filt, out = _build_xfade_filter([10, 20, 15], 2.0, 'fade', 1920, 1080, 30, False)
    assert out == 'vout'
    assert 'offset=8.000' in filt      # 10 - 2
    assert 'offset=26.000' in filt     # (10+20) - 4
    # no scaling when scale_pad is False
    assert 'scale=' not in filt
    assert 'setsar=1,fps=30,format=yuv420p' in filt


def test_xfade_scale_pad_adds_1080p_canvas():
    filt, _ = _build_xfade_filter([10, 10], 2.0, 'fade', 1920, 1080, 30, True)
    assert 'scale=1920:1080:force_original_aspect_ratio=decrease' in filt
    assert 'pad=1920:1080:(ow-iw)/2:(oh-ih)/2' in filt


def test_xfade_negative_offset_clamped_to_zero():
    filt, _ = _build_xfade_filter([1, 1], 2.0, 'fade', 1920, 1080, 30, False)
    assert 'offset=0.000' in filt      # 1 - 2 = -1 → clamped to 0


# ── chapter overlap correction ─────────────────────────────────────────────────

def _parse_chapters(meta_path):
    """Return list of (start_ms, end_ms) from an FFMETADATA chapter file."""
    chapters = []
    start = end = None
    with open(meta_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line == '[CHAPTER]':
                start = end = None
            elif line.startswith('START='):
                start = int(line.split('=', 1)[1])
            elif line.startswith('END='):
                end = int(line.split('=', 1)[1])
                chapters.append((start, end))
    return chapters


def test_chapters_no_overlap_are_contiguous_cumulative():
    tracks = [{'title': f't{i}', 'duration': 10} for i in range(3)]
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, 'album.mp3')
        meta = generate_ffmpeg_metadata(tracks, out, overlap_seconds=0.0)
        assert _parse_chapters(meta) == [(0, 10000), (10000, 20000), (20000, 30000)]


def test_chapters_with_overlap_shift_left():
    # 3×10s tracks, 2s crossfade → starts 0, 8, 16; total 30 - 2*2 = 26
    tracks = [{'title': f't{i}', 'duration': 10} for i in range(3)]
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, 'album.mp3')
        meta = generate_ffmpeg_metadata(tracks, out, overlap_seconds=2.0)
        assert _parse_chapters(meta) == [(0, 8000), (8000, 16000), (16000, 26000)]


if __name__ == '__main__':
    import sys
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith('test_') and callable(v)]
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(funcs) - failed}/{len(funcs)} passed")
    sys.exit(1 if failed else 0)
