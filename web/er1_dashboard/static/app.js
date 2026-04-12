const state = {
  game: { phase: 0, last_phase: null, phase_name: 'standby', phase_display: '0 standby', last_phase_name: '', elapsed_s: 0, current_riddle_elapsed_s: 0, current_riddle_name: '', timer_running: false, players_count: 0 },
  nodes: [],
  locks: [],
  lights: [],
  riddles: []
};

let lastSnapshot = null;
let localTimerBaseElapsed = 0;
let localTimerStartedAt = 0;
let localRiddleTimerBaseElapsed = 0;
let localRiddleTimerStartedAt = 0;

const dimDrafts = {};
const dimEditing = {};
let playersCountEditing = false;

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

function fmtTime(total) {
  total = Math.max(0, parseInt(total || 0, 10));
  const h = String(Math.floor(total / 3600)).padStart(2, '0');
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
  const s = String(total % 60).padStart(2, '0');
  return `${h}:${m}:${s}`;
}

function stableStringify(value) {
  return JSON.stringify(value);
}

function sectionChanged(nextData, key) {
  if (!lastSnapshot) return true;
  return stableStringify(lastSnapshot[key]) !== stableStringify(nextData[key]);
}

function parseIsoToMs(value) {
  if (!value) return 0;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : 0;
}

function syncLocalTimers(game) {
  const startedAtMs = parseIsoToMs(game.started_at);
  if (game.timer_running && startedAtMs) {
    localTimerStartedAt = startedAtMs;
    localTimerBaseElapsed = 0;
  } else {
    localTimerStartedAt = 0;
    localTimerBaseElapsed = parseInt(game.elapsed_s || 0, 10) || 0;
  }

  const riddleStartedAtMs = parseIsoToMs(game.current_riddle_started_at);
  if (game.timer_running && game.current_riddle_name && riddleStartedAtMs) {
    localRiddleTimerStartedAt = riddleStartedAtMs;
    localRiddleTimerBaseElapsed = 0;
  } else {
    localRiddleTimerStartedAt = 0;
    localRiddleTimerBaseElapsed = parseInt(game.current_riddle_elapsed_s || 0, 10) || 0;
  }
}

function readLocalTimer() {
  if (!state.game.timer_running || !localTimerStartedAt) return parseInt(localTimerBaseElapsed || 0, 10);
  return Math.max(0, Math.floor((Date.now() - localTimerStartedAt) / 1000));
}

function readLocalRiddleTimer() {
  if (!state.game.timer_running || !state.game.current_riddle_name || !localRiddleTimerStartedAt) return parseInt(localRiddleTimerBaseElapsed || 0, 10);
  return Math.max(0, Math.floor((Date.now() - localRiddleTimerStartedAt) / 1000));
}

function renderPlayersCountControls() {
  const input = document.getElementById('playersCountInput');
  const stored = document.getElementById('playersCountStored');
  const currentValue = Math.max(0, parseInt(state.game.players_count || 0, 10) || 0);
  if (stored) stored.textContent = `(gespeichert: ${currentValue})`;
  if (!input) return;
  if (!playersCountEditing && document.activeElement !== input) {
    input.value = String(currentValue);
  }
}

function renderTop() {
  document.getElementById('phaseValue').textContent = state.game.phase_display || `Phase ${state.game.phase}: ${state.game.phase_name_pretty || state.game.phase_name}`;
  document.getElementById('lastPhaseValue').textContent = state.game.last_phase == null
    ? '—'
    : `${state.game.last_phase}: ${state.game.last_phase_name_pretty || state.game.last_phase_name || ''}`.trim();
  document.getElementById('timerValue').textContent = fmtTime(readLocalTimer());
  document.getElementById('riddleTimerValue').textContent = fmtTime(readLocalRiddleTimer());
  renderPlayersCountControls();
}

function renderNodes() {
  const wrap = document.getElementById('nodesList');
  wrap.innerHTML = '';
  for (const item of state.nodes || []) {
    const line = document.createElement('div');
    line.className = `node-line ${item.online ? 'node-on' : 'node-off'}`;
    line.textContent = `${item.label} (${item.status})`;
    wrap.appendChild(line);
  }
}

