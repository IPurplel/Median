#!/usr/bin/env bash
# ── Median Startup Script ─────────────────────────────────────────────────────
set -euo pipefail

# Colors
BOLD='\033[1m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'
DIM='\033[2m'

step() { echo -e "${CYAN}  →${RESET} $1"; }
ok()   { echo -e "${GREEN}  ✓${RESET} $1"; }
warn() { echo -e "${YELLOW}  ⚠${RESET} $1"; }
fail() { echo -e "${RED}  ✗${RESET} $1"; }
info() { echo -e "${DIM}    $1${RESET}"; }

echo ""
echo -e "${BOLD}┌─────────────────────────────────────────┐${RESET}"
echo -e "${BOLD}│        MEDIAN  — Audio Downloader       │${RESET}"
echo -e "${BOLD}└─────────────────────────────────────────┘${RESET}"
echo ""

# ── Step 1: Docker ────────────────────────────────────────────────────────────
step "Checking Docker installation..."
if ! command -v docker &>/dev/null; then
  fail "Docker not found. Install from https://docker.com and try again."
  exit 1
fi
DOCKER_VERSION=$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')
ok "Docker found (${DOCKER_VERSION})"

if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
  fail "Docker Compose not found. Install Docker Desktop or docker-compose."
  exit 1
fi
ok "Docker Compose found"

# ── Step 2: Environment ───────────────────────────────────────────────────────
step "Checking environment configuration..."
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    warn "Created .env from .env.example (default settings)"
  else
    fail ".env file missing. Create one from .env.example"
    exit 1
  fi
else
  ok ".env found"
fi

# ── Step 3: Watched folder ────────────────────────────────────────────────────
step "Checking watched folder..."
mkdir -p watched
if [ ! -f watched/watched_urls.txt ]; then
  echo "# Add URLs here — one per line. Median will download them automatically." \
    > watched/watched_urls.txt
  ok "Created watched/watched_urls.txt"
else
  ok "watched/watched_urls.txt exists"
fi

# ── Step 4: Build / pull images ───────────────────────────────────────────────
step "Building Docker images (first run may take 2-3 minutes)..."
if docker compose build --quiet 2>/dev/null || docker-compose build --quiet 2>/dev/null; then
  ok "Docker images ready"
else
  fail "Build failed. Check logs above."
  exit 1
fi

# ── Step 5: Start containers ──────────────────────────────────────────────────
step "Starting Median containers..."
if docker compose up -d 2>/dev/null || docker-compose up -d 2>/dev/null; then
  ok "Containers started"
else
  fail "Failed to start containers."
  exit 1
fi

# ── Step 6: Wait for health ───────────────────────────────────────────────────
step "Waiting for Median to be ready..."
TIMEOUT=60
ELAPSED=0
until curl -sf http://localhost:${PORT:-8080}/api/health >/dev/null 2>&1; do
  if [ $ELAPSED -ge $TIMEOUT ]; then
    fail "Timed out waiting for Median to start."
    info "Check logs: docker compose logs median"
    exit 1
  fi
  sleep 2
  ELAPSED=$((ELAPSED + 2))
  echo -ne "\r    Waiting... ${ELAPSED}s"
done
echo ""
ok "Median is healthy"

# ── Step 7: Get LAN IP ────────────────────────────────────────────────────────
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
PORT_NUM=${PORT:-8080}

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}  ✦ Median is running!${RESET}"
echo ""
echo -e "  ${BOLD}Local:${RESET}  http://localhost:${PORT_NUM}"
echo -e "  ${BOLD}LAN:${RESET}    http://${LAN_IP}:${PORT_NUM}"
echo ""
echo -e "  ${DIM}Watched folder: $(pwd)/watched/watched_urls.txt${RESET}"
echo -e "  ${DIM}Logs:           docker compose logs -f median${RESET}"
echo -e "  ${DIM}Stop:           docker compose down${RESET}"
echo ""
echo -e "${DIM}  Files are auto-cleaned after 15 minutes. Use 'Keep' to preserve them.${RESET}"
echo ""
