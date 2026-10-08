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
        first = selector.split('/')[0]
        assert first.startswith('bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]')
        # AV1/other MP4 video is still a fallback, ahead of progressive formats
        assert 'bestvideo[ext=mp4]+bestaudio[ext=m4a]' in selector


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


# ── Found in end-to-end testing (2026-10-08) ─────────────────────────────────

def test_flac_hardcut_merge_reencodes_instead_of_stream_copying(tmp_path, monkeypatch):
    """Stream-copied FLAC keeps only the first file's STREAMINFO header;
    decoders stopped partway into track two."""
    seen = {}

    def fake_ffmpeg(args, timeout=None):
        seen['args'] = args
        Path(args[-1]).write_bytes(b'x')
        return 0, '', ''
    monkeypatch.setattr(ce, 'run_ffmpeg', fake_ffmpeg)

    for ext, expect in (('flac', ['-c:a', 'flac']), ('mp3', ['-c', 'copy'])):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                ce._run_hardcut_audio(loop, ['a', 'b'], str(tmp_path / f'out.{ext}')))
        finally:
            loop.close()
        args = seen['args']
        assert any(args[i:i + 2] == expect for i in range(len(args))), (ext, args)


def test_flac_chapters_are_written_as_vorbis_comments(tmp_path, monkeypatch):
    """ffmpeg's FLAC muxer drops chapters silently; they go in as
    CHAPTERxxx comments instead."""
    from backend import ffmpeg_chapters_handler as fch

    written = {}

    class FakeFLAC(dict):
        def __init__(self, path):
            super().__init__()
            self.tags = self

        def save(self):
            written.update(self)
    import mutagen.flac
    monkeypatch.setattr(mutagen.flac, 'FLAC', FakeFLAC)

    tracks = [{'title': 'One', 'duration': 61.5}, {'title': 'Two', 'duration': 10}]
    assert fch.add_chapters_to_file(str(tmp_path / 'a.flac'), tracks) is True
    assert written == {
        'CHAPTER001': '00:00:00.000', 'CHAPTER001NAME': 'One',
        'CHAPTER002': '00:01:01.500', 'CHAPTER002NAME': 'Two',
    }


def test_youtube_thumbnails_fall_back_to_ones_that_exist():
    from backend.image_processor import thumbnail_candidates

    c = thumbnail_candidates('https://i.ytimg.com/vi_webp/jNQXAC9IVRw/maxresdefault.webp')
    assert c[0].endswith('maxresdefault.webp')
    assert c[-1] == 'https://i.ytimg.com/vi/jNQXAC9IVRw/hqdefault.jpg'
    assert thumbnail_candidates('https://f4.bcbits.com/img/a1_10.jpg') == \
        ['https://f4.bcbits.com/img/a1_0.jpg']
    assert thumbnail_candidates('') == []


_CLASSIC_BANDCAMP = '''
<div class="ipCell"><div class="ipCellImage"><a href="/album/antm"><img/></a></div>
<div class="ipCellLabel"><div class="ipCellLabel1"><a href="/album/antm">ANTM</a></div></div></div>
<div class="ipCell"><div class="ipCellLabel1"><a href="/album/terra-damnata">Terra &amp; Damnata</a></div></div>
<a href="https://label.bandcamp.com/album/someone-else">Other artist</a>
'''


def test_classic_bandcamp_layout_yields_albums():
    """yt-dlp's Bandcamp user extractor returns nothing for the classic
    indexpage layout, so the discography came back empty."""
    from backend.discography import _bandcamp_entries_from_html

    entries = _bandcamp_entries_from_html('https://artist.bandcamp.com/music', _CLASSIC_BANDCAMP)
    assert entries == [
        {'url': 'https://artist.bandcamp.com/album/antm', 'title': 'ANTM'},
        {'url': 'https://artist.bandcamp.com/album/terra-damnata', 'title': 'Terra & Damnata'},
    ]


def test_empty_discography_is_not_cached(monkeypatch):
    from backend import discography

    async def nothing(page):
        return []

    async def no_html(page):
        return ''
    stored = []
    monkeypatch.setattr(discography, '_flat_entries', nothing)
    monkeypatch.setattr(discography, '_fetch_page', no_html)
    monkeypatch.setattr(discography.metadata_cache, 'get', lambda k: None)
    monkeypatch.setattr(discography.metadata_cache, 'set', lambda *a, **k: stored.append(a))

    result = asyncio.run(discography.resolve_discography('https://a.bandcamp.com/album/x'))
    assert result['albums'] == [] and stored == []


