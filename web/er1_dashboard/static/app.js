const state = {
  game: {
    phase: 0,
    last_phase: null,
    phase_name: 'standby',
    phase_display: '0: Bereitschaft',
    elapsed_s: 0,
    current_riddle_elapsed_s: 0,
    current_riddle_name: '',
    timer_running: false,
    players_count: 0,
    is_live: false,
  },
  nodes: [],
  locks: [],
  lights: [],
  riddles: [],
  booking: {
    kind: 'empty',
    id: '__empty__',
    bookingCode: '',
    customerEmail: '',
    players: 0,
    language: 'de',
    label: 'Keine Buchung ausgewählt',
  },
  hint_templates: { version: 1, templates: {} },
  start_assignment: { active: false, status: 'idle', message: '' },
  meta: { persistence_degraded: false },
};

const ui = {
  lastPhase: null,
  gameMode: false,
};

const TEST_BOOKING_ID = '__test__';
const EMPTY_BOOKING_ID = '__empty__';
const HINT_LANGUAGE_LABELS = Object.freeze({ de: 'Deutsch', en: 'English', it: 'Italiano' });
const HINT_RIDDLE_ORDER = Object.freeze([
  'images', 'piano', 'prison', 'wheel', 'chains', 'tangram',
  'magnet', 'chess', 'knocking', 'candles', 'stars', 'sissi',
]);
const NO_HINT_TEMPLATE_TEXT = Object.freeze({
  de: 'Für dieses Rätsel ist keine Tippvorlage hinterlegt.',
  en: 'No hint template is available for this riddle.',
  it: 'Per questo enigma non è disponibile alcun modello di suggerimento.',
});
const DIAGNOSTICS_MAX_ENTRIES = 500;
const DIAGNOSTICS_POLL_LIMIT = 200;
const DIAGNOSTICS_POLL_INTERVAL_MS = 1000;
const DIAGNOSTICS_BACKLOG_DELAY_MS = 75;
const DIAGNOSTICS_BACKLOG_PAGE_BUDGET = 4;
const DIAGNOSTICS_BACKLOG_BUDGET_PAUSE_MS = 400;
const diagnostics = {
  after: 0,
  newestSeq: 0,
  dropped: 0,
  streamId: null,
  mqttConnected: false,
  backlogPages: 0,
  entries: [],
  nodes: new Set(),
  levels: new Set(),
  logLevelDrafts: new Map(),
  generation: 0,
  timer: null,
  requestController: null,
};

let lastSnapshot = null;
let queuedSnapshot = null;
let pollInFlight = false;
let snapshotGeneration = 0;
let actionDepth = 0;
let interactionActive = false;
let interactionReleaseTimer = null;
let pendingRiddleRerender = false;
let summaryEmailBusy = false;
let bookingBusy = false;
let bookingOptions = [];
let bookingOptionsLoaded = false;
let startInFlight = false;
let hintEditorDraft = null;
let hintEditorDirty = false;
let hintEditorSignature = '';
let confirmationResolver = null;

let localTimerBaseElapsed = 0;
let localTimerSyncedAt = 0;
let localRiddleTimerBaseElapsed = 0;
let localRiddleTimerSyncedAt = 0;
let lastGameTimerSignature = '';
let lastRiddleTimerSignature = '';

const dimDrafts = {};
const dimEditing = {};
const riddleTimeDrafts = {};
const riddleTimeEditing = {};

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });

  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_error) {
    data = {};
  }

  if (!response.ok || data.ok === false) {
    let message = data.error || text || `HTTP ${response.status}`;
    if (response.status === 409 && !/aktualisieren/i.test(message)) {
      message += ' Bitte die Ansicht aktualisieren und die Aktion erneut prüfen.';
    }
    if (Number.isInteger(data.command_count) && Number.isInteger(data.queued_count)) {
      message += ` MQTT-Teilbefehle: ${data.queued_count}/${data.command_count} eingereiht; keine Rücknahme und keine Gerätebestätigung.`;
    }
    const error = new Error(message);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function safeInt(value, fallback = 0) {
  const parsed = parseInt(String(value ?? '').replace(/[^0-9-]/g, ''), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function fmtTime(totalSeconds) {
  const total = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const hours = String(Math.floor(total / 3600)).padStart(2, '0');
  const minutes = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
  const seconds = String(total % 60).padStart(2, '0');
  return `${hours}:${minutes}:${seconds}`;
}

function fmtGameTime(totalSeconds) {
  const numeric = Number(totalSeconds) || 0;
  const sign = numeric < 0 ? '-' : '';
  const total = Math.floor(Math.abs(numeric));
  const hours = Math.floor(total / 3600);
  const minutes = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
  const seconds = String(total % 60).padStart(2, '0');
  return hours > 0 ? `${sign}${hours}:${minutes}:${seconds}` : `${sign}${minutes}:${seconds}`;
}

function actionGuard() {
  return {
    expected_phase: Number(state.game.phase || 0),
    expected_run_id: currentRunId(),
  };
}

function stableStringify(value) {
  return JSON.stringify(value);
}

function sectionChanged(nextData, key) {
  if (!lastSnapshot) return true;
  return stableStringify(lastSnapshot[key]) !== stableStringify(nextData[key]);
}

function riddleRenderFingerprint(riddles) {
  return stableStringify((riddles || []).map((riddle) => {
    const stable = { ...riddle };
    delete stable.display_time_s;
    delete stable.live_time_s;
    delete stable.time_s;
    return stable;
  }));
}

function riddleStructureChanged(nextData) {
  if (!lastSnapshot) return true;
  return riddleRenderFingerprint(lastSnapshot.riddles) !== riddleRenderFingerprint(nextData.riddles);
}

function anyRiddleTimeEditing() {
  return Object.values(riddleTimeEditing).some(Boolean);
}

function showFeedback(message, kind = '') {
  const box = document.getElementById('globalFeedback');
  if (!box) return;
  box.textContent = message || '';
  box.className = `global-feedback ${kind ? `global-feedback--${kind}` : ''}`;
}

function setBookingFeedback(message, kind = '') {
  const box = document.getElementById('bookingFeedback');
  if (!box) return;
  box.textContent = message || '';
  box.className = `summary-email-feedback ${kind ? `summary-email-feedback--${kind}` : ''}`;
}

function setSummaryFeedback(message, kind = '') {
  const box = document.getElementById('summaryEmailFeedback');
  if (!box) return;
  box.textContent = message || '';
  box.className = `summary-email-feedback header-feedback ${kind ? `summary-email-feedback--${kind}` : ''}`;
}

function isLiveGame() {
  const phase = Number(state.game.phase || 0);
  return Boolean(state.game.is_live) || (phase >= 3 && phase <= 13);
}

function isGameMode() {
  return Number(state.game.phase || 0) >= 2;
}

function shouldConfirmPhaseChange() {
  return Number(state.game.phase || 0) >= 2;
}

function hasFocusedEditor() {
  const active = document.activeElement;
  if (!active) return false;
  if (active.closest?.('#diagnosticsDetails')) return false;
  // This select is outside both riddle views, so its retained focus must not
  // strand a language-driven riddle rerender after the change event.
  if (active.id === 'hintLanguageSelect') return false;
  return Boolean(active.matches('input, select, textarea, [contenteditable="true"]'));
}

function shouldDeferPatch() {
  return interactionActive || actionDepth > 0 || confirmationResolver !== null || hasFocusedEditor();
}

function rerenderRiddleViews() {
  if (shouldDeferPatch() || anyRiddleTimeEditing()) {
    pendingRiddleRerender = true;
    return;
  }
  pendingRiddleRerender = false;
  renderRiddles();
  renderCurrentRiddles();
}

function flushQueuedSnapshot() {
  if (shouldDeferPatch()) return;
  if (queuedSnapshot) {
    const next = queuedSnapshot;
    queuedSnapshot = null;
    patchState(next);
  }
  if (pendingRiddleRerender) rerenderRiddleViews();
}

function queueOrPatch(data) {
  if (shouldDeferPatch()) {
    queuedSnapshot = data;
    return;
  }
  queuedSnapshot = null;
  patchState(data);
}

async function fetchAndPatch() {
  if (pollInFlight) return;
  const generation = snapshotGeneration;
  pollInFlight = true;
  try {
    const data = await api('/api/state');
    if (generation !== snapshotGeneration) return;
    queueOrPatch(data);
  } finally {
    pollInFlight = false;
  }
}

async function pollLoop() {
  try {
    await fetchAndPatch();
  } catch (error) {
    showFeedback(`Live-Aktualisierung fehlgeschlagen: ${error.message || error}`, 'error');
  } finally {
    const knockingIsCurrent = Number(state.game.phase || 0) === 10
      || String(state.game.current_riddle_name || '') === 'knocking';
    window.setTimeout(pollLoop, knockingIsCurrent ? 250 : 1000);
  }
}

async function runAction(button, task, { silent = false } = {}) {
  snapshotGeneration += 1;
  queuedSnapshot = null;
  actionDepth += 1;
  const originalDisabled = button?.disabled;
  if (button) {
    button.disabled = true;
    button.classList.add('is-busy');
  }
  try {
    const result = await task();
    snapshotGeneration += 1;
    queuedSnapshot = null;
    await delay(90);
    await fetchAndPatch();
    return result;
  } catch (error) {
    if (!silent) showFeedback(error.message || String(error), 'error');
    throw error;
  } finally {
    actionDepth = Math.max(0, actionDepth - 1);
    if (button?.isConnected) {
      button.disabled = Boolean(originalDisabled);
      button.classList.remove('is-busy');
    }
    window.setTimeout(flushQueuedSnapshot, 120);
  }
}

function installInteractionGuard() {
  const interactiveSelector = 'button, a, input, select, textarea, summary, [role="button"]';
  document.addEventListener('pointerdown', (event) => {
    if (event.target.closest?.('#diagnosticsDetails')) return;
    if (!event.target.closest?.(interactiveSelector)) return;
    interactionActive = true;
    if (interactionReleaseTimer) window.clearTimeout(interactionReleaseTimer);
  }, true);

  const release = () => {
    if (interactionReleaseTimer) window.clearTimeout(interactionReleaseTimer);
    // Click is dispatched after pointerup, so keep protected DOM intact until it runs.
    interactionReleaseTimer = window.setTimeout(() => {
      interactionActive = false;
      flushQueuedSnapshot();
    }, 120);
  };

  document.addEventListener('pointerup', release, true);
  document.addEventListener('pointercancel', release, true);
  document.addEventListener('keydown', (event) => {
    if (!['Enter', ' '].includes(event.key)) return;
    if (event.target.closest?.('#diagnosticsDetails')) return;
    if (!event.target.closest?.(interactiveSelector)) return;
    interactionActive = true;
    if (interactionReleaseTimer) window.clearTimeout(interactionReleaseTimer);
  }, true);
  document.addEventListener('keyup', (event) => {
    if (!['Enter', ' '].includes(event.key)) return;
    release();
  }, true);
  document.addEventListener('click', release, true);
  document.addEventListener('focusout', () => window.setTimeout(flushQueuedSnapshot, 0), true);
  window.addEventListener('blur', release);
  window.addEventListener('focus', flushQueuedSnapshot);
}

function settleConfirmation(confirmed) {
  const resolver = confirmationResolver;
  if (!resolver) return;
  confirmationResolver = null;
  const dialog = document.getElementById('confirmDialog');
  if (dialog?.open) dialog.close();
  resolver(Boolean(confirmed));
  window.setTimeout(flushQueuedSnapshot, 0);
}

function confirmAction({ title, message, confirmLabel = 'Bestätigen' }) {
  if (confirmationResolver !== null) return Promise.resolve(false);
  const dialog = document.getElementById('confirmDialog');
  const titleNode = document.getElementById('confirmDialogTitle');
  const messageNode = document.getElementById('confirmDialogMessage');
  const confirmButton = document.getElementById('confirmDialogAccept');
  if (!dialog || !titleNode || !messageNode || !confirmButton) return Promise.resolve(false);

  titleNode.textContent = title;
  messageNode.textContent = message;
  confirmButton.textContent = confirmLabel;
  return new Promise((resolve) => {
    confirmationResolver = resolve;
    dialog.showModal();
    window.setTimeout(() => confirmButton.focus(), 0);
  });
}

function wireConfirmationDialog() {
  const dialog = document.getElementById('confirmDialog');
  const confirmButton = document.getElementById('confirmDialogAccept');
  const cancelButton = document.getElementById('confirmDialogCancel');
  const closeButton = document.getElementById('confirmDialogClose');
  confirmButton?.addEventListener('click', () => settleConfirmation(true));
  cancelButton?.addEventListener('click', () => settleConfirmation(false));
  closeButton?.addEventListener('click', () => settleConfirmation(false));
  dialog?.addEventListener('cancel', (event) => {
    event.preventDefault();
    settleConfirmation(false);
  });
  dialog?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      settleConfirmation(true);
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      settleConfirmation(false);
    }
  });
}

