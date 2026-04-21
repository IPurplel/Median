/* ── MEDIAN — Main App Logic ────────────────────────────────────────────── */

const API = '';  // Same origin

// ── State ────────────────────────────────────────────────────────────────────
let currentMeta = null;
let currentDownloadType = 'audio';
let currentFormat = 'mp3';
let currentBitrate = '320';
let currentCoverSettings = { ratio: '1:1', resolution: 'medium', output_format: 'mp4' };
let activePollers = {};  // download_id -> setInterval id

// ── DOM refs ──────────────────────────────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const urlInput       = $('#url-input');
const btnValidate    = $('#btn-validate');
const urlError       = $('#url-error');
const detectedPform  = $('#detected-platform');
const metaSection    = $('#meta-section');
const activeSection  = $('#active-section');
const activeList     = $('#active-downloads-list');

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type = 'info', duration = 3500) {
  const container = $('#toasts');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

// ── API helpers ───────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API + path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

// ── Collapsible panels ────────────────────────────────────────────────────────
document.querySelectorAll('.panel-header').forEach(header => {
  const panelId = header.dataset.panel;
  if (!panelId) return;

  header.addEventListener('click', () => {
    const body = header.nextElementSibling;
    if (!body) return;
    const isOpen = body.classList.contains('open');
    body.classList.toggle('open', !isOpen);
    header.classList.toggle('open', !isOpen);
    header.classList.toggle('collapsed', isOpen);

    // Lazy load panel content
    if (!isOpen) loadPanel(panelId);
  });
});

function loadPanel(id) {
  switch (id) {
    case 'queue':    renderQueue(); break;
    case 'history':  initHistory(); break;
    case 'stats':    loadStats(); break;
    case 'backup':   loadBackup(); break;
    case 'watched':  loadWatched(); break;
  }
}

// ── Platform detection (live) ─────────────────────────────────────────────────
const PATTERNS = {
  youtube:    /(?:youtube\.com\/(?:watch|playlist|@|channel)|youtu\.be\/)/i,
  soundcloud: /soundcloud\.com\//i,
  bandcamp:   /\.bandcamp\.com\//i,
};

urlInput.addEventListener('input', () => {
  const url = urlInput.value.trim();
  urlError.textContent = '';

  // Fix 4: Clear validated metadata when URL changes so the user can't click
  // Download with stale metadata from a previously validated URL.
  // This also hides the metadata panel to make clear re-validation is needed.
  if (currentMeta) {
    currentMeta = null;
    metaSection.classList.add('hidden');
  }

  let found = null;
  for (const [p, re] of Object.entries(PATTERNS)) {
    if (re.test(url)) { found = p; break; }
  }

  detectedPform.textContent = found
    ? `✦ ${found.charAt(0).toUpperCase() + found.slice(1)} detected`
    : '';

  // Highlight matching platform pill in header
  $$('.platform-pill').forEach(pill => {
    pill.classList.toggle('active-platform', pill.dataset.platform === found);
  });

  // Show Cover+Audio tab only for SC/Bandcamp
  const coverTab = $('.cover-audio-tab');
  if (found === 'soundcloud' || found === 'bandcamp') {
    coverTab.classList.remove('hidden');
  } else {
    coverTab.classList.add('hidden');
    if (currentDownloadType === 'cover_audio') selectType('audio');
  }
});

// ── VALIDATE ─────────────────────────────────────────────────────────────────
btnValidate.addEventListener('click', validateURL);
urlInput.addEventListener('keydown', e => { if (e.key === 'Enter') validateURL(); });

async function validateURL() {
  const url = urlInput.value.trim();
  if (!url) { urlError.textContent = 'Please enter a URL.'; return; }

  btnValidate.classList.add('loading');
  urlError.textContent = '';
  metaSection.classList.add('hidden');

  try {
    currentMeta = await api('POST', '/api/validate', { url });
    renderMeta(currentMeta);
    metaSection.classList.remove('hidden');
    metaSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (err) {
    urlError.textContent = err.message;
  } finally {
    btnValidate.classList.remove('loading');
  }
}

// ── RENDER META ───────────────────────────────────────────────────────────────
function renderMeta(meta) {
  const thumbEl = $('#meta-thumbnail');
  const rawThumbUrl = meta.thumbnail || '';

  // Bug #14 fix: Remove any stale placeholder from a previous render
  const zone = thumbEl.closest('.meta-thumb-zone');
  const oldPlaceholder = zone?.querySelector('.thumb-placeholder');
  if (oldPlaceholder) oldPlaceholder.remove();
  thumbEl.style.display = '';

  if (rawThumbUrl) {
    // Show a loading state while the proxy fetches the image
    thumbEl.src = '';
    thumbEl.style.opacity = '0.4';

    const proxyUrl = `/api/thumbnail?url=${encodeURIComponent(rawThumbUrl)}`;
    // Preload image to detect errors before setting src
    const img = new Image();
    img.onload = () => {
      thumbEl.src = proxyUrl;
      thumbEl.style.opacity = '1';
      thumbEl.style.display = '';
    };
    img.onerror = () => {
      thumbEl.style.display = 'none';
      thumbEl.style.opacity = '1';
      const z = thumbEl.closest('.meta-thumb-zone');
      if (z && !z.querySelector('.thumb-placeholder')) {
        const ph = document.createElement('div');
        ph.className = 'thumb-placeholder';
        ph.innerHTML = `<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect width="48" height="48" rx="8" fill="var(--bg-4)"/>
          <path d="M20 14v20l14-10L20 14z" fill="var(--accent)" opacity="0.7"/>
        </svg>`;
        z.insertBefore(ph, thumbEl);
      }
    };
    img.src = proxyUrl;
  } else {
    thumbEl.src = '';
    thumbEl.style.display = 'none';
  }

  // Artist — show prominently, empty string hides the element
  const artist = (meta.artist || '').trim();
  const artistEl = $('#meta-artist');
  artistEl.textContent = artist && artist !== 'Unknown' ? artist : '';

  // Title — for playlists this is the album/playlist name
  $('#meta-title').textContent = meta.title || '';

  // Album — for playlists show "Album by Artist", for single tracks show album if available
  const albumEl = $('#meta-album');
  if (meta.is_playlist) {
    // Artist next to album name: "by Artist Name" shown under the album title
    albumEl.textContent = artist ? `by ${artist}` : '';
    albumEl.style.display = artist ? '' : 'none';
  } else {
    // Single track: show album name if it exists
    const album = (meta.album || '').trim();
    albumEl.textContent = album ? `from "${album}"` : '';
    albumEl.style.display = album ? '' : 'none';
  }

  $('#meta-platform-badge').textContent = (meta.platform || '').charAt(0).toUpperCase() + (meta.platform || '').slice(1);

  let sub = '';
  if (meta.is_playlist) {
    sub = `${meta.track_count || 0} tracks`;
    if (meta.total_duration_display) sub += ` · ${meta.total_duration_display}`;
    $('#concat-option').classList.remove('hidden');
  } else {
    if (meta.duration_display) sub = meta.duration_display;
    $('#concat-option').classList.add('hidden');
  }
  $('#meta-sub').textContent = sub;

  // Track list
  const tracksList = $('#tracks-list');
  if (meta.is_playlist && meta.tracks?.length) {
    tracksList.classList.remove('hidden');
    tracksList.innerHTML = meta.tracks.slice(0, 50).map((t, i) => `
      <div class="track-item">
        <span class="track-num">${i + 1}</span>
        <span class="track-name">${escHtml(t.title)}</span>
        <span class="track-dur">${t.duration ? fmtDur(t.duration) : ''}</span>
      </div>
    `).join('');
  } else {
    tracksList.classList.add('hidden');
  }

  // Show Cover+Audio tab only for SoundCloud / Bandcamp (have album art)
  const coverTab = $('.cover-audio-tab');
  const p = meta.platform;
  if (p === 'soundcloud' || p === 'bandcamp') {
    coverTab.classList.remove('hidden');
  } else {
    coverTab.classList.add('hidden');
    if (currentDownloadType === 'cover_audio') selectType('audio');
  }

  // Fix 3: Reset concat toggle whenever it's hidden so it doesn't carry over
  // from a previous playlist validation to a new one
  const concatToggle = $('#concat-toggle');
  if (!meta.is_playlist && concatToggle) {
    concatToggle.checked = false;
  }
}

// ── DOWNLOAD TYPE TABS ────────────────────────────────────────────────────────
$$('.type-tab').forEach(btn => {
  btn.addEventListener('click', () => selectType(btn.dataset.type));
});

function selectType(type) {
  currentDownloadType = type;
  $$('.type-tab').forEach(b => b.classList.toggle('active', b.dataset.type === type));

  const audioFmts  = $('#audio-formats');
  const videoFmts  = $('#video-formats');
  const bitrateGrp = $('#bitrate-group');
  const coverPanel = $('#cover-settings-panel');
  const bitrateLabel = bitrateGrp?.querySelector('.row-label');

  if (type === 'audio') {
    audioFmts.classList.remove('hidden');
    videoFmts.classList.add('hidden');
    bitrateGrp.classList.remove('hidden');
    coverPanel.classList.add('hidden');
    if (bitrateLabel) bitrateLabel.textContent = 'Bitrate';
    selectFmt('mp3', 'audio');

  } else if (type === 'video') {
    audioFmts.classList.add('hidden');
    videoFmts.classList.remove('hidden');
    bitrateGrp.classList.remove('hidden');
    coverPanel.classList.add('hidden');
    if (bitrateLabel) bitrateLabel.textContent = 'Audio Bitrate';
    selectFmt('mp4', 'video');

  } else if (type === 'cover_audio') {
    audioFmts.classList.add('hidden');
    videoFmts.classList.add('hidden');
    bitrateGrp.classList.remove('hidden');
    coverPanel.classList.remove('hidden');
    if (bitrateLabel) bitrateLabel.textContent = 'Audio Bitrate';

    // Bug #15 fix: re-sync pill active classes from currentCoverSettings state
    // so DOM and JS state never desync when user switches tabs back and forth
    $$('.ratio-pill').forEach(p => {
      p.classList.toggle('active', p.dataset.ratio === currentCoverSettings.ratio);
    });
    $$('.res-pill').forEach(p => {
      p.classList.toggle('active', p.dataset.res === currentCoverSettings.resolution);
    });
    $$('.outfmt-pill').forEach(p => {
      p.classList.toggle('active', p.dataset.outfmt === currentCoverSettings.output_format);
    });
  }
}

// ── FORMAT PILLS ──────────────────────────────────────────────────────────────
$$('.fmt-pill').forEach(pill => {
  pill.addEventListener('click', () => {
    const group = pill.closest('.format-group');
    group?.querySelectorAll('.fmt-pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    currentFormat = pill.dataset.fmt;
    // Show/hide bitrate based on format
    const bitrateGrp = $('#bitrate-group');
    if (['mp3', 'aac'].includes(currentFormat)) {
      bitrateGrp.classList.remove('hidden');
    } else if (currentFormat === 'flac') {
      bitrateGrp.classList.add('hidden');
    }
  });
});

function selectFmt(fmt, group) {
  currentFormat = fmt;
  const selector = group === 'audio' ? '#audio-formats' : '#video-formats';
  $$(selector + ' .fmt-pill').forEach(p => {
    p.classList.toggle('active', p.dataset.fmt === fmt);
  });
}

$('#bitrate-select').addEventListener('change', e => {
  currentBitrate = e.target.value;
});

// ── COVER SETTINGS ────────────────────────────────────────────────────────────
$$('.ratio-pill').forEach(pill => {
  pill.addEventListener('click', () => {
    $$('.ratio-pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    currentCoverSettings.ratio = pill.dataset.ratio;
  });
});

$$('.res-pill').forEach(pill => {
  pill.addEventListener('click', () => {
    $$('.res-pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    currentCoverSettings.resolution = pill.dataset.res;
  });
});

$$('.outfmt-pill').forEach(pill => {
  pill.addEventListener('click', () => {
    $$('.outfmt-pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    currentCoverSettings.output_format = pill.dataset.outfmt;
  });
});

$('#btn-cover-preview').addEventListener('click', async () => {
  if (!currentMeta?.thumbnail) { toast('No thumbnail available', 'error'); return; }
  $('#cover-preview-info').textContent = 'Loading preview...';
  try {
    const result = await api('POST', '/api/cover/preview', {
      thumbnail_url: currentMeta.thumbnail,
      ratio: currentCoverSettings.ratio,
      resolution: currentCoverSettings.resolution,
    });
    $('#cover-preview-img').classList.remove('hidden');
    $('#cover-preview-thumb').src = result.preview;
    $('#cover-preview-info').textContent = `${result.dimensions} · ${result.size}`;
  } catch (err) {
    $('#cover-preview-info').textContent = 'Preview failed: ' + err.message;
  }
});

// ── DOWNLOAD ─────────────────────────────────────────────────────────────────
$('#btn-download').addEventListener('click', startDownload);

async function startDownload() {
  if (!currentMeta) { toast('Validate a URL first', 'error'); return; }

  const btn = $('#btn-download');
  if (btn.disabled) return;  // Bug #16 fix: guard against rapid re-clicks
  btn.disabled = true;
  btn.style.opacity = '0.6';

  try {
    const url = urlInput.value.trim();
    const concatenate = $('#concat-toggle').checked;
    let fmt = currentFormat;
    if (currentDownloadType === 'cover_audio') {
      fmt = 'mp3';
    }

    const body = {
      url,
      download_type: currentDownloadType,
      format: fmt,
      bitrate: currentBitrate,
      concatenate,
      cover_settings: currentDownloadType === 'cover_audio' ? currentCoverSettings : null,
    };

    const result = await api('POST', '/api/download', body);
    toast(`Download queued: ${result.title || url}`, 'success');
    pollDownload(result.download_id, result.title, result.artist);
    activeSection.classList.remove('hidden');

    const queueBody = $('#queue-body');
    if (queueBody.classList.contains('open')) renderQueue();
  } catch (err) {
    toast('Download failed: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.style.opacity = '';
  }
}

// ── POLLING / PROGRESS ────────────────────────────────────────────────────────

// Bug #17 fix: track page visibility so we can pause/resume pollers
let _pageHidden = document.hidden;
document.addEventListener('visibilitychange', () => {
  _pageHidden = document.hidden;
  // When page becomes visible again, immediately refresh all active pollers
  if (!_pageHidden) {
    Object.keys(activePollers).forEach(async (id) => {
      try {
        const s = await api('GET', `/api/download/${id}/status`);
        updateDlItem(id, s);
        if (['completed', 'error', 'cancelled', 'cleaned'].includes(s.status)) {
          clearInterval(activePollers[id]);
          delete activePollers[id];
          if (s.status === 'completed') toast(`Downloaded: ${s.title || id}`, 'success');
          if (s.status === 'error') toast(`Error: ${s.error_message || 'Download failed'}`, 'error');
        }
      } catch (_) {}
    });
  }
});

function pollDownload(id, title, artist) {
  const item = document.createElement('div');
  item.className = 'dl-item status-queued';
  item.id = `dl-${id}`;
  item.innerHTML = buildDlItem(id, title, artist, 'queued', 0, '', '');
  activeList.prepend(item);

  activePollers[id] = setInterval(async () => {
    // Bug #17 fix: skip poll when tab is hidden — browser throttles anyway
    // and we do a catch-up refresh when the tab becomes visible again
    if (_pageHidden) return;
    try {
      const s = await api('GET', `/api/download/${id}/status`);
      updateDlItem(id, s);
      if (['completed', 'error', 'cancelled', 'cleaned'].includes(s.status)) {
        clearInterval(activePollers[id]);
        delete activePollers[id];
        if (s.status === 'completed') toast(`Downloaded: ${s.title || title}`, 'success');
        if (s.status === 'error') toast(`Error: ${s.error_message || 'Download failed'}`, 'error');
      }
    } catch (_) {}
  }, 1200);
}

function buildDlItem(id, title, artist, status, progress, speed, eta) {
  const statusBadge = {
    queued: '<span class="dl-status-badge badge-queued">Queued</span>',
    downloading: '<span class="dl-status-badge badge-downloading">Downloading</span>',
    completed: '<span class="dl-status-badge badge-completed">Done</span>',
    error: '<span class="dl-status-badge badge-error">Error</span>',
    cancelled: '<span class="dl-status-badge badge-error">Cancelled</span>',
  }[status] || '';

  return `
    <div class="dl-header">
      <div>
        <div class="dl-title">${escHtml(title || 'Loading...')}</div>
        <div class="dl-artist">${escHtml(artist || '')}</div>
      </div>
      ${statusBadge}
    </div>
    <div class="dl-progress-bar">
      <div class="dl-progress-fill" style="width:${progress || 0}%"></div>
    </div>
    <div class="dl-meta">
      <span>${speed || (status === 'downloading' ? 'Connecting...' : '')}</span>
      <span>${progress ? Math.round(progress) + '%' : ''} ${eta ? '· ' + eta : ''}</span>
    </div>
    ${status === 'completed' ? `
      <div class="dl-actions">
        <button class="dl-btn" onclick="downloadFile('${id}')">⬇ Download File</button>
        <button class="dl-btn" onclick="keepFile('${id}', true)">Keep (skip cleanup)</button>
      </div>` : status === 'downloading' || status === 'queued' ? `
      <div class="dl-actions">
        <button class="dl-btn danger" onclick="cancelDl('${id}')">✕ Cancel</button>
      </div>` : ''}
  `;
}

function updateDlItem(id, s) {
  const el = $(`#dl-${id}`);
  if (!el) return;
  el.className = `dl-item status-${s.status}`;
  el.innerHTML = buildDlItem(
    id, s.title, s.artist, s.status,
    s.progress, s.speed, s.eta
  );
}

async function cancelDl(id) {
  await api('DELETE', `/api/download/${id}`);
  clearInterval(activePollers[id]);
  const el = $(`#dl-${id}`);
  if (el) el.remove();
  toast('Download cancelled', 'info');
}

// Bug #13 fix: use fetch so 404/410 errors show a toast, not a blank error page
async function downloadFile(id) {
  try {
    const res = await fetch(`/api/download/${id}/file`);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      toast(data.detail || `Download error (${res.status})`, 'error');
      return;
    }
    // Extract filename from Content-Disposition header
    const cd = res.headers.get('Content-Disposition') || '';
    const match = cd.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : `median_download_${id}.zip`;

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    toast('Download failed: ' + err.message, 'error');
  }
}

async function keepFile(id, keep) {
  await api('POST', `/api/download/${id}/keep`, { keep });
  toast(keep ? 'File marked as keep' : 'File will be cleaned up', 'info');
}

// Expose globally for inline handlers
window.cancelDl = cancelDl;
window.keepFile = keepFile;
window.downloadFile = downloadFile;

// ── QUEUE ─────────────────────────────────────────────────────────────────────
async function renderQueue() {
  try {
    const items = await api('GET', '/api/queue');
    const list = $('#queue-list');
    const badge = $('#queue-badge');
    badge.textContent = items.length;

    if (!items.length) {
      list.innerHTML = '<div class="empty-msg">Queue is empty</div>';
      return;
    }

    list.innerHTML = items.map((item, i) => `
      <div class="dl-item status-${item.status}">
        <div class="dl-header">
          <div>
            <div class="dl-title">${escHtml(item.title || item.url)}</div>
            <div class="dl-artist">${escHtml(item.artist || '')} · ${item.format?.toUpperCase() || ''}</div>
          </div>
          <span class="dl-status-badge badge-${item.status}">${item.status}</span>
        </div>
        <div class="dl-progress-bar">
          <div class="dl-progress-fill" style="width:${item.progress || 0}%"></div>
        </div>
        <div class="dl-meta">
          <span>${i + 1} of ${items.length} in queue</span>
          <span>${item.progress ? Math.round(item.progress) + '%' : ''}</span>
        </div>
      </div>
    `).join('');
  } catch (err) {
    $('#queue-list').innerHTML = '<div class="empty-msg">Failed to load queue</div>';
  }
}

// ── HISTORY ───────────────────────────────────────────────────────────────────
let historyPage = 1, historySearch = '', historySortBy = 'completed_at', historySortDir = 'desc';

function initHistory() {
  const body = $('#history-content');
  body.innerHTML = `
    <div class="history-controls">
      <input type="text" class="history-search" id="h-search" placeholder="Search title or artist…" value="${historySearch}">
      <select class="bitrate-select" id="h-platform" style="min-width:110px">
        <option value="">All platforms</option>
        <option value="youtube">YouTube</option>
        <option value="soundcloud">SoundCloud</option>
        <option value="bandcamp">Bandcamp</option>
      </select>
    </div>
    <div id="history-table-wrap"></div>
    <div class="history-actions-row">
      <button class="btn-secondary" style="color:var(--red)" onclick="clearHistory()">Clear History</button>
    </div>
    <div id="history-pagination"></div>
  `;

  $('#h-search').addEventListener('input', debounce(e => {
    historySearch = e.target.value;
    historyPage = 1;
    fetchHistory();
  }, 400));

  $('#h-platform').addEventListener('change', () => fetchHistory());
  fetchHistory();
}

async function fetchHistory() {
  const platform = $('#h-platform')?.value || '';
  try {
    const data = await api('GET',
      `/api/history?page=${historyPage}&search=${encodeURIComponent(historySearch)}&sort_by=${historySortBy}&sort_dir=${historySortDir}&platform=${platform}`
    );
    renderHistoryTable(data);
  } catch (_) {}
}

function renderHistoryTable(data) {
  const wrap = $('#history-table-wrap');
  if (!data.items?.length) {
    wrap.innerHTML = '<div class="empty-msg">No downloads yet</div>';
    return;
  }

  const cols = [
    { key: 'title', label: 'Title' },
    { key: 'artist', label: 'Artist' },
    { key: 'platform', label: 'Platform' },
    { key: 'format', label: 'Format' },
    { key: 'file_size', label: 'Size' },
    { key: 'completed_at', label: 'Date' },
  ];

  wrap.innerHTML = `
    <table class="history-table">
      <thead>
        <tr>${cols.map(c => `
          <th onclick="sortHistory('${c.key}')" title="Sort by ${c.label}">
            ${c.label} ${historySortBy === c.key ? (historySortDir === 'asc' ? '↑' : '↓') : ''}
          </th>`).join('')}
        </tr>
      </thead>
      <tbody>
        ${data.items.map(row => `
          <tr>
            <td title="${escHtml(row.title || '')}">${escHtml(row.title || '—')}</td>
            <td>${escHtml(row.artist || '—')}</td>
            <td>${escHtml(row.platform || '—')}</td>
            <td>${escHtml((row.format || '').toUpperCase()) || '—'}</td>
            <td>${row.file_size ? fmtSize(row.file_size) : '—'}</td>
            <td>${row.completed_at ? fmtDate(row.completed_at) : '—'}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;

  // Pagination
  const pg = $('#history-pagination');
  if (data.pages > 1) {
    const pages = Array.from({ length: data.pages }, (_, i) => i + 1);
    pg.innerHTML = `<div class="history-pagination">
      ${pages.slice(0, 10).map(p => `
        <button class="page-btn ${p === data.page ? 'active' : ''}" onclick="goHistoryPage(${p})">${p}</button>
      `).join('')}
    </div>`;
  } else {
    pg.innerHTML = '';
  }
}

function sortHistory(col) {
  if (historySortBy === col) {
    historySortDir = historySortDir === 'asc' ? 'desc' : 'asc';
  } else {
    historySortBy = col;
    historySortDir = 'desc';
  }
  fetchHistory();
}

function goHistoryPage(p) { historyPage = p; fetchHistory(); }

async function clearHistory() {
  if (!confirm('Clear all download history?')) return;
  await api('DELETE', '/api/history');
  toast('History cleared', 'info');
  fetchHistory();
}

window.sortHistory = sortHistory;
window.goHistoryPage = goHistoryPage;
window.clearHistory = clearHistory;

// ── STATISTICS ────────────────────────────────────────────────────────────────
async function loadStats() {
  const el = $('#stats-content');
  el.innerHTML = '<div class="empty-msg">Loading…</div>';
  try {
    const s = await api('GET', '/api/statistics');
    el.innerHTML = `
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-value">${s.total_downloads}</div>
          <div class="stat-label">Total Downloads</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${s.total_size_display}</div>
          <div class="stat-label">Total Downloaded</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${s.storage_display}</div>
          <div class="stat-label">Storage Used</div>
        </div>
      </div>

      <div class="chart-container">
        <div class="chart-title">Downloads — Last 7 Days</div>
        <div class="activity-chart">
          ${(() => {
            const counts = s.activity_7d.map(d => d.count);
            const max = Math.max(...counts, 1);
            return counts.map((c, i) => `
              <div class="activity-bar" style="height:${Math.max(3, (c/max)*100)}%"
                   title="${s.activity_7d[i].date}: ${c} downloads"></div>
            `).join('');
          })()}
        </div>
      </div>

      ${s.by_platform?.length ? `
      <div class="chart-container">
        <div class="chart-title">By Platform</div>
        <div class="bar-chart">
          ${(() => {
            const total = s.by_platform.reduce((a, b) => a + b.count, 0) || 1;
            return s.by_platform.map(p => `
              <div class="bar-row">
                <span class="bar-label">${p.platform || 'unknown'}</span>
                <div class="bar-track"><div class="bar-fill" style="width:${(p.count/total)*100}%"></div></div>
                <span class="bar-count">${p.count}</span>
              </div>
            `).join('');
          })()}
        </div>
      </div>` : ''}

      ${s.top_artists?.length ? `
      <div class="chart-container">
        <div class="chart-title">Top Artists</div>
        <div class="bar-chart">
          ${(() => {
            const max = s.top_artists[0]?.count || 1;
            return s.top_artists.slice(0, 5).map(a => `
              <div class="bar-row">
                <span class="bar-label">${escHtml(a.artist || 'Unknown')}</span>
                <div class="bar-track"><div class="bar-fill" style="width:${(a.count/max)*100}%"></div></div>
                <span class="bar-count">${a.count}</span>
              </div>
            `).join('');
          })()}
        </div>
      </div>` : ''}
    `;
  } catch (err) {
    el.innerHTML = `<div class="empty-msg">Error: ${err.message}</div>`;
  }
}

// ── BACKUP ────────────────────────────────────────────────────────────────────
async function loadBackup() {
  const el = $('#backup-content');
  try {
    const backups = await api('GET', '/api/backup');
    el.innerHTML = `
      <div class="backup-actions">
        <button class="btn-secondary" onclick="createBackup()">+ Create Backup</button>
      </div>
      <div class="backup-list" id="backup-list">
        ${!backups.length
          ? '<div class="empty-msg">No backups yet</div>'
          : backups.map(b => `
            <div class="backup-item">
              <div>
                <div class="backup-name">${escHtml(b.filename)}</div>
                <div class="backup-meta">${b.file_count} files · ${fmtSize(b.size)} · ${fmtDate(b.created_at)}</div>
              </div>
              <div class="backup-actions-row">
                <a href="/api/backup/${b.id}/download" class="dl-btn" download>⬇</a>
                <button class="dl-btn danger" onclick="deleteBackup('${b.id}')">✕</button>
              </div>
            </div>
          `).join('')}
      </div>
    `;
  } catch (err) {
    el.innerHTML = `<div class="empty-msg">Error loading backups</div>`;
  }
}

async function createBackup() {
  try {
    toast('Creating backup…', 'info');
    const result = await api('POST', '/api/backup', { selection: 'all' });
    toast(`Backup created: ${result.filename} (${fmtSize(result.size)})`, 'success');
    loadBackup();
  } catch (err) {
    toast('Backup failed: ' + err.message, 'error');
  }
}

async function deleteBackup(id) {
  if (!confirm('Delete this backup?')) return;
  await api('DELETE', `/api/backup/${id}`);
  toast('Backup deleted', 'info');
  loadBackup();
}

window.createBackup = createBackup;
window.deleteBackup = deleteBackup;

// ── WATCHED FOLDER ────────────────────────────────────────────────────────────
async function loadWatched() {
  const el = $('#watched-content');
  try {
    const status = await api('GET', '/api/watched');
    const dot = $('#watch-dot');
    dot.classList.toggle('active', status.watching);

    el.innerHTML = `
      <div class="watched-path">${escHtml(status.file_path)}</div>
      <p style="font-size:12px;color:var(--text-2);margin-bottom:12px">
        ${status.watching ? '● Watching for new URLs every 5 seconds.' : '○ Not watching.'}
        Add one URL per line to the file above.
      </p>
      ${status.urls?.length ? `
        <div class="watched-url-list">
          ${status.urls.map(u => `
            <div class="watched-url-item">
              <span class="watched-url-status ws-${u.status}">${u.status}</span>
              <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(u.url)}</span>
            </div>
          `).join('')}
        </div>
      ` : '<div class="empty-msg">No URLs watched yet</div>'}
    `;
  } catch (err) {
    el.innerHTML = `<div class="empty-msg">Error loading watcher status</div>`;
  }
}

// ── PLATFORM STATUS (header dots) ────────────────────────────────────────────
async function checkPlatforms() {
  try {
    const status = await api('GET', '/api/platforms');
    for (const [p, online] of Object.entries(status)) {
      const dot = $(`.platform-pill[data-platform="${p}"] .status-dot`);
      if (dot) dot.classList.toggle('online', online);
    }
  } catch (_) {}
}

// ── HELP MODAL ────────────────────────────────────────────────────────────────
$('#btn-help').addEventListener('click', () => $('#help-modal').classList.remove('hidden'));
$('#modal-close').addEventListener('click', () => $('#help-modal').classList.add('hidden'));
$('#help-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) e.currentTarget.classList.add('hidden');
});

// ── UTILS ─────────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtSize(bytes) {
  if (!bytes) return '—';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
  return (bytes / 1073741824).toFixed(2) + ' GB';
}

function fmtDur(seconds) {
  if (!seconds) return '';
  const total = Math.floor(seconds);   // yt-dlp returns floats — floor to int
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${m}:${String(s).padStart(2,'0')}`;
}

function fmtDate(dateStr) {
  if (!dateStr) return '—';
  const d = new Date(dateStr);
  return isNaN(d) ? dateStr : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ── INIT ─────────────────────────────────────────────────────────────────────
(function init() {
  checkPlatforms();
  setInterval(checkPlatforms, 60000);

  // Bug #16 fix: Restore active downloads from localStorage but validate each ID
  // against the server first — skip IDs that no longer exist in the DB.
  const saved = JSON.parse(localStorage.getItem('median_active') || '[]');
  saved.forEach(async ({ id, title, artist }) => {
    try {
      const s = await api('GET', `/api/download/${id}/status`);
      // Only resume polling for IDs that still exist and aren't finished
      if (s && !['cleaned', 'error', 'cancelled'].includes(s.status)) {
        pollDownload(id, s.title || title, s.artist || artist);
      }
      // If completed, show it as done without restarting the poller
      else if (s && s.status === 'completed') {
        activeSection.classList.remove('hidden');
        const item = document.createElement('div');
        item.className = 'dl-item status-completed';
        item.id = `dl-${id}`;
        item.innerHTML = buildDlItem(id, s.title || title, s.artist || artist, 'completed', 100, '', '');
        activeList.appendChild(item);
      }
    } catch (_) {
      // ID doesn't exist on server — silently skip (don't start poller)
    }
  });

  // Persist only active (non-finished) download IDs before unload
  window.addEventListener('beforeunload', () => {
    const active = Object.keys(activePollers).map(id => {
      const el = $(`#dl-${id}`);
      return {
        id,
        title: el?.querySelector('.dl-title')?.textContent || '',
        artist: el?.querySelector('.dl-artist')?.textContent || '',
      };
    });
    localStorage.setItem('median_active', JSON.stringify(active));
  });
})();