function renderLocks() {
  const wrap = document.getElementById('locksGrid');
  wrap.innerHTML = '';
  for (const lock of state.locks || []) {
    const card = document.createElement('div');
    card.className = 'control-card';
    const open = lock.is_open === true;
    card.innerHTML = `
      <div class="control-stack">
        <button class="control-button ${open ? 'is-open' : 'is-closed'}">
          ${lock.label}
          <span class="state-line">${(lock.state_label || '').toUpperCase()}</span>
        </button>
      </div>
    `;
    card.querySelector('button').addEventListener('click', async () => {
      const action = (lock.kind === 'toggle' && open) ? 'close' : 'open';
      await api('/api/lock', { method: 'POST', body: JSON.stringify({ lock: lock.id, action }) });
      await fetchAndPatch();
    });
    wrap.appendChild(card);
  }
}

function resolveDimValue(light) {
  if (dimEditing[light.id]) return dimDrafts[light.id] ?? light.pct;
  if (dimDrafts[light.id] != null) return dimDrafts[light.id];
  return light.pct;
}

function renderLights() {
  const wrap = document.getElementById('lightsGrid');
  wrap.innerHTML = '';
  for (const light of state.lights || []) {
    const card = document.createElement('div');
    card.className = 'control-card';
    const buttonClass = light.on ? 'is-on' : 'is-off';

    if (light.dimmable) {
      const dimValue = resolveDimValue(light);
      card.innerHTML = `
        <div class="control-stack">
          <button class="control-button ${buttonClass}">
            ${light.label}
            <span class="state-line">${light.on ? `ON (${light.pct}%)` : `OFF (${light.pct}%)`}</span>
          </button>
          <div class="dim-row">
            <button class="control-button ${buttonClass}">${light.on ? 'Off' : 'On'}</button>
            <input class="dim-input" type="number" min="0" max="100" value="${dimValue}" />
          </div>
        </div>
      `;
      const buttons = card.querySelectorAll('button');
      for (const btn of buttons) {
        btn.addEventListener('click', async () => {
          const input = card.querySelector('.dim-input');
          let pct = Math.max(0, Math.min(100, parseInt(input.value || '0', 10) || 0));
          const action = light.on ? 'off' : 'on';
          if (!light.on && pct <= 0) pct = 100;
          dimDrafts[light.id] = action === 'off' ? 0 : pct;
          await api('/api/light', { method: 'POST', body: JSON.stringify({ group: light.id, action, pct }) });
          await fetchAndPatch();
        });
      }

      const input = card.querySelector('.dim-input');
      input.addEventListener('focus', () => { dimEditing[light.id] = true; });
      input.addEventListener('blur', () => { dimEditing[light.id] = false; });
      input.addEventListener('input', () => { dimDrafts[light.id] = input.value; });
      input.addEventListener('keydown', async (e) => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        const pct = Math.max(0, Math.min(100, parseInt(input.value || '0', 10) || 0));
        dimDrafts[light.id] = pct;
        dimEditing[light.id] = false;
        await api('/api/light', { method: 'POST', body: JSON.stringify({ group: light.id, action: 'set_pct', pct }) });
        await fetchAndPatch();
      });
    } else {
      card.innerHTML = `
        <div class="control-stack">
          <button class="control-button ${buttonClass}">
            ${light.label}
            <span class="state-line">${light.on ? 'ON' : 'OFF'}</span>
          </button>
        </div>
      `;
      card.querySelector('button').addEventListener('click', async () => {
        const action = light.on ? 'off' : 'on';
        await api('/api/light', { method: 'POST', body: JSON.stringify({ group: light.id, action }) });
        await fetchAndPatch();
      });
    }
    wrap.appendChild(card);
  }
}

function renderImagesButtons(buttons) {
  if (!buttons) return '';
  const order = [['jesus', 'Jesus'], ['blumen', 'Blumen'], ['natur', 'Natur'], ['puppe', 'Puppe']];
  return `
    <div class="images-buttons">
      ${order.map(([key, label]) => `
        <span class="status-badge ${buttons[key] ? 'pill-true' : 'pill-false'}">${label}</span>
      `).join('')}
    </div>
  `;
}