function syncLocalTimers(game) {
  const gameSignature = [
    Boolean(game.timer_running),
    game.started_at || '',
    Number(game.elapsed_s || 0),
    Number(game.phase || 0),
  ].join('|');

  if (gameSignature !== lastGameTimerSignature) {
    lastGameTimerSignature = gameSignature;
    localTimerBaseElapsed = Number(game.elapsed_s || 0);
    localTimerSyncedAt = Date.now();
  }

  const riddleSignature = [
    Boolean(game.timer_running),
    game.current_riddle_name || '',
    Number(game.current_riddle_elapsed_s || 0),
    Number(game.phase || 0),
    game.last_riddle_solved_at || '',
  ].join('|');

  if (riddleSignature !== lastRiddleTimerSignature) {
    lastRiddleTimerSignature = riddleSignature;
    localRiddleTimerBaseElapsed = Math.max(0, Number(game.current_riddle_elapsed_s || 0));
    localRiddleTimerSyncedAt = Date.now();
  }
}

function readLocalTimer() {
  if (!state.game.timer_running || !localTimerSyncedAt) return Math.floor(localTimerBaseElapsed || 0);
  return Math.floor(localTimerBaseElapsed + ((Date.now() - localTimerSyncedAt) / 1000));
}

function readLocalRiddleTimer() {
  if (!state.game.timer_running || !state.game.current_riddle_name || !localRiddleTimerSyncedAt) {
    return Math.floor(localRiddleTimerBaseElapsed || 0);
  }
  return Math.max(0, Math.floor(localRiddleTimerBaseElapsed + ((Date.now() - localRiddleTimerSyncedAt) / 1000)));
}

function normalizeHintLanguage(value) {
  const normalized = String(value ?? '')
    .trim()
    .toLowerCase()
    .replace(/_/g, '-')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '');
  const base = normalized.split('-', 1)[0];
  if (base === 'de' || ['deutsch', 'german', 'tedesco'].includes(normalized)) return 'de';
  if (base === 'en' || ['english', 'englisch', 'inglese'].includes(normalized)) return 'en';
  if (base === 'it' || ['italiano', 'italian', 'italienisch'].includes(normalized)) return 'it';
  return 'de';
}

function normalizeBooking(raw = {}) {
  const rawKind = String(raw.kind || raw.type || '').toLowerCase();
  const rawId = String(raw.id || raw.bookingCode || raw.booking_code || '');
  const hasBookingIdentity = Boolean(rawKind || rawId || raw.date || raw.slot || raw.customerEmail || raw.customer_email);
  const kind = !hasBookingIdentity || rawKind === 'empty' || rawId === EMPTY_BOOKING_ID
    ? 'empty'
    : (rawKind === 'test' || rawId === TEST_BOOKING_ID ? 'test' : 'booking');
  const players = kind === 'empty'
    ? 0
    : Math.max(1, safeInt(raw.players ?? raw.players_count ?? raw.playerCount, kind === 'test' ? 2 : 1));
  const bookingCode = String(raw.bookingCode || raw.booking_code || '').trim();
  const customerEmail = String(raw.customerEmail || raw.customer_email || raw.email || '').trim();
  const id = kind === 'empty'
    ? EMPTY_BOOKING_ID
    : (kind === 'test' ? TEST_BOOKING_ID : String(raw.id || bookingCode || `${raw.date || ''}-${raw.slot || ''}-${customerEmail}`));

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
    language: normalizeHintLanguage(raw.language),
    label: String(raw.label || '').trim(),
    bookingStatus: String(raw.bookingStatus || raw.booking_status || '').trim(),
    paymentStatus: String(raw.paymentStatus || raw.payment_status || '').trim(),
  };
}

function bookingKey(booking = {}) {
  const normalized = normalizeBooking(booking);
  if (normalized.kind === 'empty') return EMPTY_BOOKING_ID;
  return normalized.kind === 'test' ? TEST_BOOKING_ID : String(normalized.id || normalized.bookingCode || '');
}

function bookingOptionLabel(booking) {
  const item = normalizeBooking(booking);
  if (item.kind === 'empty') return 'Keine Buchung ausgewählt';
  if (item.kind === 'test') return 'Testbuchung';
  const dateTime = [item.date, item.slot].filter(Boolean).join(' ');
  const who = item.customerName || item.customerEmail || item.bookingCode || `Buchung ${item.id}`;
  const pieces = [];
  if (dateTime) pieces.push(dateTime);
  pieces.push(`${item.players} Pers.`);
  pieces.push(who);
  if (item.bookingCode) pieces.push(item.bookingCode);
  return pieces.join(' · ');
}

function bookingCompactLabel(booking) {
  const item = normalizeBooking(booking);
  if (item.kind === 'empty') return 'Keine Buchung ausgewählt';
  if (item.kind === 'test') return `Testbuchung · ${item.players} Pers.`;
  return [item.date, item.slot, `${item.players} Pers.`, item.customerName || item.bookingCode]
    .filter(Boolean)
    .join(' · ');
}

async function loadBookings({ silent = false } = {}) {
  const empty = normalizeBooking({ kind: 'empty', id: EMPTY_BOOKING_ID, players: 0, label: 'Keine Buchung ausgewählt' });
  const fallback = normalizeBooking({
    kind: 'test',
    id: TEST_BOOKING_ID,
    customerEmail: 'rudolf.dosser@gmail.com',
    players: 2,
    language: 'de',
    label: 'Testbuchung',
  });

  bookingBusy = true;
  renderBookingControls();
  try {
    const result = await api('/api/bookings');
    const loaded = Array.isArray(result.bookings) ? result.bookings.map(normalizeBooking) : [];
    bookingOptions = loaded.length ? loaded : [empty, fallback];
    if (!bookingOptions.some((item) => item.kind === 'empty')) bookingOptions.unshift(empty);
    if (!bookingOptions.some((item) => item.kind === 'test')) bookingOptions.unshift(fallback);
    bookingOptionsLoaded = true;
    if (!silent) setBookingFeedback(result.warning || '', result.warning ? 'warn' : '');
  } catch (error) {
    bookingOptions = [empty, fallback];
    bookingOptionsLoaded = true;
    if (!silent) setBookingFeedback(error.message || 'Buchungen konnten nicht geladen werden.', 'warn');
  } finally {
    bookingBusy = false;
    renderBookingControls();
  }
  return bookingOptions;
}

async function saveSelectedBooking(booking, { expectedRunId = '', languageOnly = false } = {}) {
  const normalized = normalizeBooking(booking);
  const previousBooking = normalizeBooking(state.booking);
  const previousLanguage = normalizeHintLanguage(state.booking?.language);
  const scopedRunId = expectedRunId || (isLiveGame() ? currentRunId() : '');
  const optimistic = !expectedRunId;
  snapshotGeneration += 1;
  queuedSnapshot = null;
  bookingBusy = true;
  if (optimistic) {
    state.booking = normalized;
    state.game.players_count = normalized.players;
    if (previousLanguage !== normalized.language) rerenderRiddleViews();
  }
  renderBookingControls();
  try {
    const requestBody = languageOnly
      ? { hint_language: normalized.language, ...actionGuard() }
      : { booking: normalized, ...actionGuard() };
    requestBody.expected_run_id = scopedRunId || actionGuard().expected_run_id;
    const result = await api('/api/select-booking', {
      method: 'POST',
      body: JSON.stringify(requestBody),
    });
    // The direct response is newer than any snapshot deferred during this save.
    snapshotGeneration += 1;
    queuedSnapshot = null;
    state.booking = normalizeBooking(result.booking || normalized);
    state.game.players_count = state.booking.players;
    const savedKey = bookingKey(state.booking);
    bookingOptions = bookingOptions.map((item) => bookingKey(item) === savedKey ? state.booking : item);
    if (savedKey && !bookingOptions.some((item) => bookingKey(item) === savedKey)) {
      bookingOptions.unshift(state.booking);
    }
    if (previousLanguage !== state.booking.language) rerenderRiddleViews();
    setBookingFeedback(
      languageOnly
        ? 'Tipp-Sprache gespeichert.'
        : state.booking.kind === 'empty'
        ? 'Keine Buchung ausgewählt.'
        : state.booking.kind === 'test'
        ? 'Testbuchung ausgewählt.'
        : 'Buchungsbefehl an den Game Master übergeben.',
      'ok',
    );
    return state.booking;
  } catch (error) {
    if (optimistic) {
      const failedLanguage = normalizeHintLanguage(state.booking?.language);
      state.booking = previousBooking;
      state.game.players_count = previousBooking.players;
      if (failedLanguage !== previousBooking.language) rerenderRiddleViews();
    }
    throw error;
  } finally {
    bookingBusy = false;
    renderBookingControls();
    window.setTimeout(flushQueuedSnapshot, 0);
  }
}

function renderBookingControls() {
  const select = document.getElementById('bookingSelect');
  const languageSelect = document.getElementById('hintLanguageSelect');
  const info = document.getElementById('bookingSelectedInfo');
  const fields = document.getElementById('testBookingFields');
  const emailInput = document.getElementById('testBookingEmail');
  const playersInput = document.getElementById('testBookingPlayers');
  const refreshButton = document.getElementById('refreshBookingsBtn');
  const applyButton = document.getElementById('applyBookingBtn');
  const compact = document.getElementById('bookingCompactValue');
  const gameCompact = document.getElementById('gameBookingValue');
  const gameHintLanguage = document.getElementById('gameHintLanguage');
  if (!select) return;

  if (!bookingOptionsLoaded && !bookingOptions.length) {
    bookingOptions = [normalizeBooking({
      kind: 'empty',
      id: EMPTY_BOOKING_ID,
      players: 0,
      label: 'Keine Buchung ausgewählt',
    }), normalizeBooking({
      kind: 'test',
      id: TEST_BOOKING_ID,
      customerEmail: 'rudolf.dosser@gmail.com',
      players: 2,
      language: 'de',
      label: 'Testbuchung',
    })];
  }

  const current = normalizeBooking(state.booking || bookingOptions[0] || {});
  const currentKey = bookingKey(current);
  if (currentKey && !bookingOptions.some((item) => bookingKey(item) === currentKey)) {
    bookingOptions = [current, ...bookingOptions];
  }

  const signature = bookingOptions.map((item) => `${bookingKey(item)}:${bookingOptionLabel(item)}`).join('|');
  if (select.dataset.signature !== signature) {
    select.innerHTML = bookingOptions
      .map((item) => `<option value="${escapeAttr(bookingKey(item))}">${escapeHtml(bookingOptionLabel(item))}</option>`)
      .join('');
    select.dataset.signature = signature;
  }

  select.value = currentKey;
  select.disabled = bookingBusy;
  if (languageSelect) {
    languageSelect.value = current.language;
    languageSelect.disabled = bookingBusy || startInFlight || Boolean(state.start_assignment?.active);
  }
  if (refreshButton) refreshButton.disabled = bookingBusy;
  if (applyButton) applyButton.disabled = bookingBusy;
  if (fields) fields.classList.toggle('hidden', current.kind === 'empty');

  if (emailInput && document.activeElement !== emailInput) {
    emailInput.value = current.customerEmail || (current.kind === 'test' ? 'rudolf.dosser@gmail.com' : '');
  }
  if (playersInput && document.activeElement !== playersInput) {
    playersInput.value = String(current.kind === 'empty' ? 0 : (current.players || (current.kind === 'test' ? 2 : 1)));
  }

  if (info) {
    if (current.kind === 'empty') {
      info.textContent = 'Noch keine Buchung und keine Spieleranzahl zugeordnet.';
    } else if (current.kind === 'test') {
      info.textContent = `Testbuchung · ${current.players} Personen · ${current.customerEmail || 'keine E-Mail'}`;
    } else {
      const first = [current.date, current.slot, `${current.players} Personen`].filter(Boolean).join(' · ');
      const second = [current.customerName, current.customerEmail, current.bookingCode].filter(Boolean).join(' · ');
      info.textContent = [first, second].filter(Boolean).join(' | ') || 'Keine Buchung ausgewählt';
    }
  }

  const compactLabel = bookingCompactLabel(current) || 'Keine Buchung ausgewählt';
  if (compact) compact.textContent = compactLabel;
  if (gameCompact) gameCompact.textContent = compactLabel;
  if (gameHintLanguage) gameHintLanguage.textContent = HINT_LANGUAGE_LABELS[current.language] || 'Deutsch';
}

