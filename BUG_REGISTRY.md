# 🐞 Median Bug Registry

**Repository:** `IPurplel/Median`  
**Audited branch:** `main`  
**Audited commit:** `bf343b8f41a7000f0861e026629e4125a08c3a0d`  
**Audit date:** `2026-10-07`  
**Verification date:** `2026-10-07`  
**Total bugs:** 22  
**Status:** 22 original IDs fixed (10 `Verified`, 12 `Fixed`) plus 9 new IDs found in end-to-end testing; see [Fix Log](#fix-log--2026-10-08) and [End-to-End Pass](#end-to-end-pass--2026-10-08)

---

## Registry Rules

- Every bug gets a permanent ID. IDs are never recycled, renamed, or reassigned.
- A bug stays in this register after it is fixed. Only its status changes.
- Normal lifecycle: `Confirmed` -> `Fixing` -> `Fixed` -> `Verified` -> `Merged`
- If a verified bug returns, keep the same ID and set its status to `Reopened`.
- A bug is not marked `Verified` until the fix is tested against the real affected flow.
- When a fix is made, record the commit and/or PR in the bug section.

---

## Bug Summary Table

| Bug ID | Severity | Priority | Area | Title | Status | Fix Plan |
|---|---|---|---|---|---|---|
| `CORE-001` | High | P1 | YouTube runtime / Docker | Container is missing the JavaScript challenge stack required for reliable current YouTube extraction | `Fixed` | [Independent verification](#independent-verification-pass--2026-10-07) |
| `BE-001` | High | P1 | Spotify -> YouTube matching | YouTube extractor failures are misreported as "song not found" | `Fixed` | [P1 Fix Plan](#p1-high-priority-fix-plans) |
| `CORE-002` | High | P1 | Dependency lifecycle | yt-dlp update logic does not match the way yt-dlp is installed in the container | `Verified` | [P1 Fix Plan](#p1-high-priority-fix-plans) |
| `BE-002` | High | P1 | Spotify album download / concatenate | Concatenated Spotify downloads bypass the normal retry and fallback-source path | `Fixed` | [P1 Fix Plan](#p1-high-priority-fix-plans) |
| `FE-001` | High | P1 | Frontend / Authentication | Enabling `MEDIAN_API_TOKEN` breaks protected UI actions because the frontend never sends the bearer token | `Verified` | [P1 Fix Plan](#p1-high-priority-fix-plans) |
| `SEC-001` | High | P1 | API Authentication | Several mutating endpoints are not protected by the configured API token | `Verified` | [P1 Fix Plan](#p1-high-priority-fix-plans) |
| `SEC-002` | High | P1 | Rate limiting / Proxy trust | Rate limiting can be bypassed by spoofing `X-Forwarded-For` | `Fixed` | [P1 Fix Plan](#p1-high-priority-fix-plans) |
| `SEC-003` | High | P1 | Cover uploads / Resource limits | Cover upload size is checked only after the whole request body is read | `Fixed` | [P1 Fix Plan](#p1-high-priority-fix-plans) |
| `BE-003` | Critical | P0 | Download storage / Data integrity | Separate-track downloads of the same album reuse the same folder and can collide | `Verified` | [P0 Fix Plan](#p0-fix-first-critical) |
| `BE-004` | High | P1 | Spotify fallback / Partial files | Fallback YouTube sources can reuse partial files created by a different source | `Fixed` | [P1 Fix Plan](#p1-high-priority-fix-plans) |
| `BE-005` | High | P1 | YouTube video format selection | MP4 selector asks for `bestaudio[ext=mp4]` instead of M4A and can fall back to lower-quality combined formats | `Verified` | [P1 Fix Plan](#p1-high-priority-fix-plans) |
| `CORE-003` | High | P1 | Cancellation / Executor lifecycle | Cancelling a download does not stop the running yt-dlp executor thread | `Verified` | [P1 Fix Plan](#p1-high-priority-fix-plans) |

| `BE-006` | Critical | P0 | Video concatenation | WebM merge path uses H.264 + AAC inside a `.webm` output | `Verified` | [P0 Fix Plan](#p0-fix-first-critical) |
| `BE-007` | Medium | P2 | Video concatenation / Chapters | Merged video playlists do not get chapter markers | `Verified` | [P2 Fix Plan](#p2-medium-priority-fix-plans) |
| `BE-008` | Medium | P2 | Audio concatenation / Chapters | Chapter embedding failure can be ignored while the download is still marked successful | `Fixed` | [P2 Fix Plan](#p2-medium-priority-fix-plans) |
| `FE-002` | Medium | P2 | Frontend / Error reporting | Real download error text is lost while the download state is still in memory | `Fixed` | [P2 Fix Plan](#p2-medium-priority-fix-plans) |
| `CORE-004` | Medium | P2 | Startup script | `startup.sh` does not read `PORT` from `.env` before probing the health endpoint | `Fixed` | [P2 Fix Plan](#p2-medium-priority-fix-plans) |
| `BE-009` | Medium | P2 | Backup system | Two backups created in the same second can share one path | `Fixed` | [P2 Fix Plan](#p2-medium-priority-fix-plans) |
| `BE-011` | Medium | P2 | Custom covers | Uploaded custom covers have no cleanup, TTL, quota, or delete path | `Verified` | [P2 Fix Plan](#p2-medium-priority-fix-plans) |
| `API-001` | Medium | P2 | Request validation | API accepts incompatible `download_type` + `format` combinations | `Fixed` | [P2 Fix Plan](#p2-medium-priority-fix-plans) |
| `CORE-005` | Low | P3 | Startup script | Cleanup interval display includes the inline .env comment | `Fixed` | [P3 Fix Plan](#p3-low-priority-fix-plans) |
| `BE-010` | Low | P3 | Backup API | Backup selection parameter is accepted but ignored | `Verified` | [P3 Fix Plan](#p3-low-priority-fix-plans) |


| `BE-011` | Medium | P2 | Custom covers | Uploaded custom covers have no cleanup, TTL, quota, or delete path | `Verified` | [P2 Fix Plan](#p2-medium-priority-fix-plans) |
| `API-001` | Medium | P2 | Request validation | API accepts incompatible `download_type` + `format` combinations | `Fixed` | [P2 Fix Plan](#p2-medium-priority-fix-plans) |

| Bug ID | Severity | Priority | Area | Title | Status |
|---|---|---|---|---|---|
| `CORE-001` | Critical | P0 | YouTube runtime / Docker | Container is missing the JavaScript challenge stack required for reliable current YouTube extraction | `Fixed` |
| `BE-001` | High | P1 | Spotify -> YouTube matching | YouTube extractor failures are misreported as "song not found" | `Fixed` |
| `CORE-002` | High | P1 | Dependency lifecycle | yt-dlp update logic does not match the way yt-dlp is installed in the container | `Verified` |
| `BE-002` | High | P1 | Spotify album download / concatenate | Concatenated Spotify downloads bypass the normal retry and fallback-source path | `Fixed` |
| `FE-001` | High | P1 | Frontend / Authentication | Enabling `MEDIAN_API_TOKEN` breaks protected UI actions because the frontend never sends the bearer token | `Verified` |
| `SEC-001` | High | P1 | API Authentication | Several mutating endpoints are not protected by the configured API token | `Verified` |
| `SEC-002` | High | P1 | Rate limiting / Proxy trust | Rate limiting can be bypassed by spoofing `X-Forwarded-For` | `Fixed` |
| `SEC-003` | High | P1 | Cover uploads / Resource limits | Cover upload size is checked only after the whole request body is read | `Fixed` |
| `BE-003` | Critical | P0 | Download storage / Data integrity | Separate-track downloads of the same album reuse the same folder and can collide | `Verified` |
| `BE-004` | High | P1 | Spotify fallback / Partial files | Fallback YouTube sources can reuse partial files created by a different source | `Fixed` |
| `BE-005` | High | P1 | YouTube video format selection | MP4 selector asks for `bestaudio[ext=mp4]` instead of M4A and can fall back to lower-quality combined formats | `Verified` |
| `BE-006` | Critical | P0 | Video concatenation | WebM merge path uses H.264 + AAC inside a `.webm` output | `Verified` |
| `BE-007` | Medium | P2 | Video concatenation / Chapters | Merged video playlists do not get chapter markers | `Verified` |
| `BE-008` | Medium | P2 | Audio concatenation / Chapters | Chapter embedding failure can be ignored while the download is still marked successful | `Fixed` |
---

## P0 — Fix First (Critical)

| Bug | Description | Impact | Suggested Fix |
|---|---|---|---|
| `BE-003` | Separate-track downloads of the same album reuse the same deterministic folder, causing collisions | File mixing, concurrent writes, wrong cleanup | Implement `ensure_unique_path()` in `file_organizer.py`, reserve unique path before creation |
| `BE-006` | WebM merge path uses H.264 + AAC inside a `.webm` container | FFmpeg merge failure, invalid WebM output | Branch codec selection: WebM = VP8/VP9 + Opus, MP4 = H.264 + AAC, MKV = compatible streams |

`CORE-001` was independently verified as `PARTIALLY CONFIRMED` and moved to
High/P1; see the verification appendix.

---

## P1 — High Priority Fix Plans

### BE-001 — YouTube extractor failures misreported as "song not found"

**Primary file:** `backend/utils/yt_match.py`
**Related:** `backend/queue_manager.py`

**Current behavior:** `_is_downloadable()` catches generic exceptions and returns `False`. The Spotify layer receives only a failed match and converts it to "couldn't find on YouTube".

**Fix:** Return a classified result instead of boolean.

### CORE-002 — yt-dlp update logic does not match installation model

**Primary files:** `backend/app.py`, `backend/scheduler.py`
**Related:** `Dockerfile`, `requirements.txt`

**Current behavior:** Two conflicting update strategies: `yt-dlp -U` on startup and `pip install --upgrade yt-dlp` in scheduler.

**Fix:** Prefer deterministic image-based updates.

### BE-002 — Concatenated Spotify downloads bypass retry and fallback

**Primary file:** `backend/downloader.py`
**Related:** `backend/tests/test_spotify_download.py`

**Current behavior:** Normal path uses `_fetch_with_fallback()` with primary + alternatives. Concatenate path calls `ydl.download([track_url])` directly.

**Fix:** Use the same `_fetch_with_fallback()` primitive in both paths. Add regression coverage for `concatenate=True`.

### FE-001 — Enabling MEDIAN_API_TOKEN breaks protected UI actions

**Primary files:** `frontend/app.js`, `backend/app.py`, `README.md`

**Current behavior:** Frontend API helper only sends `Content-Type` header. No `Authorization` header.

**Fix:** Add secure UI authentication flow. Ensure API helper attaches `Authorization: Bearer <token>`.

### SEC-001 — Several mutating endpoints not protected by API token

**Primary file:** `backend/app.py`

**Current behavior:** Authentication is attached manually per-route rather than through a consistent router/policy layer.

**Affected endpoints:** `DELETE /api/download/{download_id}`, `POST /api/download/{download_id}/keep`, `POST /api/cover/upload`, `POST /api/cover/preview`

**Fix:** Group mutating routes under a router-level authentication dependency. Add tests enumerating every state-changing endpoint.

### SEC-002 — Rate limiting bypass via X-Forwarded-For spoofing

**Primary files:** `backend/app.py`, `nginx.conf`

**Current behavior:** Backend trusts first address in `X-Forwarded-For`. nginx appends real client IP to existing header.

**Fix:** Trust only the immediate reverse proxy. Derive client address from trusted proxy configuration. Add middleware to whitelist trusted proxy IPs.

### SEC-003 — Cover upload size checked after full read

**Primary files:** `backend/app.py`, `nginx.conf`

**Current behavior:** `data = await file.read()` before checking `len(data) > settings.max_upload_size_bytes`.

**Fix:**
1. Enforce reverse-proxy body-size limit (`client_max_body_size` in nginx)
2. Stream/limit application reads
3. Reject based on `Content-Length` when reliable, while still enforcing a streamed hard limit

### BE-004 — Fallback YouTube sources can reuse partial files

**Primary file:** `backend/downloader.py`

**Current behavior:** `_fetch_with_fallback()` reuses same `ydl_opts`, `outtmpl`, same temp stem for different candidate URLs.

**Fix:** Clear source-specific partials before switching to different candidate URL, or assign a unique temporary stem per candidate.

### BE-005 — MP4 selector uses wrong audio extension

**Primary file:** `backend/utils/ydl_opts_builder.py`

**Current behavior:** `bestaudio[ext=mp4]` instead of `bestaudio[ext=m4a]`.

**Fix:** Use `bestvideo[ext=mp4]+bestaudio[ext=m4a]` with robust fallback chain.

### CORE-003 — Cancelling download does not stop yt-dlp worker thread

**Primary files:** `backend/queue_manager.py`, `backend/downloader.py`

**Current behavior:** Cancellation applied only to asyncio task. Blocking yt-dlp in executor thread has no cooperative cancellation.

**Fix:** Implement cooperative cancellation through yt-dlp progress hooks or isolate yt-dlp in a process that can be terminated safely.
## P2 — Medium Priority Fix Plans

### BE-007 — Merged video playlists do not get chapter markers

**Primary files:** `backend/concatenation_engine.py`, `frontend/app.js`, `README.md`

**Current behavior:** Audio and cover+audio merge paths embed chapters, but normal video concatenation does not.

**Fix:** Pass track metadata into video concatenation and embed chapter metadata after merge.

### BE-008 — Chapter embedding failure silently ignored

**Primary files:** `backend/concatenation_engine.py`, `backend/ffmpeg_chapters_handler.py`

**Current behavior:** `add_chapters_to_file()` can return `False`, but caller ignores return value.

**Fix:** Check the return value and emit a warning or fail according to intended product behavior.

### FE-002 — Real download error text is lost while state is in memory

**Primary files:** `backend/queue_manager.py`, `frontend/app.js`

**Current behavior:** Backend stores failure text under two different key names: DB column `error_message`, in-memory state `error`. Frontend reads `error_message`.

**Fix:** Use one status schema everywhere, preferably `error_message`. Add schema-level tests for in-memory and DB-backed status responses.

### CORE-004 — startup.sh does not read PORT from .env

**Primary files:** `startup.sh`, `docker-compose.yml`, `.env.example`

**Current behavior:** Docker Compose reads `.env`, but `startup.sh` does not source it. Health check uses `${PORT:-8080}` or shell's PORT.

**Fix:** Read effective port from `.env` safely (e.g., `export $(grep -E '^PORT=' .env | xargs)`, or ask Docker Compose for published port).

### BE-009 — Two backups created in the same second can share one path

**Primary file:** `backend/backup_manager.py`

**Current behavior:** Archive filename `median_backup_YYYYMMDD_HHMMSS.zip` uses only second-resolution timestamp.

**Fix:** Include backup UUID or random suffix: `median_backup_YYYYMMDD_HHMMSS_{uuid4().hex[:8]}.zip`

### BE-011 — Uploaded custom covers have no cleanup lifecycle

**Primary files:** `backend/app.py`, `backend/downloader.py`, `backend/config.py`

**Current behavior:** Every cover upload creates a file. No cleanup job, TTL, quota, or delete endpoint.

**Fix:** Add one or more: TTL cleanup, max cache size, explicit delete endpoint, reference tracking, startup cleanup for stale uploads.

### API-001 — API accepts incompatible download_type + format combinations

**Primary files:** `backend/app.py`, `backend/utils/ydl_opts_builder.py`, `backend/downloader.py`

**Current behavior:** Field validation constrains each field independently, no cross-field validator for incompatible combinations (audio+mp4, video+mp3).

**Fix:** Define allowed combinations explicitly and validate cross-field.
## P3 — Low Priority Fix Plans

### CORE-005 — Cleanup interval display includes inline comment

**Primary files:** `startup.sh`, `.env.example`

**Current behavior:** `.env.example` has inline comment on `CLEANUP_INTERVAL`. startup.sh extracts everything after `=` and only removes spaces.

**Fix:** Strip comments before printing, or parse `.env` using same semantics as Docker Compose (`source` or `set -a`).

### BE-010 — Backup selection parameter ignored

**Primary files:** `backend/backup_manager.py`, `backend/app.py`

**Current behavior:** `selection` parameter accepted in API but never used in backup builder.

**Fix:** Implement selection semantics or remove unused field from API contract.

---

## Independent Verification Pass — 2026-10-07
The results below do not assume a bug is real because it is listed here.
Each verdict comes from the implementation at the audited commit
`bf343b8f41a7000f0861e026629e4125a08c3a0d`. No production code was modified.

Live probes run during verification:

- `get_playlist_folder('A','B')` returned `'A - B'` twice: deterministic.
- `get_ydl_opts('video','mp4','',...)` returned
  `bestvideo[ext=mp4]+bestaudio[ext=mp4]/best[ext=mp4]/best`: M4A not selected.

### CORE-001 — Container is missing the JavaScript challenge stack

Verdict: `PARTIALLY CONFIRMED` (dependency/runtime gap confirmed; universal
YouTube-breakage claim not established).

Exact location:

- `requirements.txt:3` — plain `yt-dlp>=2026.03.17`, no JS runtime.
- `Dockerfile:19-23` — installs `ffmpeg`, `curl`, CA certs, tzdata; no
  Deno/Node/QuickJS.
- `backend/app.py:323-334` — `/api/health` reports `yt_dlp_version` but no JS
  runtime/EJS diagnostic.
- `backend/utils/yt_match.py:197-218` — extractor errors collapse to `False`.

Root cause: image lacks a JS runtime alongside yt-dlp.

Evidence gap: nothing audited proves every/current extraction needs EJS.
No test, log, runtime probe, or version-specific requirement was available.

Reproduction: inspect `requirements.txt:3`, `Dockerfile:19-23`, and call
`/api/health` (`backend/app.py:307-350`).

Severity/priority: downgrade Critical/P0 to High/P1 pending a live failing
extraction trace.
### BE-001 — YouTube extractor failures misreported as "song not found"

Verdict: `CONFIRMED`.

Exact location:

- `backend/utils/yt_match.py:197-218` — generic `except Exception` returns
  `False`.
- `backend/utils/yt_match.py:221-272` — `match_track_sync()` returns `None`.
- `backend/queue_manager.py:268-272`, `312-323` — `None` becomes user-facing
  absence messages.

Expected: infrastructure failure surfaced as extractor failure.
Actual: every validation exception becomes "not found".

### CORE-002 — yt-dlp update logic does not match installation model

Verdict: `CONFIRMED`.

Exact location:

- `backend/app.py:102-116` — lifespan runs `yt-dlp -U` at startup.
- `backend/scheduler.py:282-301` — `update_yt_dlp()` runs pip upgrades.
- `backend/scheduler.py:388-393` — scheduled `update_yt_dlp` job.
- `requirements.txt:3`, `Dockerfile:6-13,31` — build-time pip install, later
  non-root `median` runtime.

Expected: immutable-image dependency lifecycle.
Actual: two runtime self-mutation strategies over build-time installation.

### BE-002 — Concatenated Spotify downloads bypass retry and fallback

Verdict: `CONFIRMED`.

Exact location:

- `backend/downloader.py:394-446` — `_fetch_with_fallback()` tries primary
  plus alternatives.
- `backend/downloader.py:546-548` — separate-track path uses fallback.
- `backend/downloader.py:615-693` — concatenate loop calls local `_dl()`
  (`downloader.py:666-671`) with only `track_url`.

Expected: merged albums use the same fallback list.
Actual: merged albums skip runner-ups; one dead primary loses a merge track.

### FE-001 — Enabling MEDIAN_API_TOKEN breaks protected UI actions

Verdict: `CONFIRMED` as UI wiring gap; documentation-claim wording narrowed.

Exact location:

- `frontend/app.js:41-48` — API helper sends only `Content-Type`.
- `backend/app.py:40-44` — bearer auth enforced.
- `backend/app.py:431,498,791,1771,1847,1858` — protected endpoints.
- `README.md:97` — token documented as optional API auth; no audited UI text
  saying the browser automatically injects it was found.

Reproduction: set token, call a protected endpoint from UI, observe 401.

### SEC-001 — Several mutating endpoints are not protected

Verdict: `CONFIRMED`, narrower than section text.

Unprotected mutators found:

- `backend/app.py:1213` — `DELETE /api/download/{download_id}`.
- `backend/app.py:1219` — `POST /api/download/{download_id}/keep`.
- `backend/app.py:1955` — `POST /api/cover/upload`.
- `backend/app.py:1994` — `POST /api/cover/preview`.

Not state-changing: `/api/validate` (`app.py:383`) and `/api/discography`
(`app.py:473`) are read-only metadata endpoints and should not be grouped
with the four mutators above.

### SEC-002 — Rate limiting bypass through X-Forwarded-For spoofing

Verdict: `CONFIRMED` as control-flow defect.

Exact location:

- `backend/app.py:70-74` — first XFF entry becomes client IP.
- `backend/app.py:54-67` — bucket keyed by that IP.
- `nginx.conf:86,101` — `$proxy_add_x_forwarded_for` appends rather than
  replacing client-supplied input.

Expected: key from trusted proxy chain.
Actual: first client-controlled entry selects the bucket.

### SEC-003 — Cover upload limit enforced after full read

Verdict: `CONFIRMED`.

Exact location:

- `backend/app.py:1963` — `data = await file.read()`.
- `backend/app.py:1964-1965` — size check afterward.
- `nginx.conf:58` — `client_max_body_size 0`.

Expected: reject before full memory/IO cost.
Actual: full upload buffered first, then rejected.

### BE-003 — Separate-track downloads reuse the same folder

Verdict: `CONFIRMED`.

Exact location:

- `backend/utils/file_organizer.py:26-29` — deterministic
  `Artist - Album` folder name.
- `backend/downloader.py:811-813` — separate-track path builds
  `download_dir / get_playlist_folder(artist, album)` and calls `mkdir`
  directly; no `ensure_unique_path()` reservation.
- `backend/downloader.py:9-13` — `ensure_unique_path` is imported but unused
  on this path.

Live probe: `get_playlist_folder('A','B')` returned `'A - B'` twice.

Expected: unique reserved folder per download record.
Actual: same artist/album maps to the same directory across downloads.

Reproduction: start two separate-track downloads for the same artist/album
before cleanup; both write into `downloads/Artist - Album/`.

### BE-004 — Fallback sources can reuse partial files

Verdict: `CONFIRMED`.

Exact location:

- `backend/downloader.py:394-446` — one shared `ydl_opts`/`outtmpl` across
  every `sources` entry; no cleanup or unique temp stem between source
  identities.
- `backend/downloader.py:467-564` — per-track path passes one stem plus all
  `url_alternatives` into the same shared-template fallback call.
- `backend/queue_manager.py:463-486` — retry path cleans partials between
  whole-download attempts, not between fallback sources within one call.

Expected: source switch starts from a clean or uniquely named temp stem.
Actual: runner-up can resume/reuse a partial left by another source.

### BE-005 — MP4 selector uses wrong audio extension

Verdict: `CONFIRMED`.

Exact location:

- `backend/utils/ydl_opts_builder.py:96-101` — MP4 selector uses
  `bestaudio[ext=mp4]`, with fallback to combined `best[ext=mp4]/best`.

Live probe: `get_ydl_opts('video','mp4','',...)` returned
`bestvideo[ext=mp4]+bestaudio[ext=mp4]/best[ext=mp4]/best`.

Expected: `bestvideo[ext=mp4]+bestaudio[ext=m4a]` split-format chain.
Actual: normal M4A audio-only streams cannot match, so yt-dlp can fall
through to lower-quality combined MP4 formats.

### BE-006 — WebM merge path uses H.264 + AAC

Verdict: `CONFIRMED`, remains P0.

Exact location:

- `backend/concatenation_engine.py:375-446` — `concatenate_video()` accepts
  `output_format` but never branches on it.
- `backend/concatenation_engine.py:418-429` — hard-cut path always uses
  `VIDEO_CODEC_H264` plus `AUDIO_CODEC_AAC`.
- `backend/config.py:82-87` — those settings are H.264/AAC.
- Contrast path: `concatenation_engine.py:616-639` already branches for WebM
  with VP8/VP9 plus Opus, proving the correct pattern exists elsewhere.

Expected: WebM merge emits VP8/VP9 plus Opus.
Actual: WebM merge requests H.264 plus AAC inside `.webm`.

Reproduction: merge a multi-track video playlist as WebM with Merge enabled;
FFmpeg receives incompatible codec/container args.

### BE-007 — Merged video playlists do not get chapter markers

Verdict: `CONFIRMED`.

Exact location:

- `backend/concatenation_engine.py:265-315` — audio merge accepts
  `tracks_meta` and embeds chapters.
- `backend/concatenation_engine.py:375-446` — video merge has no
  `tracks_meta` input and never calls chapter embedding.

Expected: merged video albums expose the same chapter metadata.
Actual: merged video output has no chapter path.

### BE-008 — Chapter embedding failure can be ignored

Verdict: `CONFIRMED`.

Exact location:

- `backend/ffmpeg_chapters_handler.py:90-132` — `add_chapters_to_file()`
  returns `True`/`False`.
- `backend/concatenation_engine.py:290-306` — caller awaits completion but
  never checks the boolean; success is still returned.

Expected: missing chapters surface as a warning/failure.
Actual: download marked complete while silently missing chapter metadata.

### FE-002 — Real download error text is lost while state is in memory

Verdict: `CONFIRMED`.

Exact location:

- `backend/queue_manager.py:544-547` — exception path writes DB
  `error_message` and in-memory `error` under different keys.
- `backend/queue_manager.py:639-657` — `get_download_status()` prefers
  in-memory state while present; DB mapping preserves `error_message`.
- `frontend/app.js:778` — notifier reads only `s.error_message`, falling back
  to generic "Download failed".

Expected: one error schema everywhere.
Actual: live failures display generic text while the real text remains under
the other key.
### CORE-003 — Cancelling does not stop the running yt-dlp thread

Verdict: `CONFIRMED`.

Exact location:

- `backend/queue_manager.py:619-623` — `cancel_download()` calls
  `active_downloads[download_id].cancel()`.
- `backend/queue_manager.py:404-408`, `425, 495, 506, 671, 781` — blocking
  yt-dlp/FFmpeg work runs through `run_in_executor`.
- `backend/queue_manager.py:534-540` — comment admits the executor thread may
  still be writing; cleanup runs while it can still be active.

Expected: cancel stops active network/disk work.
Actual: only the asyncio wrapper is cancelled; the blocking worker can keep
writing, with late partials racing cleanup.

### CORE-004 — startup.sh does not read PORT from .env

Verdict: `CONFIRMED`.

Exact location:

- `startup.sh:38-49` — validates/copies `.env` but never sources it.
- `startup.sh:70,84,89-90` — health probe and display use shell
  `${PORT:-8080}`.
- `docker-compose.yml:36` — nginx publishes `${PORT:-8080}:80`.

Reproduction: set `PORT=9090` in `.env` without exporting `PORT` in the
calling shell, run `startup.sh`, and observe the health probe check the wrong
port.

Expected: script probes the effective Compose port.
Actual: script probes the shell default unless the shell exports `PORT`.

### CORE-005 — Cleanup interval display includes inline comment text

Verdict: `CONFIRMED`.

Exact location:

- `.env.example:14` — `CLEANUP_INTERVAL=90` followed by an inline comment.
- `startup.sh:95-96` — `cut -d= -f2 | tr -d ' '` strips spaces but not `#...`.

Expected: display shows `90`.
Actual: display can include trailing comment text.

### BE-009 — Backups created in the same second can share one path

Verdict: `CONFIRMED`.

Exact location:

- `backend/backup_manager.py:19` — backup ID is a UUID.
- `backend/backup_manager.py:23-25` — archive name uses only
  `%Y%m%d_%H%M%S`, with no UUID/random suffix.
- `backend/backup_manager.py:72-87` — zip opens the colliding path for
  writing.

Expected: unique archive path per backup record.
Actual: two backups in the same second target the same ZIP; records can share
one path and deletion of one can remove the other's file.

### BE-010 — Backup selection parameter is ignored

Verdict: `CONFIRMED`.

Exact location:

- `backend/app.py:252-255` — `BackupRequest.selection` accepted.
- `backend/app.py:1847-1850` — value forwarded to `create_backup()`.
- `backend/backup_manager.py:14-68` — signature accepts `selection`, but the
  builder only branches on `date_from`/`date_to`; no code reads `selection`.

Expected: selection changes backup scope, or the field is removed.
Actual: accepted but inert API parameter.

### BE-011 — Uploaded custom covers have no cleanup lifecycle

Verdict: `CONFIRMED`, scoped to uploaded custom covers.

Exact location:

- `backend/app.py:1967-1975` — upload writes UUID-named files into
  `CUSTOM_COVER_DIR`.
- `backend/downloader.py:326` — uploaded covers are intentionally retained.
- No delete endpoint, TTL, quota, reference tracking, or cleanup lifecycle
  for custom-cover files was found in the audited code.

Scope note: `backend/image_processor.py:104-122` prunes the separate derived
`.cover_cache` directory. That is not the uploaded custom-cover directory, so
it does not resolve this bug.

Expected: bounded lifecycle for user uploads.
Actual: uploads accumulate indefinitely.

### API-001 — API accepts incompatible download_type and format combinations

Verdict: `CONFIRMED`.

Exact location:

- `backend/app.py:161-215` — `DownloadRequest` validates each field
  independently; no cross-field validator exists.
- Allowed literals permit `audio+mp4`, `audio+mkv`, `audio+webm`,
  `video+mp3`, `video+flac`, and `video+aac`.
- `backend/utils/ydl_opts_builder.py:43-101` and
  `backend/downloader.py:591-594,675-680` branch primarily on
  `download_type`, so the accepted `format` can disagree with produced
  extensions.

Expected: API rejects or normalizes unsupported combinations.
Actual: API accepts combinations the UI never generates.

### Duplicates, false positives, priority changes

- Duplicates: none. BE-007/BE-008 concern different stages (absent chapter
  path vs ignored chapter result). BE-009/BE-010 concern different defects
  (filename collision vs ignored selection). FE-002 concerns status-schema
  mismatch, not chapter handling.
- False positives: none.
- Already fixed: none.
- Priority changes: one. `CORE-001` moves Critical/P0 to High/P1 because the
  dependency gap is confirmed but universal breakage was not independently
  established. All other priorities stand: `BE-003` and `BE-006` remain P0;
  P1 remains 10 bugs; P2 remains 7 bugs; P3 remains 2 bugs.
- Final real bug count: 22 listed IDs, with 21 `CONFIRMED` and 1
  `PARTIALLY CONFIRMED` (`CORE-001`).

---

## Fix Log — 2026-10-08

All 22 IDs moved `Confirmed` → `Fixed` in the working tree on top of
`bf343b8` (not yet committed). Per the registry rules none is `Verified`:
the fixes are covered by unit/HTTP tests (`backend/tests/test_registry_fixes.py`,
`backend/tests/test_yt_match.py`; full suite 300 passed), but no Docker build,
real YouTube/Spotify download, or ffmpeg merge was run.

| Bug ID | Fix | Regression test |
|---|---|---|
| `CORE-001` | `yt-dlp[default]` (pulls `yt-dlp-ejs`); `deno` added to the runtime image (Alpine community, system-wide so `median` can run it); `/api/health` reports `ejs_available`, `js_runtime`, `youtube_challenges_ok`; startup logs a warning when either is missing | — (needs Docker smoke test) |
| `BE-001` | `_check_availability()` classifies probe errors (content-unavailable / extractor / network) and raises `CandidateProbeError` for infrastructure failures; a failed search raises instead of returning `[]`; other candidates are still tried; queue reports "extraction failed" instead of "not found" | `test_yt_match.py` (5 new tests) |
| `CORE-002` | Removed startup `yt-dlp -U` and the scheduled pip upgrade; updates via image rebuild; `AUTO_UPDATE_INTERVAL` kept only so old `.env` files load; README/.env.example updated | — |
| `BE-002` | Concatenate path uses `_fetch_with_fallback()` with `url_alternatives` | `test_concatenate_falls_back_to_runner_up_urls` |
| `FE-001` | `authFetch()` attaches `Authorization: Bearer`, prompts for the token on 401, keeps it in `localStorage`, forgets a rejected token; used for JSON API calls and cover upload; token never in source | — (needs browser test) |
| `SEC-001` | Middleware requires the token on every POST/PUT/PATCH/DELETE under `/api/` when configured; explicit `Depends(require_token)` on the four named routes | `test_mutating_endpoints_require_the_token`, `test_every_mutating_route_is_covered` |
| `SEC-002` | Client IP from nginx-set `X-Real-IP` (else TCP peer), never `X-Forwarded-For`; nginx overwrites both headers with `$remote_addr` | `test_spoofed_forwarded_for_does_not_change_the_rate_limit_key` |
| `SEC-003` | nginx `client_max_body_size 25m`; ASGI cap rejects by `Content-Length` and counts streamed bytes (413); handler reads at most limit+1 bytes | `test_oversized_upload_is_refused_from_its_content_length`, `test_oversized_streamed_upload_is_cut_off` |
| `BE-003` | `reserve_playlist_folder()` reserves with atomic `mkdir(exist_ok=False)` + ` (N)` suffix | `test_two_downloads_of_one_album_get_separate_folders`, `test_concurrent_reservations_are_all_unique` |
| `BE-004` | Switching to a different source deletes every file under the track's stem (`.part`, `.ytdl`, finished `fNNN` streams) | `test_source_switch_clears_every_leftover_under_the_stem`, `test_fallback_starts_without_the_primary_sources_partial` |
| `BE-005` | `bestvideo[ext=mp4]+bestaudio[ext=m4a]` first, then `bestvideo[ext=mp4]+bestaudio`, then progressive | `test_mp4_selector_pairs_mp4_video_with_m4a_audio` |
| `BE-006` | `_merge_video_codecs()`: WebM → VP9 (or VP8 if no libvpx-vp9) + Opus; MP4/MKV unchanged; used by hard-cut and crossfade paths | `test_webm_merge_uses_vp9_and_opus`, `test_mp4_and_mkv_merges_keep_h264_and_aac` |
| `BE-007` | `concatenate_video()` takes `tracks_meta` and embeds chapters after merge (crossfade overlap applied) | `test_merged_video_gets_chapter_markers` |
| `BE-008` | `add_chapters_to_file()` result is checked in audio, video and cover+audio paths; failure surfaces as a user-visible warning | `test_chapter_failure_on_a_merged_video_is_reported` |
| `FE-002` | In-memory state uses `error_message`, same as the DB row and frontend | `test_error_text_is_exposed_under_error_message` |
| `CORE-003` | Cooperative cancel: per-download `threading.Event` checked from yt-dlp progress hooks (captured at hook creation so the queue clearing its registry can't un-cancel); `DownloadCancelled` is never retried or swallowed | `test_cancel_hook_aborts_after_cancel_is_requested`, `test_cancel_still_aborts_after_the_queue_clears_its_registry` |
| `CORE-004` | `startup.sh` resolves `PORT` like Compose (shell env, else `.env`) | manual shell check |
| `CORE-005` | Same resolver strips inline `# comments` and quotes | manual shell check |
| `BE-009` | Archive name includes the backup UUID prefix | `test_same_second_backups_do_not_share_a_path` |
| `BE-010` | `selection` removed from `BackupRequest`/`create_backup()`; UI no longer sends it | `test_backup_request_no_longer_advertises_selection` |
| `BE-011` | Hourly sweep deletes uploads older than `COVER_UPLOAD_TTL_HOURS` (24); `DELETE /api/cover/upload/{id}`; UI frees a cleared cover unless a queued download uses it | `test_expired_covers_are_swept`, `test_small_upload_still_works_and_can_be_deleted` |
| `API-001` | `model_validator` on `DownloadRequest`: audio → mp3/flac/aac, video → mp4/mkv/webm, cover_audio → mp3 | `test_incompatible_type_and_format_are_rejected` |

### Still needed before `Verified`

- `docker compose build` and confirm `deno --version` runs as `median`, and `/api/health` shows `youtube_challenges_ok: true`.
- Real YouTube single, Spotify album (separate and merged), and WebM/MP4/MKV merged playlist downloads; check chapters with `ffprobe`.
- Cancel a large download mid-transfer and confirm network/disk activity stops.
- With `MEDIAN_API_TOKEN` set: UI prompts once, then download/cancel/keep/cover upload work.
- `startup.sh` with `PORT=9090` in `.env`.

---

## End-to-End Pass — 2026-10-08

Median was run for real (uvicorn, Python 3.11 with the pinned requirements,
ffmpeg 7.0.2, deno 2.9) and driven over the API and in headless Chromium
against live YouTube, YouTube Music, Spotify, SoundCloud and Bandcamp. Not a
Docker build — the image itself is still unverified.

### Moved to `Verified` (observed working in a real flow)

| Bug ID | Evidence |
|---|---|
| `CORE-003` | Cancelled a 4K download mid-transfer at 37 MiB/s: partial deleted, no further disk growth over 16 s |
| `BE-003` | Three downloads of one playlist landed in `…`, `… (1)`, `… (2)` |
| `BE-005` | MP4 downloads carry AAC audio from the m4a stream |
| `BE-006` | WebM merge (hard cut and crossfade) → VP9 + Opus, plays, correct length |
| `BE-007` | Merged MP4/MKV/WebM have 3 chapters; crossfaded ones shift by the overlap |
| `FE-001` | With `MEDIAN_API_TOKEN` set the UI prompted once, then validate/download/panels worked |
| `SEC-001` | Real HTTP: mutating calls 401 without the token, succeed with it |
| `BE-010` | UI backup create/download/delete without `selection` |
| `BE-011` | Cover upload/preview/delete; cover+audio with an uploaded cover |
| `CORE-002` | Server starts without self-update; health reports yt-dlp version |

### New bugs found and fixed

| Bug ID | Severity | Area | Title | Fix | Status |
|---|---|---|---|---|---|
| `BE-012` | Critical | Audio concatenation | Merged FLAC albums lose audio after the first track (stream-copied FLAC keeps only the first STREAMINFO; decoding stopped at 16 s of 36 s) | Hard-cut merge re-encodes FLAC (lossless) | `Verified` |
| `BE-013` | Medium | Chapters | FLAC merges never get chapters — ffmpeg's FLAC muxer drops them while reporting success | Chapters written as `CHAPTERxxx` Vorbis comments via mutagen | `Verified` |
| `BE-014` | Medium | Thumbnails / covers | YouTube `maxresdefault` thumbnail 404s for older/low-res videos: broken preview image (502) and no fallback cover | `thumbnail_candidates()` walks maxres → sd → hq in proxy and cover download | `Verified` |
| `BE-015` | Low | Cover+audio | Using an uploaded cover left yt-dlp's thumbnail behind as an orphan `_tmp_*.jpg` | Unused fetched thumbnails deleted | `Verified` |
| `BE-016` | High | Discography | Bandcamp discography empty for artists on the classic "indexpage" layout (yt-dlp extractor returns 0 entries); the empty result was then cached for 6 h | Scrape `/music` HTML (same artist host only) when yt-dlp finds nothing; empty results not cached | `Verified` |
| `BE-017` | Low | Progress | Separate-track video playlists showed "Downloaded 4/2 tracks" — yt-dlp fires `finished` per format | Count each video id once | `Fixed` |
| `BE-018` | Medium | Video format | MP4 downloads picked AV1 video, which many TVs/phones/QuickTime can't play | Prefer `avc1` (H.264), AV1/other MP4 as fallback | `Verified` |
| `CORE-006` | High | Docker | `apk add deno` fails on 32-bit ARM (not packaged), failing the whole image build | deno installed in its own non-fatal step; health reports it missing | `Fixed` |
| `API-002` | High | URL validation | YouTube Music, `m.youtube.com`, Shorts/live/embed, `?…&v=` links and `m.`/`on.soundcloud.com` links rejected as "Platform not supported" | Broadened platform patterns (backend + UI badge) | `Verified` |

### Checked and found working

Single downloads in mp3/flac/aac/original and mp4/mkv/webm; playlist
separate, merged, crossfaded (audio and video), cover+audio single/merged;
Spotify track, album, playlist (per-track YouTube matching); SoundCloud track
and set; Bandcamp track and albums; discography on Bandcamp, YouTube and
SoundCloud; batch zip; backups; keep; cleanup; history filters; SSE events;
description.md; restart recovery; theme/help/panels in the UI with no console
errors.

Not bugs: MP3 CHAP frames appear out of order in ffprobe, but the CTOC frame
orders them correctly (what players use); batch-zip filenames drop the
`001 -` prefix by design (files carry track tags); discography mode ignores a
per-track selection by design (UI locks it).

