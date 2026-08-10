# 🎵 Median

> Self-hosted audio and video downloader. Paste a URL, pick a format, get your file.

Supports **YouTube**, **SoundCloud**, **Bandcamp**, and **Spotify**\* — runs entirely on your own machine with Docker.

<sub>\* Spotify's audio is DRM-protected and cannot be downloaded. Median reads the tracklist from a Spotify link and fetches the matching recordings from YouTube — see [Spotify](#-spotify).</sub>

---

## ✨ Features

| | |
|---|---|
| 🎵 **Audio** | MP3, FLAC, AAC · 128 / 192 / 256 / 320 kbps · or **Original**, which copies the source stream without re-encoding it (smaller files, no generation loss) |
| 🟢 **Spotify Links** | Paste a track, album, playlist or artist URL · the tracklist, cover art and exact durations come from Spotify, the audio from YouTube · files land properly tagged with real titles, artists, album and track numbers instead of `Song (Official Video)` · matches are picked by duration so an album version isn't swapped for the radio edit, and a shaky match is flagged rather than passed off · no account, API key or subscription needed |
| 🎬 **Video** | MP4, MKV, WebM |
| 🖼️ **Cover + Audio** | Album art looped as the video stream alongside the audio · MP4 / MKV / WebM · single tracks or full albums |
| 🏷️ **Tags & Cover Art** | Title, artist, album, year, genre, and cover embedded in every format that supports it (ID3 / Vorbis / MP4 atom / MKV attachment) · WebM can't embed art, so a sidecar cover + note is provided instead |
| 📀 **Playlists** | Full album downloads · optional single-file merge with chapter markers · track order and chapters always match the album |
| ☑️ **Pick Your Tracks** | Every track on an album is listed with a tick box — untick the songs you don't want and only the rest are downloaded · works for separate tracks and merged files alike · picked songs keep their original album track numbers |
| 🗂️ **Whole Discography** | Paste one album URL (or a Spotify artist link) and queue every album by that artist · pick which ones from a checklist · each album lands in its own folder with its own cover, tags and `description.md` · grab the lot afterwards as one zip named after the band, a folder per album · albums that failed are listed inside the archive rather than quietly missing · a batch is remembered by the server, so refreshing the page or coming back hours later still gets you the zip |
| 📥 **Sensible Delivery** | A single track, or an album merged into one file, downloads as plain playable audio — you only get a ZIP when there's genuinely more than one file to carry · large downloads stream to disk instead of being buffered in the browser, so they show real progress and can resume |
| 🔁 **Resilient Downloads** | Transient failures (expired URLs, dropped connections, `HTTP 416` from a stale resume) are retried once automatically on a clean slate · failures that a retry can't fix are recognised and not re-attempted · half-written `.part` files are deleted when a download fails or is cancelled, and a scheduled sweep clears any stragglers |
| 🎚️ **Crossfade** | Blend merged tracks into each other (0.5–12s, audio & video) · chapter times auto-corrected for the overlap · falls back to hard cuts with a visible note when not feasible |
| 📑 **Chapters & Description** | Chapter table on finished merges with one-click *Copy for YouTube description* · optional `description.md` bundled in the zip: tracklist, lyrics, source link, release date, hashtag tags, and artist credits · in a discography zip every album gets its own, inside its folder — written a second time if the first attempt fell over, since a generated file has no copy on disk to fall back on |
| 📝 **Lyrics** | Bandcamp lyrics fetched automatically and embedded in the media tags (ID3 USLT / ©lyr / Vorbis) per track — combined into one tag for merged albums — plus included in `description.md` |
| 🎨 **Custom Covers** | Upload your own image · 1:1, 4:3, 16:9, 9:16, Original ratio · 480p / 720p / 1080p / Original resolution |
| ⏱️ **Time Remaining** | Live countdown and transfer rate while a download runs · album estimates are weighted by track length, so a 30-second interlude doesn't skew them · shown only once there's enough evidence to be worth trusting, and never for a phase whose duration isn't knowable |
| 📋 **History** | Every download logged with size and format · re-download button while the file is still on the server |
| 🌈 **Themes** | Dark / light toggle plus six extra color themes cycled with one button |
| ⚠️ **Transparent Fallbacks** | Skipped tracks, crossfade fallbacks, and format limits surface as notes under the download — never silent |
| 📊 **Statistics** | Per-platform and per-artist download totals |
| 💾 **Backups** | One-click backup and restore |
| 🧹 **Auto-cleanup** | Files deleted after a configurable interval · mark files as "Keep" to exempt · discography batches are held until you collect the zip, then removed minutes later · a **Clean now** button frees everything immediately when the disk fills up, optionally including files Median has no record of, and always leaving in-progress downloads alone |
| 🔄 **Auto-update** | yt-dlp updates itself on every startup |
| 🔒 **Optional Auth** | Protect your instance with a bearer token |

