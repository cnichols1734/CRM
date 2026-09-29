import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../frontend/controllers/client_messages_controller.js', import.meta.url), 'utf8');
const { default: Messages } = await import('data:text/javascript;base64,' + Buffer.from(
  source.replace(/^import .*;$/gm, '').replace('export default class extends Controller', 'export default class')
).toString('base64'));

function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function snapshot(key = 'inquiry-1', extra = {}) {
  return { ok: true, selected_key: key, threads_html: '<a>Conversation</a>',
    conversation_html: '<section>Messages</section>', csrf_token: 'dummy-csrf',
    attention_count: 1, view: 'all', search: '', ...extra };
}

function fixture(t) {
  const globals = new Map(['window', 'document', 'FormData', 'fetch', 'setTimeout', 'clearTimeout']
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  t.after(() => {
    for (const [key, descriptor] of globals) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  const timers = new Map();
  let timerId = 0;
  globalThis.setTimeout = (fn, delay) => { const id = ++timerId; timers.set(id, { fn, delay }); return id; };
  globalThis.clearTimeout = id => timers.delete(id);
  const listeners = new Map();
  const add = (name, fn) => listeners.set(name, fn);
  const remove = name => listeners.delete(name);
  const location = new URL('http://127.0.0.1:5024/messages?thread=inquiry-1');
  const pushed = [];
  globalThis.window = { location, addEventListener: add, removeEventListener: remove,
    history: { pushState(_state, _unused, url) { pushed.push(String(url)); }, replaceState() {} },
    getSelection: () => null };
  globalThis.document = { hidden: false, activeElement: null, addEventListener: add, removeEventListener: remove };
  globalThis.FormData = class {
    constructor(form) { this.values = new Map(Object.entries(form.fields)); }
    has(key) { return this.values.has(key); }
    get(key) { return this.values.get(key) ?? null; }
    set(key, value) { this.values.set(key, value); }
    [Symbol.iterator]() { return this.values[Symbol.iterator](); }
  };
  const c = new Messages();
  Object.assign(c, { alive: true, sequence: 0, failures: 0, drafts: new Map(),
    currentURL: new URL(location), currentKey: 'inquiry-1', hasBodyTarget: true,
    hasErrorTarget: true, errorTarget: { textContent: '', hidden: true },
    hasSyncStatusTarget: true, syncStatusTarget: { textContent: '', dataset: {} },
    bodyTarget: { value: '', style: {}, scrollHeight: 48 },
    element: { querySelectorAll: () => [] },
    conversationTarget: { querySelector: selector => selector.includes('~="body"') ? c.bodyTarget : null,
      replaceChildren() {} },
  });
  const applied = [];
  c.applySnapshot = (data, options = {}) => {
    applied.push({ data, options });
    if (options.changedThread) {
      c.bodyTarget = { value: '', style: {}, scrollHeight: 48 };
      c.restoreDraft();
    }
  };
  const scheduled = [];
  c.schedule = delay => scheduled.push(delay);
  function form(body = c.bodyTarget.value, key = c.currentKey) {
    const button = { disabled: false, attributes: new Map(),
      setAttribute(k, v) { this.attributes.set(k, v); }, removeAttribute(k) { this.attributes.delete(k); } };
    const action = `http://127.0.0.1:5024/messages?thread=${key}`;
    return { action,
      fields: { body, csrf_token: 'dummy-csrf', action: 'reply' }, button, attributes: new Map([['action', action]]),
      getAttribute(k) { return this.attributes.get(k) ?? null; },
      reportValidity: () => true, querySelectorAll: () => [button],
      setAttribute(k, v) { this.attributes.set(k, v); }, removeAttribute(k) { this.attributes.delete(k); } };
  }
  return { c, timers, listeners, applied, scheduled, pushed, form,
    event: form => ({ currentTarget: form, preventDefault() {} }) };
}

function response(data, { status = 200, redirected = false, contentType = 'application/json' } = {}) {
  return { status, redirected, ok: status >= 200 && status < 300,
    headers: { get: () => contentType }, json: async () => data };
}

test('an older overlapping read cannot replace the latest selected conversation', async t => {
  const { c, applied, pushed } = fixture(t);
  const first = deferred(), second = deferred();
  const calls = [];
  c.request = (_url, options) => { calls.push(options); return calls.length === 1 ? first.promise : second.promise; };
  const oldLoad = c.load('/messages?thread=inquiry-2', { navigation: true, push: true });
  const newLoad = c.load('/messages?thread=deal-3', { navigation: true, push: true });
  assert.equal(calls[0].signal.aborted, true);
  second.resolve(snapshot('deal-3'));
  await newLoad;
  first.resolve(snapshot('inquiry-2'));
  await oldLoad;
  assert.equal(c.currentKey, 'deal-3');
  assert.deepEqual(applied.map(item => item.data.selected_key), ['deal-3']);
  assert.equal(pushed.length, 1);
  assert.match(pushed[0], /thread=deal-3/);
});

test('typing while another conversation loads is saved to the conversation being left', async t => {
  const { c } = fixture(t);
  const pending = deferred();
  c.bodyTarget.value = 'Original draft';
  c.request = () => pending.promise;
  const load = c.load('/messages?thread=inquiry-2', { navigation: true });
  c.bodyTarget.value = 'Latest draft typed while loading';
  pending.resolve(snapshot('inquiry-2'));
  await load;
  assert.equal(c.drafts.get('inquiry-1').body, 'Latest draft typed while loading');
  assert.equal(c.bodyTarget.value, '');
  c.request = async () => snapshot('inquiry-1');
  await c.load('/messages?thread=inquiry-1', { navigation: true });
  assert.equal(c.bodyTarget.value, 'Latest draft typed while loading');
});

test('a successful send preserves text typed while sending', async t => {
  const { c, form, event, applied } = fixture(t);
  const pending = deferred();
  c.bodyTarget.value = 'Saturday works.';
  const outgoing = form();
  c.request = () => pending.promise;
  const sending = c.submit(event(outgoing));
  assert.equal(outgoing.button.disabled, true);
  c.bodyTarget.value = 'Also, where should we meet?';
  c.draftChanged();
  const nextRequestId = c.drafts.get(c.currentKey).requestId;
  pending.resolve(snapshot());
  await sending;
  assert.equal(c.bodyTarget.value, 'Also, where should we meet?');
  assert.equal(c.drafts.get(c.currentKey).requestId, nextRequestId);
  assert.equal(outgoing.button.disabled, false);
  assert.equal(applied[0].options.sent, true);
});

test('sending without further edits clears only the confirmed draft', async t => {
  const { c, form, event } = fixture(t);
  c.bodyTarget.value = 'See you Saturday.';
  c.request = async () => snapshot();
  await c.submit(event(form()));
  assert.equal(c.bodyTarget.value, '');
  assert.equal(c.drafts.has('inquiry-1'), false);
  assert.equal(c.syncStatusTarget.dataset.state, 'connected');
});

test('a send that finishes after a thread switch cannot replace the new thread or its draft', async t => {
  const { c, form, event, applied } = fixture(t);
  const post = deferred();
  c.bodyTarget.value = 'Saturday works.';
  c.request = (_url, options = {}) => options.method === 'POST' ? post.promise : Promise.resolve(snapshot('inquiry-2'));
  const sending = c.submit(event(form()));
  await c.load('/messages?thread=inquiry-2', { navigation: true, push: true });
  c.bodyTarget.value = 'New conversation draft';
  c.draftChanged();
  post.resolve(snapshot('inquiry-1'));
  await sending;
  assert.equal(c.currentKey, 'inquiry-2');
  assert.equal(c.bodyTarget.value, 'New conversation draft');
  assert.equal(c.drafts.has('inquiry-1'), false);
  assert.equal(c.drafts.get('inquiry-2').body, 'New conversation draft');
  assert.deepEqual(applied.map(item => item.data.selected_key), ['inquiry-2']);
});

test('uncertain send failure retains the draft and reuses its request id on retry', async t => {
  const { c, form, event } = fixture(t);
  c.bodyTarget.value = 'Please confirm the showing.';
  const ids = [];
  c.request = async (_url, options) => {
    ids.push(options.body.get('request_id'));
    if (ids.length === 1) throw new Error('Network disconnected after request');
    return snapshot();
  };
  await c.submit(event(form()));
  assert.equal(c.bodyTarget.value, 'Please confirm the showing.');
  assert.equal(c.errorTarget.hidden, false);
  assert.equal(c.drafts.get('inquiry-1').requestId, ids[0]);
  await c.submit(event(form()));
  assert.equal(ids.length, 2);
  assert.equal(ids[0], ids[1]);
  assert.equal(c.bodyTarget.value, '');
});

test('a changed draft gets a new request id after a failed send', async t => {
  const { c, form, event } = fixture(t);
  c.bodyTarget.value = 'First message';
  const ids = [];
  c.request = async (_url, options) => { ids.push(options.body.get('request_id')); throw new Error('Offline'); };
  await c.submit(event(form()));
  c.bodyTarget.value = 'Changed message';
  c.draftChanged();
  await c.submit(event(form()));
  assert.notEqual(ids[0], ids[1]);
});

test('duplicate submits are ignored while the first send is in flight', async t => {
  const { c, form, event } = fixture(t);
  const pending = deferred();
  c.bodyTarget.value = 'Only send this once.';
  let calls = 0;
  c.request = () => { calls++; return pending.promise; };
  const sending = c.submit(event(form()));
  await c.submit(event(form()));
  assert.equal(calls, 1);
  pending.resolve(snapshot());
  await sending;
});

test('hidden tabs, expired sessions, and pending mutations do not start refresh reads', async t => {
  const { c, timers } = fixture(t);
  c.schedule = Messages.prototype.schedule;
  let calls = 0;
  c.load = async () => { calls++; };
  document.hidden = true;
  c.schedule();
  await c.refresh();
  assert.equal(calls, 0);
  assert.equal(timers.size, 0);
  document.hidden = false;
  c.sessionExpired = true;
  c.schedule();
  await c.refresh();
  assert.equal(calls, 0);
  assert.equal(timers.size, 0);
  c.sessionExpired = false;
  c.mutation = true;
  await c.refresh();
  assert.equal(calls, 0);
  assert.equal(timers.size, 1);
  assert.equal([...timers.values()][0].delay, 5000);
  c.alive = false;
  c.schedule();
  assert.equal(timers.size, 0);
});

test('session expiry preserves unsent text and stops automatic retries', async t => {
  const { c, form, event, timers } = fixture(t);
  c.schedule = Messages.prototype.schedule;
  c.bodyTarget.value = 'Keep this reply.';
  const outgoing = form();
  c.element.querySelectorAll = () => [outgoing.button];
  c.request = async () => { throw Object.assign(new Error('Expired'), { status: 401 }); };
  await c.submit(event(outgoing));
  assert.equal(c.sessionExpired, true);
  assert.equal(c.bodyTarget.value, 'Keep this reply.');
  assert.equal(outgoing.button.disabled, true);
  assert.match(c.errorTarget.textContent, /sign in again/i);
  assert.equal(timers.size, 0);
});

test('disconnect cancels reads and timers and prevents a late response from applying', async t => {
  const { c, applied, timers } = fixture(t);
  const pending = deferred();
  c.request = () => pending.promise;
  c.pollTimer = setTimeout(() => {}, 5000);
  c.searchTimer = setTimeout(() => {}, 300);
  const loading = c.load('/messages?thread=inquiry-2');
  const signal = c.readAbort.signal;
  c.disconnect();
  assert.equal(signal.aborted, true);
  assert.equal(timers.size, 0);
  pending.resolve(snapshot('inquiry-2'));
  await loading;
  assert.equal(applied.length, 0);
});

test('JSON requests preserve session credentials, avoid caches, and clean internal URL flags', async t => {
  const { c, timers } = fixture(t);
  let received;
  globalThis.fetch = async (url, options) => { received = { url, options }; return response(snapshot()); };
  const result = await c.request('/messages?thread=inquiry-1&mark_read=0&format=old');
  assert.equal(result.selected_key, 'inquiry-1');
  assert.equal(received.url.searchParams.get('format'), 'json');
  assert.equal(received.url.searchParams.has('mark_read'), false);
  assert.equal(received.options.credentials, 'same-origin');
  assert.equal(received.options.cache, 'no-store');
  assert.equal(received.options.headers.Accept, 'application/json');
  assert.equal(timers.size, 0);
});

test('redirected login pages and explicit 401 responses become session errors', async t => {
  const { c, timers } = fixture(t);
  for (const options of [{ redirected: true, contentType: 'text/html' }, { status: 401 }]) {
    globalThis.fetch = async () => response({}, options);
    await assert.rejects(c.request('/messages'), error => error.status === 401);
    assert.equal(timers.size, 0);
  }
});

test('non-JSON responses and JSON action errors are rejected without accepting a snapshot', async t => {
  const { c, timers } = fixture(t);
  globalThis.fetch = async () => response('<html>Gateway error</html>', { contentType: 'text/html', status: 502 });
  await assert.rejects(c.request('/messages'), /unexpected response/);
  globalThis.fetch = async () => response({ ok: false, error: 'Another agent accepted this client.' }, { status: 409 });
  await assert.rejects(c.request('/messages'), error => error.status === 409 && /Another agent/.test(error.message));
  assert.equal(timers.size, 0);
});

test('malformed successful JSON is rejected before it can clear the selected thread', async t => {
  const { c, applied, timers } = fixture(t);
  for (const data of [null, [], {}, { ok: true }, snapshot('inquiry-1', { conversation_html: null }), snapshot(42)]) {
    globalThis.fetch = async () => response(data);
    await assert.rejects(c.request('/messages'), undefined, `Malformed payload was accepted: ${JSON.stringify(data)}`);
  }
  assert.equal(c.currentKey, 'inquiry-1');
  assert.equal(applied.length, 0);
  assert.equal(timers.size, 0);
});

test('request timeout aborts the fetch and removes its timer', async t => {
  const { c, timers } = fixture(t);
  let signal;
  globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {
    signal = options.signal;
    signal.addEventListener('abort', () => reject(Object.assign(new Error('Aborted'), { name: 'AbortError' })), { once: true });
  });
  const pending = c.request('/messages');
  const timeout = [...timers.values()][0];
  assert.equal(timeout.delay, 15000);
  timeout.fn();
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(signal.aborted, true);
  assert.equal(timers.size, 0);
});

test('aborting a superseded request propagates to fetch', async t => {
  const { c, timers } = fixture(t);
  const abort = new AbortController();
  globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(Object.assign(new Error('Aborted'), { name: 'AbortError' })), { once: true });
  });
  const pending = c.request('/messages', { signal: abort.signal });
  abort.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(timers.size, 0);
});