function bookingOverrideMessage(booking) {
  const item = normalizeBooking(booking);
  const details = [bookingOptionLabel(item)];
  if (item.matchStatus) details.push(`Zeitabstand: ${item.matchStatus}`);
  const statuses = [item.bookingStatus, item.paymentStatus].filter(Boolean).join(' / ');
  if (statuses) details.push(`Status: ${statuses}`);
  if (item.kind === 'test') details.push('Status: Testbuchung');
  if (item.kind === 'empty') details.push('Status: ohne Buchung fortfahren');
  details.push('Diese manuelle Auswahl überschreibt eine automatische Zuordnung, falls eine läuft.');
  return details.join('\n');
}

async function confirmAndSaveBooking(booking) {
  const normalized = normalizeBooking(booking);
  const confirmed = await confirmAction({
    title: 'Buchung manuell übernehmen?',
    message: bookingOverrideMessage(normalized),
  });
  if (!confirmed) {
    renderBookingControls();
    return null;
  }
  return saveSelectedBooking(normalized);
}

function renderSummaryControls() {
  const sendButton = document.getElementById('sendSummaryEmailBtn');
  const postGameActions = document.getElementById('postGameActions');
  if (!sendButton || !postGameActions) return;
  const finished = Number(state.game.phase || 0) >= 14 || Boolean(state.game.ended_at);
  const code = String(state.game.leaderboard_code || '').trim();
  postGameActions.classList.toggle('hidden', !finished);
  sendButton.disabled = summaryEmailBusy || !finished || !code;
  sendButton.textContent = summaryEmailBusy
    ? 'Wird gesendet…'
    : (code ? `Spielzusammenfassung senden (${code})` : 'Spielzusammenfassung senden');
}

function renderStartAssignmentStatus() {
  const box = document.getElementById('startAssignmentStatus');
  if (!box) return;
  const assignment = state.start_assignment || {};
  const status = String(assignment.status || 'idle');
  const message = String(assignment.message || '');
  const degraded = Boolean(state.meta?.persistence_degraded);
  const parts = [];
  if (message) parts.push(message);
  if (degraded) parts.push('Speicherwarnung: Dauerhafte Speicherung ist derzeit nicht vollständig bestätigt.');
  box.textContent = parts.join(' ');
  box.className = `start-assignment-status start-assignment-status--${degraded ? 'failed' : status}`;
  box.classList.toggle('hidden', parts.length === 0);
}

function renderTop() {
  const phaseValue = document.getElementById('phaseValue');
  const lastPhaseValue = document.getElementById('lastPhaseValue');
  const timerValue = document.getElementById('timerValue');
  const riddleTimerValue = document.getElementById('riddleTimerValue');
  const prepareCounterWrap = document.getElementById('prepareCounterWrap');
  const startButton = document.getElementById('startGameBtn');

  const phase = Number(state.game.phase || 0);
  const phaseLabel = String(state.game.phase_name_pretty || state.game.phase_name || '').trim();
  if (phaseValue) phaseValue.textContent = `Phase ${phase} · ${phaseLabel}`;
  if (lastPhaseValue) {
    lastPhaseValue.textContent = state.game.last_phase == null
      ? '—'
      : `${state.game.last_phase}: ${state.game.last_phase_name_pretty || state.game.last_phase_name || ''}`.trim();
  }
  if (timerValue) timerValue.textContent = fmtGameTime(readLocalTimer());
  if (riddleTimerValue) {
    riddleTimerValue.textContent = phase >= 3 && state.game.current_riddle_name
      ? fmtTime(readLocalRiddleTimer())
      : '—';
  }

  const preparedRunReady = phase === 2 && Boolean(currentRunId());
  prepareCounterWrap?.classList.toggle('hidden', phase !== 2);
  if (startButton) {
    startButton.textContent = phase >= 3 ? 'Spiel läuft' : 'Spiel starten';
    startButton.disabled = !preparedRunReady || bookingBusy || startInFlight || Boolean(state.start_assignment?.active);
    startButton.classList.toggle('is-current-phase', phase >= 3);
  }

  const currentAction = phase === 0 ? 'standby' : (phase === 1 ? 'maintenance' : (phase === 2 ? 'prepare' : 'start'));
  document.querySelectorAll('[data-phase-action]').forEach((button) => {
    const active = button.dataset.phaseAction === currentAction;
    button.classList.toggle('is-current-phase', active);
    button.disabled = active;
  });

  renderBookingControls();
  renderSummaryControls();
  renderStartAssignmentStatus();
  renderRiddleProgress();
  renderPanelVisibility();
}

function renderPanelVisibility() {
  const gameMode = isGameMode();
  const currentPanel = document.getElementById('currentRiddlesPanel');
  const drawerStrip = document.getElementById('gameDrawerStrip');
  const bookingDetails = document.getElementById('bookingDetails');
  const allRiddles = document.getElementById('allRiddlesPanel');
  const diagnosticsDetails = document.getElementById('diagnosticsDetails');
  const hintEditor = document.getElementById('maintenanceHintEditor');

  document.body.classList.toggle('dashboard-game-mode', gameMode);
  document.body.classList.toggle('dashboard-pregame', !gameMode);
  currentPanel?.classList.toggle('hidden', !gameMode);
  drawerStrip?.classList.toggle('hidden', !gameMode);

  if (ui.gameMode !== gameMode) {
    if (gameMode) {
      for (const panel of [bookingDetails, allRiddles, diagnosticsDetails, hintEditor]) {
        if (panel) panel.open = false;
      }
    } else {
      if (bookingDetails) bookingDetails.open = true;
      if (allRiddles) allRiddles.open = true;
      if (diagnosticsDetails) diagnosticsDetails.open = false;
      if (hintEditor) hintEditor.open = false;
    }
    ui.gameMode = gameMode;
  }
  ui.lastPhase = Number(state.game.phase || 0);
  updateDrawerButtons();
}

function renderRiddleProgress() {
  const riddles = state.riddles || [];
  const solved = riddles.filter((riddle) => ['solved', 'skipped'].includes(String(riddle.phase_state || ''))).length;
  const text = `${solved} gelöst / ${riddles.length || 12}`;
  const summary = document.getElementById('riddleProgressValue');
  const drawer = document.getElementById('gameRiddleProgress');
  if (summary) summary.textContent = text;
  if (drawer) drawer.textContent = text;
}

function updateDrawerButtons() {
  document.querySelectorAll('[data-panel-target]').forEach((button) => {
    const panel = document.getElementById(button.dataset.panelTarget);
    const expanded = Boolean(panel?.open);
    button.setAttribute('aria-expanded', String(expanded));
    button.classList.toggle('is-expanded', expanded);
  });
}

function renderNodes() {
  const statusBar = document.getElementById('nodeStatusBar');
  const tableBody = document.getElementById('nodeDiagnosticsBody');
  const rebootAll = document.getElementById('rebootAllNodesBtn');
  if (!statusBar || !tableBody) return;
  statusBar.innerHTML = '';
  tableBody.innerHTML = '';
  const phase = Number(state.game.phase || 0);
  const phaseZero = phase === 0;

  const rebootNode = async (node, button) => {
    const confirmed = await confirmAction({
      title: node === 'all' ? 'Alle Steuergeräte neu starten?' : `${button.dataset.nodeLabel} neu starten?`,
      message: node === 'all'
        ? 'Der Neustart wird an alle physischen Nodes gesendet. OTA wird nicht gestartet.'
        : `Der Neustart wird an ${button.dataset.nodeLabel} gesendet. Eine Gerätebestätigung ist nicht verfügbar.`,
      confirmLabel: 'Neu starten',
    });
    if (!confirmed) return;
    await runAction(button, async () => {
      const result = await api('/api/node-reboot', {
        method: 'POST',
        body: JSON.stringify({ node, ...actionGuard() }),
      });
      showFeedback(
        node === 'all'
          ? `${result.queued_count} Neustartbefehle an MQTT übergeben; die Geräteneustarts sind nicht bestätigt.`
          : `${button.dataset.nodeLabel}: Neustartbefehl an MQTT übergeben; der Neustart ist nicht bestätigt.`,
        'warn',
      );
      return result;
    });
  };

  if (rebootAll) {
    rebootAll.disabled = !phaseZero;
    rebootAll.title = phaseZero
      ? 'REBOOT an alle physischen Steuergeräte senden'
      : 'Alle Steuergeräte dürfen nur in Phase 0 neu gestartet werden.';
    rebootAll.onclick = () => rebootNode('all', rebootAll).catch(() => {});
  }

  const diagnosticNodes = (state.nodes || []).filter((item) => item.id !== 'stop_timer');
  const statusNodes = diagnosticNodes;
  const diagnosticOnlineCount = diagnosticNodes.filter((item) => item.online).length;
  const statusOnlineCount = statusNodes.filter((item) => item.online).length;
  const diagnosticHealthText = `${diagnosticOnlineCount} online · ${diagnosticNodes.length - diagnosticOnlineCount} offline`;
  const statusHealthText = `${statusOnlineCount} online · ${statusNodes.length - statusOnlineCount} offline`;
  const diagnosticsSummary = document.getElementById('nodeDiagnosticsSummary');
  const gameHealth = document.getElementById('gameNodeHealth');
  if (diagnosticsSummary) diagnosticsSummary.textContent = diagnosticHealthText;
  if (gameHealth) gameHealth.textContent = statusHealthText;

  for (const item of diagnosticNodes) {
    if (item.id !== 'stop_timer') {
      const card = document.createElement('article');
      card.className = `node-status-card ${item.online ? 'node-on' : 'node-off'}`;
      const cardLabel = document.createElement('strong');
      cardLabel.textContent = item.label;
      const cardState = document.createElement('span');
      cardState.className = 'node-status-state';
      cardState.textContent = item.online ? 'Online' : 'Offline';
      card.append(cardLabel, cardState);
      statusBar.appendChild(card);
    }

    const row = document.createElement('tr');
    const labelCell = document.createElement('td');
    labelCell.textContent = item.label;
    const statusCell = document.createElement('td');
    statusCell.innerHTML = `<span class="node-table-status ${item.online ? 'is-online' : 'is-offline'}">${item.online ? 'Online' : 'Offline'}</span>`;
    const contactCell = document.createElement('td');
    contactCell.textContent = Number.isFinite(Number(item.last_seen_s)) ? `vor ${Math.max(0, Math.round(Number(item.last_seen_s)))} s` : '—';
    const firmwareCell = document.createElement('td');
    firmwareCell.textContent = item.firmware || '—';
    const errorCell = document.createElement('td');
    errorCell.textContent = item.error || '—';
    const actionCell = document.createElement('td');
    const reboot = document.createElement('button');
    reboot.type = 'button';
    reboot.className = 'node-reboot-btn';
    reboot.textContent = 'Reboot';
    reboot.dataset.nodeLabel = item.label;
    const phaseRestricted = item.id === 'maglock' && !phaseZero;
    reboot.disabled = !item.online || phaseRestricted;
    reboot.title = phaseRestricted
      ? 'Die Schlosssteuerung darf nur in Phase 0 neu gestartet werden.'
      : (item.online
        ? 'REBOOT über MQTT einreihen'
        : 'Knoten ist offline; Neustart ist deaktiviert. Diagnose-Logs bleiben verfügbar.');
    reboot.addEventListener('click', () => rebootNode(item.id, reboot).catch(() => {}));
    actionCell.appendChild(reboot);
    row.append(labelCell, statusCell, contactCell, firmwareCell, errorCell, actionCell);
    tableBody.appendChild(row);
  }
}

