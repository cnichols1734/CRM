import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../frontend/controllers/contact_picker_controller.js', import.meta.url), 'utf8');
const { default: Picker } = await import('data:text/javascript;base64,' + Buffer.from(
  source.replace(/^import .*;$/gm, '').replace('export default class extends Controller', 'export default class')
).toString('base64'));

function element() {
  return {
    value: '', required: true, hidden: true, children: [], dataset: {}, attributes: {},
    setAttribute(key, value) { this.attributes[key] = value; },
    removeAttribute(key) { delete this.attributes[key]; },
    setCustomValidity(value) { this.validity = value; },
    append(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
    scrollIntoView() {},
  };
}
function fixture() {
  globalThis.window = { location: { origin: 'https://example.com' } };
  globalThis.document = { createElement: element };
  const picker = new Picker();
  for (const target of ['query', 'value', 'results', 'status']) picker[`${target}Target`] = element();
  picker.resultsTarget.id = 'results';
  picker.urlValue = '/tasks/contact-search';
  picker.connect();
  return picker;
}
const contacts = [
  { id: 4, name: 'Jane Doe', email: 'jane@example.com', phone: '5551110000' },
  { id: 8, name: 'Jane Doe', email: 'other@example.com', phone: '5552220000' },
];
async function results(picker, data = contacts) {
  globalThis.fetch = async () => ({ ok: true, json: async () => data });
  await picker.load('Jane', picker.version);
}
function key(picker, key) { picker.keydown({ key, preventDefault() {} }); }

test('keyboard selection stores the chosen ID even when names match', async () => {
  const picker = fixture();
  await results(picker);
  key(picker, 'ArrowUp');
  assert.equal(picker.activeIndex, 1);
  key(picker, 'Enter');
  assert.equal(picker.valueTarget.value, '8');
  assert.equal(picker.queryTarget.validity, '');
  assert.equal(picker.resultsTarget.hidden, true);
  assert.match(picker.statusTarget.textContent, /other@example.com/);
});

test('typing after selection clears the old ID and blocks unmatched text', async () => {
  const picker = fixture();
  await results(picker);
  picker.choose(0);
  picker.queryTarget.value = 'Someone else';
  picker.search();
  assert.equal(picker.valueTarget.value, '');
  assert.match(picker.queryTarget.validity, /Select a contact/);
  picker.disconnect();
});

for (const action of ['search', 'leave', 'disconnect']) {
  test(`${action} prevents an old response reopening results`, async () => {
    const picker = fixture();
    let resolve;
    globalThis.fetch = () => new Promise(done => { resolve = done; });
    const pending = picker.load('Jane', picker.version);
    picker[action]();
    resolve({ ok: true, json: async () => contacts });
    await pending;
    assert.deepEqual(picker.items, []);
    assert.equal(picker.resultsTarget.hidden, true);
    picker.disconnect();
  });
}

test('empty results and network errors allow another search', async () => {
  const picker = fixture();
  await results(picker, []);
  assert.equal(picker.statusTarget.textContent, 'No matching contacts.');
  globalThis.fetch = async () => ({ ok: false });
  await picker.load('Jane', picker.version);
  assert.match(picker.statusTarget.textContent, /retry/);
  await results(picker);
  assert.equal(picker.resultsTarget.children.length, 2);
  assert.equal(picker.resultsTarget.hidden, false);
});

test('Escape and Tab close results without selecting a contact', async () => {
  for (const command of ['Escape', 'Tab']) {
    const picker = fixture();
    await results(picker);
    key(picker, 'ArrowDown');
    key(picker, command);
    assert.equal(picker.valueTarget.value, '');
    assert.equal(picker.resultsTarget.hidden, true);
    assert.equal(picker.queryTarget.attributes['aria-activedescendant'], undefined);
  }
});

test('optional empty contact remains valid', () => {
  const picker = fixture();
  picker.queryTarget.required = false;
  picker.validateSelection();
  assert.equal(picker.queryTarget.validity, '');
});