function renderChessSlots(slots) {
  if (!slots || !slots.length) return '';
  return `
    <div class="inline-info inline-info-wrap">
      ${slots.map(slot => `
        <span>${slot.slot}: <span class="inline-status ${slot.correct ? 'inline-status-good' : 'inline-status-bad'}">${slot.value}</span></span>
      `).join('<span class="inline-sep">|</span>')}
    </div>
  `;
}

function renderAttemptsSummary(summary) {
  if (!summary) return '';
  const attempts = (summary.attempts || []).join(' <span class="inline-sep">|</span> ');
  if (!attempts) return '';
  return `<div class="inline-info">${attempts}</div>`;
}

function renderStarSliderSummary(summary) {
  if (!summary) return '';
  const current = (summary.current || []).join(' ');
  const attempts = (summary.attempts || []).map(item => `(${item.join(' ')})`).join(' ');
  return `
    <div class="inline-info inline-info-wrap">
      <span>Current: ${current || 'none'}</span>
      <span class="inline-sep">|</span>
      <span>Attempts: ${attempts || '—'}</span>
    </div>
  `;
}

function renderPianoSummary(summary) {
  if (!summary || !summary.played_notes || !summary.played_notes.length) return '';
  return `
    <div class="inline-info inline-info-wrap">
      <span><b>Played Notes:</b> ${summary.played_notes.map(n => {
        const cls = n.accepted ? 'inline-status-good' : 'inline-status-bad';
        const text = (n.encoded || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        return `<span class="inline-status ${cls}">${text}</span>`;
      }).join(' ')}</span>
    </div>
  `;
}

function renderRiddleInfo(riddle) {
  if (riddle.id === 'images') return renderImagesButtons(riddle.images_buttons);
  if (riddle.id === 'piano') return renderPianoSummary(riddle.piano_summary);
  if (riddle.id === 'chess') return renderChessSlots(riddle.chess_slots);
  if (riddle.id === 'knocking' || riddle.id === 'candles') return renderAttemptsSummary(riddle.attempts_summary);
  if (riddle.id === 'star_slider' || riddle.id === 'stars') return renderStarSliderSummary(riddle.star_slider_summary);
  return riddle.info || '';
}

function hintCellHtml(riddle) {
  const count = Math.max(0, parseInt(riddle.hint_count || 0, 10) || 0);
  return `
    <div class="hint-counter-wrap">
      <span class="hint-counter-value">${count}</span>
      <div class="hint-counter-buttons">
        <button class="hint-counter-btn" data-delta="1">+</button>
        <button class="hint-counter-btn" data-delta="-1">-</button>
      </div>
    </div>
  `;
}

function buildRiddleRow(riddle) {
  const infoHtml = renderRiddleInfo(riddle);
  const currentPhaseName = (state.game.phase_name || '').trim();
  const canSolve = riddle.phase_state === 'active' || riddle.phase_state === 'solved_pending' || (riddle.manual && currentPhaseName === riddle.id && riddle.phase_state !== 'solved');
  const tr = document.createElement('tr');
  tr.dataset.riddleId = riddle.id;
  tr.innerHTML = `
    <td>${riddle.label}</td>
    <td><span class="status-badge ${riddle.phase_state_class}">${riddle.phase_state_label || riddle.phase_state}</span></td>
    <td><button class="solve-btn ${canSolve ? 'active' : 'inactive'}" ${canSolve ? '' : 'disabled'}>Solve</button></td>
    <td>${infoHtml}</td>
    <td>${hintCellHtml(riddle)}</td>
  `;

  const solveBtn = tr.querySelector('.solve-btn');
  if (canSolve && riddle.phase_state !== 'solved') {
    solveBtn.addEventListener('click', async () => {
      await api('/api/solve', { method: 'POST', body: JSON.stringify({ node: riddle.id }) });
      await fetchAndPatch();
    });
  }

  tr.querySelectorAll('.hint-counter-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const delta = parseInt(btn.dataset.delta || '0', 10) || 0;
      if (!delta) return;
      await api('/api/hints', { method: 'POST', body: JSON.stringify({ riddle: riddle.id, delta }) });
      await fetchAndPatch();
    });
  });

  return tr;
}

