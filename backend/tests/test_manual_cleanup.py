"""Tests for the "clean now" button's endpoints.

An escape hatch for a full disk: delete every finished download immediately
rather than waiting out the retention window. The safety property that matters
is that an in-flight download is never touched. Run with:

    py -3 -m pytest backend/tests/test_manual_cleanup.py
"""
import uuid

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    from backend import app as app_mod, db_models, scheduler

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(db_models.settings, "DATABASE_PATH", str(tmp_path / "m.db"))
    monkeypatch.setattr(app_mod.settings, "UPLOAD_FOLDER", str(downloads))
    monkeypatch.setattr(scheduler.settings, "UPLOAD_FOLDER", str(downloads))
    db_models.init_db()

    def add(name, status='completed', as_dir=False, keep=0, size=5,
            download_id=None, outside=None):
        if outside is not None:
            target = outside
        elif as_dir:
            target = downloads / name
            target.mkdir()
            (target / "001 - Song.mp3").write_bytes(b"x" * size)
        else:
            target = downloads / f"{name}.mp3"
            target.write_bytes(b"x" * size)

        did = download_id or str(uuid.uuid4())
        db = db_models.get_db()
        try:
            db.execute("""
                INSERT INTO downloads (id, url, platform, title, artist, album,
                    status, file_path, file_size, keep_file, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?, datetime('now'))
            """, (did, 'u', 'bandcamp', name, 'A', name, status,
                  str(target), size, keep))
            db.commit()
        finally:
            db.close()
        return did, target

    def status_of(did):
        db = db_models.get_db()
        try:
            r = db.execute("SELECT status FROM downloads WHERE id=?", (did,)).fetchone()
            return r['status'] if r else None
        finally:
            db.close()

    return {'add': add, 'status_of': status_of, 'downloads': downloads}


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import app as app_mod, queue_manager
    from backend.app import app

    monkeypatch.setattr(queue_manager, "active_downloads", {})
    # The rate-limit store is module-global; clear it so a busy earlier test
    # file can't spend this one's allowance.
    app_mod._rl_store.clear()
    return TestClient(app)


# ── preview ───────────────────────────────────────────────────────────────────

def test_preview_reports_what_would_be_freed(client, env):
    env['add']("One", size=100)
    env['add']("Two", as_dir=True, size=200)

    p = client.get("/api/cleanup/preview").json()
    assert p['items'] == 2
    assert p['bytes'] == 300
    assert p['active_downloads'] == 0


def test_preview_changes_nothing(client, env):
    _, path = env['add']("One")
    client.get("/api/cleanup/preview")
    assert path.exists()


def test_preview_is_empty_when_there_is_nothing_to_clean(client, env):
    p = client.get("/api/cleanup/preview").json()
    assert p['items'] == 0
    assert p['size'] == "0 B"


# ── the purge ─────────────────────────────────────────────────────────────────

def test_finished_downloads_are_deleted_immediately(client, env):
    a, fa = env['add']("One", size=100)
    b, fb = env['add']("Two", as_dir=True, size=200)

    r = client.post("/api/cleanup/now").json()
    assert r['removed'] == 2
    assert r['freed_bytes'] == 300
    assert not fa.exists() and not fb.exists()
    assert env['status_of'](a) == 'cleaned'
    assert env['status_of'](b) == 'cleaned'


def test_kept_files_are_included(client, env):
    # The point of the button is reclaiming space, so an explicit Keep does
    # not survive it — the UI warns about exactly this before asking.
    did, path = env['add']("Kept", keep=1)

    client.post("/api/cleanup/now")
    assert not path.exists()
    assert env['status_of'](did) == 'cleaned'


def test_failed_and_cancelled_downloads_are_included(client, env):
    a, fa = env['add']("Bad", status='error')
    b, fb = env['add']("Stopped", status='cancelled')

    client.post("/api/cleanup/now")
    assert not fa.exists() and not fb.exists()


def test_a_running_download_is_never_touched(client, env, monkeypatch):
    from backend import queue_manager

    running_id = str(uuid.uuid4())
    did, path = env['add']("InFlight", status='downloading', download_id=running_id)
    other, other_path = env['add']("Done")

    monkeypatch.setattr(queue_manager, "active_downloads", {running_id: object()})
    r = client.post("/api/cleanup/now").json()

    # yt-dlp is mid-write; pulling the file would corrupt it, not free space
    assert path.exists()
    assert env['status_of'](did) == 'downloading'
    assert r['skipped_active'] == 1
    assert not other_path.exists()