@pytest.mark.parametrize('url,platform,playlist', [
    ('https://music.youtube.com/watch?v=jNQXAC9IVRw', 'youtube', False),
    ('https://m.youtube.com/watch?v=jNQXAC9IVRw', 'youtube', False),
    ('https://www.youtube.com/watch?app=desktop&v=jNQXAC9IVRw', 'youtube', False),
    ('https://youtube.com/shorts/Y3wF1czl9GY', 'youtube', False),
    ('https://music.youtube.com/playlist?list=OLAK5uy_abc', 'youtube', True),
    ('https://music.youtube.com/browse/MPREb_abc', 'youtube', True),
    ('https://on.soundcloud.com/AbC12', 'soundcloud', False),
    ('https://m.soundcloud.com/ethmusic/track', 'soundcloud', False),
    ('https://example.com/youtube.com/watch?v=x', None, False),
])
def test_share_link_variants_are_recognised(url, platform, playlist):
    """Mobile, YouTube Music, Shorts and reordered-query links were rejected
    as 'Platform not supported'."""
    from backend.utils.validators import detect_platform, is_playlist_url
    assert detect_platform(url) == platform
    assert is_playlist_url(url) == playlist


# ── Second bug hunt (2026-10-08) ─────────────────────────────────────────────

# BE-019: a download cancelled while still queued settles as cancelled

def test_cancel_while_queued_settles_the_download(tmp_path, monkeypatch):
    from contextlib import suppress
    from backend import db_models, queue_manager

    monkeypatch.setattr(db_models.settings, 'DATABASE_PATH', str(tmp_path / 'm.db'))
    db_models.init_db()

    async def scenario():
        # Every slot taken: the new download can only wait.
        queue_manager._dl_semaphore = asyncio.Semaphore(0)
        did = await queue_manager.enqueue_download({
            'url': 'https://youtu.be/dQw4w9WgXcQ', 'download_type': 'audio',
            'format': 'mp3', 'metadata': {'title': 'T', 'platform': 'youtube'},
        })
        task = queue_manager.active_downloads[did]
        await asyncio.sleep(0)                 # now parked on the semaphore
        assert queue_manager.cancel_download(did)
        with suppress(asyncio.CancelledError):
            await task
        return did

    try:
        did = asyncio.run(scenario())
    finally:
        queue_manager._dl_semaphore = None
    assert queue_manager.get_download_status(did)['status'] == 'cancelled'
    assert did not in queue_manager.active_downloads
    assert did not in downloader._cancel_flags
    assert queue_manager.get_queue() == []


# BE-020 / BE-021 / BE-025: separate-track albums survive a bad entry

class _OneBadEntryYDL:
    opts_seen = None
    succeed = True

    def __init__(self, opts):
        self.opts = opts
        _OneBadEntryYDL.opts_seen = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        folder = Path(self.opts['outtmpl']).parent
        if _OneBadEntryYDL.succeed:
            (folder / '001 - Good.mp3').write_bytes(b'a')
        # With ignoreerrors yt-dlp reports the bad entry and carries on.
        self.opts['logger'].error('ERROR: [youtube] abc: Private video')


def _separate_album(tmp_path, monkeypatch, download_id):
    import yt_dlp
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', _OneBadEntryYDL)
    monkeypatch.setattr(downloader.settings, 'UPLOAD_FOLDER', str(tmp_path))
    warnings = []

    async def progress(pct, msg='', warning=None, **kw):
        if warning:
            warnings.append(warning)

    meta = {'artist': 'A', 'album': 'B', 'platform': 'youtube', 'track_count': 2,
            'tracks': [{'title': 'Good'}, {'title': 'Bad'}]}
    run = downloader.download_playlist(
        'https://www.youtube.com/playlist?list=PLx', 'audio', 'mp3', '192', meta,
        progress_callback=progress, download_id=download_id)
    return run, warnings


def test_one_bad_playlist_entry_no_longer_fails_the_album(tmp_path, monkeypatch):
    _OneBadEntryYDL.succeed = True
    run, warnings = _separate_album(tmp_path, monkeypatch, 'be020')
    try:
        result = asyncio.run(run)
    finally:
        downloader.discard_temp_entries('be020')
    opts = _OneBadEntryYDL.opts_seen
    assert opts['ignoreerrors'] == 'only_download'
    assert opts['playlistend'] == downloader.settings.MAX_PLAYLIST_TRACKS
    assert result['track_count'] == 1
    assert any('skipped' in w and 'Private video' in w for w in warnings)


