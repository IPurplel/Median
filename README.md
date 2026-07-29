# 🎵 Median

> Self-hosted audio and video downloader. Paste a URL, pick a format, get your file.

Supports **YouTube**, **SoundCloud**, and **Bandcamp** — runs entirely on your own machine with Docker.

---

## ✨ Features

| | |
|---|---|
| 🎵 **Audio** | MP3, FLAC, AAC · 128 / 192 / 256 / 320 kbps |
| 🎬 **Video** | MP4, MKV, WebM |
| 🖼️ **Cover + Audio** | Album art looped as the video stream alongside the audio · MP4 / MKV / WebM · single tracks or full albums |
| 🏷️ **Tags & Cover Art** | Title, artist, album, year, genre, and cover embedded in every format that supports it (ID3 / Vorbis / MP4 atom / MKV attachment) · WebM can't embed art, so a sidecar cover + note is provided instead |
| 📀 **Playlists** | Full album downloads · optional single-file merge with chapter markers · track order and chapters always match the album |
| ☑️ **Pick Your Tracks** | Every track on an album is listed with a tick box — untick the songs you don't want and only the rest are downloaded · works for separate tracks and merged files alike · picked songs keep their original album track numbers |
| 🗂️ **Whole Discography** | Paste one album URL and queue every album by that artist · pick which ones from a checklist · each album lands in its own folder with its own cover, tags and `description.md` · grab the lot afterwards as one zip, a folder per album |
| 🎚️ **Crossfade** | Blend merged tracks into each other (0.5–12s, audio & video) · chapter times auto-corrected for the overlap · falls back to hard cuts with a visible note when not feasible |
| 📑 **Chapters & Description** | Chapter table on finished merges with one-click *Copy for YouTube description* · optional `description.md` bundled in the zip: tracklist, lyrics, source link, release date, hashtag tags, and artist credits |
| 📝 **Lyrics** | Bandcamp lyrics fetched automatically and embedded in the media tags (ID3 USLT / ©lyr / Vorbis) per track — combined into one tag for merged albums — plus included in `description.md` |
| 🎨 **Custom Covers** | Upload your own image · 1:1, 4:3, 16:9, 9:16, Original ratio · 480p / 720p / 1080p / Original resolution |
| 📋 **History** | Every download logged with size and format · re-download button while the file is still on the server |
| 🌈 **Themes** | Dark / light toggle plus six extra color themes cycled with one button |
| ⚠️ **Transparent Fallbacks** | Skipped tracks, crossfade fallbacks, and format limits surface as notes under the download — never silent |
| 📊 **Statistics** | Per-platform and per-artist download totals |
| 💾 **Backups** | One-click backup and restore |
| 🧹 **Auto-cleanup** | Files deleted after a configurable interval · mark files as "Keep" to exempt |
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

Any site supported by [yt-dlp](https://github.com/yt-dlp/yt-dlp) may work, but only the three above are tested and officially supported.

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
| `GET` | `/api/discography/batch/{id}` | Progress of one discography batch |
| `GET` | `/api/discography/batch/{id}/file` | Every finished album of a batch as one streamed zip |
| `GET` | `/api/download/{id}/status` | Poll download status |
| `GET` | `/api/downloads/status?ids=` | Poll many downloads in one request (used by discography batches) |
| `GET` | `/api/download/{id}/events` | Server-sent events stream for live progress |
| `POST` | `/api/download/{id}/keep` | Mark a file to skip auto-cleanup |
| `DELETE` | `/api/download/{id}` | Cancel a download |
| `GET` | `/api/download/{id}/file` | Download the completed file as a ZIP |
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
