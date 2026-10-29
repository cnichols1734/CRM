import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const transitions = await readFile(new URL('../../static/js/transitions.js', import.meta.url), 'utf8');

const source = await readFile(new URL('../../frontend/controllers/contacts_page_controller.js', import.meta.url), 'utf8');
const { default: ContactsPage } = await import('data:text/javascript;base64,' + Buffer.from(
  source.replace(/^import .*;$/gm, '').replace('export default class extends Controller', 'export default class')
).toString('base64'));

function fixture() {
  const timers = new Map();
  let nextTimer = 0;
  const history = [];
  const initialURL = 'https://example.com/contacts?q=old&page=3&view=all&owners=2&groups=4&zips=75001&commission=high&sort=name&dir=desc&per_page=50';
  globalThis.window = {
    location: { href: initialURL },
    setTimeout(callback) { timers.set(++nextTimer, callback); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
    history: { state: { preserved: true }, replaceState(state, _, url) { history.push({ state, url: String(url) }); window.location.href = String(url); } },
  };
  globalThis.DOMParser = class {
    parseFromString(html) {
      return { querySelector(selector) {
        if (html === 'login') return null;
        return { innerHTML: `${html}:${selector}`, getAttribute: () => `/export-contacts?q=${html}` };
      } };
    }
  };
  const page = new ContactsPage();
  page.searchVersion = 0;
  page.hasSearchInputTarget = true;
  page.searchInputTarget = { value: 'Jane', selectionStart: 2, selectionEnd: 2, closest: () => null };
  const input = page.searchInputTarget;
  for (const name of ['searchResults', 'searchSummary', 'searchCount']) {
    page[`${name}Target`] = { innerHTML: 'old', setAttribute(key, value) { this[key] = value; } };
  }
  page.searchErrorTarget = { hidden: true };
  page.exportLinkTarget = {};
  const filterQuery = { value: 'old' };
  page.filterPanelTarget = { querySelector: () => filterQuery };
  const requests = [];
  globalThis.fetch = (url, options) => new Promise(resolve => requests.push({ url, options, resolve }));
  const finish = (index, body, overrides = {}) => requests[index].resolve({ ok: true, redirected: false, text: async () => body, ...overrides });
  return { page, input, filterQuery, history, timers, requests, finish, initialURL };
}

test('typing debounces without navigating or changing the focused input', async () => {
  const f = fixture();
  for (const value of ['J', 'Ja', 'Jan', 'Jane']) {
    f.input.value = value;
    f.page.queueSearch({ target: f.input });
  }
  assert.equal(f.timers.size, 1);
  assert.equal(window.location.href, f.initialURL);
  const pending = [...f.timers.values()][0]();
  assert.equal(f.requests.length, 1);
  f.finish(0, 'Jane');
  await pending;
  assert.equal(f.page.searchInputTarget, f.input);
  assert.equal(f.input.value, 'Jane');
  assert.equal(f.input.selectionStart, 2);
  assert.equal(f.filterQuery.value, 'Jane');
  const url = new URL(f.history[0].url);
  assert.equal(url.searchParams.get('q'), 'Jane');
  assert.equal(url.searchParams.has('page'), false);
  for (const key of ['view', 'owners', 'groups', 'zips', 'commission', 'sort', 'dir', 'per_page']) {
    assert.equal(url.searchParams.get(key), new URL(f.initialURL).searchParams.get(key));
  }
  assert.deepEqual(f.history[0].state, { preserved: true });
  assert.match(f.page.searchResultsTarget.innerHTML, /^Jane:/);
  assert.equal(f.page.searchResultsTarget['aria-busy'], 'false');
});

test('a newer keystroke invalidates in-flight responses before the debounce fires', async () => {
  const f = fixture();
  const first = f.page.loadSearch();
  f.input.value = 'Janet';
  f.page.queueSearch({ target: f.input });
  assert.equal(f.requests[0].options.signal.aborted, true);
  f.finish(0, 'Jane');
  await first;
  assert.equal(f.page.searchResultsTarget.innerHTML, 'old');
  assert.equal(f.history.length, 0);
  const second = f.page.loadSearch();
  f.finish(1, 'Janet');
  await second;
  assert.match(f.page.searchResultsTarget.innerHTML, /^Janet:/);
});

test('clear removes the query and keeps existing filters', async () => {
  const f = fixture();
  f.page.clearSearch({ preventDefault() {} });
  assert.equal(f.input.value, '');
  const pending = f.page.loadSearch();
  f.finish(0, 'all');
  await pending;
  assert.equal(new URL(f.history[0].url).searchParams.has('q'), false);
  assert.equal(new URL(f.history[0].url).searchParams.get('owners'), '2');
  assert.equal(f.filterQuery.value, '');
});

test('HTTP failures, auth redirects and unexpected pages preserve results and allow retry', async () => {
  for (const failure of [{ ok: false }, { redirected: true }, {}]) {
    const f = fixture();
    const pending = f.page.loadSearch();
    f.finish(0, 'login', failure);
    await pending;
    assert.equal(f.page.searchErrorTarget.hidden, false);
    assert.equal(f.page.searchResultsTarget.innerHTML, 'old');
    assert.equal(f.history.length, 0);
    assert.equal(f.page.searchResultsTarget['aria-busy'], 'false');
    const retry = f.page.loadSearch();
    f.finish(1, 'Jane');
    await retry;
    assert.equal(f.page.searchErrorTarget.hidden, true);
    assert.match(f.page.searchResultsTarget.innerHTML, /^Jane:/);
  }
});

test('composition waits for committed text and disconnect invalidation ignores late responses', async () => {
  const f = fixture();
  f.page.queueSearch({ isComposing: true });
  assert.equal(f.timers.size, 0);
  f.page.queueSearch({ isComposing: false });
  assert.equal(f.timers.size, 1);
  const pending = f.page.loadSearch();
  globalThis.document = { removeEventListener() {} };
  f.page.disconnect();
  f.finish(0, 'Jane');
  await pending;
  assert.equal(f.requests[0].options.signal.aborted, true);
  assert.equal(f.history.length, 0);
  assert.equal(f.page.searchResultsTarget.innerHTML, 'old');
});

test('network failure keeps the current input and list available', async () => {
  const f = fixture();
  globalThis.fetch = async () => { throw new Error('Offline'); };
  await f.page.loadSearch();
  assert.equal(f.page.searchInputTarget, f.input);
  assert.equal(f.input.value, 'Jane');
  assert.equal(f.page.searchResultsTarget.innerHTML, 'old');
  assert.equal(f.page.searchErrorTarget.hidden, false);
  assert.equal(f.history.length, 0);
});


test('search resizes the contacts slider to the updated list height', async () => {
  const f = fixture();
  const context = { window, document: { documentElement: {}, readyState: 'loading', addEventListener() {} } };
  vm.runInNewContext(transitions, context);
  const body = { get scrollHeight() { return f.page.searchResultsTarget.innerHTML === 'old' ? 2400 : 480; } };
  const slider = { style: { height: '2400px' }, getAttribute: () => '1', querySelector: () => ({ querySelector: () => body }) };
  f.page.searchResultsTarget.closest = () => slider;
  const pending = f.page.loadSearch();
  f.finish(0, 'Jane');
  await pending;
  assert.equal(slider.style.height, '480px');
  assert.equal(f.page.searchErrorTarget.hidden, true);
});
