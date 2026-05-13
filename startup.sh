#!/usr/bin/env bash
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'
DIM='\033[2m'

step() { echo -e "${CYAN}  ->${RESET} $1"; }
ok()   { echo -e "${GREEN}  v${RESET} $1"; }
warn() { echo -e "${YELLOW}  !${RESET} $1"; }
fail() { echo -e "${RED}  x${RESET} $1"; }
info() { echo -e "${DIM}    $1${RESET}"; }

echo ""
echo -e "${BOLD}┌─────────────────────────────────────────┐${RESET}"
echo -e "${BOLD}│        MEDIAN v2  — Audio Downloader   │${RESET}"
echo -e "${BOLD}└─────────────────────────────────────────┘${RESET}"
echo ""

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

step "Building Docker images (first run may take 2-3 minutes)..."
if docker compose build --quiet 2>/dev/null || docker-compose build --quiet 2>/dev/null; then
  ok "Docker images ready"
else
  fail "Build failed. Check logs above."
  exit 1
fi

step "Starting Median containers..."
if docker compose up -d 2>/dev/null || docker-compose up -d 2>/dev/null; then
  ok "Containers started"
else
  fail "Failed to start containers."
  exit 1
fi

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

LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
PORT_NUM=${PORT:-8080}

echo ""
echo -e "${BOLD}${GREEN}  Median v2 is running!${RESET}"
echo ""
echo -e "  ${BOLD}Local:${RESET}  http://localhost:${PORT_NUM}"
echo -e "  ${BOLD}LAN:${RESET}    http://${LAN_IP}:${PORT_NUM}"
echo ""
echo -e "  ${DIM}Logs:           docker compose logs -f median${RESET}"
echo -e "  ${DIM}Stop:           docker compose down${RESET}"
echo ""
CLEANUP_MINS=$(grep -E '^CLEANUP_INTERVAL=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ' || echo 15)
echo -e "${DIM}  Files are auto-cleaned after ${CLEANUP_MINS:-15} minutes. Use 'Keep' to preserve them.${RESET}"
echo ""
