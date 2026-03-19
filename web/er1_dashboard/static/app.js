
const state = {
  game: { players: [], elapsed_s: 0, started_at: null, ended_at: null, mode: 'MODE_STANDBY' },
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

function fmtTime(total) {
  total = Math.max(0, parseInt(total || 0, 10));
  const h = String(Math.floor(total / 3600)).padStart(2, '0');
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
  const s = String(total % 60).padStart(2, '0');
  return `${h}:${m}:${s}`;
}

function renderPlayers() {
  const wrap = document.getElementById('playersList');
  wrap.innerHTML = '';
  state.game.players.forEach((name, idx) => {
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

function renderLocks() {
  const names = document.getElementById('locksNamesRow');
  const buttons = document.getElementById('locksButtonsRow');
  names.innerHTML = '';
  buttons.innerHTML = '';
  state.locks.forEach(lock => {
    const th = document.createElement('th');
    th.textContent = lock.label;
    names.appendChild(th);

    const td = document.createElement('td');
    const btn = document.createElement('button');
    btn.textContent = lock.button;
    btn.addEventListener('click', async () => {
      const action = (lock.kind === 'toggle' && lock.button === 'Close') ? 'close' : 'open';
      if (lock.kind === 'toggle') {
        lock.button = action === 'open' ? 'Close' : 'Open';
      }
      await api('/api/lock', { method: 'POST', body: JSON.stringify({ lock: lock.id, action }) });
      await refreshState();
    });
    td.appendChild(btn);
    buttons.appendChild(td);
  });
}

function renderLights() {
  const names = document.getElementById('lightsNamesRow');
  const buttons = document.getElementById('lightsButtonsRow');
  names.innerHTML = '';
  buttons.innerHTML = '';
  state.lights.forEach(light => {
    const th = document.createElement('th');
    th.textContent = light.label;
    names.appendChild(th);

    const td = document.createElement('td');
    const btn = document.createElement('button');
    btn.textContent = light.button;
    btn.addEventListener('click', async () => {
      const action = light.button === 'On' ? 'on' : 'off';
      light.button = action === 'on' ? 'Off' : 'On';
      await api('/api/light', { method: 'POST', body: JSON.stringify({ group: light.id, action }) });
      await refreshState();
    });
    td.appendChild(btn);
    buttons.appendChild(td);
  });
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
  state.riddles.forEach(riddle => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${riddle.label}</td>
      <td><button class="solve-btn">Solve</button></td>
      <td>${riddle.hb_state || ''}</td>
      <td>${riddle.solved || ''}</td>
      <td>${riddle.solve_time || ''}</td>
      <td>${riddle.tries || ''}</td>
      <td>${riddle.info || ''}</td>
      <td>${hintCellHtml(riddle)}</td>
    `;
    tr.querySelector('.solve-btn').addEventListener('click', async () => {
      if (riddle.id === 'free_sissi') {
        state.game.mode = 'MODE_STANDBY';
      }
      if (riddle.solved !== 'true') {
        riddle.solved = 'true';
      }
      renderTop();
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
  });
}

function renderTop() {
  document.getElementById('gameMode').textContent = state.game.mode || 'MODE_STANDBY';
  document.getElementById('timer').textContent = fmtTime(state.game.elapsed_s);
  renderPlayers();
}

function renderAll() {
  renderTop();
  renderLocks();
  renderLights();
  renderRiddles();
}

async function refreshState() {
  const data = await api('/api/state');
  state.game = data.game;
  state.locks = data.locks;
  state.lights = data.lights;
  state.riddles = data.riddles;
  renderAll();
}

function wireTopControls() {
  document.querySelectorAll('[data-mode]').forEach(btn => {
    btn.addEventListener('click', async () => {
      state.game.mode = btn.dataset.mode;
      renderTop();
      await api('/api/mode', { method: 'POST', body: JSON.stringify({ mode: btn.dataset.mode }) });
      await refreshState();
    });
  });
  const input = document.getElementById('playerInput');
  input.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const name = input.value.trim();
    if (!name) return;
    const next = [...state.game.players, name];
    await api('/api/players', { method: 'POST', body: JSON.stringify({ players: next }) });
    state.game.players = next;
    input.value = '';
    renderPlayers();
  });
}

setInterval(() => {
  if (state.game.started_at && !state.game.ended_at && state.game.mode === 'MODE_INGAME') {
    state.game.elapsed_s = (state.game.elapsed_s || 0) + 1;
    document.getElementById('timer').textContent = fmtTime(state.game.elapsed_s);
  }
}, 1000);

setInterval(() => {
  refreshState().catch(console.error);
}, 500);

wireTopControls();
refreshState().catch(console.error);
