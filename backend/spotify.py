"""Read Spotify metadata without an API key, then find the music elsewhere.

Spotify's own audio is Widevine-DRM encrypted and yt-dlp has no extractor for
it, so Median never downloads *from* Spotify. What it does is read a link's
metadata — title, artist, tracklist, cover, durations — and look the same
recordings up on YouTube. Spotify says what the music is; YouTube supplies the
audio. That means fidelity is whatever YouTube serves, and a match is a
best guess rather than a certainty; `backend.utils.yt_match` scores its
confidence so a shaky one can be surfaced to the user instead of hidden.

The official Web API is deliberately not used. Since February 2026 its
Development Mode requires an active *paid* Premium subscription, is capped at
one client ID and five users, and Spotify has said it is moving metadata
endpoints off the client-credentials flow. The embed player needs none of that:
it server-renders everything into a ``__NEXT_DATA__`` JSON blob that any HTTP
client can read anonymously. That blob is undocumented and can change without
warning, so every field read here is defensive and degrades rather than raises.

One thing the embed player will not give up is an artist's back catalogue —
``/embed/artist`` returns ten top tracks, and the plain artist page is an empty
JS shell. Discographies therefore come from MusicBrainz, which is free,
documented, and also needs no key.
"""
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from backend.config import settings
from backend.logger import app_logger

PLATFORM = 'spotify'

_EMBED_URL = 'https://open.spotify.com/embed/{kind}/{spotify_id}'
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)

# open.spotify.com/track/ID, with the /intl-xx/ locale segment Spotify now
# injects, and the spotify:track:ID URI form the desktop app copies.
_WEB_URL_RE = re.compile(
    r'^(?:https?://)?(?:www\.|open\.)?spotify\.com'
    r'(?:/intl-[a-z]{2,3})?'
    r'/(track|album|playlist|artist)/([A-Za-z0-9]{22})',
    re.IGNORECASE,
)
_URI_RE = re.compile(
    r'^spotify:(track|album|playlist|artist):([A-Za-z0-9]{22})$', re.IGNORECASE
)

_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)

KINDS = ('track', 'album', 'playlist', 'artist')


def parse_spotify_url(url: str) -> Optional[Tuple[str, str]]:
    """('album', '4m2880…') for any Spotify link form, or None."""
    if not url:
        return None
    raw = url.strip()
    for pattern in (_URI_RE, _WEB_URL_RE):
        m = pattern.match(raw)
        if m:
            return m.group(1).lower(), m.group(2)
    return None


def is_spotify_url(url: str) -> bool:
    return parse_spotify_url(url) is not None


def canonical_url(kind: str, spotify_id: str) -> str:
    return f'https://open.spotify.com/{kind}/{spotify_id}'


