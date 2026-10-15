import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../web/er1_dashboard/static/app.js', import.meta.url), 'utf8');
const urlPrefixSource = fs.readFileSync(new URL('../web/er1_dashboard/static/url-prefix.js', import.meta.url), 'utf8');
const clientSource = source.slice(
  source.indexOf('function delay('),
  source.indexOf('\nfunction escapeHtml('),
);
const diagnosticsPreferenceSource = source.slice(
  source.indexOf('function readDiagnosticsAutoScrollPreference('),
  source.indexOf('\nfunction delay('),
);
const diagnosticsReplaceSource = source.slice(
  source.indexOf('function replaceDiagnosticsLogs('),
  source.indexOf('\nfunction appendDiagnosticsLogs('),
);
const renderLightsSource = source.slice(
  source.indexOf('function renderLights('),
  source.indexOf('\nfunction renderImagesButtons('),
);
const riddleActionsSource = source.slice(
  source.indexOf('function riddleActionsHtml('),
  source.indexOf('\nfunction riddleTimeEditorHtml('),
);
const hintPanelSource = source.slice(
  source.indexOf('function hintPanelHtml('),
  source.indexOf('\nfunction formatHintOfferTime('),
);
const timerFormatSource = source.slice(
  source.indexOf('function fmtTime('),
  source.indexOf('\nfunction fmtEditableTime('),
);
const timerSource = source.slice(
  source.indexOf('function syncLocalTimers('),
  source.indexOf('\nfunction normalizeHintLanguage('),
);
const fingerprintSource = source.slice(
  source.indexOf('function riddleRenderFingerprint('),
  source.indexOf('\nfunction anyRiddleTimeEditing('),
);
const rankingUpdateSource = source.slice(
  source.indexOf('function updateRankingDisplays('),
  source.indexOf('\nasync function refreshRankingPreview('),
);
const starSummarySource = source.slice(
  source.indexOf('function renderStarSliderSummary('),
  source.indexOf('\nfunction renderCandlesSummary('),
);

function response(status, data) {
  return {
    status,
    ok: status >= 200 && status < 300,
    text: async () => JSON.stringify(data),
  };
}

