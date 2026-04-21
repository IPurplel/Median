# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-alpine AS builder

RUN apk add --no-cache gcc musl-dev libffi-dev

WORKDIR /build

COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-alpine

LABEL maintainer="Median Audio Downloader"
LABEL description="Self-hosted audio downloader for YouTube, SoundCloud, Bandcamp"

# System deps
RUN apk add --no-cache \
    ffmpeg \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/cache/apk/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# App directory
WORKDIR /app

# Copy application files
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Create runtime directories
RUN mkdir -p \
    /app/downloads \
    /app/backups \
    /app/watched \
    /app/logs \
    /app/database

# Create watched_urls.txt placeholder
RUN echo "# Add one URL per line. Median will auto-download them." \
    > /app/watched/watched_urls.txt

# Non-root user
RUN addgroup -S median && adduser -S median -G median
RUN chown -R median:median /app
USER median

EXPOSE 5000

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "5000", \
     "--workers", "1", "--loop", "asyncio", "--log-level", "info"]
