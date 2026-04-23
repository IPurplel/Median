🎵 Median - The Ultimate Self-Hosted Music Downloader

Median is a sophisticated, self-hosted music management and acquisition system. It is designed for users who want a high-quality, organized, and automated music library without the hassle of manual editing or intrusive advertisements.




🚀 Key Features

🛠 Core Functionality

•
Multi-Platform Support: Download music from YouTube, Spotify, and thousands of other sites supported by yt-dlp.

•
Professional Metadata Enrichment: Automatically fetches and embeds high-resolution cover art, artist names, album titles, and release years directly into the file's ID3 tags.

•
Smart Folder Hierarchy: Automatically organizes downloads into a structured directory (e.g., /Artist/Album/Track.mp3).

•
Advanced Chapter Splitting: Automatically detects and splits "Full Album" videos into individual tracks based on timestamps.

🤖 Automation & Intelligence

•
Watched Folders: Monitor specific folders for text files containing links. Drop a link, and Median downloads it automatically in the background.

•
Queue Management: Add hundreds of songs or entire playlists to a download queue with live progress tracking.

•
Scheduled Tasks: Built-in scheduler for periodic maintenance and automated downloads.

🎧 Audiophile Quality

•
High-Fidelity Formats: Choose between lossless FLAC, ALAC, or high-bitrate MP3 (320kbps) and AAC.

•
Professional Transcoding: Powered by FFmpeg for industry-standard audio processing.

🔒 Privacy & UI

•
Self-Hosted & Ad-Free: No trackers, no data collection, and zero ads.

•
Modern Web Interface: A clean, responsive dashboard built with React for managing your library and downloads.




🐳 Docker Deployment (Recommended)

Median is designed to be Docker-native for easy setup and isolation.

1. Prerequisites

•
Docker and Docker Compose installed on your system.

2. Setup

Create a docker-compose.yml file:

YAML


services:
  median:
    image: ipurplel/median:latest # Replace with your actual image tag if available
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



3. Launch

Bash


docker-compose up -d



Access the UI at http://localhost:8080.




🛠 Technical Architecture

•
Backend: FastAPI (Python 3.11+ ) - High-performance asynchronous API.

•
Frontend: React - Modern and responsive user interface.

•
Processing Engine: yt-dlp for acquisition and FFmpeg for audio manipulation.

•
Database: SQLite for lightweight and portable data management.

•
Reverse Proxy: Nginx (included in Docker) for serving the frontend and proxying API requests.




📂 Project Structure

Plain Text


├── backend/            # FastAPI application logic
│   ├── utils/          # Helper functions (validators, file organizers)
│   ├── downloader.py   # Core download & yt-dlp logic
│   ├── metadata.py     # Metadata extraction & tagging
│   └── app.py          # API Endpoints
├── frontend/           # React source code
├── watched/            # Default directory for automated link monitoring
├── Dockerfile          # Multi-stage build for the entire stack
└── docker-compose.yml  # Deployment configuration






🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1.
Fork the Project

2.
Create your Feature Branch (git checkout -b feature/AmazingFeature)

3.
Commit your Changes (git commit -m 'Add some AmazingFeature')

4.
Push to the Branch (git push origin feature/AmazingFeature)

5.
Open a Pull Request




📜 License

Distributed under the MIT License. See LICENSE for more information.




Developed with ❤️ by IPurplel

