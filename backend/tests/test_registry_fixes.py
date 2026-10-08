"""Regression tests for the bugs in BUG_REGISTRY.md.

Each test names the registry ID it guards. Nothing here touches the network
or needs ffmpeg — yt-dlp and ffmpeg are stubbed at their call sites. Run with:

    py -3 -m pytest backend/tests/test_registry_fixes.py
"""
import asyncio
import threading
from pathlib import Path

import pytest

from backend import downloader
from backend import concatenation_engine as ce


# ── CORE-003: cancellation reaches the yt-dlp worker thread ──────────────────

def test_cancel_hook_aborts_after_cancel_is_requested():
    from yt_dlp.utils import DownloadCancelled

    hook = downloader.make_cancel_hook('dl-cancel-1')
    hook({'status': 'downloading'})          # not cancelled yet: no-op
    downloader.request_cancel('dl-cancel-1')
    with pytest.raises(DownloadCancelled):
        hook({'status': 'downloading'})
    downloader._clear_cancel('dl-cancel-1')


def test_cancel_still_aborts_after_the_queue_clears_its_registry():
    """The queue's `finally` clears the flag the moment the task is cancelled,
    while the executor thread is still inside yt-dlp. The hook must keep
    seeing the cancel, or the transfer carries on after the UI said stop."""
    from yt_dlp.utils import DownloadCancelled

    calls = []
    hook = downloader.make_cancel_hook('dl-cancel-2', calls.append)
    downloader.request_cancel('dl-cancel-2')
    downloader._clear_cancel('dl-cancel-2')
    with pytest.raises(DownloadCancelled):
        hook({'status': 'downloading'})
    assert calls == []                       # inner hook never ran


# ── BE-003: same-album downloads never share a folder ────────────────────────

def test_two_downloads_of_one_album_get_separate_folders(tmp_path):
    from backend.utils.file_organizer import reserve_playlist_folder

    a = reserve_playlist_folder(tmp_path, 'Daft Punk', 'Discovery')
    b = reserve_playlist_folder(tmp_path, 'Daft Punk', 'Discovery')
    assert a != b and a.is_dir() and b.is_dir()


def test_concurrent_reservations_are_all_unique(tmp_path):
    from backend.utils.file_organizer import reserve_playlist_folder

    results, barrier = [], threading.Barrier(8)

    def grab():
        barrier.wait()
        results.append(reserve_playlist_folder(tmp_path, 'A', 'B'))

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 8


# ── BE-004: a fallback source never inherits another source's files ─────────

def test_source_switch_clears_every_leftover_under_the_stem(tmp_path):
    stem = tmp_path / '002 - Song'
    leftovers = ['002 - Song.f251.webm.part', '002 - Song.f251.webm.ytdl',
                 '002 - Song.f137.mp4', '002 - Song.webm.part']
    keep = ['002 - Song Two.mp3', '003 - Other.mp3']
    for name in leftovers + keep:
        (tmp_path / name).write_bytes(b'x')

    removed = downloader._clear_source_partials(str(stem) + '.%(ext)s')
    assert removed == len(leftovers)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(keep)


class _PartialThenFailYDL:
    """Primary source leaves a .part and dies; any other source records what
    it found on disk before 'downloading'."""
    seen_on_start: list = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        base = self.opts['outtmpl'].replace('.%(ext)s', '')
        parent = Path(base).parent
        if urls[0] == 'primary':
            Path(base + '.f251.webm.part').write_bytes(b'half of song A')
            raise RuntimeError('ERROR: [youtube] primary: Video unavailable')
        _PartialThenFailYDL.seen_on_start.append(
            sorted(p.name for p in parent.iterdir()))
        Path(base + '.mp3').write_bytes(b'song B')


def test_fallback_starts_without_the_primary_sources_partial(tmp_path, monkeypatch):
    import yt_dlp
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', _PartialThenFailYDL)
    _PartialThenFailYDL.seen_on_start = []

    opts = {'outtmpl': str(tmp_path / 'track') + '.%(ext)s'}
    asyncio.run(downloader._fetch_with_fallback(['primary', 'alt'], opts, 'T'))
    assert _PartialThenFailYDL.seen_on_start == [[]]


# ── BE-002: merged Spotify albums use the same fallback ladder ───────────────

class _DeadPrimaryYDL:
    attempts: list = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        _DeadPrimaryYDL.attempts.append(urls[0])
        if urls[0].endswith('/dead'):
            raise RuntimeError(f'ERROR: [youtube] {urls[0]}: Video unavailable')
        Path(self.opts['outtmpl'].replace('.%(ext)s', '.mp3')).write_bytes(b'a')


