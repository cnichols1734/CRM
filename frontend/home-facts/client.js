import { Draft } from './draft.js';

const root = document.querySelector('[data-home-facts-client]');
const app = document.getElementById('hf-app');
const errorBox = document.getElementById('hf-error');
const base = `/home-facts/r/${encodeURIComponent(root.dataset.responseId)}`;
const storageKey = `home-facts:v1:${root.dataset.scope}:${root.dataset.responseId}`;
const recipient = root.dataset.recipient === 'true';
let retryTimer, retryDelay = 1500, reportTimer, submissionPending = false, uploading = false;
let draft, timer, saving = false, mode = 'editor', pdfTimer, pdfVersion = 0, pdfBytes;
let previewOpen = false;
const expandedRows = new Set();

function el(tag, text, attributes = {}) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}
function button(text, action, className = 'hf-secondary') {
  const node = el('button', text, { type: 'button', class: className });
  node.addEventListener('click', action);
  return node;
}
function showError(message) { errorBox.textContent = message || ''; errorBox.hidden = !message; }
function store() {
  try { localStorage.setItem(storageKey, JSON.stringify(draft.persisted())); return true; }
  catch { showError('This browser could not save a device copy. Keep this page open until you finish and submit.'); return false; }
}
async function api(path = '/data', options = {}) {
  const response = await fetch(base + path, { credentials: 'same-origin', ...options,
    headers: { 'X-CSRF-Token': root.dataset.csrf, ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...options.headers } });
  const data = await response.json();
  if (!response.ok) { const error = new Error(data.error || 'Could not connect. Please try again.'); error.code = data.code; error.httpStatus = response.status; throw error; }
  return data;
}
function changed(key, value) {
  draft.edit(key, value);
  store();
  clearTimeout(timer); timer = setTimeout(save, 650);
  clearTimeout(pdfTimer); pdfTimer = setTimeout(renderPDF, 900);
}
async function save() {
  if (saving || uploading || !draft || draft.server.state !== 'draft') return;
  const payload = draft.begin();
  if (!payload) return;
  clearTimeout(retryTimer);
  let retry = false;
  saving = true; store();
  try {
    const snapshot = await api('/data', { method: 'PATCH', body: JSON.stringify(payload) });
    draft.acknowledge(snapshot); store(); showError('');
    retryDelay = 1500;
  } catch (error) {
    if (error.code === 'revision_conflict') {
      draft.flight = null;
      try { draft.reconcile(await api()); store(); render(); }
      catch (refreshError) { showError(refreshError.message); retry = !refreshError.httpStatus || refreshError.httpStatus >= 500 || refreshError.httpStatus === 429; }
    } else {
      retry = !error.httpStatus || error.httpStatus >= 500 || error.httpStatus === 429;
      showError(retry ? 'Connection interrupted. Your answers are saved on this device. We’ll upload them automatically when the connection returns.' : error.message);
    }
  } finally {
    saving = false;
    if (retry) { retryTimer = setTimeout(save, retryDelay); retryDelay = Math.min(retryDelay * 2, 30000); }
    else if (!draft.flight && Object.keys(draft.pending).length && !Object.keys(draft.conflicts).length) timer = setTimeout(save, 650);
  }
}
window.addEventListener('online', save);
window.addEventListener('beforeunload', (event) => {
  if (draft && Object.keys(draft.pending).length) { store(); event.preventDefault(); event.returnValue = ''; }
});
document.addEventListener('visibilitychange', () => { if (document.hidden && draft) { store(); save(); } });