async function waitForDashboardRestart() {
  await delay(1500);
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      const response = await fetch(`/api/state?restart_check=${Date.now()}`, { cache: 'no-store' });
      if (response.ok) {
        window.location.reload();
        return;
      }
    } catch (_error) {
      // The short connection failure is expected while systemd restarts Flask.
    }
    await delay(500);
  }
  showFeedback('Das Dashboard wurde neu gestartet, ist aber noch nicht wieder erreichbar. Bitte die Seite neu laden.', 'warn');
}

async function restartSystemService(target, button) {
  const labels = { game_master: 'Game Master', dashboard: 'Dashboard' };
  const label = labels[target];
  if (!label) return;
  const confirmed = await confirmAction({
    title: `${label} neu starten?`,
    message: target === 'dashboard'
      ? 'Die Dashboard-Verbindung wird kurz unterbrochen. Die Seite lädt automatisch neu, sobald der Dienst wieder erreichbar ist.'
      : 'Der Game Master wird kontrolliert durch systemd neu gestartet. Währenddessen können Spielereignisse kurz verzögert verarbeitet werden.',
    confirmLabel: 'Dienst neu starten',
  });
  if (!confirmed) return;

  const result = await runAction(button, () => api('/api/service-restart', {
    method: 'POST',
    body: JSON.stringify({ target, confirmed: true, ...actionGuard() }),
  }));
  showFeedback(`${result.label}: Neustart wurde eingeplant.`, 'warn');
  if (target === 'dashboard') waitForDashboardRestart().catch(() => {});
}

async function confirmManualControl(title, message) {
  if (!isLiveGame()) return true;
  return confirmAction({
    title,
    message: `${message} Das Spiel läuft gerade; dieser Eingriff kann den normalen Ablauf verändern.`,
  });
}

