import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../web/er1_dashboard/static/app.js', import.meta.url), 'utf8');
const clientSource = source.slice(
  source.indexOf('function delay('),
  source.indexOf('\nfunction escapeHtml('),
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

function createClient(fetchHandler, { initial = [], randomUUID } = {}) {
  const storage = createStorage(initial);
  const crypto = {
    getRandomValues(bytes) {
      for (let index = 0; index < bytes.length; index += 1) bytes[index] = index + 1;
      return bytes;
    },
  };
  if (randomUUID) crypto.randomUUID = randomUUID;
  const context = vm.createContext({
    AbortController,
    Date,
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
  const { client } = createClient(async (path, options = {}) => {
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
  assert.deepEqual(client.storedPendingOperations(), []);
});

test('a missing receipt is never replayed and requires explicit inspection before release', async () => {
  const pending = {
    id: '57a969ff-7c14-4f3d-9faa-b9b5c6307df3',
    path: '/api/lock',
    method: 'POST',
    body: '{}',
    createdAt: Date.now(),
  };
  const { client } = createClient(async () => response(404, {
    ok: false,
    operation_status: 'not_found',
  }), { initial: [pending] });

  await assert.rejects(
    client.reconcileStoredOperations(false),
    (error) => error.data?.operation_status === 'uncertain',
  );
  assert.equal(client.storedPendingOperations().length, 1);
  await assert.rejects(
    client.reconcileStoredOperations(true),
    (error) => error.data?.operation_status === 'recovered',
  );
  assert.deepEqual(client.storedPendingOperations(), []);
});