function conflictUI() {
  if (!Object.keys(draft.conflicts).length) return null;
  const box = el('section', null, { class: 'hf-conflict', 'aria-label': 'Changes from another device' });
  box.append(el('h3', 'Choose which answers to keep'));
  const definitions = draft.server.manifest.sections.flatMap(s => s.groups.flatMap(g => g.fields));
  for (const [key, conflict] of Object.entries(draft.conflicts)) {
    box.append(el('strong', definitions.find(f => f.id === key)?.label || key));
    box.append(el('p', `This device: ${format(conflict.mine) || 'Blank'}`), el('p', `Saved to CRM: ${format(conflict.theirs) || 'Blank'}`));
    box.append(button('Keep this device’s answer', () => { draft.resolve(key, true); store(); render(); save(); }));
    box.append(button('Use the saved answer', () => { draft.resolve(key, false); store(); render(); save(); }));
  }
  return box;
}
function format(value) { return Array.isArray(value) ? value.join(', ') : String(value ?? ''); }
function hasAnswer(group) { return group.fields.some(f => format(draft.answers[f.id]).trim()); }
function visibleGroups(section) {
  const optional = section.groups.filter(g => g.optional_row);
  const firstBlank = optional.find(g => !hasAnswer(g));
  return section.groups.filter(g => !g.optional_row || hasAnswer(g) || g === firstBlank || expandedRows.has(g.id));
}
function move(sectionIndex, groupIndex = null) {
  draft.position = { section: sectionIndex, group: groupIndex ?? 0 }; store(); render();
  if (groupIndex !== null) {
    const group = visibleGroups(draft.server.manifest.sections[sectionIndex])[groupIndex];
    const heading = document.getElementById(`hf-group-${group.id}`);
    heading?.focus({ preventScroll: true });
    heading?.scrollIntoView({ block: 'start', behavior: 'instant' });
    return;
  }
  document.querySelector('.hf-editor h2')?.focus({ preventScroll: true });
  document.querySelector('.hf-editor')?.scrollIntoView({ block: 'start', behavior: 'instant' });
}
function sectionSelect() {
  const select = el('select', null, { class: 'hf-mobile-section', 'aria-label': 'Questionnaire section' });
  draft.server.manifest.sections.forEach((section, index) => {
    const option = el('option', `${index + 1}. ${section.title}`, { value: index });
    option.selected = index === draft.position.section; select.append(option);
  });
  select.addEventListener('change', () => move(Number(select.value)));
  return select;
}
function fieldControl(field) {
  const wrapper = el(field.kind === 'text' ? 'label' : 'fieldset', null, { class: 'hf-field', 'data-field': field.id });
  wrapper.append(el(field.kind === 'text' ? 'span' : 'legend', field.label));
  const value = draft.answers[field.id];
  if (field.kind === 'text') {
    const input = el(field.multiline ? 'textarea' : 'input', null, { 'aria-label': field.label, maxlength: field.max_length });
    if (!field.multiline) input.type = 'text';
    input.value = value || '';
    input.autocomplete = 'off';
    input.addEventListener('input', () => changed(field.id, input.value || null));
    input.addEventListener('focus', () => { root.dataset.pdfPage = field.widgets[0].page; renderPDF(); });
    wrapper.append(input);
  } else {
    const choices = el('div', null, { class: 'hf-choices' });
    for (const option of field.options) {
      const label = el('label', null, { class: 'hf-choice' });
      const input = el('input', null, { type: field.kind === 'multi' ? 'checkbox' : 'radio', name: field.id, value: option });
      input.checked = field.kind === 'multi' ? (value || []).includes(option) : value === option;
      input.addEventListener('change', () => changed(field.id, field.kind === 'multi'
        ? [...choices.querySelectorAll('input:checked')].map(i => i.value) : option));
      label.append(input, document.createTextNode(option)); choices.append(label);
    }
    wrapper.append(choices, button('Clear answer', () => { changed(field.id, null); wrapper.querySelectorAll('input').forEach(i => { i.checked = false; }); }, 'hf-clear'));
  }
  return wrapper;
}
function render() {
  app.replaceChildren(); app.setAttribute('aria-busy', 'false');
  if (recipient || draft.server.state === 'submitted') { renderReports(); return; }
  if (mode === 'review') { renderReview(); return; }
  const sections = draft.server.manifest.sections;
  draft.position.section = Math.min(draft.position.section, sections.length - 1);
  const section = sections[draft.position.section];
  const groups = visibleGroups(section);
  draft.position.group = Math.min(draft.position.group, groups.length - 1);
  const group = groups[draft.position.group];
  const layout = el('div', null, { class: 'hf-layout' });
  const nav = el('nav', null, { class: 'hf-sections', 'aria-label': 'Home Facts sections' });
  sections.forEach((s, i) => { const item = button(`${String(i + 1).padStart(2, '0')}  ${s.title}`, () => move(i), ''); item.setAttribute('aria-current', String(i === draft.position.section)); item.replaceChildren(el('span', String(i + 1).padStart(2, '0'), { class: 'hf-section-number' }), el('span', s.title)); nav.append(item); });
  nav.append(button('Review and submit', () => { mode = 'review'; render(); }, 'hf-link'));
  const editor = el('section', null, { class: 'hf-editor' });
  const sectionHeading = el('div', null, { class: 'hf-section-heading' });
  sectionHeading.append(el('span', String(draft.position.section + 1).padStart(2, '0'), { class: 'hf-section-index', 'aria-hidden': 'true' }));
  const title = el('div'); title.append(el('p', `SECTION ${draft.position.section + 1} OF ${sections.length}`, { class: 'hf-eyebrow' }), el('h2', section.title, { tabindex: '-1' }));
  sectionHeading.append(title); editor.append(sectionSelect(), sectionHeading);
  const progress = el('div', null, { class: 'hf-progress', 'aria-hidden': 'true' });
  sections.forEach((_, i) => progress.append(el('span', null, { class: i <= draft.position.section ? 'is-reached' : '' })));
  editor.append(progress);
  editor.append(el('p', section.help || 'Every answer is optional. Skip anything that does not apply, or write unsure.', { class: 'hf-context' }));
  if (draft.server.correction_note) {
    const note = el('aside', null, { class: 'hf-conflict' });
    note.append(el('h3', 'A note from your agent'), el('p', draft.server.correction_note)); editor.append(note);
  }
  const conflicts = conflictUI(); if (conflicts) editor.append(conflicts);
  for (const questionGroup of groups) {
    const block = el('section', null, { class: 'hf-question-group', 'aria-labelledby': `hf-group-${questionGroup.id}` });
    block.append(el('h3', questionGroup.title, { class: 'hf-group-title', id: `hf-group-${questionGroup.id}`, tabindex: '-1' }));
    for (const field of questionGroup.fields) block.append(fieldControl(field));
    editor.append(block);
  }
  const more = section.groups.find(g => g.optional_row && !groups.includes(g));
  if (more) editor.append(button('Add another', () => { expandedRows.add(more.id); move(draft.position.section, visibleGroups(section).findIndex(g => g.id === more.id)); }, 'hf-link'));
  const controls = el('div', null, { class: 'hf-controls' });
  const previous = button('Back', () => move(Math.max(0, draft.position.section - 1)));
  previous.setAttribute('aria-label', 'Previous section');
  previous.disabled = !draft.position.section;
  controls.append(previous);
  const next = button('Next', () => {
    if (draft.position.section + 1 < sections.length) move(draft.position.section + 1);
    else { mode = 'review'; render(); }
  }, 'hf-primary hf-next');
  next.replaceChildren(el('span', draft.position.section === sections.length - 1 ? 'Review answers' : 'Continue'), el('span', '→', { 'aria-hidden': 'true' }));
  controls.append(next);
  const utilities = el('div', null, { class: 'hf-utilities' });
  utilities.append(button('View original PDF', () => { previewOpen = true; document.querySelector('.hf-preview')?.classList.add('is-open'); renderPDF(); }, 'hf-link'), button('Review answers', () => { mode = 'review'; render(); }, 'hf-link'));
  editor.append(utilities, controls);
  const preview = el('aside', null, { class: `hf-preview${previewOpen ? ' is-open' : ''}`, 'aria-label': 'Original questionnaire preview' });
  const heading = el('div', null, { class: 'hf-preview-heading' });
  heading.append(el('p', 'Your PDF · Current device answers'), button('Close PDF', () => { previewOpen = false; preview.classList.remove('is-open'); }, 'hf-secondary hf-preview-close'));
  preview.append(heading, el('div', null, { id: 'hf-pdf-page', class: 'hf-pdf-page' }), el('p', 'Tap a field to answer it. Long answers print on continuation pages.', { class: 'hf-context' }));
  layout.append(nav, editor, preview); app.append(layout);
  root.dataset.pdfPage = group.fields[0].widgets[0].page;
  renderPDF();
}
function filesUI(container, artifacts) {
  const list = el('div', null, { class: 'hf-files' });
  for (const file of artifacts) {
    const row = el('div', null, { class: 'hf-file' });
    row.append(el('a', file.filename, { href: `${base}/files/${file.id}`, target: '_blank', rel: 'noopener' }));
    row.append(el('a', 'Download', { href: `${base}/files/${file.id}?download=1` })); list.append(row);
  }
  container.append(list);
}
function renderReview() {
  const view = el('section', null, { class: 'hf-review' });
  view.append(el('h1', 'Ready to share with your agent?'), el('p', 'Unanswered questions are fine. Review what you know, attach any helpful documents, and return your questionnaire.'));
  if (draft.server.correction_note) {
    const note = el('aside', null, { class: 'hf-conflict' });
    note.append(el('h3', 'A note from your agent'), el('p', draft.server.correction_note)); view.append(note);
  }
  const conflicts = conflictUI(); if (conflicts) view.append(conflicts);
  for (const [i, section] of draft.server.manifest.sections.entries()) {
    const details = el('details'); const answered = section.groups.flatMap(g => g.fields).filter(f => format(draft.answers[f.id]));
    details.append(el('summary', `${section.title} · ${answered.length ? `${answered.length} answers` : 'Skipped'}`));
    const list = el('dl');
    answered.forEach(field => list.append(el('dt', field.label), el('dd', format(draft.answers[field.id]))));
    const edit = button('Edit section', () => { mode = 'editor'; move(i); }, 'hf-link'); edit.disabled = submissionPending;
    details.append(list, edit); view.append(details);
  }
  filesUI(view, draft.server.artifacts.filter(a => a.kind === 'attachment'));
  const upload = el('label', 'Add receipts, warranties, manuals or other documents', { class: 'hf-upload' });
  const input = el('input', null, { type: 'file', accept: 'application/pdf,image/jpeg,image/png' });
  input.disabled = submissionPending;
  input.addEventListener('change', async () => {
    if (!input.files[0] || submissionPending || uploading) return;
    if (saving || draft.flight) { showError('Wait for your answers to finish saving, then add the file again.'); input.value = ''; return; }
    uploading = true;
    input.disabled = true;
    try {
      const file = input.files[0];
      const identity = new TextEncoder().encode(`${draft.server.id}\0${file.name}\0`);
      const bytes = new Uint8Array(await file.arrayBuffer());
      const source = new Uint8Array(identity.length + bytes.length); source.set(identity); source.set(bytes, identity.length);
      const hex = [...new Uint8Array(await crypto.subtle.digest('SHA-256', source))].slice(0,16).map(b => b.toString(16).padStart(2,'0')).join('');
      const mutation = `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20,32)}`;
      const form = new FormData(); form.set('file', file); form.set('mutation_id', mutation);
      const snapshot = await api('/actions/attachments', { method: 'POST', body: form }); draft.reconcile(snapshot); store(); render();
    }
    catch (error) { showError(error.message); input.disabled = false; }
    finally { uploading = false; save(); }
  }); upload.append(input); view.append(upload);
  const ack = el('label', null, { class: 'hf-ack' }); const checkbox = el('input', null, { type: 'checkbox' });
  ack.append(checkbox, el('span', draft.server.manifest.acknowledgment)); view.append(ack);
  const submit = button(submissionPending ? 'Check submission' : 'Submit Home Facts', async () => {
    if (uploading) { showError('Wait for your document to finish uploading before submitting.'); return; }
    submit.disabled = true; showError('');
    await save();
    if (saving || draft.flight || Object.keys(draft.pending).length || Object.keys(draft.conflicts).length) {
      showError('Finish saving and resolve any changed answers before submitting.'); submit.disabled = false; return;
    }
    try {
      const key = `${storageKey}:submit`;
      let payload; try { payload = JSON.parse(localStorage.getItem(key)); } catch { /* A fresh request follows. */ }
      if (!payload || payload.expected_revision !== draft.server.revision) payload = { mutation_id: crypto.randomUUID(), expected_revision: draft.server.revision, acknowledged: true };
      localStorage.setItem(key, JSON.stringify(payload));
      submissionPending = true;
      view.querySelectorAll('button,input,textarea,select').forEach(control => { control.disabled = true; });
      draft.reconcile(await api('/actions/submit', { method: 'POST', body: JSON.stringify(payload) }));
      localStorage.removeItem(key); localStorage.removeItem(storageKey); submissionPending = false; render();
    } catch (error) {
      if (error.httpStatus >= 400 && error.httpStatus < 500) { submissionPending = false; localStorage.removeItem(`${storageKey}:submit`); }
      showError(submissionPending ? 'Your submission is not confirmed yet. Check it again before changing your answers.' : error.message); render();
    }
  }, 'hf-primary');
  submit.disabled = !submissionPending; checkbox.addEventListener('change', () => { submit.disabled = !checkbox.checked && !submissionPending; });
  const keepAnswering = button('Keep answering', () => { mode = 'editor'; render(); }, 'hf-link');
  keepAnswering.disabled = submissionPending;
  view.append(submit, keepAnswering); app.append(view);
}
function renderReports() {
  const data = draft.server;
  const view = el('section', null, { class: 'hf-review' });
  view.append(el('h1', recipient ? 'Your property documents' : 'Your agent has your Home Facts'), el('p', recipient
    ? 'These are the exact documents selected for you. Contact your agent with any questions.'
    : 'Thank you. Your answers are saved. Your agent will review the Property Brief and Home Guide with you before sharing them.'));
  const artifacts = data.artifacts.filter(a => recipient || a.kind === 'attachment' || a.revision === data.submitted_revision);
  filesUI(view, artifacts);
  if (!recipient && data.artifact_hash && !data.seller_reviewed) {
    view.append(el('h2', 'Review your reports'), el('p', 'Open both the Appraiser Property Brief and Buyer Home Guide above. If something needs changing, contact your agent. Confirm when both reflect the information you shared.'));
    const confirm = button('I reviewed both reports', async () => {
      confirm.disabled = true;
      try { draft.reconcile(await api('/actions/review', { method: 'POST', body: JSON.stringify({ revision: data.submitted_revision, artifact_hash: data.artifact_hash }) })); render(); }
      catch (error) { showError(error.message); confirm.disabled = false; }
    }, 'hf-primary'); view.append(confirm);
  } else if (data.seller_reviewed) view.append(el('p', 'Your review is recorded for this report version.'));
  if (!recipient && !data.artifact_hash) view.append(el('p', 'Your reports are being prepared. You can close this page and return later.'));
  app.append(view);
  clearTimeout(reportTimer);
  if (!recipient && !data.artifact_hash) reportTimer = setTimeout(async () => {
    try { draft.reconcile(await api()); render(); }
    catch { renderReports(); }
  }, 3000);
}
async function renderPDF() {
  const mount = document.getElementById('hf-pdf-page');
  if (!mount || !mount.offsetWidth || !draft) return;
  const operation = ++pdfVersion;
  try {
    const [{ PDFDocument, StandardFonts }, pdfjs, worker] = await Promise.all([import('pdf-lib'), import('pdfjs-dist'), import('pdfjs-dist/build/pdf.worker.min.mjs?url')]);
    pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
    if (!pdfBytes) {
      const cache = await caches.open('home-facts-templates-v1');
      const url = draft.server.template_url || `/home-facts/template/${draft.server.schema_version}.pdf`;
      let response = await cache.match(url);
      if (!response) { response = await fetch(url); if (!response.ok) throw new Error('Could not load the PDF.'); await cache.put(url, response.clone()); }
      pdfBytes = await response.arrayBuffer();
      const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', pdfBytes))].map(b => b.toString(16).padStart(2, '0')).join('');
      if (hash !== draft.server.manifest.template_sha256) { pdfBytes = null; await cache.delete(url); throw new Error('The template changed. Refresh to load its current version.'); }
    }
    const pdfDocument = await PDFDocument.load(pdfBytes.slice(0)); const form = pdfDocument.getForm();
    const font = await pdfDocument.embedFont(StandardFonts.Helvetica);
    const definitions = draft.server.manifest.sections.flatMap(s => s.groups.flatMap(g => g.fields));
    for (const field of definitions) for (const widget of field.widgets) {
      const value = draft.answers[field.id];
      if (field.kind === 'text') {
        const input = form.getTextField(widget.name); let text = format(value);
        input.acroField.setDefaultAppearance('/Helv 8 Tf 0 g');
        // The server prints full overflow on continuation pages. Keep this
        // navigation preview legible, and retain full answers in the editor.
        if (text.length > Math.max(20, Math.floor((widget.rect[2] - widget.rect[0]) / 5))) text = 'See full answer in editor';
        input.setText(text); input.setFontSize(8);
      } else {
        const checked = Array.isArray(value) ? value.includes(widget.option) : value === widget.option;
        const input = form.getCheckBox(widget.name); checked ? input.check() : input.uncheck();
      }
    }
    form.updateFieldAppearances(font); form.flatten();
    const documentBytes = await pdfDocument.save();
    const pdf = await pdfjs.getDocument({ data: documentBytes, isEvalSupported: false }).promise;
    const number = Number(root.dataset.pdfPage || 1); const page = await pdf.getPage(number);
    const viewport = page.getViewport({ scale: mount.offsetWidth / page.getViewport({ scale: 1 }).width });
    const canvas = document.createElement('canvas'); const ratio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = viewport.width * ratio; canvas.height = viewport.height * ratio;
    canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`;
    await page.render({ canvasContext: canvas.getContext('2d'), viewport, transform: [ratio, 0, 0, ratio, 0, 0] }).promise;
    if (operation !== pdfVersion || !mount.isConnected) { await pdf.destroy(); return; }
    mount.replaceChildren(canvas);
    definitions.forEach(field => field.widgets.filter(w => w.page === number).forEach(widget => {
      const [x1, y1, x2, y2] = viewport.convertToViewportRectangle(widget.rect);
      const target = button('', () => {
        draft.server.manifest.sections.forEach((s, si) => s.groups.forEach(g => { if (!g.fields.some(f => f.id === field.id)) return; expandedRows.add(g.id); previewOpen = false; move(si, visibleGroups(s).findIndex(v => v.id === g.id)); }));
        requestAnimationFrame(() => document.querySelector(`[data-field="${field.id}"] input, [data-field="${field.id}"] textarea`)?.focus());
      }, 'hf-pdf-hit');
      target.setAttribute('aria-label', `Answer ${field.label}`); Object.assign(target.style, { left: `${Math.min(x1, x2)}px`, top: `${Math.min(y1, y2)}px`, width: `${Math.abs(x2 - x1)}px`, height: `${Math.abs(y2 - y1)}px` }); mount.append(target);
    }));
    await pdf.destroy();
  } catch (error) { if (operation === pdfVersion && mount.isConnected) mount.replaceChildren(el('p', 'PDF preview is unavailable. Your answers are safe in the editor.'), button('Try again', renderPDF)); }
}
new ResizeObserver(() => { clearTimeout(pdfTimer); pdfTimer = setTimeout(renderPDF, 250); }).observe(app);

async function start() {
  try {
    const snapshot = await api();
    let saved; try { saved = JSON.parse(localStorage.getItem(storageKey) || '{}'); } catch { saved = {}; }
    if (saved.schema_version && (saved.schema_version !== snapshot.schema_version || saved.template_sha256 !== snapshot.manifest?.template_sha256)) {
      throw new Error('This questionnaire template changed while you had a device draft. Keep this page open and contact your agent so those answers can be recovered.');
    }
    draft = new Draft(snapshot, saved);
    submissionPending = snapshot.state === 'draft' && !!localStorage.getItem(`${storageKey}:submit`);
    if (submissionPending) mode = 'review';
    render(); if (!recipient && snapshot.state === 'draft') save();
  } catch (error) { showError(error.message); app.replaceChildren(button('Try again', start)); }
}
start();
