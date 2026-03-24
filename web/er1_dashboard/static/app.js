const state = {
  game: { phase: 1, phase_name: 'standby', phase_display: '1 standby', last_phase: null, last_phase_name: '', elapsed_s: 0, timer_running: false, players: [] },
  nodes: [],
  locks: [],
  lights: [],
  riddles: []
};

const dimDrafts = {};
const dimEditing = {};
let playerEditing = false;
const hintEditing = new Set();

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

function renderPlayers() {
  const input = document.getElementById('playerInput');
  if (!playerEditing) {
    const currentFocused = document.activeElement === input;
    input.value = '';
    if (currentFocused) input.focus();
  }

  const wrap = document.getElementById('playersList');
  wrap.innerHTML = '';
  (state.game.players || []).forEach((name, idx) => {
    const chip = document.createElement('div');
    chip.className = 'player-chip';
    chip.innerHTML = `<span>${name}</span>`;
    const btn = document.createElement('button');
    btn.className = 'player-remove';
    btn.textContent = 'x';
    btn.addEventListener('click', async () => {
      const next = state.game.players.filter((_, i) => i !== idx);
      await api('/api/players', { method: 'POST', body: JSON.stringify({ players: next }) });
      state.game.players = next;
      renderPlayers();
    });
    chip.appendChild(btn);
    wrap.appendChild(chip);
  });
}

function renderTop() {
  document.getElementById('phaseValue').textContent = state.game.phase_display || `${state.game.phase} ${state.game.phase_name}`;
  document.getElementById('lastPhaseValue').textContent = state.game.last_phase == null
    ? '—'
    : `${state.game.last_phase} ${state.game.last_phase_name || ''}`.trim();
  document.getElementById('timerValue').textContent = fmtTime(state.game.elapsed_s || 0);
  renderPlayers();
}

