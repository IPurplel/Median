🎵 Median - The Ultimate Self-Hosted Music Downloader

Median is a sophisticated, self-hosted music management and acquisition system. It is designed for users who want a high-quality, organized, and automated music library without the hassle of manual editing or intrusive advertisements.

short brief 

Smart Downloads: High-fidelity audio (FLAC / 320kbps) from YouTube, Spotify, and bandcamp.
Zero Manual Editing: Auto-fetches cover art and metadata, embedding them directly into your tracks.
Pure Automation: "Watched Folders" detect and download links in the background automatically.
Instant Splitting: Automatically turns full-album videos into individual, tagged tracks using Chapters.
Docker-Ready: One-command setup with a smart auto-cleanup system to save your disk space.




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

Smart Auto-Cleanup System: Features a built-in scheduler that automatically purges temporary files and non-persistent downloads every 15 minutes to optimize storage space 

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





🐳 Median is designed to be Docker-native 




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


├── backend/                # FastAPI application logic
│   ├── utils/              # Helper functions (validators, file organizers)
│   ├── app.py              # Main API entry point & endpoints
│   ├── downloader.py       # Core download & yt-dlp logic
│   ├── metadata_handler.py # Metadata extraction & tagging
│   ├── db_models.py        # Database schema & models
│   ├── queue_manager.py    # Download queue logic
│   └── scheduler.py        # Background task scheduling
├── frontend/               # React source code for the web UI
├── watched/                # Directory for automated link monitoring
├── .env                    # Environment variables configuration
├── Dockerfile              # Multi-stage build for the entire stack
├── docker-compose.yml      # Deployment configuration
├── nginx.conf              # Nginx reverse proxy configuration
└── startup.sh              # Container startup script




🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.




Developed by IPurplel

It is worth noting that this project was developed entirely using AI as a proof of concept  However, for future projects, I plan to take a more hands-on development approach

