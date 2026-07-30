"""Tests for reclaiming discography albums after their zip is collected.

Once the user holds the combined archive the server copies are redundant, so
they go on a short timer of their own rather than the normal retention window.
Run with:

    py -3 -m pytest backend/tests/test_collected_cleanup.py
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _stamp(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime(
        '%Y-%m-%d %H:%M:%S'
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    from backend import db_models, scheduler

    monkeypatch.setattr(db_models.settings, "DATABASE_PATH", str(tmp_path / "m.db"))
    monkeypatch.setattr(scheduler.settings, "BATCH_DELETE_MINUTES", 3)
    monkeypatch.setattr(scheduler.settings, "BATCH_HOLD_HOURS", 3)
    db_models.init_db()

    downloads = tmp_path / "downloads"
    downloads.mkdir()

    def add(batch_id, name, collected_minutes_ago=None, as_dir=True,
            status='completed'):
        if as_dir:
            target = downloads / name
            target.mkdir()
            (target / "001 - Song.mp3").write_bytes(b"audio")
        else:
            target = downloads / f"{name}.mp3"
            target.write_bytes(b"audio")

        did = str(uuid.uuid4())
        db = db_models.get_db()
        try:
            db.execute("""
                INSERT INTO downloads (id, url, platform, title, artist, album,
                    status, file_path, keep_file, batch_id, collected_at,
                    created_at, completed_at)
                VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?)
            """, (did, 'u', 'bandcamp', name, 'The Artist', name, status,
                  str(target), batch_id,
                  _stamp(collected_minutes_ago) if collected_minutes_ago is not None else None,
                  _stamp(600), _stamp(500)))
            db.commit()
        finally:
            db.close()
        return did, target

    def row(did):
        db = db_models.get_db()
        try:
            r = db.execute(
                "SELECT status, keep_file FROM downloads WHERE id=?", (did,)
            ).fetchone()
            return dict(r) if r else None
        finally:
            db.close()

    return {'add': add, 'row': row, 'downloads': downloads}


def _sweep():
    from backend import scheduler
    asyncio.run(scheduler.cleanup_collected_batches())


# ── the short timer ───────────────────────────────────────────────────────────

def test_collected_albums_are_removed_after_the_delay(env):
    b = str(uuid.uuid4())
    did, folder = env['add'](b, "First", collected_minutes_ago=5)

    _sweep()
    assert not folder.exists()
    assert env['row'](did)['status'] == 'cleaned'
    assert env['row'](did)['keep_file'] == 0


def test_freshly_collected_albums_are_left_alone(env):
    b = str(uuid.uuid4())
    did, folder = env['add'](b, "First", collected_minutes_ago=1)

    _sweep()
    assert folder.exists()                       # still inside the grace period
    assert env['row'](did)['status'] == 'completed'


def test_uncollected_batches_are_never_touched(env):
    b = str(uuid.uuid4())
    did, folder = env['add'](b, "First", collected_minutes_ago=None)

    _sweep()
    # No zip taken yet, so the albums must survive however old they are
    assert folder.exists()
    assert env['row'](did)['keep_file'] == 1


def test_merged_album_files_are_removed_too(env):
    b = str(uuid.uuid4())
    did, path = env['add'](b, "Merged", collected_minutes_ago=5, as_dir=False)

    _sweep()
    assert not path.exists()
    assert env['row'](did)['status'] == 'cleaned'


def test_whole_batch_goes_together(env):
    b = str(uuid.uuid4())
    a1, f1 = env['add'](b, "First", collected_minutes_ago=5)
    a2, f2 = env['add'](b, "Second", collected_minutes_ago=5)

    _sweep()
    assert not f1.exists() and not f2.exists()
    assert env['row'](a1)['status'] == 'cleaned'
    assert env['row'](a2)['status'] == 'cleaned'


def test_a_failed_album_with_no_file_is_still_marked_cleaned(env):
    b = str(uuid.uuid4())
    did, folder = env['add'](b, "Broken", collected_minutes_ago=5, status='error')

    import shutil
    shutil.rmtree(folder)                        # nothing on disk to remove

    _sweep()
    assert env['row'](did)['status'] == 'cleaned'


def test_sweeping_twice_is_harmless(env):
    b = str(uuid.uuid4())
    did, folder = env['add'](b, "First", collected_minutes_ago=5)

    _sweep()
    _sweep()
    assert not folder.exists()
    assert env['row'](did)['status'] == 'cleaned'


# ── interaction with the slow hold-release job ────────────────────────────────

def test_the_hold_release_job_ignores_collected_batches(env, monkeypatch):
    from backend import queue_manager, scheduler

    b = str(uuid.uuid4())
    did, folder = env['add'](b, "First", collected_minutes_ago=1)

    monkeypatch.setattr(queue_manager, "active_downloads", {})
    asyncio.run(scheduler.release_stale_batches())

    # Left for the short-timer job — otherwise the delay would depend on
    # whichever job happened to run first.
    assert env['row'](did)['keep_file'] == 1
    assert folder.exists()


def test_collected_batches_drop_off_the_listing(env, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import app as app_mod
    from backend.app import app

    monkeypatch.setattr(app_mod.settings, "UPLOAD_FOLDER", str(env['downloads']))
    b = str(uuid.uuid4())
    env['add'](b, "First", collected_minutes_ago=1)

    ids = [x['batch_id'] for x in
           TestClient(app).get("/api/discography/batches").json()['batches']]
    assert b not in ids
