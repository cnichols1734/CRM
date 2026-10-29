import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../frontend/controllers/contacts_page_controller.js', import.meta.url), 'utf8');
const { default: ContactsPage } = await import('data:text/javascript;base64,' + Buffer.from(
  source.replace(/^import .*;$/gm, '').replace('export default class extends Controller', 'export default class')
).toString('base64'));

function fixture() {
  const navigations = [];
  const previews = [];
  globalThis.window = { location: { assign: href => navigations.push(href) } };
  const page = new ContactsPage();
  let open = false;
  page.hasLayoutTarget = true;
  page.element = { contains: () => true };
  page.layoutTarget = { classList: { contains: () => open } };
  page.openRail = () => { open = true; };
  page.closePreview = () => { open = false; page.activeRow = null; };
  page.markActiveRow = row => { page.activeRow = row; };
  page.renderSkeleton = () => {};
  page.fetchPreview = id => previews.push(id);
  const row = { dataset: { contactId: '42', contactHref: '/contact/42' } };
  const event = (overrides = {}) => ({
    currentTarget: row, target: { closest: () => null }, detail: 1,
    button: 0, preventDefault() { this.defaultPrevented = true; }, ...overrides,
  });
  return { page, row, event, navigations, previews, isOpen: () => open };
}

test('single clicks open and toggle the preview without navigating', () => {
  const f = fixture();
  f.page.openPreview(f.event());
  assert.equal(f.isOpen(), true);
  assert.deepEqual(f.previews, ['42']);
  f.page.openPreview(f.event());
  assert.equal(f.isOpen(), false);
  assert.deepEqual(f.navigations, []);
});

test('click, click, double-click opens full contact without closing the preview on click two', () => {
  const f = fixture();
  f.page.openPreview(f.event());
  f.page.openPreview(f.event({ detail: 2 }));
  assert.equal(f.isOpen(), true);
  assert.deepEqual(f.previews, ['42']);
  const event = f.event({ detail: 2 });
  f.page.openContact(event);
  assert.deepEqual(f.navigations, ['/contact/42']);
  assert.equal(event.defaultPrevented, true);
});

test('nested links, form controls, menus and editable content keep their own actions', () => {
  for (const selector of ['a', 'button', 'input', 'select', 'textarea', 'label', 'summary', "[role='button']", "[role='checkbox']", "[role='menuitem']", '[contenteditable]']) {
    const f = fixture();
    const event = f.event({ target: { closest: selectors => selectors.split(', ').includes(selector) ? {} : null } });
    f.page.openPreview(event);
    f.page.openContact(event);
    assert.deepEqual(f.previews, [], selector);
    assert.deepEqual(f.navigations, [], selector);
    assert.equal(event.defaultPrevented, undefined, selector);
  }
});

test('modified, prevented and non-primary double-clicks do not navigate', () => {
  for (const override of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }, { button: 2 }, { defaultPrevented: true }]) {
    const f = fixture();
    f.page.openContact(f.event(override));
    assert.deepEqual(f.navigations, []);
  }
});

test('a row without a contact URL does not navigate', () => {
  const f = fixture();
  delete f.row.dataset.contactHref;
  f.page.openContact(f.event());
  assert.deepEqual(f.navigations, []);
});
