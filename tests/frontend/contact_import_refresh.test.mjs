import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../frontend/controllers/contacts_page_controller.js', import.meta.url), 'utf8');
const { default: ContactsPage } = await import('data:text/javascript;base64,' + Buffer.from(
  source.replace(/^import .*;$/gm, '').replace('export default class extends Controller', 'export default class')
).toString('base64'));

function fixture({ data, ok = true, unavailableStorage = false, reject = false } = {}) {
  const stored = new Map();
  const reports = [];
  let reloads = 0;
  globalThis.window = {
    location: { reload() { reloads++; } },
    sessionStorage: {
      setItem(key, value) {
        if (unavailableStorage) throw new Error('Storage blocked');
        stored.set(key, value);
      },
      getItem: key => stored.get(key) ?? null,
      removeItem: key => stored.delete(key),
    },
  };
  globalThis.fetch = async () => {
    if (reject) throw new Error('Network unavailable');
    return { ok, json: async () => data };
  };
  const page = new ContactsPage();
  page.element = { dataset: { importResultKey: 'contacts-import-result:42' }, querySelector: () => null };
  page.pendingImportFile = new Blob(['first_name,email\nAlex,alex@example.com']);
  page.importPreviewTarget = { classList: { add() {} } };
  page.setImportState = () => {};
  page.renderImportStatus = report => reports.push(report);
  return { page, stored, reports, reloads: () => reloads };
}

for (const status of ['success', 'partial_success']) {
  test(`${status} refreshes and restores the import report only once`, async () => {
    const data = { status, success_count: 2, error_details: status === 'partial_success' ? ['Row 3: missing name'] : [] };
    const f = fixture({ data });
    await f.page.confirmImport({ preventDefault() {} });
    assert.equal(f.reloads(), 1);
    assert.equal(f.page.pendingImportFile, null);
    f.reports.length = 0;
    f.page.restoreImportStatus();
    assert.deepEqual(f.reports, [data]);
    f.page.restoreImportStatus();
    assert.equal(f.reports.length, 1);
    assert.equal(f.stored.size, 0);
  });
}

test('failed, duplicate-only, and rejected HTTP imports keep their report without reloading', async () => {
  for (const result of [
    { data: { status: 'error', success_count: 0, error_details: ['Invalid CSV'] } },
    { data: { status: 'success', success_count: 0, duplicates_skipped: 2 } },
    { ok: false, data: { status: 'success', success_count: 1 } },
  ]) {
    const f = fixture(result);
    await f.page.confirmImport({ preventDefault() {} });
    assert.equal(f.reloads(), 0);
    assert.deepEqual(f.reports, [result.data]);
    assert.equal(f.stored.size, 0);
  }
});

test('storage restrictions do not prevent refreshing newly imported contacts', async () => {
  const f = fixture({ data: { status: 'success', success_count: 1 }, unavailableStorage: true });
  await f.page.confirmImport({ preventDefault() {} });
  assert.equal(f.reloads(), 1);
});

test('network errors do not reload or save a success report', async t => {
  t.mock.method(console, 'error', () => {});
  const f = fixture({ reject: true });
  await f.page.confirmImport({ preventDefault() {} });
  assert.equal(f.reloads(), 0);
  assert.equal(f.reports[0].status, 'error');
  assert.equal(f.stored.size, 0);
});

test('restoring reports is scoped to the signed-in user and tolerates invalid storage', () => {
  const f = fixture();
  f.stored.set('contacts-import-result:99', JSON.stringify({ status: 'success', success_count: 5 }));
  f.stored.set('contacts-import-result:42', 'invalid json');
  f.page.restoreImportStatus();
  assert.equal(f.reports.length, 0);
  assert.equal(f.stored.has('contacts-import-result:42'), false);
  assert.equal(f.stored.has('contacts-import-result:99'), true);
});