function renderLocks() {
  const wrap = document.getElementById('locksGrid');
  if (!wrap) return;
  wrap.innerHTML = '';

  for (const lock of state.locks || []) {
    const card = document.createElement('div');
    card.className = 'control-card';
    const open = lock.is_open === true;
    const action = lock.kind === 'toggle' && open ? 'close' : 'open';
    const actionLabel = action === 'close' ? 'schließen' : 'öffnen';
    const commandBlocked = Boolean(lock.command_blocked);
    const protectionLabel = lock.boot_guard
      ? 'STARTSPERRE'
      : (lock.cooldown ? 'SPERRZEIT' : (lock.pulsing ? 'IMPULS AKTIV' : ''));
    card.innerHTML = `
      <button class="control-button ${open ? 'is-open' : 'is-closed'}" type="button" ${commandBlocked ? 'disabled' : ''}>
        ${escapeHtml(lock.label)}
        <span class="state-line">${escapeHtml(protectionLabel || String(lock.state_label || 'unbekannt').toUpperCase())}</span>
      </button>
    `;
    const button = card.querySelector('button');
    button.title = commandBlocked
      ? 'Öffnen ist durch Impuls, Sperrzeit oder Startschutz vorübergehend blockiert.'
      : `${lock.label} ${actionLabel}`;
    button.addEventListener('click', async () => {
      const confirmed = await confirmManualControl(
        'Schloss manuell betätigen?',
        `${lock.label} wird manuell ${actionLabel}.`,
      );
      if (!confirmed) return;
      await runAction(button, () => api('/api/lock', {
        method: 'POST',
        body: JSON.stringify({ lock: lock.id, action, ...actionGuard() }),
      }));
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
  if (!wrap) return;
  wrap.innerHTML = '';

  for (const light of state.lights || []) {
    const card = document.createElement('div');
    card.className = 'control-card';
    const anyLightOn = Boolean(light.any_on ?? light.on);
    const lightState = String(light.state || (light.on ? 'on' : 'off'));
    const buttonClass = {
      on: 'is-on',
      off: 'is-off',
      mixed: 'is-mixed',
      partial: 'is-partial',
      unknown: 'is-unknown',
    }[lightState] || 'is-unknown';
    const stateLabel = String(light.state_label || (light.on ? 'an' : 'aus')).toUpperCase();

    if (light.dimmable) {
      const dimValue = resolveDimValue(light);
      card.innerHTML = `
        <div class="control-stack">
          <button class="control-button ${buttonClass}" type="button">
            ${escapeHtml(light.label)}
            <span class="state-line">${escapeHtml(stateLabel)}</span>
          </button>
          <div class="dim-row dim-row-v4">
            <input class="dim-input" type="number" min="0" max="100" value="${escapeAttr(dimValue)}" aria-label="Helligkeit ${escapeAttr(light.label)}" />
            <button class="dim-apply-button" type="button">Setzen</button>
          </div>
        </div>
      `;
      const toggleButton = card.querySelector('.control-button');
      const input = card.querySelector('.dim-input');
      const applyButton = card.querySelector('.dim-apply-button');

      toggleButton.addEventListener('click', async () => {
        let pct = Math.max(0, Math.min(100, safeInt(input.value, light.pct || 100)));
        const action = anyLightOn ? 'off' : 'on';
        if (!anyLightOn && pct <= 0) pct = 100;
        const confirmed = await confirmManualControl(
          'Licht manuell schalten?',
          `${light.label} wird ${action === 'on' ? 'eingeschaltet' : 'ausgeschaltet'}.`,
        );
        if (!confirmed) return;
        dimDrafts[light.id] = action === 'off' ? 0 : pct;
        await runAction(toggleButton, () => api('/api/light', {
          method: 'POST',
          body: JSON.stringify({ group: light.id, action, pct, ...actionGuard() }),
        }));
      });

      input.addEventListener('focus', () => { dimEditing[light.id] = true; });
      input.addEventListener('blur', () => {
        dimEditing[light.id] = false;
        window.setTimeout(flushQueuedSnapshot, 0);
      });
      input.addEventListener('input', () => { dimDrafts[light.id] = input.value; });

      const applyDim = async () => {
        const pct = Math.max(0, Math.min(100, safeInt(input.value, 0)));
        const confirmed = await confirmManualControl(
          'Helligkeit manuell ändern?',
          `${light.label} wird auf ${pct} % gesetzt.`,
        );
        if (!confirmed) return;
        dimDrafts[light.id] = pct;
        dimEditing[light.id] = false;
        await runAction(applyButton, () => api('/api/light', {
          method: 'POST',
          body: JSON.stringify({ group: light.id, action: 'set_pct', pct, ...actionGuard() }),
        }));
      };

      applyButton.addEventListener('click', applyDim);
      input.addEventListener('keydown', async (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        await applyDim();
      });
    } else {
      card.innerHTML = `
        <button class="control-button ${buttonClass}" type="button">
          ${escapeHtml(light.label)}
          <span class="state-line">${escapeHtml(stateLabel)}</span>
        </button>
      `;
      const button = card.querySelector('button');
      button.addEventListener('click', async () => {
        const action = anyLightOn ? 'off' : 'on';
        const confirmed = await confirmManualControl(
          'Licht manuell schalten?',
          `${light.label} wird ${action === 'on' ? 'eingeschaltet' : 'ausgeschaltet'}.`,
        );
        if (!confirmed) return;
        await runAction(button, () => api('/api/light', {
          method: 'POST',
          body: JSON.stringify({ group: light.id, action, ...actionGuard() }),
        }));
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
      ${slots.map((slot) => `
        <span>${escapeHtml(slot.slot)}: <span class="inline-status ${slot.correct ? 'inline-status-good' : 'inline-status-bad'}">${escapeHtml(slot.value)}</span><small class="chess-expected">Soll: ${escapeHtml(slot.expected || slot.slot)}</small></span>
      `).join('<span class="inline-sep">|</span>')}
    </div>
  `;
}

function renderAttemptsSummary(summary) {
  if (!summary) return '';
  const attempts = (summary.attempts || []).map(escapeHtml).join(' <span class="inline-sep">|</span> ');
  if (!attempts) return '<span class="muted-info">Noch kein vollständiger Versuch.</span>';
  return `<div class="inline-info"><b>Versuche:</b> ${attempts}</div>`;
}

function renderKnockingSummary(summary) {
  const expected = [1, 1, 1, 1, 1, 1, 2, 2, 3, 3, 3, 3];
  const current = Array.isArray(summary?.sequence_current)
    ? summary.sequence_current.map((value) => Number(value)).filter((value) => [1, 2, 3].includes(value))
    : [];
  const prefixCorrect = current.every((value, index) => value === expected[index]);
  const dots = expected.map((sensor, index) => {
    const entered = index < current.length;
    const stateClass = !entered ? 'knocking-dot-pending' : (prefixCorrect ? 'knocking-dot-good' : 'knocking-dot-bad');
    return `<span class="knocking-dot ${stateClass}" title="Sensor ${sensor}, Position ${index + 1}">${sensor}</span>`;
  });
  const groups = [dots.slice(0, 6), dots.slice(6, 8), dots.slice(8, 12)];
  const attempts = renderAttemptsSummary(summary);
  return `
    <div class="knocking-telemetry ${current.length && !prefixCorrect ? 'knocking-attempt-wrong' : ''}">
      <div class="knocking-sequence" aria-label="Erwartete Klopffolge: sechsmal Sensor 1, zweimal Sensor 2, viermal Sensor 3">
        ${groups.map((group, index) => `<span class="knocking-group" data-sensor="${index + 1}">${group.join('')}</span>`).join('')}
      </div>
      ${current.length && !prefixCorrect ? '<strong class="knocking-error">Aktueller Versuch weicht von der erwarteten Folge ab.</strong>' : ''}
      ${attempts}
    </div>
  `;
}

function renderStarSliderSummary(summary) {
  if (!summary) return '';
  const current = (summary.current || []).map(escapeHtml).join(' ');
  const attempts = (summary.attempts || []).map((item) => `(${item.map(escapeHtml).join(' ')})`).join(' ');
  return `
    <div class="inline-info inline-info-wrap">
      <span><b>Aktuell:</b> ${current || 'keine'}</span>
      <span class="inline-sep">|</span>
      <span><b>Versuche:</b> ${attempts || '—'}</span>
    </div>
  `;
}

function renderPianoSummary(summary) {
  if (!summary || !summary.played_notes || !summary.played_notes.length) {
    return '<span class="muted-info">Noch keine Töne erkannt.</span>';
  }
  return `
    <div class="inline-info inline-info-wrap">
      <span><b>Erkannte Töne:</b> ${summary.played_notes.map((note) => {
        const cls = note.accepted ? 'inline-status-good' : 'inline-status-bad';
        return `<span class="inline-status ${cls}">${escapeHtml(note.encoded || '')}</span>`;
      }).join(' ')}</span>
    </div>
  `;
}

function renderRiddleInfo(riddle) {
  if (riddle.id === 'images') return renderImagesButtons(riddle.images_buttons);
  if (riddle.id === 'piano') return renderPianoSummary(riddle.piano_summary);
  if (riddle.id === 'chess') return renderChessSlots(riddle.chess_slots);
  if (riddle.id === 'knocking') return renderKnockingSummary(riddle.attempts_summary);
  if (riddle.id === 'candles') return renderAttemptsSummary(riddle.attempts_summary);
  if (riddle.id === 'stars' || riddle.id === 'star_slider') return renderStarSliderSummary(riddle.star_slider_summary);
  return riddle.info ? `<span>${escapeHtml(riddle.info)}</span>` : '<span class="muted-info">Keine zusätzlichen Live-Daten.</span>';
}

function riddleTimeText(riddle) {
  if (riddleTimeEditing[riddle.id] || riddleTimeDrafts[riddle.id] != null) {
    return String(riddleTimeDrafts[riddle.id] ?? '');
  }
  const seconds = Number(riddle.display_time_s ?? riddle.time_s ?? riddle.live_time_s ?? riddle.solve_time_s ?? 0);
  return seconds >= 1 ? String(Math.round(seconds * 1000) / 1000) : '';
}

function hintTemplatesFor(riddleId, language = state.booking?.language) {
  const selectedLanguage = normalizeHintLanguage(language);
  const templates = state.hint_templates?.templates?.[String(riddleId || '')];
  return templates?.[selectedLanguage] || [];
}

function riddleMutationsAvailable() {
  return Number(state.game.phase || 0) >= 3 && Boolean(currentRunId());
}

function hintPanelHtml(riddle, compact = false) {
  const language = normalizeHintLanguage(state.booking?.language);
  const templates = hintTemplatesFor(riddle.id, language);
  const count = Math.max(0, safeInt(riddle.hint_count, 0));
  const canMutate = riddleMutationsAvailable();
  if (compact) {
    return `<span class="riddle-hint-summary">${count} / ${templates.length}</span>`;
  }
  const templateContent = templates.length
    ? `<ol class="hint-template-list">${templates.map((text, index) => `<li><span>${index + 1}</span><p>${escapeHtml(text)}</p></li>`).join('')}</ol>`
    : `<p class="hint-template-empty">${escapeHtml(NO_HINT_TEMPLATE_TEXT[language])}</p>`;
  return `
    <section class="hint-panel">
      <div class="hint-template-content">
        <div class="hint-template-heading">Tipps für ${escapeHtml(riddle.label)} <span>· ${escapeHtml(HINT_LANGUAGE_LABELS[language])}</span></div>
        ${templateContent}
      </div>
      <div class="hint-given-block">
        <div class="hint-given-label">Aktuelle Tippanzahl</div>
        <strong class="hint-counter-value">${count}</strong>
        ${canMutate ? `<div class="hint-counter-buttons">
          <button class="hint-counter-btn" type="button" data-delta="1" aria-label="Tippzähler um eins erhöhen">+</button>
          <button class="hint-counter-btn" type="button" data-delta="-1" aria-label="Tippzähler um eins verringern" ${count === 0 ? 'disabled' : ''}>−</button>
        </div>` : ''}
      </div>
    </section>
  `;
}

function riddleActionsHtml(riddle, compact = false) {
  const stateName = String(riddle.phase_state || 'pending');
  const canSolve = Boolean(riddle.can_solve);
  const canSkip = riddleMutationsAvailable()
    && riddle.id !== 'sissi'
    && stateName !== 'pending'
    && stateName !== 'skipped';

  return `
    <div class="riddle-actions ${compact ? 'riddle-actions-card' : ''}">
      <button class="solve-btn ${canSolve ? 'active' : 'inactive'}" type="button" ${canSolve ? '' : 'disabled'}>Gelöst</button>
      ${canSkip ? `<button class="skip-btn ${riddle.skipped ? 'is-toggled' : ''}" type="button">Überspringen</button>` : ''}
    </div>
  `;
}

function riddleTimeEditorHtml(riddle) {
  const timeEditable = riddleMutationsAvailable()
    && ['solved', 'skipped', 'not_solved'].includes(String(riddle.phase_state || 'pending'));
  if (!timeEditable) return '<span class="muted-info">—</span>';
  return `
    <div class="riddle-time-edit">
      <input class="riddle-time-input" type="number" inputmode="decimal" min="1" step="0.001" value="${escapeAttr(riddleTimeText(riddle))}" placeholder="Min. 1 Sek." aria-label="Rätselzeit in Sekunden, mindestens eine Sekunde" />
      <button class="riddle-time-save" type="button">OK</button>
    </div>
  `;
}

async function solveRiddle(riddle, button) {
  if (!riddle.can_solve) return;

  const confirmed = await confirmAction({
    title: `${riddle.label} als gelöst markieren?`,
    message: riddle.solve_advances
      ? 'Diese Aktion setzt die Rätselzeit fest und wechselt zum nächsten Spielschritt.'
      : 'Diese Aktion markiert das Rätsel als gelöst.',
    confirmLabel: 'Als gelöst markieren',
  });
  if (!confirmed) return;

  await runAction(button, () => {
    if (riddle.solve_advances) {
      return api('/api/solve', {
        method: 'POST',
        body: JSON.stringify({ node: riddle.id, ...actionGuard() }),
      });
    }
    return api('/api/riddle-outcome', {
      method: 'POST',
      body: JSON.stringify({ riddle: riddle.id, outcome: 'solved', advance: false, ...actionGuard() }),
    });
  });
}

async function toggleSkip(riddle, button) {
  const stateName = String(riddle.phase_state || 'pending');
  if (stateName === 'pending' || stateName === 'skipped') return;
  const confirmed = await confirmAction({
    title: `${riddle.label} überspringen?`,
    message: riddle.solve_advances
      ? 'Das aktive Rätsel wird als übersprungen markiert und der Spielablauf wird fortgesetzt.'
      : 'Das bereits erreichte Rätsel wird nachträglich als übersprungen markiert.',
  });
  if (!confirmed) return;
  const body = {
    riddle: riddle.id,
    outcome: 'skipped',
    advance: Boolean(riddle.solve_advances),
    ...actionGuard(),
  };
  await runAction(button, () => api('/api/riddle-outcome', {
    method: 'POST',
    body: JSON.stringify(body),
  }));
}

async function saveRiddleTime(riddle, input, button) {
  if (String(riddle.phase_state || 'pending') === 'pending') {
    showFeedback('Die Zeit eines noch nicht erreichten Rätsels kann nicht geändert werden.', 'warn');
    return;
  }
  const value = input.value.trim();
  if (!Number.isFinite(Number(value)) || Number(value) < 1) {
    showFeedback('Die Rätselzeit muss mindestens eine Sekunde betragen.', 'error');
    input.focus();
    return;
  }

  await runAction(button, async () => {
    await api('/api/riddle-time', {
      method: 'POST',
      body: JSON.stringify({ riddle: riddle.id, time_text: value, ...actionGuard() }),
    });
    delete riddleTimeDrafts[riddle.id];
    riddleTimeEditing[riddle.id] = false;
  });
}

async function changeHint(riddle, delta, button) {
  await runAction(button, () => api('/api/hints', {
    method: 'POST',
    body: JSON.stringify({ riddle: riddle.id, delta, ...actionGuard() }),
  }));
}

function bindRiddleActions(container, riddle, { includeTimeEditor = false } = {}) {
  const solveButton = container.querySelector('.solve-btn');
  if (solveButton && !solveButton.disabled) {
    solveButton.addEventListener('click', () => solveRiddle(riddle, solveButton).catch(() => {}));
  }

  const skipButton = container.querySelector('.skip-btn');
  if (skipButton && !skipButton.disabled) {
    skipButton.addEventListener('click', () => toggleSkip(riddle, skipButton).catch(() => {}));
  }

  container.querySelectorAll('.hint-counter-btn').forEach((button) => {
    if (button.disabled) return;
    button.addEventListener('click', () => {
      const delta = safeInt(button.dataset.delta, 0);
      if (!delta) return;
      changeHint(riddle, delta, button).catch(() => {});
    });
  });

  if (!includeTimeEditor) return;
  const input = container.querySelector('.riddle-time-input');
  const saveButton = container.querySelector('.riddle-time-save');
  if (!input || !saveButton || input.disabled || saveButton.disabled) return;

  input.addEventListener('focus', () => { riddleTimeEditing[riddle.id] = true; });
  input.addEventListener('input', () => { riddleTimeDrafts[riddle.id] = input.value; });
  input.addEventListener('blur', () => {
    window.setTimeout(() => {
      riddleTimeEditing[riddle.id] = false;
      flushQueuedSnapshot();
    }, 180);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      delete riddleTimeDrafts[riddle.id];
      riddleTimeEditing[riddle.id] = false;
      flushQueuedSnapshot();
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      saveRiddleTime(riddle, input, saveButton).catch(() => {});
    }
  });
  saveButton.addEventListener('pointerdown', () => { riddleTimeEditing[riddle.id] = true; });
  saveButton.addEventListener('click', () => saveRiddleTime(riddle, input, saveButton).catch(() => {}));
}

function buildRiddleRow(riddle) {
  const row = document.createElement('tr');
  row.dataset.riddleId = riddle.id;
  row.className = riddle.phase_state === 'active' ? 'current-riddle-row' : '';
  row.innerHTML = `
    <td>${escapeHtml(riddle.label)}</td>
    <td><span class="status-badge ${escapeAttr(riddle.phase_state_class || '')}">${escapeHtml(riddle.phase_state_label || riddle.phase_state)}</span></td>
    <td>${riddleTimeEditorHtml(riddle)}</td>
    <td>${riddleActionsHtml(riddle)}</td>
    <td><div class="riddle-info-cell">${renderRiddleInfo(riddle)}</div></td>
    <td>${hintPanelHtml(riddle, true)}</td>
  `;
  bindRiddleActions(row, riddle, { includeTimeEditor: true });
  return row;
}

function buildCurrentRiddleCard(riddle) {
  const card = document.createElement('article');
  card.className = 'current-riddle-card';
  card.dataset.riddleId = riddle.id;
  card.innerHTML = `
    <div class="current-riddle-card-head">
      <div>
        <div class="card-heading"><span class="card-icon" aria-hidden="true">&#10021;</span><span>Aktuelles Rätsel</span></div>
        <h2>${escapeHtml(riddle.label)}</h2>
      </div>
      <div class="current-card-status">
        <span class="status-badge ${escapeAttr(riddle.phase_state_class || '')}">${escapeHtml(riddle.phase_state_label || riddle.phase_state)}</span>
      </div>
    </div>
    <div class="current-riddle-facts">
      <div><span>Rätselzeit</span><strong class="current-card-time">${fmtTime(riddle.display_time_s || 0)}</strong></div>
      <div><span>Live-Informationen</span><div class="current-fact-content">${renderRiddleInfo(riddle)}</div></div>
      <div><span>Typ</span><strong>${riddle.manual ? 'Manuelles Rätsel' : 'Elektronisches Rätsel'}</strong></div>
    </div>
    <div class="current-riddle-controls">${riddleActionsHtml(riddle, true)}</div>
    ${hintPanelHtml(riddle)}
  `;
  bindRiddleActions(card, riddle);
  return card;
}

function renderRiddles() {
  const body = document.getElementById('riddlesBody');
  if (!body) return;
  body.innerHTML = '';
  for (const riddle of state.riddles || []) body.appendChild(buildRiddleRow(riddle));
}

function renderCurrentRiddles() {
  const wrap = document.getElementById('currentRiddlesGrid');
  if (!wrap) return;
  wrap.innerHTML = '';
  if (Number(state.game.phase || 0) === 2) {
    const preparation = document.createElement('article');
    preparation.className = 'current-riddle-card preparation-card';
    preparation.innerHTML = `
      <div>
        <div class="card-heading"><span class="card-icon" aria-hidden="true">&#9654;</span><span>Vorbereitung</span></div>
        <h2>Bereit zum Spielstart</h2>
        <p>Prüfe Buchung, Spieleranzahl und Raum. Der Start beginnt den Countdown und kann nicht versehentlich ohne Bestätigung ausgelöst werden.</p>
      </div>
      <button class="preparation-start-button" type="button">Spiel wirklich starten</button>
    `;
    const button = preparation.querySelector('.preparation-start-button');
    button.disabled = !currentRunId() || bookingBusy || startInFlight || Boolean(state.start_assignment?.active);
    button.addEventListener('click', () => handleStart(button));
    wrap.appendChild(preparation);
    return;
  }
  const current = (state.riddles || []).filter((riddle) => (
    riddle.phase_state === 'active'
  ));
  if (!current.length) {
    wrap.innerHTML = '<div class="viewer-message">Für diese Phase ist derzeit kein aktives Rätsel gemeldet.</div>';
    return;
  }
  for (const riddle of current) wrap.appendChild(buildCurrentRiddleCard(riddle));
}

function copyHintTemplateMap(payload = state.hint_templates) {
  const source = payload?.templates && typeof payload.templates === 'object' ? payload.templates : {};
  const copy = {};
  for (const riddle of HINT_RIDDLE_ORDER) {
    copy[riddle] = {};
    for (const language of Object.keys(HINT_LANGUAGE_LABELS)) {
      copy[riddle][language] = Array.isArray(source?.[riddle]?.[language])
        ? source[riddle][language].map((tip) => String(tip))
        : [];
    }
  }
  return copy;
}

function selectedHintEditorValues() {
  const language = normalizeHintLanguage(document.getElementById('hintEditorLanguage')?.value || 'de');
  const selectedRiddle = String(document.getElementById('hintEditorRiddle')?.value || 'images');
  const riddle = HINT_RIDDLE_ORDER.includes(selectedRiddle) ? selectedRiddle : 'images';
  return { language, riddle };
}

function renderHintEditor() {
  const panel = document.getElementById('maintenanceHintEditor');
  const languageSelect = document.getElementById('hintEditorLanguage');
  const riddleSelect = document.getElementById('hintEditorRiddle');
  const list = document.getElementById('hintEditorList');
  const addButton = document.getElementById('hintEditorAdd');
  const saveButton = document.getElementById('hintEditorSave');
  const status = document.getElementById('hintEditorStatus');
  const compactStatus = document.getElementById('hintEditorCompactStatus');
  if (!panel || !languageSelect || !riddleSelect || !list || !addButton || !saveButton || !status) return;

  const serverSignature = stableStringify(state.hint_templates || {});
  if (!hintEditorDraft || (!hintEditorDirty && serverSignature !== hintEditorSignature)) {
    hintEditorDraft = copyHintTemplateMap();
    hintEditorSignature = serverSignature;
  }
  const previousRiddle = riddleSelect.value || 'images';
  const labels = new Map((state.riddles || []).map((riddle) => [riddle.id, riddle.label]));
  const optionSignature = HINT_RIDDLE_ORDER.map((id) => `${id}:${labels.get(id) || id}`).join('|');
  if (riddleSelect.dataset.signature !== optionSignature) {
    riddleSelect.innerHTML = HINT_RIDDLE_ORDER
      .map((id) => `<option value="${escapeAttr(id)}">${escapeHtml(labels.get(id) || id)}</option>`)
      .join('');
    riddleSelect.dataset.signature = optionSignature;
    riddleSelect.value = HINT_RIDDLE_ORDER.includes(previousRiddle) ? previousRiddle : 'images';
  }

  const maintenance = Number(state.game.phase || 0) === 1;
  const { language, riddle } = selectedHintEditorValues();
  const tips = hintEditorDraft?.[riddle]?.[language] || [];
  list.innerHTML = tips.length
    ? tips.map((tip, index) => `
      <div class="hint-editor-row" data-index="${index}">
        <textarea class="hint-editor-text" rows="3" maxlength="2000" ${maintenance ? '' : 'disabled'}>${escapeHtml(tip)}</textarea>
        <div class="hint-editor-row-actions">
          <button type="button" data-editor-action="up" ${!maintenance || index === 0 ? 'disabled' : ''} aria-label="Tipp nach oben">↑</button>
          <button type="button" data-editor-action="down" ${!maintenance || index === tips.length - 1 ? 'disabled' : ''} aria-label="Tipp nach unten">↓</button>
          <button type="button" data-editor-action="delete" ${maintenance ? '' : 'disabled'}>Löschen</button>
        </div>
      </div>
    `).join('')
    : `<p class="hint-template-empty">${escapeHtml(NO_HINT_TEMPLATE_TEXT[language])}</p>`;
  languageSelect.disabled = false;
  riddleSelect.disabled = false;
  addButton.disabled = !maintenance;
  saveButton.disabled = !maintenance || !hintEditorDirty;
  status.textContent = maintenance
    ? (hintEditorDirty ? 'Ungespeicherte Änderungen.' : 'Bearbeitung ist in Wartung freigeschaltet.')
    : 'Schreibgeschützt. Bearbeitung ist nur in Phase 1 (Wartung) möglich.';
  if (compactStatus) compactStatus.textContent = maintenance ? 'Bearbeitung freigeschaltet' : 'Außerhalb der Wartung schreibgeschützt';
  panel.classList.toggle('hint-editor-readonly', !maintenance);
}

function wireHintEditor() {
  const languageSelect = document.getElementById('hintEditorLanguage');
  const riddleSelect = document.getElementById('hintEditorRiddle');
  const list = document.getElementById('hintEditorList');
  const addButton = document.getElementById('hintEditorAdd');
  const saveButton = document.getElementById('hintEditorSave');
  languageSelect?.addEventListener('change', renderHintEditor);
  riddleSelect?.addEventListener('change', renderHintEditor);
  list?.addEventListener('input', (event) => {
    const input = event.target.closest?.('.hint-editor-text');
    if (!input || Number(state.game.phase || 0) !== 1) return;
    const index = safeInt(input.closest('.hint-editor-row')?.dataset.index, -1);
    const { language, riddle } = selectedHintEditorValues();
    if (index < 0 || !hintEditorDraft?.[riddle]?.[language]) return;
    hintEditorDraft[riddle][language][index] = input.value;
    hintEditorDirty = true;
    if (saveButton) saveButton.disabled = false;
    const status = document.getElementById('hintEditorStatus');
    if (status) status.textContent = 'Ungespeicherte Änderungen.';
  });
  list?.addEventListener('click', (event) => {
    const button = event.target.closest?.('[data-editor-action]');
    if (!button || button.disabled || Number(state.game.phase || 0) !== 1) return;
    const index = safeInt(button.closest('.hint-editor-row')?.dataset.index, -1);
    const { language, riddle } = selectedHintEditorValues();
    const tips = hintEditorDraft?.[riddle]?.[language];
    if (!tips || index < 0 || index >= tips.length) return;
    if (button.dataset.editorAction === 'delete') tips.splice(index, 1);
    if (button.dataset.editorAction === 'up' && index > 0) [tips[index - 1], tips[index]] = [tips[index], tips[index - 1]];
    if (button.dataset.editorAction === 'down' && index < tips.length - 1) [tips[index], tips[index + 1]] = [tips[index + 1], tips[index]];
    hintEditorDirty = true;
    renderHintEditor();
  });
  addButton?.addEventListener('click', () => {
    if (Number(state.game.phase || 0) !== 1) return;
    const { language, riddle } = selectedHintEditorValues();
    hintEditorDraft[riddle][language].push('Neuer Tipp');
    hintEditorDirty = true;
    renderHintEditor();
    list?.querySelector('.hint-editor-row:last-child .hint-editor-text')?.focus();
  });
  saveButton?.addEventListener('click', async () => {
    if (Number(state.game.phase || 0) !== 1 || !hintEditorDirty) return;
    try {
      await runAction(saveButton, async () => {
        const result = await api('/api/hint-templates', {
          method: 'POST',
          body: JSON.stringify({ templates: hintEditorDraft, ...actionGuard() }),
        });
        state.hint_templates = result.hint_templates;
        hintEditorDirty = false;
        hintEditorSignature = stableStringify(state.hint_templates);
        rerenderRiddleViews();
        renderHintEditor();
        showFeedback('Tippvorlagen atomar gespeichert.', result.directory_synced ? 'ok' : 'warn');
      });
    } catch (_error) {
      renderHintEditor();
    }
  });
}

function updateRiddleTimeDisplays() {
  for (const riddle of state.riddles || []) {
    const seconds = riddle.display_time_s ?? riddle.time_s ?? riddle.live_time_s ?? riddle.solve_time_s ?? 0;
    const row = document.querySelector(`#riddlesBody tr[data-riddle-id="${CSS.escape(riddle.id)}"]`);
    const input = row?.querySelector('.riddle-time-input');
    if (input && !riddleTimeEditing[riddle.id] && riddleTimeDrafts[riddle.id] == null) {
      input.value = seconds >= 1 ? String(Math.round(Number(seconds) * 1000) / 1000) : '';
    }
    const card = document.querySelector(`#currentRiddlesGrid .current-riddle-card[data-riddle-id="${CSS.escape(riddle.id)}"]`);
    const cardTime = card?.querySelector('.current-card-time');
    if (cardTime) cardTime.textContent = fmtTime(seconds);
  }
}

function patchState(data) {
  const initialRender = !lastSnapshot;
  const rerenderTop = sectionChanged(data, 'game') || sectionChanged(data, 'booking') || sectionChanged(data, 'start_assignment') || sectionChanged(data, 'meta');
  const rerenderNodes = sectionChanged(data, 'nodes') || sectionChanged(data, 'game');
  const rerenderLocks = sectionChanged(data, 'locks');
  const rerenderLights = sectionChanged(data, 'lights');
  const riddlesChanged = sectionChanged(data, 'riddles');
  const riddlesStructurallyChanged = riddleStructureChanged(data);
  const hintTemplatesChanged = sectionChanged(data, 'hint_templates');
  const previousLanguage = normalizeHintLanguage(state.booking?.language);
  const nextBooking = normalizeBooking(data.booking || state.booking);
  const languageChanged = previousLanguage !== nextBooking.language;

  syncLocalTimers(data.game || state.game);
  state.game = data.game || state.game;
  state.nodes = data.nodes || [];
  state.locks = data.locks || [];
  state.lights = data.lights || [];
  state.riddles = data.riddles || [];
  state.booking = nextBooking;
  state.hint_templates = data.hint_templates || state.hint_templates;
  state.start_assignment = data.start_assignment || state.start_assignment;
  state.meta = data.meta || state.meta;

  for (const light of state.lights) {
    if (!dimEditing[light.id] && light.dimmable && dimDrafts[light.id] == null) {
      dimDrafts[light.id] = light.pct;
    }
  }

  if (initialRender) {
    renderTop();
    renderNodes();
    renderLocks();
    renderLights();
    rerenderRiddleViews();
    renderHintEditor();
  } else {
    if (rerenderTop) renderTop();
    if (rerenderNodes) renderNodes();
    if (rerenderLocks) renderLocks();
    if (rerenderLights) renderLights();
    if (riddlesStructurallyChanged || languageChanged) {
      rerenderRiddleViews();
    } else if (riddlesChanged) {
      updateRiddleTimeDisplays();
    }
    if (hintTemplatesChanged) {
      if (!hintEditorDirty) rerenderRiddleViews();
      renderHintEditor();
    } else if (rerenderTop) {
      renderHintEditor();
    }
  }

  lastSnapshot = { ...data, booking: state.booking };
}

function currentRunId() {
  return String(state.game.run_id || '').trim();
}

async function handleStart(button) {
  const startClickedAtMs = Date.now();
  if (startInFlight) return;
  if (Number(state.game.phase || 0) !== 2) {
    showFeedback('Das Spiel kann nur aus der Phase „Vorbereitung“ gestartet werden.', 'warn');
    return;
  }
  if (!currentRunId()) {
    showFeedback('Der vorbereitete Lauf wurde noch nicht vom Game Master bestätigt.', 'warn');
    return;
  }

  const confirmed = await confirmAction({
    title: 'Spiel wirklich starten?',
    message: 'Diese Aktion startet den Countdown und die Spielzeit. Prüfe vorher, ob alle Spieler bereit sind.',
    confirmLabel: 'Spiel starten',
  });
  if (!confirmed) return;

  startInFlight = true;
  renderTop();
  try {
    const result = await runAction(button, async () => {
      const response = await api('/api/phase', {
        method: 'POST',
        body: JSON.stringify({ action: 'start', start_clicked_at_ms: startClickedAtMs, ...actionGuard() }),
      });
      if (response.start_assignment) {
        state.start_assignment = response.start_assignment;
        renderTop();
      }
      return response;
    }, { silent: true });
    setSummaryFeedback('');
    showFeedback(
      result.idempotent
        ? 'Der Spielstart ist bereits serverseitig beansprucht. Der aktuelle Zuordnungsstand wird unten angezeigt.'
        : 'Startbefehl an MQTT übergeben. Der aktuelle serverseitige Zuordnungsstand wird unten angezeigt.',
      'ok',
    );
  } catch (error) {
    showFeedback(error.message || String(error), 'error');
  } finally {
    startInFlight = false;
    renderTop();
  }
}

async function handlePhaseAction(action, button) {
  if (action === 'start') {
    await handleStart(button);
    return;
  }

  if (shouldConfirmPhaseChange()) {
    const label = {
      standby: 'Bereitschaft',
      maintenance: 'Wartung',
      prepare: 'Vorbereitung',
    }[action] || action;
    const confirmed = await confirmAction({
      title: `Phase auf „${label}“ ändern?`,
      message: 'Das Spiel läuft gerade. Ein Phasenwechsel kann Timer, Licht, Schlösser und den aktuellen Spielstand verändern.',
      confirmLabel: 'Phase ändern',
    });
    if (!confirmed) return;
  }

  startInFlight = false;
  await runAction(button, () => api('/api/phase', {
    method: 'POST',
    body: JSON.stringify({ action, ...actionGuard() }),
  })).catch(() => {});
}

async function sendSummaryEmail(button) {
  summaryEmailBusy = true;
  renderSummaryControls();
  setSummaryFeedback('Buchung wird kontrolliert…');
  try {
    await runAction(button, async () => {
      await loadBookings({ silent: true });
      const saved = normalizeBooking(state.booking);
      setSummaryFeedback('Spielzusammenfassung wird gesendet…');
      const result = await api('/api/send-summary-email', {
        method: 'POST',
        body: JSON.stringify({ booking: saved }),
      });
      const email = result?.email || {};
      const target = saved.customerEmail || '';
      if (email.skipped) {
        setSummaryFeedback(`E-Mail nicht gesendet: ${email.reason || 'kein Grund angegeben'}.`, 'warn');
      } else {
        setSummaryFeedback(target ? `Spielzusammenfassung an ${target} gesendet.` : 'Spielzusammenfassung gesendet.', 'ok');
      }
    }, { silent: true });
  } catch (error) {
    setSummaryFeedback(
      error.message || 'E-Mail derzeit nicht verfügbar. Das Spiel bleibt gespeichert und der Versand kann später erneut versucht werden.',
      'warn',
    );
  } finally {
    summaryEmailBusy = false;
    renderSummaryControls();
  }
}

function wireTopControls() {
  document.querySelectorAll('[data-phase-action]').forEach((button) => {
    button.addEventListener('click', () => handlePhaseAction(button.dataset.phaseAction, button));
  });

  const startButton = document.getElementById('startGameBtn');
  startButton?.addEventListener('click', () => handleStart(startButton));

  const sendButton = document.getElementById('sendSummaryEmailBtn');
  sendButton?.addEventListener('click', () => sendSummaryEmail(sendButton));

  document.querySelectorAll('[data-panel-target]').forEach((button) => {
    button.addEventListener('click', () => {
      const panel = document.getElementById(button.dataset.panelTarget);
      if (!panel) return;
      panel.open = !panel.open;
      updateDrawerButtons();
    });
  });

  for (const panelId of ['bookingDetails', 'allRiddlesPanel', 'diagnosticsDetails', 'maintenanceHintEditor']) {
    document.getElementById(panelId)?.addEventListener('toggle', updateDrawerButtons);
  }

  document.querySelectorAll('.booking-details > summary, .section-summary').forEach((summary) => {
    summary.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      window.setTimeout(updateDrawerButtons, 0);
    });
  });

  const bookingSelect = document.getElementById('bookingSelect');
  const languageSelect = document.getElementById('hintLanguageSelect');
  const refreshBookingsButton = document.getElementById('refreshBookingsBtn');
  const applyBookingButton = document.getElementById('applyBookingBtn');
  const emailInput = document.getElementById('testBookingEmail');
  const playersInput = document.getElementById('testBookingPlayers');

  bookingSelect?.addEventListener('change', async () => {
    const selected = bookingOptions.find((item) => bookingKey(item) === bookingSelect.value);
    if (!selected) return;
    const normalized = normalizeBooking(selected);
    try {
      await confirmAndSaveBooking(normalized);
      await fetchAndPatch();
    } catch (error) {
      setBookingFeedback(error.message || 'Buchung konnte nicht gespeichert werden.', 'error');
    }
  });

  languageSelect?.addEventListener('change', () => {
    const corrected = normalizeBooking({ ...state.booking, language: languageSelect.value });
    saveSelectedBooking(corrected, { languageOnly: true }).catch((error) => {
      setBookingFeedback(error.message || 'Tipp-Sprache konnte nicht gespeichert werden.', 'error');
    });
    window.setTimeout(flushQueuedSnapshot, 0);
  });

  refreshBookingsButton?.addEventListener('click', async () => {
    setBookingFeedback('Buchungen werden geladen…');
    await runAction(refreshBookingsButton, () => loadBookings()).catch(() => {});
  });

  const applyBookingDraft = async () => {
    if (bookingBusy) return;
    const option = bookingOptions.find((item) => bookingKey(item) === bookingSelect?.value) || state.booking;
    const selected = normalizeBooking(option);
    const draft = selected.kind === 'empty' ? selected : normalizeBooking({
      ...selected,
      customerEmail: String(emailInput?.value || selected.customerEmail || '').trim(),
      players: Math.max(1, safeInt(playersInput?.value, selected.players || 1)),
    });
    try {
      await confirmAndSaveBooking(draft);
      await fetchAndPatch();
    } catch (error) {
      setBookingFeedback(error.message || 'Buchung konnte nicht gespeichert werden.', 'error');
    }
  };

  applyBookingButton?.addEventListener('click', applyBookingDraft);
  emailInput?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    applyBookingDraft();
  });

  playersInput?.addEventListener('input', () => {
    playersInput.value = playersInput.value.replace(/[^0-9]/g, '');
  });
  playersInput?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    applyBookingDraft();
  });
}

