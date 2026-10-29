import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../frontend/controllers/document_review_workspace_controller.js', import.meta.url), 'utf8');
const { default: ReviewWorkspace } = await import('data:text/javascript;base64,' + Buffer.from(
  source.replace(/^import .*;$/gm, '').replace('extends PdfPaneController', '')
).toString('base64'));

function fixture() {
  const review = new ReviewWorkspace();
  const boxes = [];
  const navigation = [];
  const page = {
    querySelectorAll: () => [],
    scrollIntoView: () => navigation.push('page scroll'),
    appendChild: box => boxes.push(box),
  };
  review.pageEls = [null, page];
  review.scale = 1.2;
  review.selectedIndex = 0;
  review.fieldCardTargets = [{
    dataset: { page: '1', quote: 'Purchase price' },
    classList: { add() {}, remove() {} },
    focus() {}, scrollIntoView() {},
  }];
  review.pdfDoc = { getPage: async () => ({
    getViewport: () => ({ transform: [] }),
    getTextContent: async () => ({ items: [{ str: 'Purchase price', transform: [1, 0, 0, 1, 10, 50], width: 100, height: 12 }] }),
  }) };
  review.pulsePage = () => assert.fail('Expected a quote match');
  review.goToPage = number => navigation.push(number);
  globalThis.window = { pdfjsLib: { Util: { transform: (_, value) => value } }, setTimeout() {} };
  globalThis.document = { createElement: () => ({ style: {} }) };
  return { review, boxes, navigation };
}

async function settleHighlight() {
  await Promise.resolve();
  await Promise.resolve();
}

test('layout redraw restores a selected quote without changing the restored reading position', async () => {
  const { review, boxes, navigation } = fixture();
  review.afterPdfLayout();
  await settleHighlight();
  assert.equal(boxes.length, 1);
  assert.equal(boxes[0].className, 'drw-quote-box');
  assert.deepEqual(navigation, []);
});

test('selecting a field still navigates to its page and draws its quote', async () => {
  const { review, boxes, navigation } = fixture();
  review.selectIndex(0);
  await settleHighlight();
  assert.deepEqual(navigation, [1]);
  assert.equal(boxes.length, 1);
});
