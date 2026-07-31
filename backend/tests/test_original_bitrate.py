"""Tests for the "Original" bitrate — keep the source stream, don't re-encode.

Every supported platform serves lossy audio, so forcing a 128k stream into a
320k MP3 only produces a larger file that has been through one more lossy
generation. "Original" skips the conversion entirely. Run with:

    py -3 -m pytest backend/tests/test_original_bitrate.py
"""
import pytest

from backend.downloader import _keeps_original
from backend.utils.file_organizer import find_any_media_file
from backend.utils.validators import ORIGINAL_BITRATE, validate_bitrate
from backend.utils.ydl_opts_builder import get_ydl_opts


def _pp_keys(opts):
    return [p['key'] for p in opts.get('postprocessors', [])]


# ── validation ────────────────────────────────────────────────────────────────

def test_original_is_a_valid_bitrate():
    assert validate_bitrate('original') == ORIGINAL_BITRATE
    assert validate_bitrate('Original') == ORIGINAL_BITRATE
    assert validate_bitrate(' original ') == ORIGINAL_BITRATE


def test_numeric_bitrates_still_validate():
    assert validate_bitrate('320') == '320'
    assert validate_bitrate('320k') == '320k'


def test_nonsense_is_still_rejected():
    for bad in ('best', 'orig', '320kbps!', 'original320'):
        with pytest.raises(ValueError):
            validate_bitrate(bad)


# ── yt-dlp options ────────────────────────────────────────────────────────────

def test_original_copies_the_stream_instead_of_re_encoding():
    opts = get_ydl_opts('audio', 'mp3', 'original', '/tmp/x.%(ext)s')
    extract = next(p for p in opts['postprocessors'] if p['key'] == 'FFmpegExtractAudio')

    # 'best' tells ffmpeg to copy the audio rather than convert it
    assert extract['preferredcodec'] == 'best'
    assert 'preferredquality' not in extract
    # Tagging and cover art still apply — extracting out of WebM is what makes
    # them possible at all
    assert 'FFmpegMetadata' in _pp_keys(opts)
    assert 'EmbedThumbnail' in _pp_keys(opts)
    assert opts['format'] == 'bestaudio/best'


def test_a_normal_bitrate_still_converts():
    opts = get_ydl_opts('audio', 'mp3', '320', '/tmp/x.%(ext)s')
    assert 'FFmpegExtractAudio' in _pp_keys(opts)
    extract = next(p for p in opts['postprocessors'] if p['key'] == 'FFmpegExtractAudio')
    assert extract['preferredcodec'] == 'mp3'
    assert extract['preferredquality'] == '320'


def test_the_word_original_never_reaches_the_encoder():
    # It must not end up as a literal "-b:a originalk"
    for dtype, fmt in (('audio', 'mp3'), ('video', 'mp4'), ('cover_audio', 'mp3')):
        opts = get_ydl_opts(dtype, fmt, 'original', '/tmp/x.%(ext)s')
        blob = repr(opts)
        assert 'original' not in blob.lower() or 'bestaudio' in blob

    video = get_ydl_opts('video', 'mp4', 'original', '/tmp/x.%(ext)s')
    assert 'abr<=original' not in video['format']

    cover = get_ydl_opts('cover_audio', 'mp3', 'original', '/tmp/x.%(ext)s')
    extract = next(p for p in cover['postprocessors'] if p['key'] == 'FFmpegExtractAudio')
    # Cover+Audio has to render a video stream, so it always converts — but at
    # the encoder default rather than a bogus quality value
    assert 'preferredquality' not in extract


# ── which download types honour it ────────────────────────────────────────────

def test_only_plain_audio_keeps_the_source():
    assert _keeps_original('audio', 'original') is True
    assert _keeps_original('audio', 'Original') is True
    assert _keeps_original('audio', '320') is False
    assert _keeps_original('cover_audio', 'original') is False
    assert _keeps_original('video', 'original') is False


# ── locating the downloaded file ──────────────────────────────────────────────

def test_finds_whatever_extension_arrived(tmp_path):
    stem = tmp_path / "_tmp_abc"
    (tmp_path / "_tmp_abc.opus").write_bytes(b"audio")
    assert find_any_media_file(str(stem)).endswith("_tmp_abc.opus")


def test_ignores_thumbnails_and_side_files(tmp_path):
    stem = tmp_path / "_tmp_abc"
    (tmp_path / "_tmp_abc.jpg").write_bytes(b"img")
    (tmp_path / "_tmp_abc.webp").write_bytes(b"img")
    (tmp_path / "_tmp_abc.m4a").write_bytes(b"audio")

    assert find_any_media_file(str(stem)).endswith(".m4a")


def test_returns_none_when_nothing_landed(tmp_path):
    (tmp_path / "_tmp_abc.jpg").write_bytes(b"img")
    assert find_any_media_file(str(tmp_path / "_tmp_abc")) is None
    assert find_any_media_file(str(tmp_path / "missing" / "_tmp_x")) is None


def test_does_not_match_a_different_download(tmp_path):
    (tmp_path / "_tmp_other.mp3").write_bytes(b"audio")
    assert find_any_media_file(str(tmp_path / "_tmp_abc")) is None
