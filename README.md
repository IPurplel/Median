# 🎵 Median - The Ultimate Self-Hosted Music Downloader

> A sophisticated, self-hosted music management and acquisition system for users who want a high-quality, organized, and automated music library without hassle.

---

## ✨ Features

### 🛠 Core Functionality

- **Multi-Platform Support** — Download music from YouTube, Spotify, and thousands of other sites powered by yt-dlp
- **Professional Metadata Enrichment** — Automatically fetches and embeds high-resolution cover art, artist names, album titles, and release years via ID3 tags
- **Smart Folder Hierarchy** — Automatically organizes downloads into `/Artist/Album/Track.mp3` structure
- **Advanced Chapter Splitting** — Intelligently detects and splits full album videos into individual tracks based on timestamps

### 🤖 Automation & Intelligence

- **Watched Folders** — Monitor directories for text files containing links; drop a link and Median handles the rest
- **Queue Management** — Add hundreds of songs or entire playlists with live progress tracking
- **Scheduled Tasks** — Built-in scheduler for periodic maintenance and automated downloads

### 🎧 Audiophile Quality

- **High-Fidelity Formats** — Choose between lossless FLAC, ALAC, or high-bitrate MP3 (320kbps) and AAC
- **Professional Transcoding** — Powered by FFmpeg for industry-standard audio processing

### 🔒 Privacy & User Experience

- **Self-Hosted & Ad-Free** — No trackers, no data collection, zero ads
- **Modern Web Interface** — Clean, responsive React dashboard for managing your library and downloads

---

## 🚀 Quick Start with Docker (Recommended)

### Prerequisites

- Docker and Docker Compose installed

### Setup

1. **Create a `docker-compose.yml` file:**

```yaml
services:
  median:
    image: ipurplel/median:latest
    container_name: median
    ports:
      - "8080:80"
    volumes:
      - ./downloads:/app/downloads
      - ./watched:/app/watched
      - ./config:/app/config
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=UTC
    restart: unless-stopped