def _fetch(url: str, timeout: int = 20, accept: str = 'text/html') -> str:
    req = urllib.request.Request(
        url, headers={'User-Agent': _UA, 'Accept': accept}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', 'replace')


def fetch_entity(kind: str, spotify_id: str) -> dict:
    """The embed page's entity object: name, artists, trackList, cover art.

    Raises RuntimeError with a user-readable message when the page can't be
    read or its shape has changed — callers surface that as a 422.
    """
    if kind not in KINDS:
        raise RuntimeError(f"Unsupported Spotify link type: {kind}")

    html = _fetch(_EMBED_URL.format(kind=kind, spotify_id=spotify_id))
    match = _NEXT_DATA_RE.search(html)
    if not match:
        raise RuntimeError(
            "Spotify's page didn't contain the expected data. The link may be "
            "region-locked or private, or Spotify changed their page layout."
        )
    try:
        blob = json.loads(match.group(1))
        entity = blob['props']['pageProps']['state']['data']['entity']
    except (ValueError, KeyError, TypeError) as e:
        raise RuntimeError(f"Could not read Spotify's page data: {e}")

    if not isinstance(entity, dict) or not entity.get('name'):
        raise RuntimeError("Spotify returned no details for that link.")
    return entity


# ── field extraction ─────────────────────────────────────────────────────────

def _best_image(entity: dict) -> str:
    """Largest cover art URL the embed offers (it caps at 300px; the URL is
    upgraded to 640px by image_processor.upgrade_thumbnail_url)."""
    images = ((entity.get('visualIdentity') or {}).get('image')) or []
    usable = [
        img for img in images
        if isinstance(img, dict) and (img.get('url') or '').startswith('http')
    ]
    if not usable:
        return ''
    best = max(usable, key=lambda i: i.get('maxWidth') or i.get('maxHeight') or 0)
    from backend.image_processor import upgrade_thumbnail_url
    return upgrade_thumbnail_url(best.get('url') or '')


def _clean_text(value) -> str:
    """Collapse Spotify's exotic whitespace into ordinary spaces.

    Multiple artists arrive joined by a comma and a non-breaking space
    ('Daft Punk,\\xa0Julian Casablancas'), which would otherwise travel all the
    way into filenames and ID3 frames.
    """
    return re.sub(r'\s+', ' ', str(value or '').replace('\xa0', ' ')).strip()


def _ms_to_seconds(value) -> int:
    try:
        return int(value or 0) // 1000
    except (TypeError, ValueError):
        return 0


def _release_iso(entity: dict) -> str:
    """'19871112' from releaseDate.isoString, or '' — albums often omit it."""
    iso = ((entity.get('releaseDate') or {}) or {}).get('isoString') or ''
    digits = ''.join(c for c in str(iso)[:10] if c.isdigit())
    return digits[:8] if len(digits) >= 8 else ''


def _entity_artist(entity: dict) -> str:
    """Artist name, from whichever field this entity type actually populates.

    Tracks carry an `artists` list; albums leave it null and put the artist in
    `subtitle`. Playlists put the *owner* in `subtitle`, which is not an artist
    at all — that case is handled by the caller.
    """
    for artist in (entity.get('artists') or []):
        name = (artist or {}).get('name')
        if name:
            return _clean_text(name)
    return _clean_text(entity.get('subtitle'))


def _artist_url(entity: dict) -> str:
    for artist in (entity.get('artists') or []):
        uri = (artist or {}).get('uri') or ''
        if uri.startswith('spotify:artist:'):
            return canonical_url('artist', uri.rsplit(':', 1)[-1])
    return ''


def _tracks(entity: dict, cover: str) -> list:
    """Normalized track dicts. `url` is left empty — yt_match fills it in."""
    tracks = []
    for i, item in enumerate(entity.get('trackList') or []):
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get('title'))
        if not title:
            continue
        tracks.append({
            'index': i + 1,
            'title': title,
            'artist': _clean_text(item.get('subtitle')),
            'duration': _ms_to_seconds(item.get('duration')),
            # Filled in by the YouTube matcher at download time. Kept as a key
            # so the shape matches what the rest of Median expects from a
            # playlist, and so an unresolved track is obvious rather than
            # silently absent.
            'url': '',
            'thumbnail': cover,
            'spotify_uri': item.get('uri') or '',
        })
    return tracks


def _various_artists(tracks: list) -> str:
    """One name if every track shares it, otherwise 'Various Artists'.

    A playlist's `subtitle` is its owner ('Spotify', a username) — useless as
    an artist tag, and it would end up in every folder name and ID3 frame.
    """
    names = {(t.get('artist') or '').strip() for t in tracks}
    names.discard('')
    if len(names) == 1:
        return names.pop()
    return 'Various Artists' if names else ''


