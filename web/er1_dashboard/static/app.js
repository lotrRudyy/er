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
  start_assignment: { active: false, status: 'idle', message: '' },
  meta: { persistence_degraded: false },
};

const ui = {
  allRiddlesOpen: false,
  emergencyOpen: false,
  lastPhase: null,
};

const TEST_BOOKING_ID = '__test__';
const EMPTY_BOOKING_ID = '__empty__';
const RESETTABLE_RIDDLES = new Set(['prison', 'wheel', 'chains', 'tangram', 'magnet']);
const HINT_LANGUAGE_LABELS = Object.freeze({ de: 'Deutsch', en: 'English', it: 'Italiano' });
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

function immutableHintSet(de, en, it) {
  return Object.freeze({
    de: Object.freeze(de),
    en: Object.freeze(en),
    it: Object.freeze(it),
  });
}

const HINT_TEMPLATES = Object.freeze({
  images: immutableHintSet(
    ['Fällt euch irgendetwas Ungewöhnliches im Raum auf? Was könnte man damit machen?'],
    ['Do you notice anything unusual in the room? What could you do with it?'],
    ["Notate qualcosa di insolito nella stanza? Che cosa si potrebbe fare con quell'elemento?"],
  ),
  piano: immutableHintSet(
    ['Ihr sucht keine Zahlenfolge, sondern müsst die Holzscheiben auf eine Zahl einstellen und dann die Melodie entschlüsseln. Diese Zahl habt ihr schon einmal irgendwo gelesen. Danach dürft ihr sie nicht mehr verändern.'],
    ['You are not looking for a sequence of numbers. Set the wooden discs to a single number and then decode the melody. You have seen that number somewhere before. Once it is set, do not change it again.'],
    ['Non state cercando una sequenza di numeri: dovete impostare i dischi di legno su un unico numero e poi decifrare la melodia. Quel numero lo avete già letto da qualche parte. Una volta impostato, non dovete più cambiarlo.'],
  ),
  prison: immutableHintSet(
    ['Habt ihr die Tische genau durchsucht?'],
    ['Have you searched the tables carefully?'],
    ['Avete controllato attentamente i tavoli?'],
  ),
  wheel: immutableHintSet(
    ['Was könnte man mit dem Wagenrad tun?'],
    ['What could you do with the wagon wheel?'],
    ['Che cosa si potrebbe fare con la ruota del carro?'],
  ),
  chains: immutableHintSet(
    [
      'Als Erstes müsst ihr den Würfel in einen der drei Baumstämme stecken und richtig ausrichten. Bei jedem Baumstamm gibt es drei Hälften eines Symbols; die jeweils andere Hälfte befindet sich auf dem Würfel.',
      'Sobald ihr ihn ausgerichtet habt, zeigt euch das Seil auf dem Würfel den Pfad, den das andere Seil in Wirklichkeit nehmen muss. Arbeitet dabei konzentriert und achtet auf die drei Ebenen.',
      'Das Seil ist jetzt richtig eingefädelt. Nun müsst ihr auf dem Würfel den letzten Hinweis finden: Sucht das richtige Auge – hier das Auge mit dem Dreieck. Es zeigt euch, aus welcher Richtung ihr auf das Gerüst schauen müsst.',
      'Falls ihr etwas nur schwer erkennen könnt, könnt ihr das Seil mit der Tafel nachzeichnen. Achtung: immer nur von der ersten bis zur letzten Holzscheibe.',
    ],
    [
      'First, place the cube into one of the three tree trunks and orient it correctly. Each trunk shows three halves of symbols; the matching other halves are on the cube.',
      'Once the cube is aligned, the rope on it shows the route that the real rope must follow. Work carefully and pay attention to the three levels.',
      'The rope is now threaded correctly. Find the final clue on the cube: look for the correct eye – here, the eye with the triangle. It tells you from which direction to view the frame.',
      'If the route is hard to see, use the board to trace the rope. Important: trace only from the first wooden disc to the last.',
    ],
    [
      'Per prima cosa dovete inserire il cubo in uno dei tre tronchi e orientarlo correttamente. Su ogni tronco ci sono tre metà di simboli; le rispettive altre metà si trovano sul cubo.',
      'Quando il cubo è orientato correttamente, la corda sul cubo vi mostra il percorso che deve seguire la corda vera. Procedete con attenzione e distinguete i tre livelli.',
      "Ora la corda è infilata correttamente. Sul cubo dovete trovare l'ultimo indizio: cercate l'occhio giusto – in questo caso quello con il triangolo. Vi indica da quale direzione dovete guardare la struttura.",
      "Se fate fatica a distinguere il percorso, potete riprodurre la corda sulla lavagna. Attenzione: sempre e soltanto dal primo disco di legno all'ultimo.",
    ],
  ),
  tangram: immutableHintSet([], [], []),
  magnet: immutableHintSet(
    ['Habt ihr alle Tische schon durchsucht?'],
    ['Have you searched all the tables?'],
    ['Avete già controllato tutti i tavoli?'],
  ),
  chess: immutableHintSet(
    [
      'Achtung: Zeile und Spalte nicht verwechseln. Das Pferd steht in derselben Zeile wie die Dame, und die Dame muss mit drei anderen Figuren in einer Zeile stehen. Wo könnte das sein?',
      'Das Pferd muss auf einem Randfeld stehen. Der König steht auf einem weißen Feld und ist diagonal mit dem Pferd verbunden. Das heißt …?',
      'Der König darf nur mit dem schwarzen Pferd diagonal verbunden sein.',
    ],
    [
      'Be careful not to mix up rows and columns. The knight is in the same row as the queen, and the queen must be in a row with three other pieces. Where could that be?',
      'The knight must be on an edge square. The king is on a white square and is diagonally connected to the knight. What does that imply?',
      'The king may be diagonally connected only to the black knight.',
    ],
    [
      'Attenzione a non confondere righe e colonne. Il cavallo si trova nella stessa riga della regina, e la regina deve trovarsi in una riga con altri tre pezzi. Dove potrebbe essere?',
      'Il cavallo deve trovarsi su una casella del bordo. Il re si trova su una casella bianca ed è collegato diagonalmente al cavallo. Questo significa …?',
      'Il re può essere collegato diagonalmente soltanto al cavallo nero.',
    ],
  ),
  knocking: immutableHintSet(
    [
      'Ihr müsst die Pfade so legen, dass die 1 zur 1, die 2 zur 2 und die 3 zur 3 führt.',
      'Dann müsst ihr die Scheiben umdrehen und zählen.',
    ],
    [
      'Arrange the paths so that 1 connects to 1, 2 to 2, and 3 to 3.',
      'Then turn the discs over and count.',
    ],
    [
      "Dovete disporre i percorsi in modo che l'1 arrivi all'1, il 2 al 2 e il 3 al 3.",
      'Poi dovete girare i dischi e contare.',
    ],
  ),
  candles: immutableHintSet(
    [
      'Wie kann man Kerzen löschen?',
      'Jetzt müsst ihr noch die richtige Reihenfolge herausfinden.',
      'Für jede Kerze gibt es drei Löcher: ein Loch für die zwei Holzscheiben und zwei Löcher für das kleine Holzfenster mit den Augen. Richtet die Holzscheiben richtig aus und schaut dann durch das Fenster.',
      'Wenn ihr anschließend die Striche zählt, erhaltet ihr für jede Kerze eine Zahl von 1 bis 4.',
    ],
    [
      'How can candles be extinguished?',
      'Now you still need to determine the correct order.',
      'Each candle has three holes: one hole for the two wooden discs and two holes for the small wooden window with the eyes. Align the wooden discs correctly, then look through the window.',
      'Then count the lines. This gives you a number from 1 to 4 for each candle.',
    ],
    [
      'Come si possono spegnere le candele?',
      "Ora dovete ancora trovare l'ordine corretto.",
      'Per ogni candela ci sono tre fori: un foro per i due dischi di legno e due fori per la piccola finestrella di legno con gli occhi. Allineate correttamente i dischi di legno e poi guardate attraverso la finestrella.',
      'Contando poi le linee, ottenete per ogni candela un numero da 1 a 4.',
    ],
  ),
  stars: immutableHintSet(
    ['Eine gute Strategie ist, zuerst die Sterne zu zählen. Dann müsst ihr die richtige Holzscheibe nicht mehr unter allen sieben, sondern nur noch unter zwei oder drei Holzscheiben suchen.'],
    ['A good strategy is to count the stars first. Then you only need to find the correct wooden disc among two or three instead of all seven.'],
    ['Una buona strategia è contare prima le stelle. In questo modo non dovrete più cercare il disco di legno corretto fra tutti e sette, ma soltanto fra due o tre.'],
  ),
  sissi: immutableHintSet([], [], []),
});

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
let bookingDraftSaveTimer = null;
let startInFlight = false;

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