def test_failed_album_folder_is_removed_by_partial_cleanup(tmp_path, monkeypatch):
    _OneBadEntryYDL.succeed = False
    run, _ = _separate_album(tmp_path, monkeypatch, 'be021')
    with pytest.raises(RuntimeError, match='Private video'):
        asyncio.run(run)
    assert any(p.is_dir() for p in tmp_path.iterdir())
    downloader.cleanup_partials('be021')
    assert not any(p.is_dir() for p in tmp_path.iterdir())


# BE-022: video merges finish in time

def test_identical_inputs_are_joined_without_reencoding(tmp_path, monkeypatch):
    calls, files = _stub_video_merge(monkeypatch, tmp_path)
    sig = (('video', 'h264', 1920, 1080, '30/1', None, None),
           ('audio', 'aac', None, None, '0/0', '44100', 2))
    monkeypatch.setattr(ce, 'get_stream_signature', lambda f: sig)
    seen = []

    def fake_ffmpeg(args, timeout=None):
        seen.append(args)
        Path(args[-1]).write_bytes(b'video')
        return 0, '', ''
    monkeypatch.setattr(ce, 'run_ffmpeg', fake_ffmpeg)

    assert asyncio.run(ce.concatenate_video(files, str(tmp_path / 'o.mp4'), 'mp4'))
    assert len(seen) == 1 and '-c' in seen[0]
    assert seen[0][seen[0].index('-c') + 1] == 'copy'


def test_stream_copy_rules():
    h264 = (('video', 'h264', 1280, 720, '30/1', None, None),
            ('audio', 'aac', None, None, '0/0', '44100', 2))
    vp9 = (('video', 'vp9', 1280, 720, '30/1', None, None),
           ('audio', 'opus', None, None, '0/0', '48000', 2))
    assert ce._can_stream_copy([h264, h264], 'mp4')
    assert not ce._can_stream_copy([h264, vp9], 'mkv')      # inputs differ
    assert not ce._can_stream_copy([h264, h264], 'webm')    # webm can't hold h264
    assert ce._can_stream_copy([vp9, vp9], 'webm')
    assert not ce._can_stream_copy([h264, None], 'mp4')     # unreadable input


def test_webm_reencode_uses_fast_vp9_settings_and_timeouts_scale():
    args = ce._merge_video_codecs('webm', reencoded=False)
    assert args[args.index('-deadline') + 1] == 'realtime'
    assert '-row-mt' in args
    assert ce._scaled_timeout(600, 3600, 4) == 14400
    assert ce._scaled_timeout(600, 10, 4) == 600


# BE-023: merge progress stays within what is left of the bar

def test_merge_progress_is_scaled_and_never_says_complete_early():
    seen = []

    async def cb(pct, message='', warning=None, **kw):
        seen.append((pct, message, warning))

    scaled = downloader._scaled_progress(cb, 75, 99)

    async def go():
        await scaled(10, 'Concatenating audio...')
        await scaled(100, 'Complete')
        await scaled(None, '', warning='heads up')
    asyncio.run(go())
    assert seen[0][0] == pytest.approx(77.4)
    assert seen[1][:2] == (99, 'Finishing...')
    assert seen[2] == (None, '', 'heads up')


# BE-024: cancelling a merge stops ffmpeg

def test_cancel_kills_a_running_ffmpeg_and_blocks_new_ones(tmp_path, monkeypatch):
    import sys
    import time
    from backend.utils import ffmpeg_handler as fh

    monkeypatch.setattr(fh, 'get_ffmpeg_path', lambda: sys.executable)
    out = str(tmp_path / 'Artist - Album.mp3')
    result = {}

    def run():
        result['r'] = fh.run_ffmpeg(['-c', 'import time; time.sleep(30)', out])
    t = threading.Thread(target=run)
    start = time.monotonic()
    t.start()
    for _ in range(100):                       # wait for the process to exist
        if fh._running:
            break
        time.sleep(0.05)

    downloader._register_temp('be024', 'stem', out)
    downloader.cleanup_partials('be024')
    t.join(10)
    assert not t.is_alive() and time.monotonic() - start < 10
    assert result['r'][0] != 0 and 'cancelled' in result['r'][2]
    # A follow-up step on the same output (chapters, cover) must not start.
    assert fh.run_ffmpeg(['-c', 'pass', out])[2] == 'FFmpeg cancelled'