function setDiagnosticsStatus(message, kind = '') {
  const status = document.getElementById('diagnosticsStatus');
  if (!status) return;
  status.textContent = message || '';
  status.className = `diagnostics-status ${kind ? `diagnostics-status--${kind}` : ''}`;
}

function readDiagnosticsFilters() {
  diagnostics.nodes = new Set(
    Array.from(document.querySelectorAll('.diagnostics-node-filter:checked'), (input) => input.value),
  );
  diagnostics.levels = new Set(
    Array.from(document.querySelectorAll('.diagnostics-level-filter:checked'), (input) => input.value),
  );
}

function formatDiagnosticsTimestamp(value) {
  if (value == null || value === '') return '';
  let milliseconds;
  if (typeof value === 'number' || /^\d+(?:\.\d+)?$/.test(String(value))) {
    const numeric = Number(value);
    milliseconds = numeric > 10_000_000_000 ? numeric : numeric * 1000;
  } else {
    milliseconds = Date.parse(String(value));
  }
  if (!Number.isFinite(milliseconds)) return String(value);
  const date = new Date(milliseconds);
  if (!Number.isFinite(date.getTime())) return String(value);
  try {
    return date.toLocaleString('de-DE', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      fractionalSecondDigits: 3,
      hour12: false,
    });
  } catch (_error) {
    return date.toISOString();
  }
}

