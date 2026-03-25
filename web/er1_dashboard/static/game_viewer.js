const viewerStatus = document.getElementById('viewerStatus');
const viewerContent = document.getElementById('viewerContent');
const gameIdInput = document.getElementById('gameIdInput');
const loadGameBtn = document.getElementById('loadGameBtn');

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmtSeconds(value) {
  if (value == null || value === '') return '—';
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  return `${num.toFixed(3)} s`;
}

function renderSummary(game) {
  const grid = document.getElementById('gameSummaryGrid');
  const players = (game.player_names || []).length ? game.player_names.join(', ') : '—';
  const items = [
    ['Game ID', game.id],
    ['Date', game.date || '—'],
    ['Started At', game.started_at || '—'],
    ['Ended At', game.ended_at || '—'],
    ['Duration', fmtSeconds(game.duration_s)],
    ['Players', players],
    ['Hint Count', game.hint_count ?? '0'],
  ];
  grid.innerHTML = items.map(([label, value]) => `
    <div class="viewer-summary-card">
      <div class="viewer-summary-label">${escapeHtml(label)}</div>
      <div class="viewer-summary-value">${escapeHtml(value)}</div>
    </div>
  `).join('');
}

function renderRiddles(rows) {
  const body = document.getElementById('viewerRiddlesBody');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7">No riddle rows found.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.riddle)}</td>
      <td>${escapeHtml(row.source)}</td>
      <td>${escapeHtml(row.activated_at || '—')}</td>
      <td>${escapeHtml(row.solved_at || '—')}</td>
      <td><span class="status-badge ${row.solved ? 'phase-solved' : 'phase-pending'}">${row.solved ? 'yes' : 'no'}</span></td>
      <td>${escapeHtml(fmtSeconds(row.solve_time_from_run_start_s))}</td>
      <td>${escapeHtml(fmtSeconds(row.solve_time_from_activation_s))}</td>
    </tr>
  `).join('');
}

function renderHints(rows) {
  const body = document.getElementById('viewerHintsBody');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="4">No hints saved for this game.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.id)}</td>
      <td>${escapeHtml(row.at || '—')}</td>
      <td>${escapeHtml(row.riddle)}</td>
      <td>${escapeHtml(row.hint_text)}</td>
    </tr>
  `).join('');
}

async function loadGame(gameId) {
  viewerStatus.textContent = 'Loading...';
  viewerStatus.className = 'viewer-status';
  viewerContent.hidden = true;
  try {
    const res = await fetch(`/api/game/${encodeURIComponent(gameId)}`);
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Failed to load game');
    renderSummary(data.game);
    renderRiddles(data.riddles || []);
    renderHints(data.hints || []);
    viewerStatus.textContent = `Loaded game ${data.game.id} from ${data.meta.db_path}`;
    viewerStatus.className = 'viewer-status ok';
    viewerContent.hidden = false;
  } catch (err) {
    viewerStatus.textContent = err.message || String(err);
    viewerStatus.className = 'viewer-status error';
  }
}

loadGameBtn.addEventListener('click', async () => {
  const gameId = gameIdInput.value.trim();
  if (!gameId) {
    viewerStatus.textContent = 'Please enter a game id.';
    viewerStatus.className = 'viewer-status error';
    return;
  }
  await loadGame(gameId);
});

gameIdInput.addEventListener('keydown', async (e) => {
  if (e.key !== 'Enter') return;
  e.preventDefault();
  loadGameBtn.click();
});