def test_concatenate_falls_back_to_runner_up_urls(tmp_path, monkeypatch):
    import yt_dlp

    async def no_sleep(_):
        return None

    merged = {}

    async def fake_concat(files, output_path, tracks_meta, **kwargs):
        merged['files'] = list(files)
        Path(output_path).write_bytes(b'merged')
        return True

    monkeypatch.setattr(yt_dlp, 'YoutubeDL', _DeadPrimaryYDL)
    monkeypatch.setattr(downloader.asyncio, 'sleep', no_sleep)
    monkeypatch.setattr(downloader, 'concatenate_audio', fake_concat)
    monkeypatch.setattr(downloader.settings, 'UPLOAD_FOLDER', str(tmp_path))
    _DeadPrimaryYDL.attempts = []

    meta = {
        'artist': 'Daft Punk', 'album': 'RAM', 'is_playlist': True,
        'tracks': [
            {'index': 1, 'title': 'One', 'artist': 'Daft Punk', 'duration': 100,
             'url': 'https://youtu.be/dead',
             'url_alternatives': ['https://youtu.be/alt']},
            {'index': 2, 'title': 'Two', 'artist': 'Daft Punk', 'duration': 100,
             'url': 'https://youtu.be/two'},
        ],
    }
    try:
        asyncio.run(downloader.download_playlist(
            'https://open.spotify.com/album/x', 'audio', 'mp3', '192', meta,
            concatenate=True,
        ))
    except Exception:
        pass   # post-merge tagging may need ffmpeg; the merge input is what counts

    assert 'https://youtu.be/alt' in _DeadPrimaryYDL.attempts
    assert len(merged.get('files', [])) == 2


# ── BE-005: MP4 video picks M4A audio, not a progressive fallback ────────────

def test_mp4_selector_pairs_mp4_video_with_m4a_audio():
    from backend.utils.ydl_opts_builder import get_ydl_opts

    for bitrate in ('', '192'):
        selector = get_ydl_opts('video', 'mp4', bitrate, 'x.%(ext)s')['format']
        assert 'bestaudio[ext=mp4]' not in selector
        assert selector.split('/')[0].startswith('bestvideo[ext=mp4]+bestaudio[ext=m4a]')


# ── BE-006: WebM merges use WebM codecs ──────────────────────────────────────

@pytest.mark.parametrize('reencoded', [False, True])
def test_webm_merge_uses_vp9_and_opus(reencoded, monkeypatch):
    monkeypatch.setattr(ce, '_vp9_encoder', lambda: 'libvpx-vp9')
    args = ce._merge_video_codecs('webm', reencoded=reencoded)
    assert args[args.index('-c:v') + 1] == 'libvpx-vp9'
    assert args[args.index('-c:a') + 1] == 'libopus'
    assert ce.settings.VIDEO_CODEC_H264 not in args
    assert ce.settings.AUDIO_CODEC_AAC not in args


@pytest.mark.parametrize('fmt', ['mp4', 'mkv'])
def test_mp4_and_mkv_merges_keep_h264_and_aac(fmt):
    args = ce._merge_video_codecs(fmt, reencoded=False)
    assert args[args.index('-c:v') + 1] == ce.settings.VIDEO_CODEC_H264
    assert args[args.index('-c:a') + 1] == ce.settings.AUDIO_CODEC_AAC


# ── BE-007 / BE-008: merged videos get chapters; failures are reported ──────

def _stub_video_merge(monkeypatch, tmp_path, chapters_ok=True):
    calls = {}

    def fake_ffmpeg(args, timeout=None):
        Path(args[-1]).write_bytes(b'video')
        return 0, '', ''

    def fake_chapters(path, tracks, overlap=0.0):
        calls['chapters'] = (path, [t['title'] for t in tracks], overlap)
        return chapters_ok

    monkeypatch.setattr(ce, 'run_ffmpeg', fake_ffmpeg)
    monkeypatch.setattr(ce, 'add_chapters_to_file', fake_chapters)
    monkeypatch.setattr(ce.settings, 'CONCATENATION_CREATE_CHAPTERS', True)
    files = []
    for n in (1, 2):
        f = tmp_path / f'{n}.mp4'
        f.write_bytes(b'v')
        files.append(str(f))
    return calls, files


def test_merged_video_gets_chapter_markers(tmp_path, monkeypatch):
    calls, files = _stub_video_merge(monkeypatch, tmp_path)
    tracks = [{'title': 'One', 'duration': 10}, {'title': 'Two', 'duration': 10}]
    ok = asyncio.run(ce.concatenate_video(
        files, str(tmp_path / 'out.mp4'), 'mp4', tracks_meta=tracks))
    assert ok
    assert calls['chapters'][1] == ['One', 'Two']