function createStorage(initial = []) {
  const values = new Map();
  if (initial.length) values.set('er1-dashboard-pending-operations-v1', JSON.stringify(initial));
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

function createClient(fetchHandler, { initial = [], randomUUID, urlPrefix = '' } = {}) {
  const storage = createStorage(initial);
  const crypto = {
    getRandomValues(bytes) {
      for (let index = 0; index < bytes.length; index += 1) bytes[index] = index + 1;
      return bytes;
    },
  };
  if (randomUUID) crypto.randomUUID = randomUUID;
  const document = { body: { dataset: { urlPrefix } } };
  const context = vm.createContext({
    AbortController,
    Date,
    document,
    Error,
    JSON,
    Math,
    Uint8Array,
    fetch: fetchHandler,
    window: {
      clearTimeout,
      crypto,
      sessionStorage: storage,
      setTimeout,
    },
  });
  vm.runInContext(urlPrefixSource, context);
  vm.runInContext(`
    const MUTATION_ATTEMPT_TIMEOUT_MS = 1500;
    const MUTATION_RESOLUTION_TIMEOUT_MS = 10000;
    const OPERATION_STATUS_TIMEOUT_MS = 1000;
    const PENDING_OPERATIONS_KEY = 'er1-dashboard-pending-operations-v1';
    let mutationQueue = Promise.resolve();
    async function confirmAction() { return true; }
    ${clientSource}
    globalThis.operationClient = {
      api,
      executeIdempotentMutation,
      reconcileStoredOperations,
      storedPendingOperations,
    };
  `, context);
  return { client: context.operationClient, storage };
}

test('URL prefix helper keeps direct paths and avoids supported double prefixes', () => {
  const document = { body: { dataset: { urlPrefix: '' } } };
  const context = vm.createContext({ document, window: {} });
  vm.runInContext(urlPrefixSource, context);

  assert.equal(context.window.ER1Urls.withPrefix('/api/state?poll=1'), '/api/state?poll=1');
  document.body.dataset.urlPrefix = '/admin/game-master/';
  assert.equal(context.window.ER1Urls.currentPrefix(), '/admin/game-master');
  assert.equal(
    context.window.ER1Urls.withPrefix('/api/state?poll=1'),
    '/admin/game-master/api/state?poll=1',
  );
  assert.equal(
    context.window.ER1Urls.withPrefix('/admin/game-master/api/state?poll=1'),
    '/admin/game-master/api/state?poll=1',
  );
  assert.equal(
    context.window.ER1Urls.withPrefix('/admin/game-master/proxy/api/state?poll=1'),
    '/admin/game-master/proxy/api/state?poll=1',
  );
  assert.equal(
    context.window.ER1Urls.canonicalApiPath('/admin/game-master/proxy/api/hints'),
    '/api/hints',
  );
  assert.equal(
    context.window.ER1Urls.canonicalApiPath('/admin/game-master/api/hints'),
    '/api/hints',
  );
  document.body.dataset.urlPrefix = '/admin/game-master/proxy';
  assert.equal(
    context.window.ER1Urls.withPrefix('/api/state'),
    '/admin/game-master/proxy/api/state',
  );
  assert.equal(
    context.window.ER1Urls.withPrefix('/admin/game-master/proxy/api/state'),
    '/admin/game-master/proxy/api/state',
  );
});

test('a lost mutation response is recovered from its durable receipt', async () => {
  let postCount = 0;
  const { client } = createClient(async (path, options = {}) => {
    if (options.method === 'PUT') {
      return response(201, { ok: true, operation_status: 'prepared' });
    }
    if (options.method === 'POST') {
      postCount += 1;
      throw new TypeError('response connection lost');
    }
    if (String(path).startsWith('/api/operations/')) {
      return response(200, {
        ok: true,
        operation_status: 'completed',
        http_status: 200,
        result: { ok: true, hint_count: 2 },
      });
    }
    throw new Error(`unexpected request ${options.method || 'GET'} ${path}`);
  }, { randomUUID: () => '57a969ff-7c14-4f3d-9faa-b9b5c6307df3' });

  const result = await client.executeIdempotentMutation('/api/hints', {
    method: 'POST',
    body: JSON.stringify({ riddle: 'images', delta: 1 }),
  });

  assert.equal(result.hint_count, 2);
  assert.equal(postCount, 1);
  assert.deepEqual(client.storedPendingOperations(), []);
});

test('a prepared request is resent only with the same operation ID', async () => {
  const operationIds = [];
  let postCount = 0;
  const { client } = createClient(async (path, options = {}) => {
    if (options.method === 'PUT') return response(201, { ok: true, operation_status: 'prepared' });
    if (options.method === 'POST') {
      postCount += 1;
      operationIds.push(options.headers['Idempotency-Key']);
      if (postCount === 1) throw new TypeError('request outcome unknown');
      return response(200, { ok: true, mqtt_queued: true });
    }
    if (String(path).startsWith('/api/operations/')) {
      return response(202, { ok: true, operation_status: 'prepared' });
    }
    throw new Error(`unexpected request ${options.method || 'GET'} ${path}`);
  }, { randomUUID: () => '57a969ff-7c14-4f3d-9faa-b9b5c6307df3' });

  const result = await client.executeIdempotentMutation('/api/light', {
    method: 'POST',
    body: JSON.stringify({ node: 'all', action: 'on' }),
  });

  assert.equal(result.mqtt_queued, true);
  assert.equal(postCount, 2);
  assert.deepEqual(operationIds, [
    '57a969ff-7c14-4f3d-9faa-b9b5c6307df3',
    '57a969ff-7c14-4f3d-9faa-b9b5c6307df3',
  ]);
});

test('UUID generation works when randomUUID is unavailable on plain HTTP', async () => {
  let preparedId = '';
  const { client } = createClient(async (path, options = {}) => {
    if (options.method === 'PUT') {
      preparedId = String(path).split('/').at(-1);
      return response(201, { ok: true, operation_status: 'prepared' });
    }
    if (options.method === 'POST') return response(200, { ok: true });
    throw new Error(`unexpected request ${options.method || 'GET'} ${path}`);
  });

  await client.executeIdempotentMutation('/api/log-level', {
    method: 'POST',
    body: JSON.stringify({ node: 'lighting', level: 'WRN' }),
  });

  assert.match(preparedId, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test('proxied requests prepare the canonical Pi path but send through the proxy path', async () => {
  let preparedPath = '';
  let sentPath = '';
  const { client } = createClient(async (path, options = {}) => {
    if (options.method === 'PUT') {
      preparedPath = JSON.parse(options.body).path;
      return response(201, { ok: true, operation_status: 'prepared' });
    }
    if (options.method === 'POST') {
      sentPath = path;
      return response(200, { ok: true });
    }
    throw new Error(`unexpected request ${options.method || 'GET'} ${path}`);
  }, { randomUUID: () => '57a969ff-7c14-4f3d-9faa-b9b5c6307df3' });

  await client.executeIdempotentMutation('/admin/game-master/proxy/api/hints', {
    method: 'POST',
    body: '{}',
  });

  assert.equal(preparedPath, '/api/hints');
  assert.equal(sentPath, '/admin/game-master/proxy/api/hints');
});

test('forwarded clean prefix applies to operation preparation, status and mutation URLs', async () => {
  const requests = [];
  const { client } = createClient(async (path, options = {}) => {
    requests.push([String(path), options.method || 'GET', options.body || '']);
    if (options.method === 'PUT') return response(201, { ok: true, operation_status: 'prepared' });
    if (options.method === 'POST') return response(200, { ok: true });
    throw new Error(`unexpected request ${options.method || 'GET'} ${path}`);
  }, {
    randomUUID: () => '57a969ff-7c14-4f3d-9faa-b9b5c6307df3',
    urlPrefix: '/admin/game-master',
  });

  await client.executeIdempotentMutation('/api/hints', { method: 'POST', body: '{}' });

  assert.equal(requests[0][0], '/admin/game-master/api/operations/57a969ff-7c14-4f3d-9faa-b9b5c6307df3');
  assert.equal(JSON.parse(requests[0][2]).path, '/api/hints');
  assert.equal(requests[1][0], '/admin/game-master/api/hints');
});

test('a definitive preparation rejection does not poison later mutations', async () => {
  let mutationCount = 0;
  const { client } = createClient(async (_path, options = {}) => {
    if (options.method === 'PUT') {
      return response(400, { ok: false, operation_status: 'invalid', error: 'invalid preparation' });
    }
    mutationCount += 1;
    throw new Error('mutation must not be sent');
  }, { randomUUID: () => '57a969ff-7c14-4f3d-9faa-b9b5c6307df3' });

  await assert.rejects(
    client.executeIdempotentMutation('/api/hints', { method: 'POST', body: '{}' }),
    /invalid preparation/,
  );
  assert.equal(mutationCount, 0);
  assert.deepEqual(client.storedPendingOperations(), []);
});

test('a recovered completion is surfaced and blocks the current click', async () => {
  const pending = {
    id: '57a969ff-7c14-4f3d-9faa-b9b5c6307df3',
    path: '/api/send-summary-email',
    method: 'POST',
    body: '{}',
    createdAt: Date.now(),
  };
  let mutationCount = 0;
  const requests = [];
  const { client } = createClient(async (path, options = {}) => {
    requests.push([String(path), options.method || 'GET', options.body || '']);
    if (options.method === 'PUT') {
      assert.deepEqual(JSON.parse(options.body), {
        method: pending.method,
        path: pending.path,
        body: pending.body,
      });
      return response(200, { ok: true, operation_status: 'completed' });
    }
    if (!options.method || options.method === 'GET') {
      return response(200, {
        ok: true,
        operation_status: 'completed',
        http_status: 200,
        result: { ok: true, email: { sent: true } },
      });
    }
    mutationCount += 1;
    throw new Error(`unexpected mutation ${options.method} ${path}`);
  }, { initial: [pending] });

  await assert.rejects(
    client.api('/api/light', { method: 'POST', body: '{}' }),
    (error) => error.data?.operation_status === 'recovered' && /send-summary-email/.test(error.message),
  );
  assert.equal(mutationCount, 0);
  assert.deepEqual(requests.map((request) => request.slice(0, 2)), [
    ['/api/operations/57a969ff-7c14-4f3d-9faa-b9b5c6307df3', 'PUT'],
    ['/api/operations/57a969ff-7c14-4f3d-9faa-b9b5c6307df3', 'GET'],
  ]);
  assert.deepEqual(client.storedPendingOperations(), []);
});

test('a pre-upgrade pending operation is prepared before status and resumed only interactively', async () => {
  const pending = {
    id: '57a969ff-7c14-4f3d-9faa-b9b5c6307df3',
    path: '/api/lock',
    method: 'POST',
    body: '{}',
    createdAt: Date.now(),
  };
  const requests = [];
  const { client } = createClient(async (path, options = {}) => {
    requests.push([String(path), options.method || 'GET', options.body || '']);
    if (options.method === 'PUT') {
      assert.deepEqual(JSON.parse(options.body), {
        method: pending.method,
        path: pending.path,
        body: pending.body,
      });
      return response(201, { ok: true, operation_status: 'prepared' });
    }
    if (options.method === 'POST') {
      assert.equal(options.headers['Idempotency-Key'], pending.id);
      return response(200, { ok: true, locked: true });
    }
    return response(202, { ok: true, operation_status: 'prepared' });
  }, { initial: [pending] });

  await assert.rejects(
    client.reconcileStoredOperations(false),
    (error) => error.data?.operation_status === 'uncertain',
  );
  assert.equal(client.storedPendingOperations().length, 1);
  assert.deepEqual(requests.map((request) => request[1]), ['PUT', 'GET']);
  await assert.rejects(
    client.reconcileStoredOperations(true),
    (error) => error.data?.operation_status === 'recovered',
  );
  assert.equal(requests.filter((request) => request[1] === 'POST').length, 1);
  assert.deepEqual(client.storedPendingOperations(), []);
});

test('recovery preparation collisions and errors remain pending and never send the mutation', async () => {
  const pending = {
    id: '57a969ff-7c14-4f3d-9faa-b9b5c6307df3',
    path: '/api/hints',
    method: 'POST',
    body: '{"riddle":"images","delta":1}',
    createdAt: Date.now(),
  };
  const scenarios = [
    [409, { ok: false, operation_status: 'conflict', error: 'operation collision' }],
    [503, { ok: false, operation_status: 'unavailable', error: 'preparation unavailable' }],
  ];

  for (const [status, data] of scenarios) {
    const methods = [];
    const { client } = createClient(async (_path, options = {}) => {
      const method = options.method || 'GET';
      methods.push(method);
      if (method === 'POST') throw new Error('mutation must not be sent');
      return response(status, data);
    }, { initial: [pending] });

    await assert.rejects(client.reconcileStoredOperations(true));
    assert.equal(methods[0], 'PUT');
    assert.equal(methods.includes('POST'), false);
    assert.equal(client.storedPendingOperations().length, 1);
  }
});

test('diagnostics auto-scroll defaults on, synchronizes controls, and persists explicit choices', () => {
  assert.match(source, /window\.addEventListener\('storage', handleDiagnosticsAutoScrollStorage\)/);
  const values = new Map();
  const controls = [{ checked: false }, { checked: false }];
  const context = vm.createContext({
    document: {
      querySelectorAll(selector) {
        assert.equal(selector, '.diagnostics-autoscroll');
        return controls;
      },
    },
    window: {
      localStorage: {
        getItem: (key) => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, String(value)),
      },
    },
  });
  vm.runInContext(`
    const DIAGNOSTICS_AUTOSCROLL_KEY = 'er1-dashboard-diagnostics-autoscroll-v1';
    const diagnostics = { autoScroll: true };
    let replacements = 0;
    function replaceDiagnosticsLogs() { replacements += 1; }
    ${diagnosticsPreferenceSource}
    globalThis.preferences = {
      diagnostics,
      read: readDiagnosticsAutoScrollPreference,
      set: setDiagnosticsAutoScroll,
      handleStorage: handleDiagnosticsAutoScrollStorage,
      sync: syncDiagnosticsAutoScrollControls,
      replacements: () => replacements,
    };
  `, context);

  assert.equal(context.preferences.read(), true);
  context.preferences.sync();
  assert.deepEqual(controls.map((control) => control.checked), [true, true]);

  context.preferences.set(false);
  assert.deepEqual(controls.map((control) => control.checked), [false, false]);
  assert.equal(values.get('er1-dashboard-diagnostics-autoscroll-v1'), 'false');
  assert.equal(context.preferences.replacements(), 0);

  context.preferences.set(true);
  assert.deepEqual(controls.map((control) => control.checked), [true, true]);
  assert.equal(values.get('er1-dashboard-diagnostics-autoscroll-v1'), 'true');
  assert.equal(context.preferences.replacements(), 1);

  context.preferences.handleStorage({
    key: 'er1-dashboard-diagnostics-autoscroll-v1',
    newValue: 'false',
  });
  assert.equal(context.preferences.diagnostics.autoScroll, false);
  assert.deepEqual(controls.map((control) => control.checked), [false, false]);
  assert.equal(context.preferences.replacements(), 1);

  context.preferences.handleStorage({
    key: 'er1-dashboard-diagnostics-autoscroll-v1',
    newValue: 'true',
  });
  assert.equal(context.preferences.diagnostics.autoScroll, true);
  assert.deepEqual(controls.map((control) => control.checked), [true, true]);
  assert.equal(context.preferences.replacements(), 2);

  context.preferences.handleStorage({ key: 'unrelated', newValue: 'false' });
  assert.equal(context.preferences.diagnostics.autoScroll, true);

  context.window.localStorage = {
    getItem() { throw new Error('blocked'); },
    setItem() { throw new Error('blocked'); },
  };
  assert.equal(context.preferences.read(), true);
  assert.doesNotThrow(() => context.preferences.set(false));
});

test('disabled diagnostics auto-scroll preserves the current scroll position', () => {
  const list = {
    scrollHeight: 120,
    scrollTop: 37,
    replaceChildren() { this.scrollHeight = 240; },
  };
  const context = vm.createContext({
    document: {
      createDocumentFragment: () => ({ appendChild() {} }),
      getElementById: () => list,
    },
  });
  vm.runInContext(`
    const diagnostics = { autoScroll: false };
    function visibleDiagnosticsEntries() { return [{ seq: 1 }]; }
    function createDiagnosticsLogEntry(entry) { return entry; }
    function updateDiagnosticsCount() {}
    ${diagnosticsReplaceSource}
    globalThis.logs = { diagnostics, replaceDiagnosticsLogs };
  `, context);

  context.logs.replaceDiagnosticsLogs();
  assert.equal(list.scrollTop, 37);
  context.logs.diagnostics.autoScroll = true;
  context.logs.replaceDiagnosticsLogs();
  assert.equal(list.scrollTop, 240);
});

test('non-dimmable lights render exact compact labels with accessible full-button controls', () => {
  const cards = [];
  const clickBindings = [];
  const wrap = {
    innerHTML: '',
    appendChild(card) { cards.push(card); },
  };
  const context = vm.createContext({
    document: {
      createElement() {
        const button = {
          addEventListener(type) { clickBindings.push(type); },
        };
        return {
          className: '',
          innerHTML: '',
          querySelector(selector) {
            assert.equal(selector, 'button');
            return button;
          },
        };
      },
      getElementById(id) {
        assert.equal(id, 'lightsGrid');
        return wrap;
      },
    },
  });
  vm.runInContext(`
    const state = { lights: [
      { id: 'star_sky', label: 'backend stars', dimmable: false, pct: 0, on: true, state: 'mixed', state_label: 'teilweise' },
      { id: 'r3_torch', label: 'backend r3', dimmable: false, pct: 0, on: true, state: 'on', state_label: 'an' },
      { id: 'entrance', label: 'backend entrance', dimmable: false, pct: 0, on: false, state: 'unknown', state_label: 'unbekannt' },
      { id: 'r2_torch', label: 'backend r2', dimmable: false, pct: 0, on: false, state: 'off', state_label: 'aus' },
    ] };
    const dimLastNonZero = {};
    function safeInt(value, fallback) { const parsed = Number.parseInt(value, 10); return Number.isFinite(parsed) ? parsed : fallback; }
    function escapeAttr(value) { return String(value); }
    function escapeHtml(value) { return String(value); }
    function iconSvg(name) { return '<svg data-icon="' + name + '"></svg>'; }
    ${renderLightsSource}
    renderLights();
  `, context);

  assert.equal(cards.length, 4);
  assert.equal(clickBindings.length, 4);
  const expected = [
    ['Eingang', 'Unbekannt'],
    ['Raum 2', 'Aus'],
    ['Raum 3', 'An'],
    ['Sterne', 'Teilweise'],
  ];
  for (const [card, [label, lightState]] of cards.map((card, index) => [card, expected[index]])) {
    assert.match(card.innerHTML, new RegExp(`aria-label="${label}: ${lightState}"`));
    assert.match(card.innerHTML, new RegExp(`<strong>${label}</strong><small>${lightState}</small><span class="light-toggle-icon"`));
  }
});

test('current-riddle actions stay grouped beneath their matching content columns', () => {
  const context = vm.createContext({});
  vm.runInContext(`
    const state = { game: { phase: 3 } };
    function currentRunId() { return 'run-1'; }
    function riddleMutationsAvailable() { return Number(state.game.phase || 0) >= 3 && Boolean(currentRunId()); }
    function safeInt(value, fallback) { const parsed = Number.parseInt(value, 10); return Number.isFinite(parsed) ? parsed : fallback; }
    function escapeHtml(value) { return String(value); }
    function iconSvg(name) { return '<svg data-icon="' + name + '"></svg>'; }
    ${riddleActionsSource}
    globalThis.actions = {
      render: riddleActionsHtml,
      state,
    };
  `, context);

  const html = context.actions.render({
    id: 'images',
    label: 'Bilder',
    phase_state: 'active',
    can_solve: true,
    hint_count: 2,
    skipped: false,
  }, true);
  const hintSectionAt = html.indexOf('current-riddle-hint-actions');
  assert.ok(hintSectionAt > 0);
  assert.match(html.slice(0, hintSectionAt), /current-riddle-identity[\s\S]*Bilder[\s\S]*solve-btn[\s\S]*Gelöst/);
  assert.doesNotMatch(html.slice(0, hintSectionAt), /current-hint-control/);
  assert.match(html.slice(hintSectionAt), /current-hint-control[\s\S]*<strong>2<\/strong>[\s\S]*skip-btn[\s\S]*Überspringen/);
  assert.doesNotMatch(html, /is-hint-only/);

  const nonSkippable = context.actions.render({
    id: 'sissi',
    label: 'Sissi',
    phase_state: 'active',
    can_solve: true,
    hint_count: 1,
  }, true);
  assert.match(nonSkippable, /current-riddle-hint-actions is-hint-only/);
  assert.match(nonSkippable, /current-hint-control/);
  assert.doesNotMatch(nonSkippable, /skip-btn|riddle-action-spacer/);

  context.actions.state.game.phase = 2;
  const guarded = context.actions.render({
    id: 'images',
    label: 'Bilder',
    phase_state: 'active',
    can_solve: false,
    hint_count: 0,
  }, true);
  assert.match(guarded, /solve-btn inactive[^>]*disabled/);
  assert.equal((guarded.match(/hint-counter-btn[^>]*disabled/g) || []).length, 2);
  assert.match(guarded, /current-riddle-hint-actions is-hint-only/);
  assert.doesNotMatch(guarded, /skip-btn|riddle-action-spacer/);
});

test('due hint rendering is static across repeated renders', () => {
  const context = vm.createContext({});
  vm.runInContext(`
    const state = { booking: { language: 'de' }, game: { elapsed_s: 120 } };
    const NO_HINT_TEMPLATE_TEXT = { de: 'Keine Tipps' };
    function normalizeHintLanguage(value) { return value; }
    function hintTemplatesFor() {
      return [
        { text: 'Jetzt anzeigen', offer_after_total_s: 60, offer_after_riddle_s: null },
        { text: 'Später anzeigen', offer_after_total_s: 180, offer_after_riddle_s: null },
      ];
    }
    function safeInt(value, fallback) { const parsed = Number.parseInt(value, 10); return Number.isFinite(parsed) ? parsed : fallback; }
    function escapeAttr(value) { return String(value); }
    function escapeHtml(value) { return String(value); }
    function formatHintOfferTime(value) { return value == null ? '--:--' : String(value); }
    function riddleMutationsAvailable() { return true; }
    ${hintPanelSource}
    globalThis.renderHint = hintPanelHtml;
  `, context);

  const riddle = { id: 'images', label: 'Bilder', hint_count: 0, live_time_s: 30 };
  const first = context.renderHint(riddle);
  const second = context.renderHint(riddle);
  assert.equal(first, second);
  assert.match(first, /<li class="hint-card-due">/);
  assert.match(first, /hint-threshold hint-threshold-due/);
  assert.doesNotMatch(first, /newly-due|data-hint-key|animation/i);
});

test('signed countdown bases continue with performance time across polls and page recreation', () => {
  const createTimers = (initialNow = 0) => {
    let now = initialNow;
    const context = vm.createContext({
      performance: { now: () => now },
    });
    vm.runInContext(`
      const state = { game: {} };
      let localTimerBaseElapsed = 0;
      let localTimerSyncedAt = null;
      let localRiddleTimerBaseElapsed = 0;
      let localRiddleTimerSyncedAt = null;
      let lastGameTimerSignature = '';
      let lastRiddleTimerSignature = '';
      ${timerFormatSource}
      ${timerSource}
      globalThis.timers = {
        state,
        sync: syncLocalTimers,
        total: readLocalTimer,
        riddle: readLocalRiddleTimer,
        display: fmtGameTime,
      };
    `, context);
    return { timers: context.timers, setNow: (value) => { now = value; } };
  };

  const page = createTimers();
  const countdown = {
    phase: 3,
    timer_running: true,
    started_at: 'run-1',
    elapsed_s: -5,
    current_riddle_name: 'images',
    current_riddle_elapsed_s: -5,
    last_riddle_solved_at: '',
  };
  page.timers.state.game = countdown;
  page.timers.sync(countdown);
  page.setNow(2000);
  assert.equal(page.timers.total(), -3);
  assert.equal(page.timers.riddle(), -3);
  assert.equal(page.timers.display(page.timers.total()), '00:00');

  const refreshed = createTimers(50);
  const freshState = { ...countdown, elapsed_s: 8, current_riddle_elapsed_s: 4 };
  refreshed.timers.state.game = freshState;
  refreshed.timers.sync(freshState);
  refreshed.setNow(1550);
  assert.equal(refreshed.timers.total(), 9);
  assert.equal(refreshed.timers.riddle(), 5);
});

test('elapsed and heartbeat-only riddle updates keep structure stable while ranking updates in place', () => {
  const context = vm.createContext({ JSON });
  vm.runInContext(`
    function stableStringify(value) { return JSON.stringify(value); }
    ${fingerprintSource}
    globalThis.fingerprint = riddleRenderFingerprint;
  `, context);
  const before = [{ id: 'images', phase_state: 'active', display_time_s: 2, live_time_s: 2, solve_time_s: 0, node_status: 'online (10s)', info: 'ready' }];
  const volatile = [{ ...before[0], display_time_s: 4, live_time_s: 4, solve_time_s: 1, node_status: 'online (12s)' }];
  assert.equal(context.fingerprint(before), context.fingerprint(volatile));
  assert.notEqual(context.fingerprint(before), context.fingerprint([{ ...before[0], info: 'changed' }]));

  const cell = { textContent: '' };
  const rankingContext = vm.createContext({
    CSS: { escape: (value) => value },
    document: { querySelector: () => cell },
  });
  vm.runInContext(`
    const state = { riddles: [{ id: 'images', phase_state: 'active' }] };
    const rankingState = { riddles: new Map([['images', { rank: 3 }]]) };
    ${rankingUpdateSource}
    updateRankingDisplays();
  `, rankingContext);
  assert.equal(cell.textContent, '#3');
});

test('phase changes invalidate current-riddle content when its fingerprint is unchanged', () => {
  assert.match(source, /const riddleViewChanged = riddleViewInvalidated\(data\);[\s\S]*if \(riddleViewChanged \|\| languageChanged\) \{\s*rerenderRiddleViews\(\);/);
  const context = vm.createContext({ JSON });
  vm.runInContext(`
    function stableStringify(value) { return JSON.stringify(value); }
    let lastSnapshot = null;
    ${fingerprintSource}
    globalThis.view = {
      invalidated: riddleViewInvalidated,
      snapshot(value) { lastSnapshot = value; },
    };
  `, context);
  const riddles = [{ id: 'images', phase_state: 'pending', display_time_s: 0 }];
  context.view.snapshot({ game: { phase: 0 }, riddles });
  assert.equal(context.view.invalidated({ game: { phase: 2 }, riddles }), true);
  context.view.snapshot({ game: { phase: 2 }, riddles });
  assert.equal(context.view.invalidated({ game: { phase: 0 }, riddles }), true);
  context.view.snapshot({ game: { phase: 0 }, riddles });
  assert.equal(context.view.invalidated({ game: { phase: 0 }, riddles }), false);
});

test('Stars renders sorted fixed rows and canonical numbered attempts', () => {
  assert.doesNotMatch(source, /riddle\.id === 'star_slider'/);
  const context = vm.createContext({});
  vm.runInContext(`
    const STAR_SIGN_SYMBOLS = { scorpio: 'S', aquarius: 'A', libra: 'L', gemini: 'G', leo: 'O', sagittarius: 'T', pisces: 'P', none: '-' };
    const STAR_SIGN_LABELS = { scorpio: 'Skorpion', aquarius: 'Wassermann', libra: 'Waage', gemini: 'Zwillinge', leo: 'Löwe', sagittarius: 'Schütze', pisces: 'Fische', none: 'Kein Sternzeichen' };
    function escapeAttr(value) { return String(value); }
    function escapeHtml(value) { return String(value); }
    ${starSummarySource}
    globalThis.renderStars = renderStarSliderSummary;
  `, context);
  const html = context.renderStars({
    current: { number: 2, values: ['scorpio', 'aquarius', 'libra'] },
    attempts: [
      { number: 1, values: ['gemini', 'leo', 'pisces'] },
      { number: 2, values: ['scorpio', 'aquarius', 'libra'] },
    ],
  });
  const orderedNames = ['Zwillinge', 'Waage', 'Löwe', 'Schütze', 'Skorpion', 'Wassermann', 'Fische'];
  for (let index = 1; index < orderedNames.length; index += 1) {
    assert.ok(html.indexOf(orderedNames[index - 1]) < html.indexOf(orderedNames[index]));
  }
  assert.match(html, /star-position-grid[\s\S]*Lösung[\s\S]*Aktuell/);
  assert.match(html, /star-sign-attempt"><b>1\.<\/b>/);
  assert.match(html, /star-sign-attempt"><b>2\.<\/b>/);
  assert.equal((html.match(/star-attempt-separator/g) || []).length, 1);
  assert.match(html, /star-attempt-separator" aria-hidden="true">,<\/span>/);
  assert.match(html, /star-attempt-symbol/);
  assert.match(html, /title="Skorpion" aria-label="Skorpion"/);
  assert.match(html, /title="Wassermann" aria-label="Wassermann"/);
  assert.doesNotMatch(html, /title="(?:scorpio|aquarius|libra)"/);
});
