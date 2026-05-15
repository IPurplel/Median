"""Embed/normalize tags on downloaded media files using mutagen.

Runs after yt-dlp's own postprocessors finish, and overwrites the curated
fields (title, artist, album, year, genre, cover) with values from Median's
metadata dict. This is the single source of truth for what ends up in the
file — yt-dlp's FFmpegMetadata postprocessor can lag behind (no album
fallback for YouTube, raw YYYYMMDD dates, no category-as-genre fallback).
"""
import base64
from pathlib import Path
from typing import Optional

from backend.logger import app_logger


def write_tags(
    file_path: str,
    title: str = '',
    artist: str = '',
    album: str = '',
    year: str = '',
    genre: str = '',
    cover_path: Optional[str] = None,
) -> bool:
    """Write tags onto an audio file. Returns True on success."""
    p = Path(file_path)
    if not p.is_file():
        app_logger.warning(f"tag_writer: file not found: {file_path}")
        return False

    ext = p.suffix.lower().lstrip('.')
    fields = {
        'title': (title or '').strip(),
        'artist': (artist or '').strip(),
        'album': (album or '').strip(),
        'year': (year or '').strip(),
        'genre': (genre or '').strip(),
    }

    try:
        if ext == 'mp3':
            _write_mp3(file_path, fields, cover_path)
        elif ext == 'flac':
            _write_flac(file_path, fields, cover_path)
        elif ext in ('m4a', 'aac', 'mp4', 'm4b'):
            _write_mp4(file_path, fields, cover_path)
        elif ext == 'ogg':
            _write_ogg(file_path, fields, cover_path, opus=False)
        elif ext == 'opus':
            _write_ogg(file_path, fields, cover_path, opus=True)
        else:
            app_logger.debug(f"tag_writer: skipped (unsupported ext {ext!r})")
            return False
        app_logger.debug(f"tag_writer: tagged {p.name}")
        return True
    except Exception as e:
        app_logger.warning(f"tag_writer error for {file_path}: {e}")
        return False


def _cover_mime(cover_path: str) -> str:
    return 'image/png' if cover_path.lower().endswith('.png') else 'image/jpeg'


def _write_mp3(file_path: str, f: dict, cover_path: Optional[str]):
    from mutagen.id3 import (
        ID3, ID3NoHeaderError,
        TIT2, TPE1, TALB, TDRC, TYER, TCON, APIC,
    )

    try:
        tags = ID3(file_path)
    except ID3NoHeaderError:
        tags = ID3()

    if f['title']:  tags['TIT2'] = TIT2(encoding=3, text=f['title'])
    if f['artist']: tags['TPE1'] = TPE1(encoding=3, text=f['artist'])
    if f['album']:  tags['TALB'] = TALB(encoding=3, text=f['album'])
    if f['genre']:  tags['TCON'] = TCON(encoding=3, text=f['genre'])
    if f['year']:
        # Write both — TDRC is the v2.4 frame, TYER the v2.3 one. Some players
        # (AIMP among them) read TYER preferentially even from v2.4 files.
        tags['TDRC'] = TDRC(encoding=3, text=f['year'])
        tags['TYER'] = TYER(encoding=3, text=f['year'])

    if cover_path and Path(cover_path).is_file():
        # Strip any existing covers and write a fresh one
        tags.delall('APIC')
        tags.add(APIC(
            encoding=0, mime=_cover_mime(cover_path),
            type=3, desc='', data=Path(cover_path).read_bytes(),
        ))
    else:
        # No new cover supplied, but yt-dlp's EmbedThumbnail wrote one with
        # desc='Album cover'. AIMP (and some other strict players) treat any
        # non-empty desc as a *supplementary* image rather than the album's
        # primary cover, so it silently skips displaying it. Rewrite the
        # existing frame with desc='' so AIMP picks it up.
        apics = tags.getall('APIC')
        if apics:
            front = next((a for a in apics if a.type == 3), apics[0])
            data = front.data
            mime = front.mime or 'image/jpeg'
            tags.delall('APIC')
            tags.add(APIC(encoding=0, mime=mime, type=3, desc='', data=data))

    # v2_version=3 → write ID3v2.3 for broadest player compat (AIMP, MusicBee, etc.)
    tags.save(file_path, v2_version=3)


def _write_flac(file_path: str, f: dict, cover_path: Optional[str]):
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import PictureType

    audio = FLAC(file_path)
    if f['title']:  audio['title'] = f['title']
    if f['artist']: audio['artist'] = f['artist']
    if f['album']:  audio['album'] = f['album']
    if f['year']:   audio['date'] = f['year']
    if f['genre']:  audio['genre'] = f['genre']

    if cover_path and Path(cover_path).is_file():
        audio.clear_pictures()
        pic = Picture()
        pic.type = PictureType.COVER_FRONT
        pic.mime = _cover_mime(cover_path)
        pic.desc = 'Cover'
        pic.data = Path(cover_path).read_bytes()
        audio.add_picture(pic)

    audio.save()


def _write_mp4(file_path: str, f: dict, cover_path: Optional[str]):
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(file_path)
    if f['title']:  audio['\xa9nam'] = [f['title']]
    if f['artist']: audio['\xa9ART'] = [f['artist']]
    if f['album']:  audio['\xa9alb'] = [f['album']]
    if f['year']:   audio['\xa9day'] = [f['year']]
    if f['genre']:  audio['\xa9gen'] = [f['genre']]

    if cover_path and Path(cover_path).is_file():
        data = Path(cover_path).read_bytes()
        fmt = MP4Cover.FORMAT_PNG if cover_path.lower().endswith('.png') else MP4Cover.FORMAT_JPEG
        audio['covr'] = [MP4Cover(data, imageformat=fmt)]

    audio.save()


def _write_ogg(file_path: str, f: dict, cover_path: Optional[str], opus: bool):
    from mutagen.oggvorbis import OggVorbis
    from mutagen.oggopus import OggOpus
    from mutagen.flac import Picture
    from mutagen.id3 import PictureType

    audio = OggOpus(file_path) if opus else OggVorbis(file_path)
    if f['title']:  audio['title'] = f['title']
    if f['artist']: audio['artist'] = f['artist']
    if f['album']:  audio['album'] = f['album']
    if f['year']:   audio['date'] = f['year']
    if f['genre']:  audio['genre'] = f['genre']

    if cover_path and Path(cover_path).is_file():
        pic = Picture()
        pic.type = PictureType.COVER_FRONT
        pic.mime = _cover_mime(cover_path)
        pic.desc = 'Cover'
        pic.data = Path(cover_path).read_bytes()
        audio['metadata_block_picture'] = [base64.b64encode(pic.write()).decode('ascii')]

    audio.save()