test('navigation cannot fetch another origin or a different application route', async t => {
  const { c } = fixture(t);
  let calls = 0;
  c.request = async () => { calls++; return snapshot(); };
  await c.load('https://example.invalid/messages?thread=inquiry-2');
  await c.load('/contacts');
  assert.equal(calls, 0);
  assert.equal(c.currentKey, 'inquiry-1');
});

test('a pending read started before sending cannot remove a confirmed reply', async t => {
  const { c, form, event, applied } = fixture(t);
  const oldRead = deferred();
  c.bodyTarget.value = 'Confirmed Saturday at 10:30.';
  c.request = (_url, options = {}) => options.method === 'POST'
    ? Promise.resolve(snapshot('inquiry-1', { conversation_html: '<section>Includes confirmed reply</section>' }))
    : oldRead.promise;
  const reading = c.load('/messages?thread=inquiry-1');
  const readSignal = c.readAbort.signal;
  await c.submit(event(form()));
  assert.equal(readSignal.aborted, true);
  oldRead.resolve(snapshot('inquiry-1', { conversation_html: '<section>Old history</section>' }));
  await reading;
  assert.deepEqual(applied.map(item => item.data.conversation_html), ['<section>Includes confirmed reply</section>']);
});

test('losing access to one conversation preserves drafts for other conversations', async t => {
  const { c } = fixture(t);
  c.bodyTarget.value = 'Draft for the conversation losing access';
  c.drafts.set('inquiry-2', { body: 'Keep this other draft', requestId: 'other-request' });
  c.request = async url => {
    if (url.searchParams.has('thread')) throw Object.assign(new Error('No longer assigned'), { status: 404 });
    return snapshot('');
  };
  await c.load('/messages?thread=inquiry-1');
  assert.equal(c.currentKey, '');
  assert.equal(c.drafts.has('inquiry-1'), false);
  assert.equal(c.drafts.get('inquiry-2')?.body, 'Keep this other draft');
  assert.match(c.errorTarget.textContent, /no longer available/);
});