function shouldConfirmPhaseChange() {
  return Number(state.game.phase || 0) >= 3;
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

function isDialogOpen() {
  return Boolean(document.querySelector('dialog[open]'));
}

function shouldDeferPatch() {
  return interactionActive || actionDepth > 0 || hasFocusedEditor() || isDialogOpen();
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
    window.setTimeout(pollLoop, 1000);
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

function confirmAction({ title, message, confirmLabel = 'Bestätigen', danger = true }) {
  const dialog = document.getElementById('confirmDialog');
  const titleNode = document.getElementById('confirmDialogTitle');
  const messageNode = document.getElementById('confirmDialogMessage');
  const cancelButton = document.getElementById('confirmDialogCancel');
  const confirmButton = document.getElementById('confirmDialogConfirm');

  if (!dialog || !titleNode || !messageNode || !cancelButton || !confirmButton) {
    return Promise.resolve(window.confirm(message));
  }

  if (dialog.open) dialog.close();
  titleNode.textContent = title;
  messageNode.textContent = message;
  confirmButton.textContent = confirmLabel;
  confirmButton.classList.toggle('danger-button', danger);
  confirmButton.classList.toggle('summary-email-btn', !danger);

  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      if (dialog.open) dialog.close();
      window.setTimeout(flushQueuedSnapshot, 0);
      resolve(value);
    };

    cancelButton.onclick = () => finish(false);
    confirmButton.onclick = () => finish(true);
    dialog.oncancel = (event) => {
      event.preventDefault();
      finish(false);
    };
    dialog.onclose = () => {
      if (!settled) finish(false);
    };
    dialog.showModal();
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
    localTimerBaseElapsed = Math.max(0, Number(game.elapsed_s || 0));
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
  return Math.max(0, Math.floor(localTimerBaseElapsed + ((Date.now() - localTimerSyncedAt) / 1000)));
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
  const kind = rawKind === 'empty' || rawId === EMPTY_BOOKING_ID
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

function selectedBookingDraft(baseBooking = state.booking) {
  const selected = normalizeBooking(baseBooking || {});
  const emailInput = document.getElementById('testBookingEmail');
  const playersInput = document.getElementById('testBookingPlayers');
  return normalizeBooking({
    ...selected,
    customerEmail: String(emailInput?.value || selected.customerEmail || (selected.kind === 'test' ? 'rudolf.dosser@gmail.com' : '')).trim(),
    players: selected.kind === 'empty' ? 0 : Math.max(1, safeInt(playersInput?.value, selected.players || (selected.kind === 'test' ? 2 : 1))),
    label: selected.kind === 'test' ? 'Testbuchung' : selected.label,
  });
}

async function loadBookings({ silent = false } = {}) {
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
    bookingOptions = loaded.length ? loaded : [fallback];
    if (!bookingOptions.some((item) => item.kind === 'test')) bookingOptions.unshift(fallback);
    bookingOptionsLoaded = true;
    if (!silent) setBookingFeedback(result.warning || '', result.warning ? 'warn' : '');
  } catch (error) {
    bookingOptions = [fallback];
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
      ? { hint_language: normalized.language }
      : { booking: normalized };
    if (scopedRunId) requestBody.expected_run_id = scopedRunId;
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
  const compact = document.getElementById('bookingCompactValue');
  if (!select) return;

  if (!bookingOptionsLoaded && !bookingOptions.length) {
    bookingOptions = [normalizeBooking({
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

  if (compact) compact.textContent = bookingCompactLabel(current) || 'Keine Buchung ausgewählt';
}

function openSummaryBookingConfirmation() {
  const dialog = document.getElementById('bookingConfirmDialog');
  const title = document.getElementById('bookingDialogTitle');
  const message = document.getElementById('bookingDialogMessage');
  const select = document.getElementById('bookingConfirmSelect');
  const info = document.getElementById('bookingConfirmInfo');
  const suggestionInfo = document.getElementById('bookingSuggestionInfo');
  const cancelButton = document.getElementById('bookingDialogCancel');
  const confirmButton = document.getElementById('bookingDialogConfirm');

  if (!dialog || !title || !message || !select || !cancelButton || !confirmButton) {
    return Promise.resolve(normalizeBooking(state.booking));
  }

  // Email confirmation uses the stored start-time choice; it never ranks by time again.
  const selectedAtStart = normalizeBooking(state.booking);
  const orderedKeys = new Set();
  const ordered = [];
  for (const item of [selectedAtStart, ...bookingOptions.map(normalizeBooking)]) {
    const key = bookingKey(item);
    if (!key || orderedKeys.has(key)) continue;
    orderedKeys.add(key);
    ordered.push(item);
  }
  const suggested = ordered[0] || selectedAtStart;

  select.innerHTML = ordered
    .map((item) => `<option value="${escapeAttr(bookingKey(item))}">${escapeHtml(bookingOptionLabel(item))}</option>`)
    .join('');
  select.value = bookingKey(suggested);

  title.textContent = 'Buchung vor dem Senden bestätigen';
  message.textContent = 'Kontrolliere die Buchung. Erst nach deiner Bestätigung wird die Spielzusammenfassung versendet.';
  confirmButton.textContent = 'Buchung bestätigen und senden';

  const updateInfo = () => {
    const selected = ordered.find((item) => bookingKey(item) === select.value) || suggested;
    const normalized = normalizeBooking(selected);
    if (info) {
      const first = bookingOptionLabel(normalized);
      const second = [normalized.customerEmail, normalized.bookingStatus, normalized.paymentStatus].filter(Boolean).join(' · ');
      info.textContent = [first, second].filter(Boolean).join(' | ');
    }
  };
  select.onchange = updateInfo;
  updateInfo();

  if (suggestionInfo) {
    if (selectedAtStart.kind === 'booking') {
      suggestionInfo.textContent = `Beim Spielstart automatisch zugeordnet: ${bookingOptionLabel(selectedAtStart)}. Hier wird nichts neu berechnet; ändere die Auswahl nur, falls sie falsch ist.`;
    } else {
      suggestionInfo.textContent = 'Beim Spielstart konnte keine normale Buchung automatisch zugeordnet werden. Bitte jetzt die richtige Buchung auswählen.';
    }
  }

  if (dialog.open) dialog.close();
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      if (dialog.open) dialog.close();
      window.setTimeout(flushQueuedSnapshot, 0);
      resolve(value);
    };

    cancelButton.onclick = () => finish(null);
    confirmButton.onclick = () => {
      const selected = ordered.find((item) => bookingKey(item) === select.value) || suggested;
      finish(normalizeBooking(selected));
    };
    dialog.oncancel = (event) => {
      event.preventDefault();
      finish(null);
    };
    dialog.onclose = () => {
      if (!settled) finish(null);
    };
    dialog.showModal();
  });
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
  const bookingDetails = document.getElementById('bookingDetails');

  if (phaseValue) phaseValue.textContent = state.game.phase_display || `${state.game.phase}: ${state.game.phase_name_pretty || state.game.phase_name || ''}`;
  if (lastPhaseValue) {
    lastPhaseValue.textContent = state.game.last_phase == null
      ? '—'
      : `${state.game.last_phase}: ${state.game.last_phase_name_pretty || state.game.last_phase_name || ''}`.trim();
  }
  if (timerValue) timerValue.textContent = fmtTime(readLocalTimer());
  if (riddleTimerValue) riddleTimerValue.textContent = fmtTime(readLocalRiddleTimer());

  const phase = Number(state.game.phase || 0);
  const preparedRunReady = phase === 2 && Boolean(currentRunId());
  prepareCounterWrap?.classList.toggle('hidden', phase !== 2);
  startButton?.classList.toggle('hidden', phase !== 2);
  if (startButton) startButton.disabled = !preparedRunReady || bookingBusy || startInFlight || Boolean(state.start_assignment?.active);

  const live = isLiveGame();
  if (ui.lastPhase !== phase) {
    if (bookingDetails) bookingDetails.open = !live;
    if (live) {
      ui.allRiddlesOpen = false;
      ui.emergencyOpen = false;
    }
    ui.lastPhase = phase;
  }

  document.querySelectorAll('[data-phase-action="start"]').forEach((button) => {
    button.disabled = !preparedRunReady || startInFlight || Boolean(state.start_assignment?.active);
  });

  renderBookingControls();
  renderSummaryControls();
  renderStartAssignmentStatus();
  renderPanelVisibility();
}

function renderPanelVisibility() {
  const live = isLiveGame();
  const currentPanel = document.getElementById('currentRiddlesPanel');
  const allPanel = document.getElementById('allRiddlesPanel');
  const emergencyPanel = document.getElementById('emergencyPanel');
  const allButton = document.getElementById('toggleAllRiddlesBtn');
  const emergencyButton = document.getElementById('toggleEmergencyBtn');

  currentPanel?.classList.toggle('hidden', !live);
  allPanel?.classList.toggle('hidden', live && !ui.allRiddlesOpen);
  emergencyPanel?.classList.toggle('hidden', !ui.emergencyOpen);

  if (allButton) {
    allButton.classList.toggle('hidden', !live);
    allButton.textContent = ui.allRiddlesOpen ? 'Alle Rätsel ausblenden' : 'Alle Rätsel anzeigen';
  }
  if (emergencyButton) {
    emergencyButton.textContent = ui.emergencyOpen ? 'Notfallsteuerung ausblenden' : 'Notfallsteuerung anzeigen';
  }
}

function renderNodes() {
  const wrap = document.getElementById('nodesList');
  if (!wrap) return;
  wrap.innerHTML = '';
  for (const item of state.nodes || []) {
    const line = document.createElement('div');
    line.className = `node-line ${item.online ? 'node-on' : 'node-off'}`;
    line.textContent = `${item.label} (${item.status})`;
    wrap.appendChild(line);
  }
}

async function confirmManualControl(title, message) {
  if (!isLiveGame()) return true;
  return confirmAction({
    title,
    message: `${message} Das Spiel läuft gerade; dieser Eingriff kann den normalen Ablauf verändern.`,
    confirmLabel: 'Manuell ausführen',
    danger: true,
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
    card.innerHTML = `
      <button class="control-button ${open ? 'is-open' : 'is-closed'}" type="button">
        ${escapeHtml(lock.label)}
        <span class="state-line">${escapeHtml(String(lock.state_label || 'unbekannt').toUpperCase())}</span>
      </button>
    `;
    const button = card.querySelector('button');
    button.addEventListener('click', async () => {
      const confirmed = await confirmManualControl(
        'Schloss manuell betätigen?',
        `${lock.label} wird manuell ${actionLabel}.`,
      );
      if (!confirmed) return;
      await runAction(button, () => api('/api/lock', {
        method: 'POST',
        body: JSON.stringify({ lock: lock.id, action }),
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
          body: JSON.stringify({ group: light.id, action, pct }),
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
          body: JSON.stringify({ group: light.id, action: 'set_pct', pct }),
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
          body: JSON.stringify({ group: light.id, action }),
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
        <span>${escapeHtml(slot.slot)}: <span class="inline-status ${slot.correct ? 'inline-status-good' : 'inline-status-bad'}">${escapeHtml(slot.value)}</span></span>
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
  if (riddle.id === 'knocking' || riddle.id === 'candles') return renderAttemptsSummary(riddle.attempts_summary);
  if (riddle.id === 'stars' || riddle.id === 'star_slider') return renderStarSliderSummary(riddle.star_slider_summary);
  return riddle.info ? `<span>${escapeHtml(riddle.info)}</span>` : '<span class="muted-info">Keine zusätzlichen Live-Daten.</span>';
}

function riddleTimeText(riddle) {
  if (riddleTimeEditing[riddle.id] || riddleTimeDrafts[riddle.id] != null) {
    return String(riddleTimeDrafts[riddle.id] ?? '');
  }
  return fmtTime(riddle.display_time_s ?? riddle.time_s ?? riddle.live_time_s ?? riddle.solve_time_s ?? 0);
}

function hintTemplatesFor(riddleId, language = state.booking?.language) {
  const selectedLanguage = normalizeHintLanguage(language);
  const templates = HINT_TEMPLATES[String(riddleId || '')];
  return templates?.[selectedLanguage] || [];
}

function hintPanelHtml(riddle) {
  const language = normalizeHintLanguage(state.booking?.language);
  const templates = hintTemplatesFor(riddle.id, language);
  const count = Math.max(0, safeInt(riddle.hint_count, 0));
  const templateContent = templates.length
    ? `<ol class="hint-template-list">${templates.map((text) => `<li>${escapeHtml(text)}</li>`).join('')}</ol>`
    : `<p class="hint-template-empty">${escapeHtml(NO_HINT_TEMPLATE_TEXT[language])}</p>`;
  return `
    <section class="hint-panel">
      <div class="hint-template-heading">Tippvorlagen <span>· ${escapeHtml(HINT_LANGUAGE_LABELS[language])}</span></div>
      ${templateContent}
      <div class="hint-given-block">
        <div class="hint-given-label">Gegebene Tipps: <strong class="hint-counter-value">${count}</strong></div>
        <div class="hint-counter-buttons">
          <button class="hint-counter-btn" type="button" data-delta="1">Tipp gegeben</button>
          <button class="hint-counter-btn" type="button" data-delta="-1" ${count === 0 ? 'disabled' : ''}>Letzten zurücknehmen</button>
        </div>
      </div>
    </section>
  `;
}

function riddleActionsHtml(riddle, compact = false) {
  const stateName = String(riddle.phase_state || 'pending');
  const canSolve = Boolean(riddle.can_solve);
  const showSkip = riddle.id !== 'sissi';
  const canToggleSkip = showSkip && stateName !== 'pending';
  const skipLabel = riddle.skipped ? 'Überspringen aufheben' : 'Überspringen';
  const solveLabel = riddle.manual
    ? (stateName === 'reset' ? 'Erneut gelöst' : 'Gelöst')
    : (stateName === 'reset' ? 'Erneut gelöst' : 'Manuell lösen');
  const resettable = Boolean(riddle.resettable || RESETTABLE_RIDDLES.has(riddle.id));
  const canReset = resettable && ['solved', 'skipped', 'not_solved'].includes(stateName);

  return `
    <div class="riddle-actions ${compact ? 'riddle-actions-card' : ''}">
      <button class="solve-btn ${canSolve ? 'active' : 'inactive'}" type="button" ${canSolve ? '' : 'disabled'}>${solveLabel}</button>
      ${showSkip ? `<button class="skip-btn ${riddle.skipped ? 'is-toggled' : ''}" type="button" ${canToggleSkip ? '' : 'disabled'}>${skipLabel}</button>` : ''}
      ${resettable ? `<button class="reset-riddle-btn" type="button" ${canReset ? '' : 'disabled'}>Zurücksetzen</button>` : ''}
    </div>
  `;
}

async function solveRiddle(riddle, button) {
  if (!riddle.can_solve) return;
  if (!riddle.manual && riddle.solve_advances) {
    const confirmed = await confirmAction({
      title: 'Elektronisches Rätsel manuell lösen?',
      message: `${riddle.label} schaltet normalerweise automatisch weiter. Nur bestätigen, wenn die Elektronik übergangen werden soll.`,
      confirmLabel: 'Manuell lösen',
      danger: true,
    });
    if (!confirmed) return;
  }

  await runAction(button, () => {
    if (riddle.solve_advances) {
      return api('/api/solve', {
        method: 'POST',
        body: JSON.stringify({ node: riddle.id }),
      });
    }
    return api('/api/riddle-outcome', {
      method: 'POST',
      body: JSON.stringify({ riddle: riddle.id, outcome: 'solved', advance: false }),
    });
  });
}

async function toggleSkip(riddle, button) {
  const stateName = String(riddle.phase_state || 'pending');
  if (stateName === 'pending') return;
  const body = riddle.skipped
    ? { riddle: riddle.id, outcome: 'solved', advance: false }
    : { riddle: riddle.id, outcome: 'skipped', advance: Boolean(riddle.solve_advances) };
  await runAction(button, () => api('/api/riddle-outcome', {
    method: 'POST',
    body: JSON.stringify(body),
  }));
}

async function resetRiddle(riddle, button) {
  const confirmed = await confirmAction({
    title: `${riddle.label} zurücksetzen?`,
    message: 'Der Rätselstatus und die Rätselzeit werden zurückgesetzt. Die ursprüngliche Startzeit bleibt erhalten. Phase, Licht und bereits geöffnete Mechanik werden nicht zurückgeschaltet.',
    confirmLabel: 'Rätsel zurücksetzen',
    danger: true,
  });
  if (!confirmed) return;
  await runAction(button, () => api('/api/riddle-outcome', {
    method: 'POST',
    body: JSON.stringify({ riddle: riddle.id, outcome: 'reset' }),
  }));
}

async function saveRiddleTime(riddle, input, button) {
  if (String(riddle.phase_state || 'pending') === 'pending') {
    showFeedback('Die Zeit eines noch nicht erreichten Rätsels kann nicht geändert werden.', 'warn');
    return;
  }
  const value = input.value.trim();
  const confirmed = await confirmAction({
    title: `Zeit für ${riddle.label} ändern?`,
    message: `Die gespeicherte Rätselzeit wird auf „${value || '0'}“ gesetzt. Dies ist eine manuelle Korrektur.`,
    confirmLabel: 'Zeit ändern',
    danger: true,
  });
  if (!confirmed) return;

  await runAction(button, async () => {
    await api('/api/riddle-time', {
      method: 'POST',
      body: JSON.stringify({ riddle: riddle.id, time_text: value }),
    });
    delete riddleTimeDrafts[riddle.id];
    riddleTimeEditing[riddle.id] = false;
  });
}

async function changeHint(riddle, delta, button) {
  await runAction(button, () => api('/api/hints', {
    method: 'POST',
    body: JSON.stringify({ riddle: riddle.id, delta }),
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

  const resetButton = container.querySelector('.reset-riddle-btn');
  if (resetButton && !resetButton.disabled) {
    resetButton.addEventListener('click', () => resetRiddle(riddle, resetButton).catch(() => {}));
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
  const timeEditable = String(riddle.phase_state || 'pending') !== 'pending';
  row.dataset.riddleId = riddle.id;
  row.className = riddle.phase_state === 'active' ? 'current-riddle-row' : '';
  row.innerHTML = `
    <td>${escapeHtml(riddle.label)}</td>
    <td><span class="status-badge ${escapeAttr(riddle.phase_state_class || '')}">${escapeHtml(riddle.phase_state_label || riddle.phase_state)}</span></td>
    <td>
      <div class="riddle-time-edit ${timeEditable ? '' : 'riddle-time-edit-disabled'}" ${timeEditable ? '' : 'title="Zeit erst ab Aktivierung änderbar"'}>
        <input class="riddle-time-input" type="text" inputmode="numeric" value="${escapeAttr(riddleTimeText(riddle))}" placeholder="00:00:00" ${timeEditable ? '' : 'disabled'} />
        <button class="riddle-time-save" type="button" ${timeEditable ? '' : 'disabled'}>Speichern</button>
      </div>
    </td>
    <td>${riddleActionsHtml(riddle)}</td>
    <td><div class="riddle-info-cell">${renderRiddleInfo(riddle)}</div></td>
    <td>${hintPanelHtml(riddle)}</td>
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
        <div class="eyebrow">${riddle.manual ? 'Manuelles Rätsel' : 'Elektronisches Rätsel'}</div>
        <h2>${escapeHtml(riddle.label)}</h2>
      </div>
      <div class="current-card-status">
        <span class="status-badge ${escapeAttr(riddle.phase_state_class || '')}">${escapeHtml(riddle.phase_state_label || riddle.phase_state)}</span>
        <span class="current-card-time">${fmtTime(riddle.display_time_s || 0)}</span>
      </div>
    </div>
    <div class="current-riddle-info">${renderRiddleInfo(riddle)}</div>
    ${!riddle.manual ? '<div class="electronic-note">Dieses Rätsel schaltet bei korrekter Lösung automatisch weiter.</div>' : ''}
    <div class="current-riddle-controls">${riddleActionsHtml(riddle, true)}</div>
    ${hintPanelHtml(riddle)}
  `;
  bindRiddleActions(card, riddle, { includeTimeEditor: false });
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
  const current = (state.riddles || []).filter((riddle) => (
    riddle.phase_state === 'active' || (riddle.phase_state === 'reset' && riddle.solve_advances)
  ));
  if (!current.length) {
    wrap.innerHTML = '<div class="viewer-message">Für diese Phase ist derzeit kein aktives Rätsel gemeldet.</div>';
    return;
  }
  for (const riddle of current) wrap.appendChild(buildCurrentRiddleCard(riddle));
}

function updateRiddleTimeDisplays() {
  for (const riddle of state.riddles || []) {
    const seconds = riddle.display_time_s ?? riddle.time_s ?? riddle.live_time_s ?? riddle.solve_time_s ?? 0;
    const row = document.querySelector(`#riddlesBody tr[data-riddle-id="${CSS.escape(riddle.id)}"]`);
    const input = row?.querySelector('.riddle-time-input');
    if (input && !riddleTimeEditing[riddle.id] && riddleTimeDrafts[riddle.id] == null) {
      input.value = fmtTime(seconds);
    }
    const card = document.querySelector(`#currentRiddlesGrid .current-riddle-card[data-riddle-id="${CSS.escape(riddle.id)}"]`);
    const cardTime = card?.querySelector('.current-card-time');
    if (cardTime) cardTime.textContent = fmtTime(seconds);
  }
}

function patchState(data) {
  const initialRender = !lastSnapshot;
  const rerenderTop = sectionChanged(data, 'game') || sectionChanged(data, 'booking') || sectionChanged(data, 'start_assignment') || sectionChanged(data, 'meta');
  const rerenderNodes = sectionChanged(data, 'nodes');
  const rerenderLocks = sectionChanged(data, 'locks');
  const rerenderLights = sectionChanged(data, 'lights');
  const riddlesChanged = sectionChanged(data, 'riddles');
  const riddlesStructurallyChanged = riddleStructureChanged(data);
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

  startInFlight = true;
  renderTop();
  try {
    const result = await runAction(button, async () => {
      const response = await api('/api/phase', {
        method: 'POST',
        body: JSON.stringify({ action: 'start', start_clicked_at_ms: startClickedAtMs }),
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
      message: 'Das Spiel wurde bereits gestartet oder beendet. Ein Phasenwechsel kann Timer, Licht, Schlösser und den aktuellen Spielstand verändern.',
      confirmLabel: 'Phase ändern',
      danger: true,
    });
    if (!confirmed) return;
  }

  startInFlight = false;
  await runAction(button, () => api('/api/phase', {
    method: 'POST',
    body: JSON.stringify({ action }),
  })).catch(() => {});
}

async function sendSummaryEmail(button) {
  summaryEmailBusy = true;
  renderSummaryControls();
  setSummaryFeedback('Buchung wird kontrolliert…');
  try {
    await runAction(button, async () => {
      await loadBookings({ silent: true });
      const selected = await openSummaryBookingConfirmation();
      if (!selected) {
        setSummaryFeedback('Senden abgebrochen.', 'warn');
        return;
      }
      const saved = await saveSelectedBooking(selected);
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
    setSummaryFeedback(error.message || 'Spielzusammenfassung konnte nicht gesendet werden.', 'error');
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

  const allButton = document.getElementById('toggleAllRiddlesBtn');
  allButton?.addEventListener('click', () => {
    ui.allRiddlesOpen = !ui.allRiddlesOpen;
    renderPanelVisibility();
  });

  const emergencyButton = document.getElementById('toggleEmergencyBtn');
  emergencyButton?.addEventListener('click', () => {
    ui.emergencyOpen = !ui.emergencyOpen;
    renderPanelVisibility();
  });

  const bookingSelect = document.getElementById('bookingSelect');
  const languageSelect = document.getElementById('hintLanguageSelect');
  const refreshBookingsButton = document.getElementById('refreshBookingsBtn');
  const emailInput = document.getElementById('testBookingEmail');
  const playersInput = document.getElementById('testBookingPlayers');

  bookingSelect?.addEventListener('change', async () => {
    const selected = bookingOptions.find((item) => bookingKey(item) === bookingSelect.value);
    if (!selected) return;
    const normalized = normalizeBooking(selected);
    const booking = normalized.kind === 'test' ? selectedBookingDraft(normalized) : normalized;
    try {
      await saveSelectedBooking(booking);
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

  const saveBookingDraft = async () => {
    if (bookingBusy) return;
    try {
      await saveSelectedBooking(selectedBookingDraft(state.booking));
    } catch (error) {
      setBookingFeedback(error.message || 'Buchung konnte nicht gespeichert werden.', 'error');
    }
  };

  const queueBookingDraftSave = () => {
    if (bookingDraftSaveTimer) window.clearTimeout(bookingDraftSaveTimer);
    bookingDraftSaveTimer = window.setTimeout(saveBookingDraft, 500);
  };

  emailInput?.addEventListener('input', queueBookingDraftSave);
  emailInput?.addEventListener('blur', saveBookingDraft);
  emailInput?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    saveBookingDraft();
  });

  playersInput?.addEventListener('input', () => {
    playersInput.value = playersInput.value.replace(/[^0-9]/g, '');
    queueBookingDraftSave();
  });
  playersInput?.addEventListener('blur', saveBookingDraft);
  playersInput?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    saveBookingDraft();
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
  document.getElementById('diagnosticsClearBtn')?.addEventListener('click', () => {
    stopDiagnosticsPolling();
    diagnostics.after = Math.max(diagnostics.after, diagnostics.newestSeq || 0);
    diagnostics.entries = [];
    replaceDiagnosticsLogs();
    setDiagnosticsStatus('Lokale Anzeige geleert. Auf den Geräten und im Serverpuffer wurde nichts gelöscht.');
    startDiagnosticsPolling();
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
  if (timerValue) timerValue.textContent = fmtTime(readLocalTimer());
  if (riddleTimerValue) riddleTimerValue.textContent = fmtTime(readLocalRiddleTimer());
  if (prepareCounter && Number(state.game.phase || 0) === 2) {
    prepareCounter.textContent = String(Math.floor(Date.now() / 1000) % 11);
  }
}

installInteractionGuard();
wireTopControls();
wireDiagnostics();
window.setInterval(updateFastTimers, 200);
loadBookings().catch(() => {});
pollLoop();