def test_chapter_failure_on_a_merged_video_is_reported(tmp_path, monkeypatch):
    calls, files = _stub_video_merge(monkeypatch, tmp_path, chapters_ok=False)
    warnings = []

    async def progress(pct, msg='', warning=None, **kw):
        if warning:
            warnings.append(warning)

    tracks = [{'title': 'One', 'duration': 10}, {'title': 'Two', 'duration': 10}]
    asyncio.run(ce.concatenate_video(
        files, str(tmp_path / 'out.mp4'), 'mp4',
        progress_callback=progress, tracks_meta=tracks))
    assert any('Chapter markers could not be added' in w for w in warnings)


# ── BE-009: backups in the same second get distinct archives ─────────────────

def test_same_second_backups_do_not_share_a_path(tmp_path, monkeypatch):
    from backend import backup_manager, db_models

    monkeypatch.setattr(db_models.settings, 'DATABASE_PATH', str(tmp_path / 'm.db'))
    monkeypatch.setattr(backup_manager.settings, 'BACKUP_FOLDER', str(tmp_path / 'b'))
    monkeypatch.setattr(backup_manager.settings, 'UPLOAD_FOLDER', str(tmp_path / 'd'))
    (tmp_path / 'd').mkdir()
    db_models.init_db()

    class Frozen(backup_manager.datetime):
        @classmethod
        def now(cls, tz=None):
            return backup_manager.datetime(2026, 10, 7, 12, 0, 0)
    monkeypatch.setattr(backup_manager, 'datetime', Frozen)

    async def two():
        return await backup_manager.create_backup(), await backup_manager.create_backup()

    a, b = asyncio.run(two())
    paths = [r.get('path') or r.get('archive_path') or r.get('filename') for r in (a, b)]
    assert paths[0] and paths[0] != paths[1]
    assert len(list((tmp_path / 'b').glob('*.zip'))) == 2


# ── API-001 / BE-010: request validation ────────────────────────────────────

@pytest.mark.parametrize('dtype,fmt', [
    ('audio', 'mp4'), ('audio', 'webm'), ('video', 'mp3'), ('video', 'flac'),
    ('cover_audio', 'mp4'),
])
def test_incompatible_type_and_format_are_rejected(dtype, fmt):
    from pydantic import ValidationError
    from backend.app import DownloadRequest

    with pytest.raises(ValidationError):
        DownloadRequest(url='https://youtu.be/x', download_type=dtype, format=fmt)


@pytest.mark.parametrize('dtype,fmt', [
    ('audio', 'mp3'), ('audio', 'flac'), ('audio', 'aac'),
    ('video', 'mp4'), ('video', 'mkv'), ('video', 'webm'),
    ('cover_audio', 'mp3'),
])
def test_compatible_type_and_format_are_accepted(dtype, fmt):
    from backend.app import DownloadRequest
    DownloadRequest(url='https://youtu.be/x', download_type=dtype, format=fmt)


def test_backup_request_no_longer_advertises_selection():
    from backend.app import BackupRequest
    assert 'selection' not in BackupRequest.model_fields


# ── SEC-001 / SEC-002 / SEC-003: HTTP surface ────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import app as app_module, db_models

    monkeypatch.setattr(db_models.settings, 'DATABASE_PATH', str(tmp_path / 'm.db'))
    db_models.init_db()
    monkeypatch.setattr(app_module, '_API_TOKEN', 'sekrit')
    monkeypatch.setattr(app_module, 'CUSTOM_COVER_DIR', tmp_path / 'covers')
    app_module._rl_store.clear()
    # No lifespan: the scheduler and yt-dlp checks are not under test.
    return TestClient(app_module.app, raise_server_exceptions=False)


@pytest.mark.parametrize('method,path', [
    ('DELETE', '/api/download/00000000-0000-0000-0000-000000000000'),
    ('POST', '/api/download/00000000-0000-0000-0000-000000000000/keep'),
    ('POST', '/api/cover/upload'),
    ('POST', '/api/cover/preview'),
    ('DELETE', '/api/cover/upload/00000000-0000-0000-0000-000000000000'),
])
def test_mutating_endpoints_require_the_token(client, method, path):
    assert client.request(method, path, json={'keep': True}).status_code == 401
    ok = client.request(method, path, json={'keep': True},
                        headers={'Authorization': 'Bearer sekrit'})
    assert ok.status_code != 401


def test_every_mutating_route_is_covered(client):
    """A new POST/DELETE route without auth would reopen SEC-001."""
    from backend import app as app_module
    from fastapi.routing import APIRoute

    for route in app_module.app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith('/api/'):
            continue
        for method in route.methods & {'POST', 'DELETE', 'PUT', 'PATCH'}:
            path = route.path.replace('{download_id}', 'x').replace(
                '{cover_id}', 'x').replace('{backup_id}', 'x').replace(
                '{batch_id}', 'x').replace('{filename:path}', 'x')
            r = client.request(method, path, json={})
            assert r.status_code == 401, f'{method} {route.path} is unprotected'