---

## 🚀 Quick Start

### Requirements

- Docker and Docker Compose — that's it.

### 1. Clone

```bash
git clone https://github.com/IPurplel/Median.git
cd Median
```

### 2. Configure

```bash
cp .env.example .env
```

The defaults work out of the box. See [Configuration](#️-configuration) for all options.

### 3. Start

```bash
bash startup.sh
```

Or manually:

```bash
docker compose up -d
```

Open **http://localhost:8080** 🎉

---

## ⚙️ Configuration

All options live in `.env`. None are required — the defaults are production-ready.

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8080` | External port served by nginx |
| `UPLOAD_FOLDER` | `/app/downloads` | Where downloaded files are stored |
| `BACKUP_FOLDER` | `/app/backups` | Where backup ZIPs are stored |
| `DATABASE_PATH` | `/app/database/median.db` | SQLite database path |
| `CLEANUP_INTERVAL` | `90` | Minutes until completed downloads are auto-deleted. Doubles as the sweep interval, so real retention is 90–180 min |
| `MAX_CONCURRENT_DOWNLOADS` | `3` | Maximum parallel downloads |
| `MAX_DISCOGRAPHY_ALBUMS` | `100` | Maximum albums a single discography download may queue |
| `MAX_SPOTIFY_PLAYLIST_TRACKS` | `200` | Maximum tracks taken from one Spotify album or playlist |
| `SPOTIFY_MATCH_CONCURRENCY` | `4` | Parallel YouTube searches when resolving a Spotify tracklist |
| `YT_MATCH_RESULTS` | `5` | Search results scored per track when matching |
| `YT_MATCH_VERIFY` | `3` | Top candidates checked for availability before giving up on a track |
| `BATCH_HOLD_HOURS` | `3` | Hours a finished discography batch is kept from auto-cleanup while waiting to be collected |
| `BATCH_DELETE_MINUTES` | `3` | Minutes after the combined zip is downloaded before the server's album copies are deleted |
| `MEDIAN_API_TOKEN` | _(unset)_ | Bearer token for the API — empty means no auth required |
| `AUTO_UPDATE_INTERVAL` | `48` | Hours between scheduled yt-dlp updates |
| `HISTORY_RETENTION_DAYS` | `90` | Days to keep history entries (0 = keep forever) |
| `LOG_FORMAT` | `text` | `text` or `json` |

---

## 🔒 Authentication

Set `MEDIAN_API_TOKEN` in `.env` to require a bearer token on all mutating endpoints:

```bash
MEDIAN_API_TOKEN=your-secret-token
```

Requests that start, cancel, or delete downloads must include:

```
Authorization: Bearer your-secret-token
```

The UI handles this automatically when a token is configured.

---

## 🌐 Supported Platforms

| Platform | Audio | Video | Playlists |
|---|:---:|:---:|:---:|
| YouTube | ✅ | ✅ | ✅ |
| SoundCloud | ✅ | ➖ | ✅ |
| Bandcamp | ✅ | ➖ | ✅ |
| Spotify | ✅\* | ➖ | ✅ |

Any site supported by [yt-dlp](https://github.com/yt-dlp/yt-dlp) may work, but only the four above are tested and officially supported.

---

## 🟢 Spotify

**Spotify's audio cannot be downloaded.** It is Widevine-DRM encrypted, and no tool — Median included — can extract it.

What Median does instead is what tools like spotDL do: it reads the **metadata** from your Spotify link (song titles, artists, album, cover art, exact durations), searches YouTube for each of those recordings, and downloads them from there. **Spotify says what the music is; YouTube supplies the audio.**

**No account, API key or Premium subscription is required.** Median reads Spotify's public embed player, not the Web API — since February 2026 the official API's Development Mode requires a paid Premium subscription, so it is deliberately not used.

| Link | What happens |
|---|---|
| `/track/…` | One song, matched and downloaded |
| `/album/…` | Full tracklist, one folder, correct track numbers |
| `/playlist/…` | Same, tagged `Various Artists` when the artists differ |
| `/artist/…` | Feeds the discography picker — the album list comes from [MusicBrainz](https://musicbrainz.org), since Spotify won't serve a back catalogue without a paid key |

`spotify:album:…` URIs (what the desktop app copies) and `/intl-xx/` locale links both work.

**How matching works.** Candidates are scored on duration, title overlap and channel, and the top pick is confirmed to be downloadable before it's used — YouTube's search index lists plenty of region-locked and removed videos. Duration is weighted highest: asking for the 6:09 album version of a song won't get you the 4:09 radio edit even though the radio edit is the more "official"-looking result. `Artist - Topic` channels (the label's own uploads) are preferred, and live/cover/remix/nightcore uploads are penalised unless you asked for one.

**What to expect.** Audio quality is whatever YouTube serves — generally fine, but not Spotify's stream. Matching is a best guess: when Median isn't confident, or a song isn't on YouTube at all, it says so in a note under the download instead of quietly handing you the wrong take. Spotify-exclusive content can't be found at all.

---

## 📁 File Naming

Files follow the `Artist Name - Title.ext` convention. Playlists create a folder with numbered tracks:

```
Artist Name - Album Name/
  01 - Track One.mp3
  02 - Track Two.mp3
  03 - Track Three.mp3
```

Merged playlists produce a single file: `Artist Name - Album Name.mp3`

Picking only some tracks keeps their original album positions, so they still sort correctly against the full album — selecting tracks 2, 5 and 6 gives:

```
Artist Name - Album Name/
  002 - Track Two.mp3
  005 - Track Five.mp3
  006 - Track Six.mp3
```

A whole-discography download queues each album separately, so they stay organized side by side:

```
Artist Name - First Album/
  01 - Track One.mp3
  ...
Artist Name - Second Album/
  01 - Track One.mp3
  ...
```

---

## 🐳 Docker Details

The stack is two containers:

| Container | Role |
|---|---|
| `median` | FastAPI app on port 5000 (internal) |
| `median_nginx` | nginx reverse proxy, exposed on `$PORT` |

Data is stored in named Docker volumes that survive container restarts:

| Volume | Path | Contents |
|---|---|---|
| `median_downloads` | `/app/downloads` | Downloaded files |
| `median_backups` | `/app/backups` | Database backups |
| `median_database` | `/app/database` | SQLite database |
| `median_logs` | `/app/logs` | Application logs |


---

## 🔌 API

The full API is served at `/api/`. Key endpoints:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Server status, disk space, yt-dlp version |
| `GET` | `/api/platforms` | Platform reachability check |
| `POST` | `/api/validate` | Validate a URL and fetch metadata |
| `POST` | `/api/download` | Queue a download |
| `POST` | `/api/discography` | List every album by the artist behind a URL |
| `POST` | `/api/discography/download` | Queue one download per album, each in its own folder |
| `GET` | `/api/discography/batches` | Batches still running or waiting to be collected |
| `GET` | `/api/discography/batch/{id}` | Progress of one discography batch |
| `GET` | `/api/discography/batch/{id}/file` | Every finished album of a batch as one streamed zip |
| `GET` | `/api/download/{id}/status` | Poll download status |
| `GET` | `/api/downloads/status?ids=` | Poll many downloads in one request (used by discography batches) |
| `GET` | `/api/download/{id}/events` | Server-sent events stream for live progress |
| `POST` | `/api/download/{id}/keep` | Mark a file to skip auto-cleanup |
| `GET` | `/api/cleanup/preview` | How many files and how much space a manual cleanup would free |
| `POST` | `/api/cleanup/now` | Delete every finished download immediately (skips anything still downloading) · `?include_orphans=true` also removes files Median has no record of |
| `DELETE` | `/api/download/{id}` | Cancel a download |
| `GET` | `/api/download/{id}/file` | Download the finished result — plain audio for a single file, ZIP when there's more than one |
| `GET` | `/api/download/{id}/chapters` | Chapter list of a merged file with YouTube-ready timestamps |
| `GET` | `/api/download/{id}/description.md` | Markdown description (tracklist, source, tags, credits) |
| `GET` | `/api/queue` | Active and queued downloads |
| `GET` | `/api/history` | Completed download history |
| `GET` | `/api/statistics` | Download statistics |
| `POST` | `/api/backup` | Create a database backup |
| `GET` | `/api/backup/{id}/download` | Download a backup |
| `POST` | `/api/cover/upload` | Upload a custom cover image |
| `POST` | `/api/cover/preview` | Preview a cover with settings applied |

### Example

```bash
curl -X POST http://localhost:8080/api/download \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://soundcloud.com/artist/track",
    "download_type": "audio",
    "format": "mp3",
    "bitrate": "320"
  }'
```

---

## 🛠️ Local Development

No Docker needed for local development. You'll need **Python 3.11+** and **FFmpeg** installed on your system.

```bash
# Install dependencies
pip install -r requirements.txt

# Configure paths
export UPLOAD_FOLDER=./downloads
export BACKUP_FOLDER=./backups
export DATABASE_PATH=./median.db
export LOG_FOLDER=./logs

# Start with auto-reload
uvicorn backend.app:app --reload --port 5000
```

The frontend is served directly by FastAPI — no build step needed.

---

## 📦 Built With

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — media extraction
- [FFmpeg](https://ffmpeg.org/) — audio/video processing and cover art merging
- [FastAPI](https://fastapi.tiangolo.com/) — web framework
- [APScheduler](https://apscheduler.readthedocs.io/) — background jobs
- [Pillow](https://python-pillow.org/) — cover image processing
- [mutagen](https://mutagen.readthedocs.io/) — audio metadata tags

---

## 📄 License

MIT