test('navigation started during send cannot overwrite the successful reply with older history', async t => {
  const { c, form, event, applied } = fixture(t);
  const post = deferred(), staleRead = deferred();
  let reads = 0;
  const fresh = snapshot('inquiry-1', { conversation_html: '<section>Includes confirmed reply</section>', view: 'mine' });
  c.bodyTarget.value = 'The showing is confirmed.';
  c.request = (_url, options = {}) => {
    if (options.method === 'POST') return post.promise;
    reads += 1;
    return reads === 1 ? staleRead.promise : Promise.resolve(fresh);
  };
  const sending = c.submit(event(form()));
  const navigating = c.load('/messages?thread=inquiry-1&view=mine', { navigation: true, push: true });
  post.resolve(fresh);
  await sending;
  staleRead.resolve(snapshot('inquiry-1', { conversation_html: '<section>History before the send</section>', view: 'mine' }));
  await navigating;
  assert.equal(applied.at(-1).data.conversation_html, '<section>Includes confirmed reply</section>');
  assert.equal(c.currentURL.searchParams.get('view'), 'mine');
});


test('an input named action cannot replace the form URL used to send a reply', async t => {
  const { c, form, event } = fixture(t);
  c.bodyTarget.value = 'Saturday at 10:30 works.';
  const outgoing = form();
  outgoing.action = { name: 'action', value: 'reply', toString: () => '[object HTMLInputElement]' };
  let sent;
  c.request = async (url, options) => { sent = { url, options }; return snapshot(); };
  await c.submit(event(outgoing));
  assert.equal(sent.url.pathname, '/messages');
  assert.equal(sent.url.searchParams.get('thread'), 'inquiry-1');
  assert.equal(sent.options.body.get('action'), 'reply');
  assert.equal(c.bodyTarget.value, '');
});

test('an input named action cannot redirect conversation search to a DOM string', t => {
  const { c } = fixture(t);
  const search = {
    action: { name: 'action', value: 'search', toString: () => '[object HTMLInputElement]' },
    getAttribute: name => name === 'action' ? '/messages' : null,
    fields: { q: 'Sarah', view: 'mine' },
  };
  let loaded;
  c.load = (url, options) => { loaded = { url, options }; };
  c.searchForm(search);
  assert.equal(loaded.url.pathname, '/messages');
  assert.equal(loaded.url.searchParams.get('thread'), 'inquiry-1');
  assert.equal(loaded.url.searchParams.get('q'), 'Sarah');
  assert.equal(loaded.url.searchParams.get('view'), 'mine');
  assert.equal(loaded.options.navigation, true);
});