def to_metadata(entity: dict, url: str) -> dict:
    """Normalize an entity into the dict shape metadata_handler returns.

    Matching that shape exactly is what lets every existing feature — the track
    picker, merge, crossfade, cover+audio, description.md, discography zips —
    work on Spotify links without knowing they exist.
    """
    kind = (entity.get('type') or '').lower()
    cover = _best_image(entity)
    release_date = _release_iso(entity)
    year = release_date[:4] if release_date else ''

    if kind == 'track':
        artist = _entity_artist(entity)
        return {
            'is_playlist': False,
            'platform': PLATFORM,
            'title': _clean_text(entity.get('name')) or 'Unknown',
            'artist': artist,
            'album': '',
            'genre': '',
            'year': year,
            'tags': [],
            'release_date': release_date,
            'artist_url': _artist_url(entity),
            'duration': _ms_to_seconds(entity.get('duration')),
            'thumbnail': cover,
            'url': url,
            'formats': [],
            'available_qualities': {},
            'needs_yt_match': True,
        }

    tracks = _tracks(entity, cover)
    name = _clean_text(entity.get('name')) or 'Unknown'
    artist = (
        _various_artists(tracks) if kind == 'playlist'
        else (_entity_artist(entity) or _various_artists(tracks))
    )

    max_tracks = settings.MAX_SPOTIFY_PLAYLIST_TRACKS
    truncated = len(tracks) > max_tracks
    if truncated:
        app_logger.warning(
            f"Spotify {kind} has {len(tracks)} tracks — truncating to {max_tracks}"
        )
        tracks = tracks[:max_tracks]

    meta = {
        'is_playlist': True,
        'platform': PLATFORM,
        'title': name,
        'artist': artist,
        'album': name,
        'genre': '',
        'year': year,
        'tags': [],
        'release_date': release_date,
        'artist_url': _artist_url(entity),
        'thumbnail': cover,
        'track_count': len(tracks),
        # The release's real length, which survives the user unticking tracks.
        # `track_count` shrinks with the selection, so tagging "5/3" would be
        # the alternative.
        'album_track_count': len(tracks),
        'total_duration': sum(t['duration'] for t in tracks),
        'tracks': tracks,
        'url': url,
        'formats': [],
        'needs_yt_match': True,
    }
    if truncated:
        meta['note'] = (
            f"Only the first {max_tracks} tracks are included — Spotify's page "
            f"reports {len(entity.get('trackList') or [])}."
        )
    return meta


def _enrich_album_date(meta: dict) -> None:
    """Fill in an album's release date from its first track.

    Album entities leave `releaseDate` null while their tracks carry it. Left
    unfilled, each downloaded file keeps whatever year its YouTube upload
    claimed — one album ending up as 2013 and 2023 at once, which some players
    treat as two different albums. One extra request settles it.
    """
    if meta.get('release_date') or not meta.get('tracks'):
        return
    uri = meta['tracks'][0].get('spotify_uri') or ''
    if not uri.startswith('spotify:track:'):
        return
    try:
        track = fetch_entity('track', uri.rsplit(':', 1)[-1])
    except Exception as e:
        app_logger.debug(f"Album date lookup failed: {e}")
        return
    release_date = _release_iso(track)
    if release_date:
        meta['release_date'] = release_date
        meta['year'] = release_date[:4]


def fetch_metadata(url: str) -> dict:
    """Spotify link → Median metadata dict. Raises RuntimeError on failure."""
    parsed = parse_spotify_url(url)
    if not parsed:
        raise RuntimeError("Not a Spotify link Median recognises.")
    kind, spotify_id = parsed

    if kind == 'artist':
        # An artist link has no tracklist of its own — it only makes sense
        # through the discography picker, which calls artist_discography().
        raise RuntimeError(
            "That's an artist link. Use \"Download every album by this artist\" "
            "to pick from their releases."
        )

    entity = fetch_entity(kind, spotify_id)
    meta = to_metadata(entity, canonical_url(kind, spotify_id))
    if kind == 'album':
        # A playlist spans many releases, so a single "album year" would be
        # meaningless there — only real albums get this.
        _enrich_album_date(meta)
    return meta


