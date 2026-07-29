"""Tests for the discography batch hold and its automatic release.

Batch albums are exempt from auto-cleanup until the combined zip is collected;
this covers the timer that releases an uncollected batch anyway, and the
listing that lets the UI find batches again after a page reload. Run with:

    py -3 -m pytest backend/tests/test_batch_hold.py
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _stamp(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        '%Y-%m-%d %H:%M:%S'
    )


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    from backend import db_models
    from backend import scheduler

    monkeypatch.setattr(db_models.settings, "DATABASE_PATH", str(tmp_path / "m.db"))
    monkeypatch.setattr(scheduler.settings, "BATCH_HOLD_HOURS", 3)
    db_models.init_db()

    def add(batch_id, status='completed', keep=1, completed_hours_ago=None,
            created_hours_ago=5, download_id=None):
        did = download_id or str(uuid.uuid4())
        db = db_models.get_db()
        try:
            db.execute("""
                INSERT INTO downloads
                (id, url, platform, title, artist, album, status, keep_file,
                 batch_id, file_size, created_at, completed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (did, 'https://a.bandcamp.com/x', 'bandcamp', 'T', 'The Artist',
                  'Album', status, keep, batch_id, 100,
                  _stamp(created_hours_ago),
                  _stamp(completed_hours_ago) if completed_hours_ago is not None else None))
            db.commit()
        finally:
            db.close()
        return did

    def held(batch_id):
        db = db_models.get_db()
        try:
            return db.execute(
                "SELECT COUNT(*) c FROM downloads WHERE batch_id=? AND keep_file=1",
                (batch_id,)).fetchone()['c']
        finally:
            db.close()

    return {'add': add, 'held': held}


def _run_release(monkeypatch, active=None):
    from backend import queue_manager, scheduler
    monkeypatch.setattr(queue_manager, "active_downloads", active or {})
    asyncio.run(scheduler.release_stale_batches())


# ── the release timer ─────────────────────────────────────────────────────────

def test_uncollected_batch_is_released_after_the_hold(db_env, monkeypatch):
    b = str(uuid.uuid4())
    db_env['add'](b, completed_hours_ago=4)
    db_env['add'](b, completed_hours_ago=5)

    _run_release(monkeypatch)
    assert db_env['held'](b) == 0        # normal cleanup applies again


def test_recently_finished_batch_keeps_its_hold(db_env, monkeypatch):
    b = str(uuid.uuid4())
    db_env['add'](b, completed_hours_ago=1)

    _run_release(monkeypatch)
    assert db_env['held'](b) == 1


def test_age_counts_from_the_last_album_not_the_first(db_env, monkeypatch):
    # The whole point: a long run whose earliest albums finished hours ago must
    # keep its hold while later albums are still landing.
    b = str(uuid.uuid4())
    db_env['add'](b, completed_hours_ago=9)   # first album, long done
    db_env['add'](b, completed_hours_ago=1)   # most recent album

    _run_release(monkeypatch)
    assert db_env['held'](b) == 2


def test_a_running_batch_is_never_released(db_env, monkeypatch):
    b = str(uuid.uuid4())
    done = db_env['add'](b, completed_hours_ago=9)
    running = db_env['add'](b, status='downloading', completed_hours_ago=None,
                            created_hours_ago=9)

    # Still in the live task map — the hold must survive regardless of age
    _run_release(monkeypatch, active={running: object()})
    assert db_env['held'](b) == 2
    assert done  # referenced for clarity


def test_batch_stranded_by_a_restart_is_released(db_env, monkeypatch):
    # A container restart leaves rows stuck on 'downloading' forever. Nothing is
    # actually running, so the batch must not hold its files for good.
    b = str(uuid.uuid4())
    db_env['add'](b, status='downloading', completed_hours_ago=None,
                  created_hours_ago=8)

    _run_release(monkeypatch, active={})
    assert db_env['held'](b) == 0


def test_already_released_batches_are_left_alone(db_env, monkeypatch):
    b = str(uuid.uuid4())
    db_env['add'](b, keep=0, completed_hours_ago=9)
    _run_release(monkeypatch)
    assert db_env['held'](b) == 0


def test_non_batch_downloads_are_untouched(db_env, monkeypatch):
    from backend import db_models
    db_env['add'](None, keep=1, completed_hours_ago=9)

    _run_release(monkeypatch)
    db = db_models.get_db()
    try:
        # A file the user pressed Keep on stays kept — this job only ever
        # touches discography batches.
        assert db.execute(
            "SELECT COUNT(*) c FROM downloads WHERE batch_id IS NULL AND keep_file=1"
        ).fetchone()['c'] == 1
    finally:
        db.close()


# ── the listing that survives a page reload ───────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.app import app
    return TestClient(app)


def test_listing_shows_running_and_uncollected_batches(client, db_env):
    running = str(uuid.uuid4())
    db_env['add'](running, status='downloading', keep=1, completed_hours_ago=None)
    uncollected = str(uuid.uuid4())
    db_env['add'](uncollected, keep=1, completed_hours_ago=1)

    found = {b['batch_id']: b for b in client.get("/api/discography/batches").json()['batches']}
    assert running in found and uncollected in found
    assert found[running]['all_done'] is False
    assert found[uncollected]['all_done'] is True


def test_collected_batches_drop_off_the_listing(client, db_env):
    # keep_file cleared = the zip was already fetched; nothing left to offer
    collected = str(uuid.uuid4())
    db_env['add'](collected, keep=0, completed_hours_ago=1)

    ids = [b['batch_id'] for b in client.get("/api/discography/batches").json()['batches']]
    assert collected not in ids


def test_listing_counts_failures(client, db_env):
    b = str(uuid.uuid4())
    db_env['add'](b, status='completed', keep=1, completed_hours_ago=1)
    db_env['add'](b, status='error', keep=1, completed_hours_ago=1)

    entry = [x for x in client.get("/api/discography/batches").json()['batches']
             if x['batch_id'] == b][0]
    assert entry['total'] == 2
    assert entry['completed'] == 1
    assert entry['failed'] == 1
    assert entry['all_done'] is True
