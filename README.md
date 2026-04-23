# Median — The Ultimate Self-Hosted Music Downloader

Median is a modern, self-hosted music management and acquisition system for audio enthusiasts, power users, and anyone seeking an automated, high-quality music library—free from manual editing and intrusive ads.

---

## 🚀 At a Glance

- **Smart Downloads:** Fetch high-fidelity audio (FLAC / 320kbps) from YouTube, Spotify, Bandcamp, and more.
- **Full Automation:** "Watched Folders" detect and auto-download links in the background—set and forget!
- **Zero Manual Editing:** Metadata and cover art are fetched and embedded automatically.
- **Instant Splitting:** Full-album videos are split into tagged tracks using chapter info—no manual slicing!
- **Docker-Ready:** One-command setup with automatic cleanups to keep disk usage efficient.

---

## ✨ Key Features

### 🛠 Core Functionality

- **Multi-Platform Downloads:** Download music from YouTube, Spotify, and thousands of other sites (thanks to `yt-dlp`).
- **Professional Metadata:** Auto-embeds high-res covers, artist, album, and release info into your music files.
- **Smart Library Organization:** Files are structured as `/Artist/Album/Track.mp3` for easy browsing.
- **Chapter Splitting:** Detects album-length videos and splits them into tracks based on timestamps.

### 🤖 Automation & Intelligence

- **Watched Folders:** Drop a text file with links into a folder—Median grabs and downloads in the background.
- **Queue Management:** Add songs, albums, or playlists to a queue. Monitor status with live progress tracking.
- **Scheduled Tasks:** Built-in scheduler for regular maintenance and automated downloads.
- **Auto-Cleanup:** Automatically purges temp and non-persistent files every 15 minutes to keep your storage tidy.

### 🎧 Audiophile Quality

- **Lossless & High-Bitrate:** Choose FLAC, ALAC, 320kbps MP3, or AAC—powered by FFmpeg.
- **Professional Transcoding:** Enjoy industry-leading audio quality.

### 🔒 Privacy & UX

- **Self-Hosted, Ad-Free:** No trackers, no data collection, zero ads—your library, your rules.
- **Modern Web UI:** Manage everything with a beautiful React dashboard.

---

## 🏗 Technical Architecture

- **Backend:** FastAPI (Python 3.11+) for blazing-fast, async API logic.
- **Frontend:** React—responsive, intuitive web interface.
- **Audio Engine:** `yt-dlp` for source acquisition; FFmpeg for processing.
- **Database:** SQLite for simple, portable storage.
- **Reverse Proxy:** Nginx (included in Docker) for serving frontend and proxying API.

---

## 📂 Project Structure

```
├── backend/            # FastAPI backend
│   ├── utils/          # Utility functions
│   ├── app.py          # API entry point & routes
│   ├── downloader.py   # Download engine (yt-dlp logic)
│   ├── metadata_handler.py # Metadata extraction & tagging
│   ├── db_models.py    # Database models/schema
│   ├── queue_manager.py # Download queue logic
│   └── scheduler.py    # Task scheduler 
├── frontend/           # React app (web UI)
├── watched/            # Folder for automated link monitoring
├── .env                # Environment configuration
├── Dockerfile          # Multistage Docker build
├── docker-compose.yml  # Deployment configuration
├── nginx.conf          # Nginx reverse proxy config
└── startup.sh          # Container startup script
```

---

## 🐳 Docker-Native

Median is built for easy deployment via Docker and Docker Compose — ideal for servers, Raspberry Pi, or your NAS.

---

## 🤝 Contributing

Contributions are welcome! Feel free to submit a Pull Request.

---

> _Median was developed entirely using AI as a proof of concept. Future projects may take a more hands-on approach._
>
> **Developed by [IPurplel](https://github.com/IPurplel)**