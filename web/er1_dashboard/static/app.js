const state = {
  game: { phase: 0, last_phase: null, phase_name: 'standby', phase_display: '0 standby', last_phase_name: '', elapsed_s: 0, current_riddle_elapsed_s: 0, current_riddle_name: '', timer_running: false, players_count: 0 },
  nodes: [],
  locks: [],
  lights: [],
  riddles: [],
  booking: { kind: 'test', bookingCode: '', customerEmail: 'rudolf.dosser@gmail.com', players: 2, label: 'Test booking' }
};

let lastSnapshot = null;
let localTimerBaseElapsed = 0;
let localTimerStartedAt = 0;
let localRiddleTimerBaseElapsed = 0;
let localRiddleTimerStartedAt = 0;

const dimDrafts = {};
const dimEditing = {};
const riddleTimeDrafts = {};
const riddleTimeEditing = {};
let summaryEmailBusy = false;
let bookingOptions = [];
let bookingOptionsLoaded = false;
let bookingBusy = false;
const TEST_BOOKING_ID = '__test__';

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

function escapeAttr(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function anyRiddleTimeEditing() {
  return Object.values(riddleTimeEditing).some(Boolean);
}

function riddleTimeText(riddle) {
  if (riddleTimeEditing[riddle.id] || riddleTimeDrafts[riddle.id] != null) {
    return String(riddleTimeDrafts[riddle.id] ?? '');
  }
  return fmtTime(riddle.display_time_s ?? riddle.time_s ?? riddle.live_time_s ?? riddle.solve_time_s ?? 0);
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

function safeInt(value, fallback = 0) {
  const parsed = parseInt(String(value ?? '').replace(/[^0-9-]/g, ''), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizeBooking(raw = {}) {
  const kind = String(raw.kind || raw.type || '').toLowerCase() === 'test' || String(raw.id || raw.bookingCode || raw.booking_code || '') === TEST_BOOKING_ID
    ? 'test'
    : 'booking';
  const players = Math.max(1, safeInt(raw.players ?? raw.players_count ?? raw.playerCount, kind === 'test' ? 2 : 1));
  const customerEmail = String(raw.customerEmail || raw.customer_email || raw.email || '').trim();
  const bookingCode = String(raw.bookingCode || raw.booking_code || '').trim();
  const id = kind === 'test' ? TEST_BOOKING_ID : String(raw.id || bookingCode || `${raw.date || ''}-${raw.slot || ''}-${customerEmail}`);
  return {
    ...raw,
    id,
    kind,
    bookingCode,
    customerEmail,
    customerName: String(raw.customerName || raw.customer_name || raw.name || '').trim(),
    date: String(raw.date || '').trim(),
    slot: String(raw.slot || '').trim(),
    players,
    label: String(raw.label || '').trim()
  };
}

function bookingKey(booking = {}) {
  const normalized = normalizeBooking(booking);
  return normalized.kind === 'test' ? TEST_BOOKING_ID : String(normalized.id || normalized.bookingCode || '');
}

function selectedBookingDraft(baseBooking = state.booking) {
  const selected = normalizeBooking(baseBooking || {});
  const emailInput = document.getElementById('testBookingEmail');
  const playersInput = document.getElementById('testBookingPlayers');
  const fallbackEmail = selected.kind === 'test' ? 'rudolf.dosser@gmail.com' : selected.customerEmail;
  return normalizeBooking({
    ...selected,
    customerEmail: String(emailInput?.value || fallbackEmail || '').trim(),
    players: Math.max(1, safeInt(playersInput?.value, selected.players || (selected.kind === 'test' ? 2 : 1))),
    label: selected.kind === 'test' ? 'Test booking' : selected.label
  });
}

function bookingOptionLabel(booking) {
  const item = normalizeBooking(booking);
  if (item.kind === 'test') return 'Test booking';
  const main = [item.date, item.slot].filter(Boolean).join(' ');
  const who = item.customerName || item.customerEmail || item.bookingCode || `Booking ${item.id}`;
  const pieces = [];
  if (main) pieces.push(main);
  pieces.push(`${item.players}P`);
  pieces.push(who);
  if (item.bookingCode) pieces.push(item.bookingCode);
  return pieces.join(' · ');
}

function setBookingFeedback(message, kind = '') {
  const box = document.getElementById('bookingFeedback');
  if (!box) return;
  box.textContent = message || '';
  box.className = `summary-email-feedback ${kind ? `summary-email-feedback--${kind}` : ''}`;
}

async function loadBookings() {
  const fallback = normalizeBooking({ kind: 'test', id: TEST_BOOKING_ID, customerEmail: 'rudolf.dosser@gmail.com', players: 2, label: 'Test booking' });
  try {
    const result = await api('/api/bookings');
    const loaded = Array.isArray(result.bookings) ? result.bookings.map(normalizeBooking) : [];
    bookingOptions = loaded.length ? loaded : [fallback];
    if (!bookingOptions.some(item => item.kind === 'test')) bookingOptions.unshift(fallback);
    bookingOptionsLoaded = true;
    setBookingFeedback(result.warning ? result.warning : '', result.warning ? 'warn' : '');
  } catch (error) {
    bookingOptions = [fallback];
    bookingOptionsLoaded = true;
    setBookingFeedback(error.message || 'Could not load bookings.', 'warn');
  }
  renderBookingControls();
}

async function saveSelectedBooking(booking) {
  const normalized = normalizeBooking(booking);
  bookingBusy = true;
  renderBookingControls();
  try {
    const result = await api('/api/select-booking', { method: 'POST', body: JSON.stringify({ booking: normalized }) });
    state.booking = normalizeBooking(result.booking || normalized);
    const savedKey = bookingKey(state.booking);
    bookingOptions = bookingOptions.map(item => bookingKey(item) === savedKey ? state.booking : item);
    if (savedKey && !bookingOptions.some(item => bookingKey(item) === savedKey)) bookingOptions.unshift(state.booking);
    state.game.players_count = state.booking.players;
    setBookingFeedback(state.booking.kind === 'test' ? 'Test booking selected.' : 'Booking selected and player count sent to game master.', 'ok');
    await fetchAndPatch();
  } catch (error) {
    setBookingFeedback(error.message || 'Could not select booking.', 'error');
  } finally {
    bookingBusy = false;
    renderBookingControls();
  }
}

function renderBookingControls() {
  const select = document.getElementById('bookingSelect');
  const info = document.getElementById('bookingSelectedInfo');
  const testFields = document.getElementById('testBookingFields');
  const testEmail = document.getElementById('testBookingEmail');
  const testPlayers = document.getElementById('testBookingPlayers');
  const refreshButton = document.getElementById('refreshBookingsBtn');
  if (!select) return;

  if (!bookingOptionsLoaded && !bookingOptions.length) {
    bookingOptions = [normalizeBooking({ kind: 'test', id: TEST_BOOKING_ID, customerEmail: 'rudolf.dosser@gmail.com', players: 2, label: 'Test booking' })];
  }

  const current = normalizeBooking(state.booking || bookingOptions[0] || {});
  const currentKey = bookingKey(current);
  if (currentKey && !bookingOptions.some(item => bookingKey(item) === currentKey)) {
    bookingOptions = [current, ...bookingOptions];
  }
  const optionSignature = bookingOptions.map(item => `${bookingKey(item)}:${bookingOptionLabel(item)}`).join('|');
  if (select.dataset.signature !== optionSignature) {
    select.innerHTML = bookingOptions.map(item => `<option value="${escapeAttr(bookingKey(item))}">${escapeAttr(bookingOptionLabel(item))}</option>`).join('');
    select.dataset.signature = optionSignature;
  }
  select.value = currentKey;
  select.disabled = bookingBusy;
  if (refreshButton) refreshButton.disabled = bookingBusy;

  const isTest = current.kind === 'test';
  if (testFields) testFields.classList.remove('hidden');
  if (testEmail && document.activeElement !== testEmail) testEmail.value = current.customerEmail || (isTest ? 'rudolf.dosser@gmail.com' : '');
  if (testPlayers && document.activeElement !== testPlayers) testPlayers.value = String(current.players || (isTest ? 2 : 1));

  if (info) {
    if (isTest) {
      info.textContent = `Test booking · ${current.players || 2} players · ${current.customerEmail || 'no email'}`;
    } else {
      const line1 = [current.date, current.slot, `${current.players || 0} players`].filter(Boolean).join(' · ');
      const line2 = [current.customerName, current.customerEmail, current.bookingCode].filter(Boolean).join(' · ');
      info.textContent = [line1, line2].filter(Boolean).join(' | ') || 'No booking selected';
    }
  }
}

function renderSummaryControls() {
  const sendButton = document.getElementById('sendSummaryEmailBtn');
  if (!sendButton) return;
  const code = String(state.game.leaderboard_code || '').trim();
  const finished = Number(state.game.phase || 0) >= 14 || Boolean(state.game.ended_at);
  const booking = selectedBookingDraft();
  const targetEmail = String(booking.customerEmail || '').trim();
  sendButton.disabled = summaryEmailBusy || !finished || !code || !targetEmail;
  sendButton.textContent = summaryEmailBusy
    ? 'Sending…'
    : (code ? `Send summary email (${code})` : 'Send summary email');
}

function setSummaryFeedback(message, kind = '') {
  const box = document.getElementById('summaryEmailFeedback');
  if (!box) return;
  box.textContent = message || '';
  box.className = `summary-email-feedback ${kind ? `summary-email-feedback--${kind}` : ''}`;
}

function renderTop() {
  document.getElementById('phaseValue').textContent = state.game.phase_display || `Phase ${state.game.phase}: ${state.game.phase_name_pretty || state.game.phase_name}`;
  document.getElementById('lastPhaseValue').textContent = state.game.last_phase == null
    ? '—'
    : `${state.game.last_phase}: ${state.game.last_phase_name_pretty || state.game.last_phase_name || ''}`.trim();
  document.getElementById('timerValue').textContent = fmtTime(readLocalTimer());
  document.getElementById('riddleTimerValue').textContent = fmtTime(readLocalRiddleTimer());
  renderBookingControls();
  renderSummaryControls();
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
  const canSolve = Boolean(riddle.can_solve) || riddle.phase_state === 'active' || riddle.phase_state === 'solved_pending';
  const isFinal = ['solved', 'skipped', 'not_solved'].includes(riddle.phase_state);
  const timeValue = riddleTimeText(riddle);
  const tr = document.createElement('tr');
  tr.dataset.riddleId = riddle.id;
  tr.innerHTML = `
    <td>${riddle.label}</td>
    <td><span class="status-badge ${riddle.phase_state_class}">${riddle.phase_state_label || riddle.phase_state}</span></td>
    <td>
      <div class="riddle-time-edit">
        <input class="riddle-time-input" type="text" inputmode="numeric" value="${escapeAttr(timeValue)}" placeholder="00:00:00" />
        <button class="riddle-time-save" type="button">Save</button>
      </div>
    </td>
    <td>
      <div class="riddle-actions">
        <button class="solve-btn ${canSolve ? 'active' : 'inactive'}" ${canSolve ? '' : 'disabled'}>Solve</button>
        <button class="skip-btn" type="button">Skip</button>
        <button class="not-solved-btn" type="button">Not solved</button>
        <button class="clear-outcome-btn" type="button" ${isFinal ? '' : 'disabled'}>Clear</button>
      </div>
    </td>
    <td><div class="riddle-info-cell">${infoHtml}</div></td>
    <td>${hintCellHtml(riddle)}</td>
  `;

  const timeInput = tr.querySelector('.riddle-time-input');
  const timeSave = tr.querySelector('.riddle-time-save');
  timeInput.addEventListener('focus', () => { riddleTimeEditing[riddle.id] = true; });
  timeInput.addEventListener('input', () => { riddleTimeDrafts[riddle.id] = timeInput.value; });
  timeInput.addEventListener('blur', () => {
    window.setTimeout(() => { riddleTimeEditing[riddle.id] = false; }, 150);
  });
  timeInput.addEventListener('keydown', async (event) => {
    if (event.key === 'Escape') {
      delete riddleTimeDrafts[riddle.id];
      riddleTimeEditing[riddle.id] = false;
      await fetchAndPatch();
      return;
    }
    if (event.key !== 'Enter') return;
    event.preventDefault();
    timeSave.click();
  });
  timeSave.addEventListener('mousedown', () => { riddleTimeEditing[riddle.id] = true; });
  timeSave.addEventListener('click', async () => {
    const value = timeInput.value.trim();
    await api('/api/riddle-time', { method: 'POST', body: JSON.stringify({ riddle: riddle.id, time_text: value }) });
    delete riddleTimeDrafts[riddle.id];
    riddleTimeEditing[riddle.id] = false;
    await fetchAndPatch();
  });

  const solveBtn = tr.querySelector('.solve-btn');
  if (canSolve && riddle.phase_state !== 'solved') {
    solveBtn.addEventListener('click', async () => {
      await api('/api/solve', { method: 'POST', body: JSON.stringify({ node: riddle.id }) });
      await fetchAndPatch();
    });
  }

  tr.querySelector('.skip-btn').addEventListener('click', async () => {
    await api('/api/riddle-outcome', { method: 'POST', body: JSON.stringify({ riddle: riddle.id, outcome: 'skipped', advance: true }) });
    await fetchAndPatch();
  });

  tr.querySelector('.not-solved-btn').addEventListener('click', async () => {
    await api('/api/riddle-outcome', { method: 'POST', body: JSON.stringify({ riddle: riddle.id, outcome: 'not_solved', advance: false }) });
    await fetchAndPatch();
  });

  const clearBtn = tr.querySelector('.clear-outcome-btn');
  if (!clearBtn.disabled) {
    clearBtn.addEventListener('click', async () => {
      await api('/api/riddle-outcome', { method: 'POST', body: JSON.stringify({ riddle: riddle.id, outcome: 'clear' }) });
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
  const body = document.getElementById('riddlesBody');
  if (!body) return;
  body.innerHTML = '';
  for (const riddle of state.riddles || []) {
    body.appendChild(buildRiddleRow(riddle));
  }
}

function patchState(data) {
  const rerenderTop = sectionChanged(data, 'game') || sectionChanged(data, 'booking');
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
  state.booking = data.booking || state.booking;

  for (const light of state.lights) {
    if (!dimEditing[light.id] && light.dimmable && dimDrafts[light.id] == null) {
      dimDrafts[light.id] = light.pct;
    }
  }

  if (rerenderTop) renderTop();
  if (rerenderNodes) renderNodes();
  if (rerenderLocks) renderLocks();
  if (rerenderLights) renderLights();
  if (rerenderRiddles && !anyRiddleTimeEditing()) renderRiddles();

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

  const finishButton = document.getElementById('finishGameBtn');
  const sendSummaryButton = document.getElementById('sendSummaryEmailBtn');

  finishButton?.addEventListener('click', async () => {
    setSummaryFeedback('Finishing game…');
    await api('/api/finish-game', { method: 'POST', body: JSON.stringify({}) });
    await fetchAndPatch();
    setSummaryFeedback('Game finished. Summary email can now be sent.', 'ok');
  });

  sendSummaryButton?.addEventListener('click', async () => {
    summaryEmailBusy = true;
    renderSummaryControls();
    setSummaryFeedback('Sending summary email…');
    try {
      const booking = selectedBookingDraft();
      const result = await api('/api/send-summary-email', { method: 'POST', body: JSON.stringify({ booking }) });
      const email = result?.email || {};
      const responseBooking = result?.booking || booking || {};
      const target = responseBooking.customerEmail || responseBooking.customer_email || booking.customerEmail || '';
      setSummaryFeedback(target ? `Summary sent to ${target}.` : (email.skipped ? `Summary email skipped: ${email.reason || 'unknown reason'}.` : 'Summary email sent.'), email.skipped ? 'warn' : 'ok');
    } catch (error) {
      setSummaryFeedback(error.message || 'Could not send summary email.', 'error');
    } finally {
      summaryEmailBusy = false;
      renderSummaryControls();
      await fetchAndPatch();
    }
  });

  const bookingSelect = document.getElementById('bookingSelect');
  const refreshBookingsBtn = document.getElementById('refreshBookingsBtn');
  const testEmail = document.getElementById('testBookingEmail');
  const testPlayers = document.getElementById('testBookingPlayers');

  bookingSelect?.addEventListener('change', async () => {
    const key = bookingSelect.value;
    const selected = bookingOptions.find(item => bookingKey(item) === key) || bookingOptions[0];
    if (!selected) return;
    if (normalizeBooking(selected).kind === 'test') {
      await saveSelectedBooking(selectedBookingDraft(selected));
    } else {
      await saveSelectedBooking(selected);
    }
  });

  refreshBookingsBtn?.addEventListener('click', async () => {
    setBookingFeedback('Loading bookings…');
    await loadBookings();
  });

  let bookingDraftSaveTimer = null;
  const saveBookingDraft = async () => {
    await saveSelectedBooking(selectedBookingDraft(state.booking));
  };
  const queueBookingDraftSave = () => {
    if (bookingDraftSaveTimer) window.clearTimeout(bookingDraftSaveTimer);
    bookingDraftSaveTimer = window.setTimeout(() => {
      saveBookingDraft().catch(console.error);
    }, 450);
  };

  testEmail?.addEventListener('keydown', async (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    await saveBookingDraft();
  });
  testEmail?.addEventListener('blur', saveBookingDraft);
  testEmail?.addEventListener('input', queueBookingDraftSave);
  testPlayers?.addEventListener('input', () => {
    testPlayers.value = testPlayers.value.replace(/[^0-9]/g, '');
    queueBookingDraftSave();
  });
  testPlayers?.addEventListener('keydown', async (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    await saveTestDraft();
  });
  testPlayers?.addEventListener('blur', saveTestDraft);
}

setInterval(() => {
  document.getElementById('timerValue').textContent = fmtTime(readLocalTimer());
  document.getElementById('riddleTimerValue').textContent = fmtTime(readLocalRiddleTimer());
}, 250);

setInterval(() => {
  fetchAndPatch().catch(console.error);
}, 1000);

wireTopControls();
loadBookings().catch(console.error);
fetchAndPatch().catch(console.error);