def test_paths_outside_the_download_folder_are_ignored(client, env, tmp_path):
    outside = tmp_path / "not-ours.mp3"
    outside.write_bytes(b"precious")
    did, _ = env['add']("Escape", outside=outside)

    r = client.post("/api/cleanup/now").json()
    assert outside.exists()
    assert r['removed'] == 0


def test_leftover_partials_are_swept_when_nothing_is_running(client, env):
    # No active downloads, so every temp file is orphaned regardless of age
    (env['downloads'] / "_tmp_abc.webm.part").write_bytes(b"junk")
    (env['downloads'] / "_tmp_abc.webm.ytdl").write_bytes(b"junk")

    r = client.post("/api/cleanup/now").json()
    assert r['partials_removed'] == 2
    assert not list(env['downloads'].glob("_tmp_*"))


def test_fresh_partials_survive_while_a_download_runs(client, env, monkeypatch):
    from backend import queue_manager

    running = str(uuid.uuid4())
    monkeypatch.setattr(queue_manager, "active_downloads", {running: object()})
    part = env['downloads'] / "_tmp_live.webm.part"
    part.write_bytes(b"in progress")

    client.post("/api/cleanup/now")
    assert part.exists()      # belongs to the download still in flight


# ── unrecognised files ────────────────────────────────────────────────────────

def test_orphans_are_reported_but_not_deleted_by_default(client, env):
    stray = env['downloads'] / "left over from an old install.mp3"
    stray.write_bytes(b"x" * 50)

    p = client.get("/api/cleanup/preview").json()
    assert p['orphans'] == 1
    assert p['orphan_bytes'] == 50
    assert p['orphan_names'] == ["left over from an old install.mp3"]

    r = client.post("/api/cleanup/now").json()
    assert r['orphans_removed'] == 0
    assert stray.exists()          # never removed without an explicit opt-in


def test_orphans_are_deleted_on_request(client, env):
    stray = env['downloads'] / "stray.mp3"
    stray.write_bytes(b"x" * 50)
    strays = env['downloads'] / "old album"
    strays.mkdir()
    (strays / "track.mp3").write_bytes(b"x" * 70)

    r = client.post("/api/cleanup/now?include_orphans=true").json()
    assert r['orphans_removed'] == 2
    assert r['freed_bytes'] == 120
    assert not stray.exists() and not strays.exists()


def test_tracked_files_are_not_counted_as_orphans(client, env):
    _, tracked = env['add']("Tracked", size=10)
    p = client.get("/api/cleanup/preview").json()
    assert p['items'] == 1
    assert p['orphans'] == 0
    assert tracked.exists()


def test_median_caches_are_left_alone(client, env):
    cache = env['downloads'] / ".cover_cache"
    cache.mkdir()
    (cache / "thumb.jpg").write_bytes(b"img")

    p = client.get("/api/cleanup/preview").json()
    assert p['orphans'] == 0

    client.post("/api/cleanup/now?include_orphans=true")
    assert cache.exists()          # Median's own cache, not a stray download


def test_orphans_are_skipped_while_a_download_runs(client, env, monkeypatch):
    from backend import queue_manager

    # An in-flight download has no file_path recorded yet, so its half-written
    # folder is indistinguishable from a stray — don't guess, just skip.
    monkeypatch.setattr(queue_manager, "active_downloads", {str(uuid.uuid4()): object()})
    in_progress = env['downloads'] / "Artist - Album In Progress"
    in_progress.mkdir()
    (in_progress / "001.mp3.part").write_bytes(b"partial")

    r = client.post("/api/cleanup/now?include_orphans=true").json()
    assert r['orphans_removed'] == 0
    assert in_progress.exists()


def test_cleaning_twice_is_harmless(client, env):
    env['add']("One")
    first = client.post("/api/cleanup/now").json()
    second = client.post("/api/cleanup/now").json()

    assert first['removed'] == 1
    assert second['removed'] == 0