function renderRiddles() {
  const leftBody = document.getElementById('riddlesBodyLeft');
  const rightBody = document.getElementById('riddlesBodyRight');
  leftBody.innerHTML = '';
  rightBody.innerHTML = '';

  const riddles = state.riddles || [];
  const splitIndex = Math.ceil(riddles.length / 2);
  riddles.slice(0, splitIndex).forEach(riddle => leftBody.appendChild(buildRiddleRow(riddle)));
  riddles.slice(splitIndex).forEach(riddle => rightBody.appendChild(buildRiddleRow(riddle)));
}

function patchState(data) {
  const rerenderTop = sectionChanged(data, 'game');
  const rerenderNodes = sectionChanged(data, 'nodes');
  const rerenderLocks = sectionChanged(data, 'locks');
  const rerenderLights = sectionChanged(data, 'lights');
  const rerenderRiddles = sectionChanged(data, 'riddles');

  syncLocalTimers(data.game || state.game);

  state.game = data.game || state.game;
  state.nodes = data.nodes || [];
  state.locks = data.locks || [];
  state.lights = data.lights || [];
  state.riddles = data.riddles || [];

  for (const light of state.lights) {
    if (!dimEditing[light.id] && light.dimmable && dimDrafts[light.id] == null) {
      dimDrafts[light.id] = light.pct;
    }
  }

  if (rerenderTop) renderTop();
  if (rerenderNodes) renderNodes();
  if (rerenderLocks) renderLocks();
  if (rerenderLights) renderLights();
  if (rerenderRiddles) renderRiddles();

  if (!lastSnapshot) {
    renderTop();
    renderNodes();
    renderLocks();
    renderLights();
    renderRiddles();
  }

  lastSnapshot = data;
}

async function fetchAndPatch() {
  const data = await api('/api/state');
  patchState(data);
}

function wireTopControls() {
  document.querySelectorAll('[data-phase-action]').forEach(btn => {
    btn.addEventListener('click', async () => {
      await api('/api/phase', { method: 'POST', body: JSON.stringify({ action: btn.dataset.phaseAction }) });
      await fetchAndPatch();
    });
  });

  const input = document.getElementById('playersCountInput');
  const saveButton = document.getElementById('playersCountSave');

  const getDraftCount = () => Math.max(0, parseInt(String(input?.value ?? state.game.players_count ?? 0).replace(/[^0-9-]/g, ''), 10) || 0, 0);

  const savePlayersCount = async (nextCount) => {
    const normalized = Math.max(0, parseInt(nextCount || 0, 10) || 0);
    const response = await api('/api/players-count', { method: 'POST', body: JSON.stringify({ players_count: normalized }) });
    state.game.players_count = Math.max(0, parseInt(response?.players_count ?? normalized, 10) || 0);
    playersCountEditing = false;
    renderPlayersCountControls();
    await fetchAndPatch();
  };

  if (input) {
    input.addEventListener('focus', () => { playersCountEditing = true; });
    input.addEventListener('blur', () => { playersCountEditing = false; });
    input.addEventListener('input', () => { input.value = input.value.replace(/[^0-9]/g, ''); });
    input.addEventListener('keydown', async (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      await savePlayersCount(getDraftCount());
    });
  }

  saveButton?.addEventListener('mousedown', () => { playersCountEditing = true; });
  saveButton?.addEventListener('click', async () => { await savePlayersCount(getDraftCount()); });
}

setInterval(() => {
  document.getElementById('timerValue').textContent = fmtTime(readLocalTimer());
  document.getElementById('riddleTimerValue').textContent = fmtTime(readLocalRiddleTimer());
}, 250);

setInterval(() => {
  fetchAndPatch().catch(console.error);
}, 1000);

wireTopControls();
fetchAndPatch().catch(console.error);
