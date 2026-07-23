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


def write_lyrics(file_path: str, lyrics: str) -> bool:
    """Embed unsynchronized lyrics into a media file's tags. Returns True on success.

    mp3 → ID3 USLT · m4a/mp4 → ©lyr atom · flac/ogg/opus → LYRICS comment.
    Unsupported containers (mkv/webm) are skipped."""
    p = Path(file_path)
    lyrics = (lyrics or '').strip()
    if not p.is_file() or not lyrics:
        return False
    ext = p.suffix.lower().lstrip('.')
    try:
        if ext == 'mp3':
            from mutagen.id3 import ID3, ID3NoHeaderError, USLT
            try:
                tags = ID3(file_path)
            except ID3NoHeaderError:
                tags = ID3()
            tags.delall('USLT')
            tags.add(USLT(encoding=3, lang='eng', desc='', text=lyrics))
            tags.save(file_path, v2_version=3)
        elif ext in ('m4a', 'aac', 'mp4', 'm4b'):
            from mutagen.mp4 import MP4
            audio = MP4(file_path)
            audio['\xa9lyr'] = [lyrics]
            audio.save()
        elif ext in ('flac', 'ogg', 'opus'):
            from mutagen import File as MutagenFile
            audio = MutagenFile(file_path)
            if audio is None:
                return False
            audio['LYRICS'] = [lyrics]
            audio.save()
        else:
            return False
        return True
    except Exception as e:
        app_logger.warning(f"write_lyrics error for {file_path}: {e}")
        return False


def read_title(file_path: str) -> str:
    """The file's own title tag, or '' — used to match tracks to lyrics."""
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path, easy=True)
        if audio and audio.get('title'):
            return str(audio['title'][0]).strip()
    except Exception:
        pass
    return ''


def embed_lyrics_into_download(path: str, entries: list, merged: bool) -> int:
    """Embed fetched lyrics into a finished download; returns files tagged.

    - Single track file: its lyrics go straight into the tag.
    - Merged album file: all lyrics combined (title header per track) in one tag.
    - Folder of separate tracks: each file matched to its lyrics by the file's
      own title tag, falling back to filename containment."""
    p = Path(path)
    entries = [e for e in (entries or []) if (e.get('lyrics') or '').strip()]
    if not entries:
        return 0

    if p.is_file():
        if merged and len(entries) > 1:
            text = "\n\n".join(f"{e.get('title') or '?'}\n\n{e['lyrics'].strip()}" for e in entries)
        else:
            text = entries[0]['lyrics']
        return 1 if write_lyrics(str(p), text) else 0

    if not p.is_dir():
        return 0

    by_title = {(e.get('title') or '').strip().lower(): e['lyrics'] for e in entries}
    tagged = 0
    for f in sorted(p.iterdir()):
        if not f.is_file():
            continue
        lyr = by_title.get(read_title(str(f)).lower())
        if not lyr:
            stem = f.stem.lower()
            lyr = next((l for t, l in by_title.items() if t and t in stem), None)
        if lyr and write_lyrics(str(f), lyr):
            tagged += 1
    return tagged


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
