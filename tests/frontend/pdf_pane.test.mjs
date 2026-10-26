import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../frontend/controllers/pdf_pane_controller.js', import.meta.url), 'utf8');
const { default: PdfPane } = await import('data:text/javascript;base64,' + Buffer.from(
  source.replace(/^import .*;$/gm, '').replace('extends Controller', '')
).toString('base64'));

function fixture(width = 320) {
  const pane = new PdfPane();
  pane.pdfPaneTarget = { clientWidth: width, scrollTop: 0, getBoundingClientRect: () => ({ top: 20 }) };
  pane.hasPdfPaneTarget = true;
  pane.pdfDoc = { getPage: async () => ({ getViewport: () => ({ width: 612, height: 792 }) }) };
  pane.scale = 1;
  pane.pageEls = [];
  return pane;
}

test('Fit scales a letter page into a narrow pane without a desktop minimum', async () => {
  for (const width of [220, 320, 382, 669, 951]) {
    const pane = fixture(width);
    await pane.applyFitScale();
    assert.ok(pane.scale * 612 <= width - 48 + 0.01);
  }
});

test('an old document cannot overwrite the scale after switching PDFs', async () => {
  const pane = fixture();
  let resolve;
  pane.pdfDoc = { getPage: () => new Promise(done => { resolve = done; }) };
  const fitting = pane.applyFitScale();
  pane.pdfDoc = {};
  resolve({ getViewport: () => ({ width: 612 }) });
  await fitting;
  assert.equal(pane.scale, 1);
});

test('zoom preserves the reading anchor and disables automatic fit until Fit is pressed', async () => {
  const pane = fixture();
  pane.fitMode = true;
  const anchor = { page: 12, fraction: 0.3 };
  pane.readingAnchor = () => anchor;
  let restored;
  pane.rerender = async value => { restored = value; };
  pane.zoomIn();
  assert.equal(pane.fitMode, false);
  assert.equal(pane.scale, 1.15);
  assert.deepEqual(restored, anchor);
  await pane.fitWidth();
  assert.equal(pane.fitMode, true);
  assert.deepEqual(restored, anchor);
});

test('a document switch during Fit does not rerender the replacement document', async () => {
  const pane = fixture();
  let resolve;
  pane.pdfDoc = { getPage: () => new Promise(done => { resolve = done; }) };
  let renders = 0;
  pane.rerender = () => { renders++; };
  const fitting = pane.fitWidth();
  pane.pdfDoc = {};
  resolve({ getViewport: () => ({ width: 612 }) });
  await fitting;
  assert.equal(renders, 0);
});

test('disconnect cancels pending rendering, resize work and loading', () => {
  const pane = fixture();
  let canceled = 0;
  pane.loadToken = 2;
  pane.renderToken = 4;
  pane.renderJobs = new Map([[1, { cancel: () => canceled++ }]]);
  pane.loadingTask = { destroy: () => canceled++ };
  pane.resizeObserver = { disconnect: () => canceled++ };
  pane.pageObserver = { disconnect: () => canceled++ };
  globalThis.window = { cancelAnimationFrame() {} };
  pane.disconnectPdfPane();
  assert.equal(canceled, 4);
  assert.equal(pane.pdfDoc, null);
  assert.equal(pane.renderJobs.size, 0);
  assert.equal(pane.loadToken, 3);
  assert.equal(pane.renderToken, 5);
});