function diagnosticsEntryTimestamp(entry) {
  if (entry.time_valid !== false && entry.ts != null && entry.ts !== '') {
    return formatDiagnosticsTimestamp(entry.ts);
  }
  const received = formatDiagnosticsTimestamp(entry.received_at);
  return received ? `${received} (Empfang)` : 'Empfangszeit unbekannt';
}

function createDiagnosticsLogEntry(entry) {
  const item = document.createElement('article');
  item.className = 'diagnostics-log-entry';

  const header = document.createElement('div');
  header.className = 'diagnostics-log-header';

  const timestamp = document.createElement('time');
  timestamp.className = 'diagnostics-log-time';
  timestamp.textContent = diagnosticsEntryTimestamp(entry);

  const node = document.createElement('span');
  node.className = 'diagnostics-log-node';
  node.textContent = String(entry.node ?? '');

  const level = document.createElement('span');
  const levelClass = { DBG: 'dbg', INF: 'inf', WRN: 'wrn', ERR: 'err' }[entry.lv] || 'unknown';
  level.className = `diagnostics-log-level diagnostics-log-level--${levelClass}`;
  level.textContent = String(entry.lv ?? '');

  const sequence = document.createElement('span');
  sequence.className = 'diagnostics-log-sequence';
  sequence.textContent = `#${String(entry.seq ?? '')}`;

  header.append(timestamp, node, level, sequence);
  item.appendChild(header);

  const message = document.createElement('div');
  message.className = 'diagnostics-log-message';
  message.textContent = String(entry.msg ?? '');
  item.appendChild(message);

  let detailText = '';
  try {
    detailText = JSON.stringify(Object.hasOwn(entry, 'd') ? entry.d : {}, null, 2);
  } catch (_error) {
    detailText = '[Details konnten nicht dargestellt werden]';
  }
  if (detailText && detailText !== '{}' && detailText !== '[]') {
    const detail = document.createElement('details');
    detail.className = 'diagnostics-log-detail';
    const summary = document.createElement('summary');
    const detailType = entry.d_type == null ? '' : String(entry.d_type);
    summary.textContent = detailType ? `Details (${detailType})` : 'Details';
    const pre = document.createElement('pre');
    pre.textContent = detailText;
    detail.append(summary, pre);
    item.appendChild(detail);
  }
  return item;
}

function updateDiagnosticsCount() {
  const count = document.getElementById('diagnosticsCount');
  if (count) count.textContent = `${diagnostics.entries.length} Einträge`;
}

function replaceDiagnosticsLogs() {
  const list = document.getElementById('diagnosticsLogList');
  if (!list) return;
  const fragment = document.createDocumentFragment();
  for (const entry of diagnostics.entries) fragment.appendChild(createDiagnosticsLogEntry(entry));
  list.replaceChildren(fragment);
  updateDiagnosticsCount();
}

function appendDiagnosticsLogs(entries) {
  const list = document.getElementById('diagnosticsLogList');
  if (!list) return;
  const fragment = document.createDocumentFragment();
  for (const entry of entries) fragment.appendChild(createDiagnosticsLogEntry(entry));
  list.appendChild(fragment);
  while (list.childElementCount > DIAGNOSTICS_MAX_ENTRIES) list.firstElementChild?.remove();
  updateDiagnosticsCount();
}

