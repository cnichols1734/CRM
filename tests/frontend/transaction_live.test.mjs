import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../frontend/controllers/transaction_live_controller.js', import.meta.url), 'utf8');
const { default: TransactionLive } = await import('data:text/javascript;base64,' + Buffer.from(
  source.replace(/^import .*;$/gm, '').replace('export default class extends Controller', 'export default class')
).toString('base64'));

function fixture(t) {
  let controller;
  const globals = new Map(['window', 'document', 'fetch', 'setTimeout', 'clearTimeout', 'CustomEvent']
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  t.after(() => {
    controller?.disconnect();
    for (const [key, descriptor] of globals) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  globalThis.window = new EventTarget();
  globalThis.document = new EventTarget();
  globalThis.CustomEvent = class extends Event { constructor(name, options) { super(name); this.detail = options?.detail; } };
  const timers = new Map();
  let timer = 0;
  globalThis.setTimeout = fn => { timers.set(++timer, fn); return timer; };
  globalThis.clearTimeout = id => timers.delete(id);
  const requests = [], payloads = [];
  globalThis.fetch = (_url, options) => new Promise(resolve => requests.push({ resolve, signal: options.signal }));
  window.addEventListener('transaction:live', event => payloads.push(event.detail));
  controller = new TransactionLive();
  controller.urlValue = '/transactions/1/live';
  controller.connect();
  return { controller, requests, payloads, timers };
}

const settle = () => new Promise(resolve => setImmediate(resolve));
const response = { ok: true, json: async () => ({ version: 'current', in_flight: false }) };

for (const departure of ['disconnect', 'pagehide']) {
  test(`${departure} cancels polling and prevents a late response from restarting it`, async t => {
    const { controller, requests, payloads, timers } = fixture(t);
    if (departure === 'disconnect') controller.disconnect();
    else window.dispatchEvent(new Event('pagehide'));
    assert.equal(requests[0].signal.aborted, true);
    requests[0].resolve(response);
    await settle();
    assert.deepEqual(payloads, []);
    assert.equal(timers.size, 0);
    assert.equal(controller.errorCount, 0);
  });
}

test('back-forward restoration resumes polling with a fresh request', async t => {
  const { controller, requests, payloads, timers } = fixture(t);
  window.dispatchEvent(new Event('pagehide'));
  requests[0].resolve(response);
  await settle();
  window.dispatchEvent(Object.assign(new Event('pageshow'), { persisted: true }));
  assert.equal(requests.length, 2);
  assert.equal(requests[1].signal.aborted, false);
  requests[1].resolve(response);
  await settle();
  assert.equal(payloads.length, 1);
  assert.equal(timers.size, 1);
  controller.disconnect();
  assert.equal(timers.size, 0);
});