def artist_name(spotify_id: str) -> str:
    """The artist's name, the only thing an artist link usefully yields."""
    return (fetch_entity('artist', spotify_id).get('name') or '').strip()


# ── discography, via MusicBrainz ─────────────────────────────────────────────
#
# Spotify will not serve an artist's releases anonymously, so the album list
# comes from MusicBrainz. Its terms require a descriptive User-Agent and no
# more than one request per second from an anonymous client.

MB_BASE = 'https://musicbrainz.org/ws/2'
MB_UA = 'Median/1.0 ( https://github.com/IPurplel/Median )'

# Release groups Median should not offer as "albums by this artist" — the raw
# MusicBrainz list is full of bootlegs, live tapes and best-of compilations
# that nobody means when they say "their discography".
_SKIP_SECONDARY = {'live', 'compilation', 'demo', 'bootleg', 'interview',
                   'spokenword', 'audiobook', 'mixtape/street', 'dj-mix',
                   'remix'}

_mb_lock = threading.Lock()
_mb_last = [0.0]


def _mb_get(path: str, params: dict, attempts: int = 3) -> dict:
    """One rate-limited JSON MusicBrainz call, backing off when throttled.

    MusicBrainz answers 503 rather than queueing when a client is going too
    fast or the service is busy, and documents backing off as the correct
    response — so a single 503 is not a failure worth surfacing.
    """
    params = dict(params, fmt='json')
    url = f'{MB_BASE}/{path}?{urllib.parse.urlencode(params)}'

    last_error = None
    for attempt in range(attempts):
        with _mb_lock:
            wait = 1.05 - (time.monotonic() - _mb_last[0])
            if wait > 0:
                time.sleep(wait)
            try:
                req = urllib.request.Request(
                    url, headers={'User-Agent': MB_UA, 'Accept': 'application/json'}
                )
                with urllib.request.urlopen(req, timeout=25) as resp:
                    return json.loads(resp.read().decode('utf-8', 'replace'))
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code not in (429, 503):
                    raise
            finally:
                _mb_last[0] = time.monotonic()

        if attempt < attempts - 1:
            backoff = 2 ** attempt
            app_logger.debug(
                f"MusicBrainz busy ({last_error}) — retrying in {backoff}s"
            )
            time.sleep(backoff)

    raise RuntimeError(f"MusicBrainz is busy — try again shortly ({last_error})")


def _mb_artist_id(name: str) -> str:
    data = _mb_get('artist', {'query': f'artist:"{name}"', 'limit': 1})
    for artist in data.get('artists') or []:
        if artist.get('id'):
            return artist['id']
    return ''


def release_group_url(mbid: str) -> str:
    """The handle Median carries for a MusicBrainz-sourced album.

    A real, browsable URL rather than an invented scheme — it only ever enters
    Median through a Spotify artist link, so the download is still reported as
    a Spotify one, but the album itself is identified by MusicBrainz because
    Spotify won't say.
    """
    return f'https://musicbrainz.org/release-group/{mbid}'


_MB_RG_URL_RE = re.compile(
    r'^https?://(?:www\.)?musicbrainz\.org/release-group/'
    r'([0-9a-f-]{36})', re.IGNORECASE
)


def parse_release_group_url(url: str) -> str:
    m = _MB_RG_URL_RE.match((url or '').strip())
    return m.group(1) if m else ''