function applyLastRequestedLogLevels(records) {
  if (!records || typeof records !== 'object') return;
  document.querySelectorAll('.diagnostics-level-row').forEach((row) => {
    const node = row.dataset.logLevelNode;
    const record = records[node];
    const level = String(record?.level || '');
    if (!['DBG', 'INF', 'WRN', 'ERR'].includes(level)) return;
    const select = row.querySelector('.diagnostics-level-select');
    const status = row.querySelector('.diagnostics-level-status');
    if (select && !diagnostics.logLevelDrafts.has(node) && document.activeElement !== select) select.value = level;
    if (status) {
      const requestedAt = formatDiagnosticsTimestamp(record.requested_at);
      status.textContent = `Zuletzt angefordert: ${level}${requestedAt ? ` (${requestedAt})` : ''}`;
    }
  });
}

function updateDiagnosticsStream(nextStreamId) {
  if (typeof nextStreamId !== 'string' || !nextStreamId) {
    throw new Error('Der Log-Stream hat keine gültige Kennung.');
  }
  const changed = diagnostics.streamId !== null && diagnostics.streamId !== nextStreamId;
  diagnostics.streamId = nextStreamId;
  return changed;
}

function diagnosticsPollDelay(hasMore) {
  if (!hasMore) {
    diagnostics.backlogPages = 0;
    return DIAGNOSTICS_POLL_INTERVAL_MS;
  }
  diagnostics.backlogPages += 1;
  if (diagnostics.backlogPages >= DIAGNOSTICS_BACKLOG_PAGE_BUDGET) {
    diagnostics.backlogPages = 0;
    return DIAGNOSTICS_BACKLOG_BUDGET_PAUSE_MS;
  }
  return DIAGNOSTICS_BACKLOG_DELAY_MS;
}

function diagnosticsBufferStatus(data) {
  const size = Number.isSafeInteger(data.buffer_size) ? data.buffer_size : '?';
  const capacity = Number.isSafeInteger(data.buffer_capacity) ? data.buffer_capacity : '?';
  const dropped = Number.isSafeInteger(data.dropped) && data.dropped > 0
    ? `, ${data.dropped} ältere Einträge überschrieben`
    : '';
  if (data.mqtt_connected === true) return `MQTT verbunden; Puffer ${size}/${capacity}${dropped}.`;
  return `MQTT getrennt; Pufferanzeige ${size}/${capacity}${dropped}.`;
}

function diagnosticsCanPoll() {
  const details = document.getElementById('diagnosticsDetails');
  return Boolean(details?.open)
    && document.visibilityState === 'visible'
    && diagnostics.nodes.size > 0
    && diagnostics.levels.size > 0;
}

function stopDiagnosticsPolling() {
  diagnostics.generation += 1;
  diagnostics.backlogPages = 0;
  if (diagnostics.timer) window.clearTimeout(diagnostics.timer);
  diagnostics.timer = null;
  if (diagnostics.requestController) diagnostics.requestController.abort();
  diagnostics.requestController = null;
}

function scheduleDiagnosticsPoll(delay = DIAGNOSTICS_POLL_INTERVAL_MS, generation = diagnostics.generation) {
  if (diagnostics.timer) window.clearTimeout(diagnostics.timer);
  diagnostics.timer = null;
  if (generation !== diagnostics.generation || !diagnosticsCanPoll()) return;
  diagnostics.timer = window.setTimeout(() => pollDiagnosticsLogs(generation), delay);
}

async function pollDiagnosticsLogs(generation = diagnostics.generation) {
  if (generation !== diagnostics.generation || !diagnosticsCanPoll() || diagnostics.requestController) return;
  const params = new URLSearchParams();
  params.set('after', String(diagnostics.after));
  params.set('limit', String(DIAGNOSTICS_POLL_LIMIT));
  params.set('nodes', Array.from(diagnostics.nodes).join(','));
  params.set('levels', Array.from(diagnostics.levels).join(','));
  const controller = new AbortController();
  diagnostics.requestController = controller;
  let nextDelay = DIAGNOSTICS_POLL_INTERVAL_MS;

  try {
    const data = await api(`/api/logs?${params.toString()}`, { signal: controller.signal });
    if (generation !== diagnostics.generation || !diagnosticsCanPoll()) return;
    const streamChanged = updateDiagnosticsStream(data.stream_id);
    diagnostics.mqttConnected = data.mqtt_connected === true;
    applyLastRequestedLogLevels(data.last_requested);
    if (streamChanged) {
      diagnostics.after = 0;
      diagnostics.newestSeq = 0;
      diagnostics.dropped = 0;
      diagnostics.backlogPages = 0;
      diagnostics.entries = [];
      replaceDiagnosticsLogs();
      setDiagnosticsStatus(
        `Der Dashboard-Log-Stream wurde neu gestartet; Cursor und Anzeige wurden zurückgesetzt. ${diagnosticsBufferStatus(data)}`,
        'warn',
      );
      nextDelay = DIAGNOSTICS_BACKLOG_DELAY_MS;
      return;
    }

    let resetDisplay = false;
    let resetReason = '';
    if (data.reset || data.gap) {
      diagnostics.entries = [];
      resetDisplay = true;
      resetReason = data.gap_reason === 'cursor_ahead'
        ? 'Der Dashboard-Prozess wurde neu gestartet; die Anzeige wurde zurückgesetzt.'
        : 'Ältere Logs wurden im Serverpuffer überschrieben; die Anzeige wurde zurückgesetzt.';
    }

    const incoming = Array.isArray(data.entries) ? data.entries.filter((entry) => entry && typeof entry === 'object') : [];
    diagnostics.entries.push(...incoming);
    if (diagnostics.entries.length > DIAGNOSTICS_MAX_ENTRIES) {
      diagnostics.entries.splice(0, diagnostics.entries.length - DIAGNOSTICS_MAX_ENTRIES);
    }
    if (Number.isSafeInteger(data.next_after) && data.next_after >= 0) diagnostics.after = data.next_after;
    if (Number.isSafeInteger(data.newest_seq) && data.newest_seq >= 0) diagnostics.newestSeq = data.newest_seq;
    diagnostics.dropped = Number.isSafeInteger(data.dropped) ? data.dropped : diagnostics.dropped;
    if (resetDisplay) replaceDiagnosticsLogs();
    else appendDiagnosticsLogs(incoming);

    const bufferStatus = diagnosticsBufferStatus(data);
    setDiagnosticsStatus(
      resetReason ? `${resetReason} ${bufferStatus}` : bufferStatus,
      resetReason || !diagnostics.mqttConnected ? 'warn' : '',
    );
    nextDelay = diagnosticsPollDelay(Boolean(data.has_more));
  } catch (error) {
    if (error?.name !== 'AbortError' && generation === diagnostics.generation) {
      setDiagnosticsStatus(`Log-Abruf fehlgeschlagen: ${error.message || error}`, 'error');
    }
  } finally {
    if (diagnostics.requestController === controller) {
      diagnostics.requestController = null;
      scheduleDiagnosticsPoll(nextDelay, generation);
    }
  }
}

function startDiagnosticsPolling() {
  if (!diagnosticsCanPoll()) {
    if (!diagnostics.nodes.size) setDiagnosticsStatus('Kein Knoten ausgewählt. Der Log-Abruf ist pausiert.', 'warn');
    else if (!diagnostics.levels.size) setDiagnosticsStatus('Keine Log-Stufe ausgewählt. Der Log-Abruf ist pausiert.', 'warn');
    return;
  }
  if (diagnostics.requestController || diagnostics.timer) return;
  diagnostics.generation += 1;
  pollDiagnosticsLogs(diagnostics.generation);
}

function restartDiagnosticsForFilters() {
  stopDiagnosticsPolling();
  readDiagnosticsFilters();
  diagnostics.after = 0;
  diagnostics.newestSeq = 0;
  diagnostics.entries = [];
  replaceDiagnosticsLogs();
  startDiagnosticsPolling();
}

async function requestDiagnosticLogLevel(row, button) {
  const node = row.dataset.logLevelNode;
  const select = row.querySelector('.diagnostics-level-select');
  const status = row.querySelector('.diagnostics-level-status');
  const submittedDraft = diagnostics.logLevelDrafts.get(node);
  const level = submittedDraft ?? select?.value;
  button.disabled = true;
  try {
    const result = await api('/api/log-level', {
      method: 'POST',
      body: JSON.stringify({ node, level }),
    });
    const requestedAt = formatDiagnosticsTimestamp(result.requested_at);
    if (result.requested === level && diagnostics.logLevelDrafts.get(node) === submittedDraft) {
      diagnostics.logLevelDrafts.delete(node);
      if (select) select.value = result.requested;
    }
    if (status) status.textContent = `Zuletzt angefordert: ${result.requested}${requestedAt ? ` (${requestedAt})` : ''}`;
    setDiagnosticsStatus(
      `${node}: ${result.requested} angefordert und für MQTT eingereiht. Die Anwendung auf dem Gerät ist nicht bestätigt.`,
      'ok',
    );
    scheduleDiagnosticsPoll(DIAGNOSTICS_BACKLOG_DELAY_MS);
  } catch (error) {
    if (status) status.textContent = `Anforderung fehlgeschlagen: ${error.message || error}`;
    setDiagnosticsStatus(`${node}: Log-Stufe konnte nicht angefordert werden. ${error.message || error}`, 'error');
  } finally {
    button.disabled = false;
  }
}

function wireDiagnostics() {
  const details = document.getElementById('diagnosticsDetails');
  if (!details) return;
  readDiagnosticsFilters();
  replaceDiagnosticsLogs();

  details.addEventListener('toggle', () => {
    if (details.open) {
      readDiagnosticsFilters();
      startDiagnosticsPolling();
    } else {
      stopDiagnosticsPolling();
      setDiagnosticsStatus('Log-Abruf pausiert. Beim Öffnen wird inkrementell fortgesetzt.');
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && details.open) {
      readDiagnosticsFilters();
      startDiagnosticsPolling();
    } else {
      stopDiagnosticsPolling();
    }
  });

  document.querySelectorAll('.diagnostics-node-filter, .diagnostics-level-filter').forEach((input) => {
    input.addEventListener('change', restartDiagnosticsForFilters);
  });

  document.getElementById('diagnosticsAllNodesBtn')?.addEventListener('click', () => {
    document.querySelectorAll('.diagnostics-node-filter').forEach((input) => { input.checked = true; });
    restartDiagnosticsForFilters();
  });
  document.getElementById('diagnosticsNoNodesBtn')?.addEventListener('click', () => {
    document.querySelectorAll('.diagnostics-node-filter').forEach((input) => { input.checked = false; });
    restartDiagnosticsForFilters();
  });
  document.querySelectorAll('[data-service-restart]').forEach((button) => {
    button.addEventListener('click', () => restartSystemService(button.dataset.serviceRestart, button).catch(() => {}));
  });
  document.querySelectorAll('.diagnostics-level-row').forEach((row) => {
    const node = row.dataset.logLevelNode;
    const select = row.querySelector('.diagnostics-level-select');
    const button = row.querySelector('.diagnostics-level-button');
    select?.addEventListener('change', () => {
      diagnostics.logLevelDrafts.set(node, select.value);
    });
    button?.addEventListener('click', () => requestDiagnosticLogLevel(row, button));
  });
}

function updateFastTimers() {
  const timerValue = document.getElementById('timerValue');
  const riddleTimerValue = document.getElementById('riddleTimerValue');
  const prepareCounter = document.getElementById('prepareCounterValue');
  if (timerValue) timerValue.textContent = fmtGameTime(readLocalTimer());
  if (riddleTimerValue) {
    riddleTimerValue.textContent = Number(state.game.phase || 0) >= 3 && state.game.current_riddle_name
      ? fmtTime(readLocalRiddleTimer())
      : '—';
  }
  if (prepareCounter && Number(state.game.phase || 0) === 2) {
    prepareCounter.textContent = String(Math.floor(Date.now() / 1000) % 11);
  }
}

installInteractionGuard();
wireConfirmationDialog();
wireTopControls();
wireHintEditor();
wireDiagnostics();
window.setInterval(updateFastTimers, 200);
loadBookings().catch(() => {});
pollLoop();