function renderNodes() {
  const body = document.getElementById('nodesBody');
  body.innerHTML = '';
  for (const item of state.nodes || []) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${item.label}</td>
      <td><span class="status-badge ${item.online ? 'status-online' : 'status-offline'}">${item.status}</span></td>
    `;
    body.appendChild(tr);
  }
}

function renderLocks() {
  const wrap = document.getElementById('locksGrid');
  wrap.innerHTML = '';
  for (const lock of state.locks || []) {
    const card = document.createElement('div');
    card.className = 'control-card';
    card.innerHTML = `
      <div class="control-stack">
        <button class="control-button ${lock.is_open ? 'is-open' : 'is-closed'}">
          ${lock.label}
          <span class="state-line">${(lock.state_label || '').toUpperCase()}</span>
        </button>
      </div>
    `;
    card.querySelector('button').addEventListener('click', async () => {
      const action = (lock.kind === 'toggle' && lock.is_open) ? 'close' : 'open';
      await api('/api/lock', { method: 'POST', body: JSON.stringify({ lock: lock.id, action }) });
      await refreshState();
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
      buttons[0].addEventListener('click', async () => {
        const input = card.querySelector('.dim-input');
        let pct = Math.max(0, Math.min(100, parseInt(input.value || '0', 10) || 0));
        const action = light.on ? 'off' : 'on';
        if (!light.on && pct <= 0) pct = 100;
        dimDrafts[light.id] = action === 'off' ? 0 : pct;
        await api('/api/light', { method: 'POST', body: JSON.stringify({ group: light.id, action, pct }) });
        await refreshState();
      });
      buttons[1].addEventListener('click', async () => {
        const input = card.querySelector('.dim-input');
        let pct = Math.max(0, Math.min(100, parseInt(input.value || '0', 10) || 0));
        const action = light.on ? 'off' : 'on';
        if (!light.on && pct <= 0) pct = 100;
        dimDrafts[light.id] = action === 'off' ? 0 : pct;
        await api('/api/light', { method: 'POST', body: JSON.stringify({ group: light.id, action, pct }) });
        await refreshState();
      });

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
        await refreshState();
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
        await refreshState();
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

function hintCellHtml(riddle) {
  const items = (riddle.hints || []).map(h => `
    <div class="hint-item">
      <span>${h.text}</span>
      <button class="hint-remove" data-hint-id="${h.id}">x</button>
    </div>
  `).join('');
  const inputValue = hintEditing.has(riddle.id)
    ? (document.querySelector(`tr[data-riddle-id="${riddle.id}"] .hint-input`)?.value || '')
    : '';
  return `
    <div class="hint-wrap">
      <input class="hint-input" type="text" placeholder="Type hint and press Enter" value="${inputValue}" />
      <div class="hint-list">${items}</div>
    </div>
  `;
}

function renderRiddles() {
  const body = document.getElementById('riddlesBody');
  const activeHintValues = {};
  document.querySelectorAll('#riddlesBody tr[data-riddle-id]').forEach(row => {
    const rid = row.dataset.riddleId;
    const input = row.querySelector('.hint-input');
    if (input && document.activeElement === input) {
      activeHintValues[rid] = input.value;
      hintEditing.add(rid);
    }
  });

  body.innerHTML = '';
  for (const riddle of state.riddles || []) {
    const infoHtml = riddle.id === 'images'
      ? renderImagesButtons(riddle.images_buttons)
      : (riddle.info || '');
    const isActive = riddle.phase_state === 'active';
    const tr = document.createElement('tr');
    tr.dataset.riddleId = riddle.id;
    tr.innerHTML = `
      <td>${riddle.label}</td>
      <td><span class="status-badge ${riddle.phase_state_class}">${riddle.phase_state}</span></td>
      <td><button class="solve-btn ${isActive ? 'active' : 'inactive'}" ${isActive ? '' : 'disabled'}>Solve</button></td>
      <td>${infoHtml}</td>
      <td>${hintCellHtml(riddle)}</td>
    `;
    const solveBtn = tr.querySelector('.solve-btn');
    if (isActive) {
      solveBtn.addEventListener('click', async () => {
        await api('/api/solve', { method: 'POST', body: JSON.stringify({ node: riddle.id }) });
        await refreshState();
      });
    }

    const hintInput = tr.querySelector('.hint-input');
    if (activeHintValues[riddle.id] != null) hintInput.value = activeHintValues[riddle.id];
    hintInput.addEventListener('focus', () => hintEditing.add(riddle.id));
    hintInput.addEventListener('blur', () => hintEditing.delete(riddle.id));
    hintInput.addEventListener('keydown', async (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      const text = hintInput.value.trim();
      if (!text) return;
      await api('/api/hints', { method: 'POST', body: JSON.stringify({ riddle: riddle.id, text }) });
      hintInput.value = '';
      hintEditing.delete(riddle.id);
      await refreshState();
    });
    tr.querySelectorAll('.hint-remove').forEach(btn => {
      btn.addEventListener('click', async () => {
        await api(`/api/hints/${riddle.id}/${btn.dataset.hintId}`, { method: 'DELETE' });
        await refreshState();
      });
    });
    body.appendChild(tr);
  }
}

function renderAll() {
  renderTop();
  renderNodes();
  renderLocks();
  renderLights();
  renderRiddles();
}

async function refreshState() {
  const data = await api('/api/state');
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
  renderAll();
}

function wireTopControls() {
  document.querySelectorAll('[data-phase-action]').forEach(btn => {
    btn.addEventListener('click', async () => {
      await api('/api/phase', { method: 'POST', body: JSON.stringify({ action: btn.dataset.phaseAction }) });
      await refreshState();
    });
  });

  const input = document.getElementById('playerInput');
  input.addEventListener('focus', () => { playerEditing = true; });
  input.addEventListener('blur', () => { playerEditing = false; });
  input.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const name = input.value.trim();
    if (!name) return;
    const next = [...(state.game.players || []), name];
    await api('/api/players', { method: 'POST', body: JSON.stringify({ players: next }) });
    input.value = '';
    playerEditing = false;
    await refreshState();
  });
}

setInterval(() => {
  if (state.game.timer_running) {
    state.game.elapsed_s = (state.game.elapsed_s || 0) + 1;
    document.getElementById('timerValue').textContent = fmtTime(state.game.elapsed_s);
  }
}, 1000);

setInterval(() => {
  refreshState().catch(console.error);
}, 1000);

wireTopControls();
refreshState().catch(console.error);
