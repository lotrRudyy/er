
const state = {
  game: { phase: 1, phase_name: 'standby', phase_display: '1 standby', last_phase: null, last_phase_name: '' },
  nodes: [],
  locks: [],
  lights: [],
  riddles: []
};

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

function renderPlayers() {
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
      <div class="control-head">
        <div class="control-label">${lock.label}</div>
        <span class="status-badge ${lock.state_class}">${lock.state_label}</span>
      </div>
      <button>${lock.button}</button>
    `;
    card.querySelector('button').addEventListener('click', async () => {
      const action = (lock.kind === 'toggle' && lock.is_open) ? 'close' : 'open';
      await api('/api/lock', { method: 'POST', body: JSON.stringify({ lock: lock.id, action }) });
      await refreshState();
    });
    wrap.appendChild(card);
  }
}

function renderLights() {
  const wrap = document.getElementById('lightsGrid');
  wrap.innerHTML = '';
  for (const light of state.lights || []) {
    const card = document.createElement('div');
    card.className = 'control-card';
    const dimInput = light.dimmable
      ? `<input class="dim-input" type="number" min="0" max="100" value="${light.pct}" />`
      : '';
    card.innerHTML = `
      <div class="control-head">
        <div class="control-label">${light.label}</div>
        <span class="status-badge ${light.state_class}">${light.state_label}</span>
      </div>
      <div class="light-actions">
        <button>${light.button}</button>
        ${dimInput}
      </div>
    `;

    const btn = card.querySelector('button');
    btn.addEventListener('click', async () => {
      if (light.dimmable) {
        const input = card.querySelector('.dim-input');
        const pct = Math.max(0, Math.min(100, parseInt(input.value || '0', 10) || 0));
        const action = light.on ? 'off' : 'on';
        await api('/api/light', { method: 'POST', body: JSON.stringify({ group: light.id, action, pct }) });
      } else {
        const action = light.on ? 'off' : 'on';
        await api('/api/light', { method: 'POST', body: JSON.stringify({ group: light.id, action }) });
      }
      await refreshState();
    });

    const input = card.querySelector('.dim-input');
    if (input) {
      input.addEventListener('keydown', async (e) => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        const pct = Math.max(0, Math.min(100, parseInt(input.value || '0', 10) || 0));
        await api('/api/light', { method: 'POST', body: JSON.stringify({ group: light.id, action: 'set_pct', pct }) });
        await refreshState();
      });
    }

    wrap.appendChild(card);
  }
}

function hintCellHtml(riddle) {
  const items = (riddle.hints || []).map(h => `
    <div class="hint-item">
      <span>${h.text}</span>
      <button class="hint-remove" data-hint-id="${h.id}">x</button>
    </div>
  `).join('');
  return `
    <div class="hint-wrap">
      <input class="hint-input" type="text" placeholder="Type hint and press Enter" />
      <div class="hint-list">${items}</div>
    </div>
  `;
}

function renderRiddles() {
  const body = document.getElementById('riddlesBody');
  body.innerHTML = '';
  for (const riddle of state.riddles || []) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${riddle.label}</td>
      <td><span class="status-badge ${riddle.phase_state_class}">${riddle.phase_state}</span></td>
      <td><span class="status-badge ${riddle.node_status_class}">${riddle.node_status}</span></td>
      <td><button class="solve-btn">Solve</button></td>
      <td>${riddle.tries || ''}</td>
      <td>${riddle.info || ''}</td>
      <td>${hintCellHtml(riddle)}</td>
    `;
    tr.querySelector('.solve-btn').addEventListener('click', async () => {
      await api('/api/solve', { method: 'POST', body: JSON.stringify({ node: riddle.id }) });
      await refreshState();
    });
    const hintInput = tr.querySelector('.hint-input');
    hintInput.addEventListener('keydown', async (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      const text = hintInput.value.trim();
      if (!text) return;
      await api('/api/hints', { method: 'POST', body: JSON.stringify({ riddle: riddle.id, text }) });
      hintInput.value = '';
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
  input.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const name = input.value.trim();
    if (!name) return;
    const next = [...(state.game.players || []), name];
    await api('/api/players', { method: 'POST', body: JSON.stringify({ players: next }) });
    input.value = '';
    await refreshState();
  });
}

setInterval(() => {
  refreshState().catch(console.error);
}, 750);

wireTopControls();
refreshState().catch(console.error);