def artist_discography(name: str) -> list:
    """Albums and EPs by `name`, newest information first.

    Returns the same album dicts backend.discography builds for the other
    platforms, so the existing picker renders them unchanged.
    """
    if not name:
        return []

    artist_id = _mb_artist_id(name)
    if not artist_id:
        app_logger.info(f"MusicBrainz has no artist matching {name!r}")
        return []

    data = _mb_get('release-group', {
        'artist': artist_id,
        'type': 'album|ep',
        'limit': 100,
    })

    albums = []
    for group in data.get('release-groups') or []:
        secondary = {str(s).lower() for s in (group.get('secondary-types') or [])}
        if secondary & _SKIP_SECONDARY:
            continue
        title = (group.get('title') or '').strip()
        mbid = group.get('id')
        if not title or not mbid:
            continue
        albums.append({
            'url': release_group_url(mbid),
            'title': title,
            'title_is_guess': False,
            'thumbnail': '',
            'track_count': 0,
            'year': (group.get('first-release-date') or '')[:4],
        })

    # Oldest first reads like a discography; undated entries (bootlegs that
    # slipped the filter, unreleased material) sort last rather than first.
    albums.sort(key=lambda a: a['year'] or '9999')

    if len(albums) > settings.MAX_DISCOGRAPHY_ALBUMS:
        app_logger.warning(
            f"Discography truncated to {settings.MAX_DISCOGRAPHY_ALBUMS} albums"
        )
        albums = albums[:settings.MAX_DISCOGRAPHY_ALBUMS]
    return albums


def release_group_metadata(mbid: str, url: str) -> dict:
    """Tracklist for one MusicBrainz release group, shaped like an album.

    A release group bundles every edition of one album — the original, reissues,
    regional pressings, deluxe boxes. Asking for "Homework" should give the
    16-track album, not the 31-track 25th Anniversary Edition, so an edition
    whose title matches the group's own name wins over a longer one.
    """
    data = _mb_get('release', {
        'release-group': mbid,
        'inc': 'recordings+artist-credits+release-groups',
        'limit': 25,
    })
    releases = data.get('releases') or []
    if not releases:
        raise RuntimeError("MusicBrainz has no tracklist for that release.")

    def _track_total(release):
        return sum(len(m.get('tracks') or []) for m in release.get('media') or [])

    group_title = ''
    for release in releases:
        group_title = ((release.get('release-group') or {}).get('title') or '').strip()
        if group_title:
            break

    def _rank(release):
        title = (release.get('title') or '').strip()
        return (
            # An edition called exactly what the album is called is the album.
            title.lower() == group_title.lower(),
            (release.get('status') or '') == 'Official',
            _track_total(release),
        )

    playable = [r for r in releases if _track_total(r)]
    if not playable:
        raise RuntimeError("MusicBrainz has no tracklist for that release.")
    best = max(playable, key=_rank)

    artist = ''
    for credit in best.get('artist-credit') or []:
        artist = ((credit or {}).get('artist') or {}).get('name') or ''
        if artist:
            break

    tracks = []
    for medium in best.get('media') or []:
        for item in medium.get('tracks') or []:
            title = (item.get('title') or '').strip()
            if not title:
                continue
            tracks.append({
                'index': len(tracks) + 1,
                'title': title,
                'artist': artist,
                'duration': _ms_to_seconds(item.get('length')),
                'url': '',
                'thumbnail': '',
            })

    # An individual pressing often has no date recorded; the release group's
    # first-release-date is the album's actual year in that case.
    raw_date = (best.get('date')
                or (best.get('release-group') or {}).get('first-release-date')
                or '')
    release_date = ''.join(c for c in raw_date if c.isdigit())[:8]

    return {
        'is_playlist': True,
        'platform': PLATFORM,
        'title': (best.get('title') or '').strip() or 'Unknown Album',
        'artist': artist,
        'album': (best.get('title') or '').strip() or 'Unknown Album',
        'genre': '',
        'year': release_date[:4],
        'tags': [],
        'release_date': release_date if len(release_date) == 8 else '',
        'artist_url': '',
        'thumbnail': '',
        'track_count': len(tracks),
        'album_track_count': len(tracks),
        'total_duration': sum(t['duration'] for t in tracks),
        'tracks': tracks,
        'url': url,
        'formats': [],
        'needs_yt_match': True,
    }