# SEC-004: /api/download validates the URL like /api/validate does

def test_download_rejects_unsupported_urls(client, monkeypatch):
    from backend import app as app_module

    async def boom(url, *a, **k):
        raise AssertionError('extract_metadata must not run for a rejected URL')
    monkeypatch.setattr(app_module, 'extract_metadata', boom)
    r = client.post('/api/download', headers={'Authorization': 'Bearer sekrit'},
                    json={'url': 'http://127.0.0.1:8080/admin',
                          'download_type': 'audio', 'format': 'mp3'})
    assert r.status_code == 400


# BE-026: adding chapters keeps the file's own tags

def test_chapter_pass_keeps_the_files_metadata(monkeypatch):
    from backend import ffmpeg_chapters_handler as ch
    seen = {}

    def fake_ffmpeg(args, timeout=None):
        seen['args'] = args
        return 0, '', ''
    monkeypatch.setattr(ch, 'run_ffmpeg', fake_ffmpeg)
    ch.embed_chapters('in.mkv', 'meta.txt', 'out.mkv')
    args = seen['args']
    assert args[args.index('-map_metadata') + 1] == '0'
    assert args[args.index('-map_chapters') + 1] == '1'
    # BE-030: every stream is kept, including a cover attachment.
    assert args[args.index('-map') + 1] == '0'


# BE-027: full backups skip caches and temp folders

def test_full_backup_skips_cover_cache_and_temp_dirs(tmp_path, monkeypatch):
    import zipfile
    from backend import backup_manager, db_models

    monkeypatch.setattr(db_models.settings, 'DATABASE_PATH', str(tmp_path / 'm.db'))
    monkeypatch.setattr(backup_manager.settings, 'BACKUP_FOLDER', str(tmp_path / 'b'))
    monkeypatch.setattr(backup_manager.settings, 'UPLOAD_FOLDER', str(tmp_path / 'd'))
    db_models.init_db()
    d = tmp_path / 'd'
    for rel in ('.cover_cache/abc.jpg', '_concat_1/track_000.mp3', 'A - B/01 - Song.mp3'):
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(b'x')

    result = asyncio.run(backup_manager.create_backup())
    with zipfile.ZipFile(result['path']) as zf:
        assert zf.namelist() == ['A - B/01 - Song.mp3']


# BE-028: output names are claimed atomically

def test_concurrent_file_reservations_are_all_unique(tmp_path):
    from backend.utils.file_organizer import reserve_unique_file

    results, barrier = [], threading.Barrier(8)

    def grab():
        barrier.wait()
        results.append(reserve_unique_file(tmp_path / 'Artist - Song.mp3'))

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 8 and all(p.exists() for p in results)


# BE-029: the activity chart is labelled in UTC, like the rows it counts

def test_activity_labels_are_utc_dates(client):
    from datetime import datetime, timezone
    days = client.get('/api/statistics').json()['activity_7d']
    assert days[-1]['date'] == datetime.now(timezone.utc).strftime('%Y-%m-%d')


# ── Leftover issues (2026-10-08, third round) ────────────────────────────────

# BE-031: files yt-dlp writes just after a cancel are swept again

def test_late_cleanup_only_touches_median_temp_names(tmp_path):
    entries = [('stem', str(tmp_path / '_tmp_abc')), ('dir', str(tmp_path / '_concat_x')),
               ('stem', str(tmp_path / 'Artist - Album.mp3')),
               ('dir', str(tmp_path / 'Artist - Album'))]
    kept = downloader.late_cleanup_entries(entries)
    assert [Path(raw).name for _, raw in kept] == ['_tmp_abc', '_concat_x']


def test_cancel_sweeps_again_for_late_writes(tmp_path, monkeypatch):
    from backend import queue_manager

    monkeypatch.setattr(queue_manager, 'LATE_CLEANUP_DELAYS', (0.05, 0.1))
    stem = tmp_path / '_tmp_late'

    async def scenario():
        queue_manager._schedule_late_cleanup('be031', [('stem', str(stem))])
        # The worker thread writes its thumbnail after the first cleanup ran.
        (tmp_path / '_tmp_late.webp').write_bytes(b'x')
        await asyncio.sleep(0.3)
    asyncio.run(scenario())
    assert not list(tmp_path.glob('_tmp_late*'))


