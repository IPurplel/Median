"""Tests for the combined discography zip.

Covers the batch progress summary, the streaming archive layout (one folder
per album, failures listed rather than silently dropped), and the auto-Keep
release. Uses a temporary SQLite database — no network, no yt-dlp. Run with:

    py -3 -m pytest backend/tests/test_batch_zip.py
"""
import io
import sqlite3
import uuid
import zipfile

import pytest

BATCH = str(uuid.uuid4())


def _row(db, **kw):
    fields = {
        'id': str(uuid.uuid4()), 'url': 'https://a.bandcamp.com/album/x',
        'platform': 'bandcamp', 'title': 'Album', 'artist': 'The Artist',
        'album': 'Album', 'status': 'completed', 'file_path': '',
        'file_size': 0, 'error_message': None, 'batch_id': BATCH,
        'keep_file': 1, 'is_playlist': 1, 'is_concatenated': 0,
    }
    fields.update(kw)
    cols = ','.join(fields)
    marks = ','.join('?' * len(fields))
    db.execute(
        f"INSERT INTO downloads ({cols}, created_at) VALUES ({marks}, datetime('now'))",
        tuple(fields.values()),
    )
    return fields['id']


@pytest.fixture
def batch_env(tmp_path, monkeypatch):
    """A download folder with two finished albums and one failure, plus a DB."""
    from backend import app as app_mod
    from backend import db_models

    downloads = tmp_path / "downloads"
    downloads.mkdir()

    first = downloads / "The Artist - First"
    first.mkdir()
    (first / "001 - The Artist - Song One.mp3").write_bytes(b"one")
    (first / "002 - The Artist - Song Two.mp3").write_bytes(b"two")
    # Hidden files never belong in the archive
    (first / ".hidden").write_bytes(b"x")

    merged = downloads / "The Artist - Second.mp3"
    merged.write_bytes(b"merged")

    db_path = tmp_path / "median.db"
    monkeypatch.setattr(db_models.settings, "DATABASE_PATH", str(db_path))
    monkeypatch.setattr(app_mod.settings, "UPLOAD_FOLDER", str(downloads))
    db_models.init_db()

    db = db_models.get_db()
    try:
        ids = {
            'first': _row(db, album='First', file_path=str(first), file_size=6),
            'merged': _row(db, album='Second', file_path=str(merged),
                           file_size=6, is_concatenated=1),
            'failed': _row(db, album='Third', status='error', file_path='',
                           error_message='Video unavailable'),
        }
        db.commit()
    finally:
        db.close()
    return ids


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.app import app
    return TestClient(app)


# ── batch summary ─────────────────────────────────────────────────────────────

def test_batch_summary_counts_each_outcome(client, batch_env):
    r = client.get(f"/api/discography/batch/{BATCH}")
    assert r.status_code == 200
    b = r.json()

    assert b['total'] == 3
    assert b['completed'] == 2
    assert b['failed'] == 1
    assert b['in_progress'] == 0
    assert b['all_done'] is True
    assert b['artist'] == 'The Artist'
    assert b['total_size'] == 12


def test_batch_not_done_while_an_album_runs(client, batch_env):
    from backend import db_models
    db = db_models.get_db()
    try:
        db.execute("UPDATE downloads SET status='downloading' WHERE album='Third'")
        db.commit()
    finally:
        db.close()

    b = client.get(f"/api/discography/batch/{BATCH}").json()
    assert b['all_done'] is False
    assert b['in_progress'] == 1


def test_unknown_and_malformed_batch_ids(client, batch_env):
    assert client.get(f"/api/discography/batch/{uuid.uuid4()}").status_code == 404
    assert client.get("/api/discography/batch/not-a-uuid").status_code == 400


# ── the archive itself ────────────────────────────────────────────────────────

def _open_zip(resp):
    return zipfile.ZipFile(io.BytesIO(resp.content))


def test_zip_gives_each_album_its_own_folder(client, batch_env):
    r = client.get(f"/api/discography/batch/{BATCH}/file")
    assert r.status_code == 200
    assert r.headers['content-type'] == 'application/zip'
    # Named after the band alone — the folders inside carry the album names
    assert 'filename="The Artist.zip"' in r.headers['content-disposition']

    names = _open_zip(r).namelist()
    assert "First/Song One.mp3" in names
    assert "First/Song Two.mp3" in names
    # A merged album is one file, but still gets a folder so everything
    # unzips the same way
    assert "Second/Second.mp3" in names
    assert not any(n.endswith('.hidden') for n in names)


def test_zip_is_readable_and_lists_failed_albums(client, batch_env):
    zf = _open_zip(client.get(f"/api/discography/batch/{BATCH}/file"))
    assert zf.testzip() is None          # CRCs check out
    assert zf.read("First/Song One.mp3") == b"one"

    note = zf.read("MISSING ALBUMS.txt").decode()
    assert "Third" in note and "Video unavailable" in note


def test_no_missing_note_when_everything_succeeded(client, batch_env):
    from backend import db_models
    db = db_models.get_db()
    try:
        db.execute("DELETE FROM downloads WHERE album='Third'")
        db.commit()
    finally:
        db.close()

    names = _open_zip(client.get(f"/api/discography/batch/{BATCH}/file")).namelist()
    assert not any('MISSING' in n for n in names)


def test_fetching_the_zip_stamps_the_batch_as_collected(client, batch_env):
    from backend import db_models

    db = db_models.get_db()
    try:
        assert db.execute(
            "SELECT COUNT(*) c FROM downloads WHERE batch_id=? AND collected_at IS NOT NULL",
            (BATCH,)).fetchone()['c'] == 0
    finally:
        db.close()

    client.get(f"/api/discography/batch/{BATCH}/file")

    db = db_models.get_db()
    try:
        row = db.execute(
            "SELECT COUNT(*) c, SUM(keep_file) k FROM downloads "
            "WHERE batch_id=? AND collected_at IS NOT NULL", (BATCH,)).fetchone()
        # Every album is stamped, and the keep flag deliberately stays set so
        # the short-timer sweep owns them rather than the normal retention job.
        assert row['c'] == 3
        assert row['k'] == 3
    finally:
        db.close()


def test_zip_refused_when_nothing_completed(client, batch_env):
    from backend import db_models
    db = db_models.get_db()
    try:
        db.execute("UPDATE downloads SET status='error' WHERE batch_id=?", (BATCH,))
        db.commit()
    finally:
        db.close()

    assert client.get(f"/api/discography/batch/{BATCH}/file").status_code == 404


def test_zip_reports_cleaned_up_files(client, batch_env, tmp_path):
    import shutil
    from backend import db_models

    db = db_models.get_db()
    try:
        db.execute("DELETE FROM downloads WHERE album='Second'")
        db.commit()
    finally:
        db.close()
    shutil.rmtree(tmp_path / "downloads" / "The Artist - First")

    r = client.get(f"/api/discography/batch/{BATCH}/file")
    assert r.status_code == 410


def test_files_outside_the_download_folder_are_refused(client, batch_env, tmp_path):
    from backend import db_models

    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"nope")
    db = db_models.get_db()
    try:
        db.execute("UPDATE downloads SET file_path=? WHERE album='First'",
                   (str(outside),))
        db.execute("DELETE FROM downloads WHERE album='Second'")
        db.commit()
    finally:
        db.close()

    # Nothing left that is safe to serve
    assert client.get(f"/api/discography/batch/{BATCH}/file").status_code == 410
