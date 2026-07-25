"""Tests for leftover-partial cleanup and the one-shot download retry.

Covers the temp-path registry in backend.downloader, the stale-partial sweep
in backend.scheduler, and the retry-once behavior in queue_manager. No network
or yt-dlp involved. Run with:

    py -3 -m pytest backend/tests/test_partial_cleanup.py
"""
import asyncio
import os
import time

from backend import downloader
from backend.downloader import (
    _register_temp, cleanup_partials, discard_temp_entries,
)


def _touch(path, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


# ── cleanup_partials ──────────────────────────────────────────────────────────

def test_cleanup_stem_removes_matching_files_only(tmp_path):
    stem = tmp_path / "_tmp_abc123"
    _touch(tmp_path / "_tmp_abc123.webm.part")
    _touch(tmp_path / "_tmp_abc123.webm.ytdl")
    keeper = _touch(tmp_path / "Artist - Song.mp3")

    _register_temp("dl1", "stem", stem)
    assert cleanup_partials("dl1") == 2
    assert not list(tmp_path.glob("_tmp_*"))
    assert keeper.exists()


def test_cleanup_dir_removes_whole_tree(tmp_path):
    concat_dir = tmp_path / "_concat_xyz"
    _touch(concat_dir / "track_000.mp3.part")

    _register_temp("dl2", "dir", concat_dir)
    assert cleanup_partials("dl2") == 1
    assert not concat_dir.exists()


def test_cleanup_partials_kind_keeps_finished_tracks(tmp_path):
    album = tmp_path / "Artist - Album"
    partial = _touch(album / "003 - Song.webm.part")
    ytdl = _touch(album / "003 - Song.webm.ytdl")
    finished = _touch(album / "001 - Song.mp3")

    _register_temp("dl3", "partials", album)
    assert cleanup_partials("dl3") == 2
    assert not partial.exists() and not ytdl.exists()
    assert finished.exists()


def test_cleanup_pops_registry_and_tolerates_unknown_ids(tmp_path):
    stem = tmp_path / "_tmp_once"
    _touch(tmp_path / "_tmp_once.part")
    _register_temp("dl4", "stem", stem)
    assert cleanup_partials("dl4") == 1
    assert cleanup_partials("dl4") == 0  # registry entry consumed
    assert cleanup_partials("never-registered") == 0


def test_discard_forgets_without_deleting(tmp_path):
    stem = tmp_path / "_tmp_keep"
    kept = _touch(tmp_path / "_tmp_keep.part")
    _register_temp("dl5", "stem", stem)
    discard_temp_entries("dl5")
    assert kept.exists()
    assert cleanup_partials("dl5") == 0


def test_register_ignores_missing_download_id():
    before = dict(downloader._temp_registry)
    _register_temp(None, "stem", "/nowhere")
    assert downloader._temp_registry == before


# ── scheduler stale-partial sweep ─────────────────────────────────────────────

def test_stale_sweep(tmp_path, monkeypatch):
    from backend import scheduler

    monkeypatch.setattr(scheduler.settings, "UPLOAD_FOLDER", str(tmp_path))
    old = time.time() - 7200  # 2h ago, past the 1h threshold

    stale_tmp = _touch(tmp_path / "_tmp_old.webm.part", mtime=old)
    fresh_tmp = _touch(tmp_path / "_tmp_new.webm.part")
    stale_concat = tmp_path / "_concat_old"
    _touch(stale_concat / "track_000.mp3", mtime=old)
    os.utime(stale_concat, (old, old))
    album = tmp_path / "Artist - Album"
    stale_album_part = _touch(album / "002 - Song.webm.part", mtime=old)
    finished_track = _touch(album / "001 - Song.mp3", mtime=old)
    keeper = _touch(tmp_path / "Artist - Song.mp3", mtime=old)

    asyncio.run(scheduler.cleanup_stale_partials())

    assert not stale_tmp.exists()
    assert not stale_concat.exists()
    assert not stale_album_part.exists()
    assert fresh_tmp.exists()          # too young — could be in flight
    assert finished_track.exists()     # completed media is never swept
    assert keeper.exists()


# ── queue_manager retry-once ──────────────────────────────────────────────────

def test_failed_download_cleans_up_and_retries_once(monkeypatch):
    from backend import queue_manager as qm

    calls = {"attempts": 0, "cleanups": 0}

    async def fake_single(*args, **kwargs):
        calls["attempts"] += 1
        if calls["attempts"] == 1:
            raise RuntimeError("HTTP Error 416: Requested Range Not Satisfiable")
        return {"file_path": None, "file_size": 0}

    monkeypatch.setattr(qm, "download_single", fake_single)
    monkeypatch.setattr(qm, "update_download_status", lambda *a, **k: None)
    monkeypatch.setattr(qm, "_add_to_history", lambda *a, **k: None)
    monkeypatch.setattr(
        qm, "cleanup_partials",
        lambda _id: calls.__setitem__("cleanups", calls["cleanups"] + 1),
    )
    monkeypatch.setattr(qm, "discard_temp_entries", lambda _id: None)
    monkeypatch.setattr(qm, "_dl_semaphore", None)  # semaphore binds to the loop

    params = {
        "url": "https://example.com/x",
        "download_type": "audio",
        "format": "mp3",
        "metadata": {"title": "t", "artist": "a"},
    }
    asyncio.run(qm.process_download("retry-test-id", params))

    assert calls["attempts"] == 2
    assert calls["cleanups"] == 1
    assert qm.download_states["retry-test-id"]["status"] == "completed"
    qm.download_states.pop("retry-test-id", None)


def test_permanent_errors_are_not_retried():
    from backend.queue_manager import _is_permanent_error

    assert _is_permanent_error(RuntimeError(
        "Uploaded cover image not found (id=abc). Re-upload and try again."
    ))
    assert _is_permanent_error(RuntimeError("ERROR: Video unavailable"))
    assert _is_permanent_error(RuntimeError("ERROR: Unsupported URL: foo"))
    # Transient failures must stay retryable
    assert not _is_permanent_error(RuntimeError(
        "unable to download video data: HTTP Error 416: Requested Range Not Satisfiable"
    ))
    assert not _is_permanent_error(RuntimeError("Connection reset by peer"))
    assert not _is_permanent_error(RuntimeError("Concatenation failed"))


def test_permanent_failure_skips_retry(monkeypatch):
    from backend import queue_manager as qm

    calls = {"attempts": 0}

    async def cover_missing(*args, **kwargs):
        calls["attempts"] += 1
        raise RuntimeError(
            "Cover image not found. yt-dlp may not have downloaded the thumbnail."
        )

    monkeypatch.setattr(qm, "download_single", cover_missing)
    monkeypatch.setattr(qm, "update_download_status", lambda *a, **k: None)
    monkeypatch.setattr(qm, "cleanup_partials", lambda _id: 0)
    monkeypatch.setattr(qm, "discard_temp_entries", lambda _id: None)
    monkeypatch.setattr(qm, "_dl_semaphore", None)

    params = {
        "url": "https://example.com/x",
        "download_type": "cover_audio",
        "format": "mp3",
        "metadata": {"title": "t"},
    }
    asyncio.run(qm.process_download("permanent-test-id", params))

    assert calls["attempts"] == 1  # no pointless re-download
    assert qm.download_states["permanent-test-id"]["status"] == "error"
    qm.download_states.pop("permanent-test-id", None)


def test_second_failure_reports_error(monkeypatch):
    from backend import queue_manager as qm

    calls = {"attempts": 0, "cleanups": 0}

    async def always_fails(*args, **kwargs):
        calls["attempts"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(qm, "download_single", always_fails)
    monkeypatch.setattr(qm, "update_download_status", lambda *a, **k: None)
    monkeypatch.setattr(
        qm, "cleanup_partials",
        lambda _id: calls.__setitem__("cleanups", calls["cleanups"] + 1),
    )
    monkeypatch.setattr(qm, "discard_temp_entries", lambda _id: None)
    monkeypatch.setattr(qm, "_dl_semaphore", None)

    params = {
        "url": "https://example.com/x",
        "download_type": "audio",
        "format": "mp3",
        "metadata": {"title": "t"},
    }
    asyncio.run(qm.process_download("fail-test-id", params))

    assert calls["attempts"] == 2
    assert calls["cleanups"] == 2  # once between attempts, once on final error
    assert qm.download_states["fail-test-id"]["status"] == "error"
    qm.download_states.pop("fail-test-id", None)