def test_stale_sweep_removes_media_free_album_folders(tmp_path, monkeypatch):
    import os
    import time
    from backend import scheduler

    monkeypatch.setattr(scheduler.settings, 'UPLOAD_FOLDER', str(tmp_path))
    old = time.time() - 2 * 3600
    leftover = tmp_path / 'A - B'
    leftover.mkdir()
    (leftover / '001 - x.webp').write_bytes(b'x')
    album = tmp_path / 'C - D'
    album.mkdir()
    (album / '001 - Song.mp3').write_bytes(b'x')
    cache = tmp_path / '.cover_cache'
    cache.mkdir()
    (cache / 'k.jpg').write_bytes(b'x')
    for p in (leftover, album, cache, *leftover.iterdir(), *album.iterdir(), *cache.iterdir()):
        os.utime(p, (old, old))

    scheduler._sweep_stale_partials()
    assert not leftover.exists()
    assert album.exists() and cache.exists()


# API-003: unknown /api paths are 404, not the web page

def test_unknown_api_path_is_404_but_the_app_still_loads(client):
    r = client.get('/api/does-not-exist')
    assert r.status_code == 404 and r.json() == {'detail': 'Not found'}
    assert client.get('/some/page').status_code == 200


# BE-032: cover preview works without imghdr (removed in Python 3.13)

def test_image_mime_is_sniffed_with_pillow(tmp_path):
    from PIL import Image
    from backend import app as app_module

    png = tmp_path / 'cover.bin'
    Image.new('RGB', (4, 4)).save(png, 'PNG')
    assert app_module._sniff_image_mime(str(png)) == 'image/png'
    assert app_module._sniff_image_mime(str(tmp_path / 'missing')) == 'image/jpeg'
    assert 'import imghdr' not in Path(app_module.__file__).read_text(encoding='utf-8')


# BE-033: the batch index exists on fresh installs too

def test_batch_index_is_created_on_a_fresh_database(tmp_path, monkeypatch):
    from backend import db_models

    monkeypatch.setattr(db_models.settings, 'DATABASE_PATH', str(tmp_path / 'fresh.db'))
    db_models.init_db()
    db = db_models.get_db()
    try:
        names = {r['name'] for r in db.execute("PRAGMA index_list(downloads)")}
    finally:
        db.close()
    assert 'idx_downloads_batch' in names


# Validators moved to pydantic v2 keep their behaviour

def test_v2_validators_behave_like_before():
    from pydantic import ValidationError
    from backend.app import DownloadRequest, CoverPreviewRequest

    r = DownloadRequest(url='https://youtu.be/x', download_type='audio', format='mp3',
                        selected_tracks=[3, 1, 3], crossfade_duration=999)
    assert r.selected_tracks == [1, 3]
    from backend.config import settings
    assert r.crossfade_duration == settings.CROSSFADE_MAX_DURATION   # clamped
    with pytest.raises(ValidationError):
        DownloadRequest(url='https://youtu.be/x', download_type='audio', format='mp3',
                        cover_id='not-a-uuid')
    with pytest.raises(ValidationError):
        CoverPreviewRequest(thumbnail_url='https://evil.example/x.jpg')


# ── Third bug hunt (2026-10-08) ──────────────────────────────────────────────

# SEC-005: a supported-looking prefix can't smuggle in another host

@pytest.mark.parametrize('url', [
    'https://x.bandcamp.com@127.0.0.1:5055/api/health',
    'http://a.bandcamp.com.evil.example/album/x',
    'https://user:pw@www.youtube.com/watch?v=abc',
])
def test_validator_checks_the_real_host(url):
    from backend.utils.validators import validate_url
    ok, _, _ = validate_url(url)
    assert not ok


@pytest.mark.parametrize('url,platform', [
    ('https://artist.bandcamp.com/album/x', 'bandcamp'),
    ('https://www.youtube.com/watch?v=abc', 'youtube'),
    ('youtu.be/abc', 'youtube'),
    ('https://on.soundcloud.com/Xy7Kp2', 'soundcloud'),
    ('spotify:album:4m2880jivSbbyEGAKfITCa', 'spotify'),
    ('https://musicbrainz.org/release-group/1d9e8ed6-3893-4d3b-aa7d-6cd79609e386', 'spotify'),
])
def test_validator_still_accepts_real_links(url, platform):
    from backend.utils.validators import validate_url
    assert validate_url(url)[:2] == (True, platform)


# BE-034: a watch link with &list= stays one video