def test_reads_stay_open_without_a_token(client):
    assert client.get('/api/queue').status_code == 200


def test_spoofed_forwarded_for_does_not_change_the_rate_limit_key():
    from starlette.requests import Request
    from backend.app import _get_client_ip

    def req(headers):
        return Request({
            'type': 'http', 'method': 'GET', 'path': '/',
            'headers': [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            'client': ('172.18.0.5', 1234),
        })

    a = _get_client_ip(req({'X-Forwarded-For': '1.1.1.1, 9.9.9.9', 'X-Real-IP': '9.9.9.9'}))
    b = _get_client_ip(req({'X-Forwarded-For': '2.2.2.2, 9.9.9.9', 'X-Real-IP': '9.9.9.9'}))
    assert a == b == '9.9.9.9'
    assert _get_client_ip(req({'X-Forwarded-For': '1.1.1.1'})) == '172.18.0.5'


def test_oversized_upload_is_refused_from_its_content_length(client, monkeypatch):
    from backend import app as app_module
    monkeypatch.setattr(app_module.settings, 'MAX_UPLOAD_SIZE_MB', 1)
    r = client.post('/api/cover/upload',
                    headers={'Authorization': 'Bearer sekrit',
                             'Content-Length': str(50 * 1024 * 1024),
                             'Content-Type': 'multipart/form-data; boundary=x'},
                    content=b'')
    assert r.status_code == 413


def test_oversized_streamed_upload_is_cut_off(client, monkeypatch):
    from backend import app as app_module
    monkeypatch.setattr(app_module.settings, 'MAX_UPLOAD_SIZE_MB', 1)
    sent = {'bytes': 0}

    def body():
        boundary = b'--x\r\nContent-Disposition: form-data; name="file"; ' \
                   b'filename="a.png"\r\nContent-Type: image/png\r\n\r\n'
        yield boundary
        chunk = b'\0' * 65536
        for _ in range(64):            # 4 MB, no Content-Length
            sent['bytes'] += len(chunk)
            yield chunk
        yield b'\r\n--x--\r\n'

    r = client.post('/api/cover/upload',
                    headers={'Authorization': 'Bearer sekrit',
                             'Content-Type': 'multipart/form-data; boundary=x'},
                    content=body())
    assert r.status_code == 413
    assert not list((app_module.CUSTOM_COVER_DIR).glob('*')) \
        if app_module.CUSTOM_COVER_DIR.exists() else True


def test_small_upload_still_works_and_can_be_deleted(client):
    r = client.post('/api/cover/upload',
                    headers={'Authorization': 'Bearer sekrit'},
                    files={'file': ('a.png', b'\x89PNG' + b'\0' * 100, 'image/png')})
    assert r.status_code == 200, r.text
    cover_id = r.json()['cover_id']
    d = client.delete(f'/api/cover/upload/{cover_id}',
                      headers={'Authorization': 'Bearer sekrit'})
    assert d.json() == {'deleted': True}


# ── BE-011: uploaded covers expire ───────────────────────────────────────────

def test_expired_covers_are_swept(tmp_path, monkeypatch):
    import os
    import time
    from backend import scheduler

    monkeypatch.setattr(scheduler.settings, 'CUSTOM_COVER_DIR', str(tmp_path))
    monkeypatch.setattr(scheduler.settings, 'COVER_UPLOAD_TTL_HOURS', 24)
    old, fresh = tmp_path / 'old.jpg', tmp_path / 'fresh.jpg'
    old.write_bytes(b'x')
    fresh.write_bytes(b'x')
    stale = time.time() - 25 * 3600
    os.utime(old, (stale, stale))

    assert scheduler._sweep_custom_covers() == 1
    assert not old.exists() and fresh.exists()


# ── FE-002: the in-memory status carries the real error text ─────────────────

def test_error_text_is_exposed_under_error_message(tmp_path, monkeypatch):
    from backend import db_models, queue_manager

    monkeypatch.setattr(db_models.settings, 'DATABASE_PATH', str(tmp_path / 'm.db'))
    db_models.init_db()
    did = 'fe002-test'
    db = db_models.get_db()
    try:
        db.execute("INSERT INTO downloads (id, url, platform, status) VALUES (?,?,?,?)",
                   (did, 'https://youtu.be/x', 'youtube', 'downloading'))
        db.commit()
    finally:
        db.close()
    queue_manager.download_states[did] = {'status': 'downloading'}
    try:
        queue_manager.update_download_status(did, 'error', error_message='HTTP 403')
        assert queue_manager.get_download_status(did)['error_message'] == 'HTTP 403'
    finally:
        queue_manager.download_states.pop(did, None)
