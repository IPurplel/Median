"""Tests for serving single-file downloads without a pointless zip wrapper.

A lone track (or a merged album, which is one file) is handed over as-is;
anything with more than one file to carry still gets an archive. Uses a
temporary SQLite database — no network. Run with:

    py -3 -m pytest backend/tests/test_single_file_download.py
"""
import io
import uuid
import zipfile

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    from backend import app as app_mod
    from backend import db_models

    downloads = tmp_path / "downloads"
    downloads.mkdir()

    single = downloads / "The Artist - One Song.mp3"
    single.write_bytes(b"ID3single-audio")

    album = downloads / "The Artist - An Album"
    album.mkdir()
    (album / "001 - The Artist - First.mp3").write_bytes(b"first")
    (album / "002 - The Artist - Second.mp3").write_bytes(b"second")

    db_path = tmp_path / "median.db"
    monkeypatch.setattr(db_models.settings, "DATABASE_PATH", str(db_path))
    monkeypatch.setattr(app_mod.settings, "UPLOAD_FOLDER", str(downloads))
    db_models.init_db()

    def add(**kw):
        fields = {
            'id': str(uuid.uuid4()), 'url': 'https://a.bandcamp.com/x',
            'platform': 'bandcamp', 'title': 'One Song', 'artist': 'The Artist',
            'album': 'An Album', 'status': 'completed', 'is_playlist': 0,
            'is_concatenated': 0, 'include_description': 0, 'file_path': '',
        }
        fields.update(kw)
        cols = ','.join(fields)
        marks = ','.join('?' * len(fields))
        db = db_models.get_db()
        try:
            db.execute(
                f"INSERT INTO downloads ({cols}, created_at) "
                f"VALUES ({marks}, datetime('now'))", tuple(fields.values()))
            db.commit()
        finally:
            db.close()
        return fields['id']

    return {'add': add, 'single': single, 'album': album}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.app import app
    return TestClient(app)


def _filename(resp) -> str:
    """The name the browser will save as.

    Starlette emits the RFC 5987 extended form (filename*=utf-8''...) for
    anything that isn't a bare token, so the header needs decoding before it
    can be compared.
    """
    from urllib.parse import unquote
    return unquote(resp.headers['content-disposition'])


def test_single_track_is_served_as_plain_audio(client, env):
    did = env['add'](file_path=str(env['single']))
    r = client.get(f"/api/download/{did}/file")

    assert r.status_code == 200
    assert r.content == b"ID3single-audio"          # the file itself, not an archive
    assert '.zip' not in _filename(r)
    assert 'The Artist - One Song.mp3' in _filename(r)


def test_merged_album_is_served_as_plain_audio(client, env):
    # A merged album is a single file too — named after the album
    did = env['add'](file_path=str(env['single']), is_playlist=1,
                     is_concatenated=1, album='An Album')
    r = client.get(f"/api/download/{did}/file")

    assert r.status_code == 200
    assert 'The Artist - An Album.mp3' in _filename(r)
    assert '.zip' not in _filename(r)


def test_separate_track_album_still_gets_a_zip(client, env):
    did = env['add'](file_path=str(env['album']), is_playlist=1)
    r = client.get(f"/api/download/{did}/file")

    assert r.status_code == 200
    assert '.zip' in r.headers['content-disposition']
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert sorted(names) == ['First.mp3', 'Second.mp3']


def test_single_file_with_a_description_still_gets_a_zip(client, env):
    # Two files to carry, so the archive earns its place
    did = env['add'](file_path=str(env['single']), include_description=1)
    r = client.get(f"/api/download/{did}/file")

    assert r.status_code == 200
    assert '.zip' in r.headers['content-disposition']
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert 'description.md' in names
    assert 'One Song.mp3' in names


def test_missing_file_still_reports_cleanup(client, env, tmp_path):
    did = env['add'](file_path=str(tmp_path / "downloads" / "gone.mp3"))
    assert client.get(f"/api/download/{did}/file").status_code == 410


def test_path_outside_the_download_folder_is_refused(client, env, tmp_path):
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"nope")
    did = env['add'](file_path=str(outside))
    assert client.get(f"/api/download/{did}/file").status_code == 403