def test_single_video_extraction_and_download_ignore_the_list(tmp_path, monkeypatch):
    import yt_dlp
    from backend import metadata_handler

    seen = []

    class FakeYDL:
        def __init__(self, opts):
            seen.append(opts)
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            return {'title': 'T', 'duration': 10, 'uploader': 'U'}

        def download(self, urls):
            Path(self.opts['outtmpl'].replace('.%(ext)s', '.mp3')).write_bytes(b'a')

    monkeypatch.setattr(yt_dlp, 'YoutubeDL', FakeYDL)
    monkeypatch.setattr(metadata_handler.metadata_cache, 'get', lambda u: None)
    monkeypatch.setattr(metadata_handler.metadata_cache, 'set', lambda *a, **k: None)
    url = 'https://www.youtube.com/watch?v=jNQXAC9IVRw&list=PLVL9tujUubHRVV3MTeofrpCjW-Th3mRE1'
    asyncio.run(metadata_handler.extract_metadata(url))
    assert seen[-1].get('noplaylist') is True

    monkeypatch.setattr(downloader.settings, 'UPLOAD_FOLDER', str(tmp_path))
    asyncio.run(downloader.download_single(url, 'audio', 'mp3', '192',
                                           {'title': 'T', 'artist': 'U'}))
    assert seen[-1].get('noplaylist') is True


# BE-035: a SoundCloud share link finds the uploader's real pages

def test_soundcloud_share_link_uses_the_uploader_url():
    from backend.discography import artist_pages
    pages = artist_pages('https://on.soundcloud.com/Xy7Kp2',
                         {'artist_url': 'https://soundcloud.com/realartist'})
    assert pages[0] == 'https://soundcloud.com/realartist/albums'
    assert artist_pages('https://on.soundcloud.com/Xy7Kp2', {}) == []


# ── Fourth round ─────────────────────────────────────────────────────────────

def _make_audio(path):
    import shutil, subprocess
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg not installed')
    codec = {'.m4a': ['-c:a', 'aac'], '.flac': ['-c:a', 'flac'], '.mp3': []}[path.suffix]
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi',
                    '-i', 'sine=d=1', *codec, str(path)], check=True)


@pytest.mark.parametrize('ext', ['.mp3', '.m4a', '.flac'])
@pytest.mark.parametrize('cover_fmt,cover_name,want', [
    ('WEBP', '_album_cover.jpg', 'JPEG'),  # YouTube thumbnail saved as .jpg
    ('GIF', 'upload.gif', 'JPEG'),
    ('PNG', 'mislabelled.jpg', 'PNG'),
])
def test_be036_cover_embedded_in_its_real_format(tmp_path, ext, cover_fmt, cover_name, want):
    import io
    from PIL import Image
    from backend.utils.tag_writer import write_tags
    audio = tmp_path / f'a{ext}'
    _make_audio(audio)
    cover = tmp_path / cover_name
    Image.new('RGB', (64, 64), 'red').save(cover, cover_fmt)

    assert write_tags(str(audio), title='t', cover_path=str(cover))

    if ext == '.mp3':
        from mutagen.id3 import ID3
        apic = ID3(str(audio)).getall('APIC')[0]
        data, mime = apic.data, apic.mime
    elif ext == '.m4a':
        from mutagen.mp4 import MP4, MP4Cover
        covr = MP4(str(audio))['covr'][0]
        data = bytes(covr)
        mime = 'image/png' if covr.imageformat == MP4Cover.FORMAT_PNG else 'image/jpeg'
    else:
        from mutagen.flac import FLAC
        pic = FLAC(str(audio)).pictures[0]
        data, mime = pic.data, pic.mime
    assert Image.open(io.BytesIO(data)).format == want
    assert mime == f'image/{want.lower()}'


def test_be036_sidecar_and_download_are_converted(tmp_path):
    from PIL import Image
    from backend.image_processor import save_cover_as
    src = tmp_path / 'up.webp'
    Image.new('RGB', (32, 32), 'blue').save(src, 'WEBP')
    save_cover_as(str(src), str(tmp_path / 'cover.jpg'))
    assert Image.open(tmp_path / 'cover.jpg').format == 'JPEG'
    # In place, as download_cover_image does with a WebP saved under .jpg
    inplace = tmp_path / '_cover.jpg'
    inplace.write_bytes(src.read_bytes())
    save_cover_as(str(inplace), str(inplace))
    assert Image.open(inplace).format == 'JPEG'
