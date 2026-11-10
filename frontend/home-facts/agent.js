const root = document.querySelector('[data-home-facts-agent]');
const app = document.getElementById('hf-app');
const status = document.getElementById('hf-save-status');
const errorBox = document.getElementById('hf-error');
const base = `/transactions/${root.dataset.transactionId}/home-facts`;
let data, inFlight = false;
const pendingKey = `home-facts:agent:${root.dataset.orgId}:${root.dataset.ownerId}:${root.dataset.transactionId}`;
let pending;
try { pending = JSON.parse(sessionStorage.getItem(pendingKey)); } catch { pending = null; }
function el(tag, text, attrs = {}) { const node = document.createElement(tag); if (text != null) node.textContent = text; Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v)); return node; }
function button(text, action, primary = false) { const node = el('button', text, { type: 'button', class: primary ? 'crm-btn crm-btn-primary' : 'crm-btn crm-btn-secondary' }); node.addEventListener('click', action); node.disabled = !!pending || inFlight; return node; }
function error(message) { errorBox.hidden = !message; errorBox.textContent = message || ''; }
async function api(path = '/data', body) {
  const result = await fetch(base + path, { credentials: 'same-origin', method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf }, body: body ? JSON.stringify(body) : undefined });
  const value = await result.json(); if (!result.ok) { const failure = new Error(value.error || 'Could not complete this action.'); failure.status = result.status; throw failure; } return value;
}
async function action(operation, values, control) {
  if (inFlight) return;
  const command = pending || { operation, payload: { ...values, mutation_id: crypto.randomUUID() } };
  try { sessionStorage.setItem(pendingKey, JSON.stringify(command)); }
  catch { error('This browser could not save the request. Try another browser before sending.'); return; }
  pending = command; inFlight = true; error(''); render();
  try {
    data = await api(`/actions/${command.operation}`, command.payload);
    pending = null; sessionStorage.removeItem(pendingKey);
  } catch (failure) {
    if (failure.status >= 400 && failure.status < 500) { pending = null; sessionStorage.removeItem(pendingKey); }
    error(failure.message);
  } finally { inFlight = false; render(); }
}

function render() {
  app.replaceChildren();
  if (pending) {
    const recovery = el('div', null, { class: 'hf-conflict' });
    recovery.append(el('p', inFlight ? 'Saving your request…' : 'The previous request has not been confirmed. Check it before sending anything else.'));
    const retry = button('Check previous request', () => action(pending.operation, pending.payload));
    retry.disabled = inFlight; recovery.append(retry); app.append(recovery);
  }
  if (data.available === false) {
    status.textContent = 'Not started'; app.append(el('p', 'Send the preserved Home Facts questionnaire to a seller. Their answers stay with this transaction and generate the Property Brief and Buyer Home Guide.'));
    const create = button('Set up Home Facts', async () => { create.disabled = true; try { data = await api('/data', {}); render(); } catch (failure) { error(failure.message); create.disabled = false; } }, true); app.append(create); return;
  }
  status.textContent = data.state === 'submitted' ? 'Submitted' : Object.keys(data.answers).length ? 'In progress' : data.grants.some(g => g.audience === 'seller') ? 'Awaiting seller' : 'Ready to send';
  const layout = el('div', null, { class: 'hf-agent-grid' }); const actions = el('section'); const reports = el('section');
  if (data.state !== 'submitted') {
    actions.append(el('h2', 'Send to the seller'), el('p', 'The seller receives a private email link and a ClientFlow notification. Every questionnaire answer is optional.'));
    const participants = el('div');
    for (const participant of data.participants) { const label = el('label', null, { class: 'hf-choice' }); const check = el('input', null, { type: 'checkbox', value: participant.id }); check.checked = data.participants.length === 1; check.disabled = !participant.email; label.append(check, document.createTextNode(`${participant.name} · ${participant.email || 'Add an email to invite'}`)); participants.append(label); }
    actions.append(participants);
    const selectedParticipants = () => [...participants.querySelectorAll('input:checked')].map(n => Number(n.value));
    const invite = button('Send questionnaire', () => action('invite', { participant_ids: selectedParticipants() }, invite), true); invite.disabled = !!pending || inFlight || !data.participants.some(p => p.email); actions.append(invite);
    actions.append(el('p', 'Email includes the property address, one clear action button, browser and ClientFlow instructions, and your reply-to address. A resend creates a new private link.'));
  }
  actions.append(el('h2', 'Seller answers'));
  for (const section of data.manifest.sections) {
    const details = el('details'); const fields = section.groups.flatMap(g => g.fields).filter(f => data.answers[f.id] != null);
    details.append(el('summary', `${section.title} · ${fields.length} answers`));
    const list = el('dl');
    for (const field of fields) { list.append(el('dt', field.label), el('dd', Array.isArray(data.answers[field.id]) ? data.answers[field.id].join(', ') : data.answers[field.id])); }
    details.append(list); actions.append(details);
  }
  reports.append(el('h2', 'Documents'));
  const current = data.artifacts.filter(a => a.kind === 'attachment' || a.revision === data.submitted_revision);
  const selected = el('div', null, { class: 'hf-files' });
  for (const artifact of current) {
    const row = el('div', null, { class: 'hf-file' });
    row.append(el('span', artifact.filename), el('a', artifact.content_type === 'application/pdf' ? 'View PDF' : 'View file', { href: `${base}/files/${artifact.id}`, target: '_blank', rel: 'noopener' })); selected.append(row);
  }
  reports.append(selected);
  if (data.state === 'submitted' && !data.artifact_hash) {
    reports.append(el('p', 'Reports are being prepared.'));
    if (data.jobs.some(job => job.kind === 'generate' && job.state === 'failed')) {
      const retry = button('Retry report generation', () => action('retry_generation', {}, retry)); reports.append(retry);
    }
  } else if (data.state !== 'submitted') {
    reports.append(el('p', 'The completed questionnaire, Appraiser Property Brief and Buyer Home Guide appear here after the seller submits.'));
  }
  if (data.state !== 'submitted' && data.grants.length) {
    reports.append(el('h2', 'Private links'));
    for (const grant of data.grants) {
      const row = el('div', null, { class: 'hf-file' }); row.append(el('span', `${grant.email} · ${grant.audience === 'seller' ? 'Seller' : 'Recipient'}${grant.revoked ? ' · Revoked' : ''}`));
      if (!grant.revoked) { const revoke = button('Revoke', () => action('revoke', { grant_id: grant.id }, revoke)); row.append(revoke); } reports.append(row);
    }
  }
  reports.append(el('h2', 'Activity'));
  const timeline = el('ol', null, { class: 'hf-activity', tabindex: '0', 'aria-label': 'Home Facts activity' });
  const labels = { created: 'Questionnaire set up', submitted: 'Seller submitted', email_queued: 'Email queued', email_accepted: 'Email accepted by provider', reports_ready: 'Reports generated', reports_reviewed: 'Report review recorded', download_requested: 'Document access requested', opened: 'Private link activated', changes_requested: 'Changes requested', access_revoked: 'Link revoked', sharing_approved: 'Documents approved for sharing', delivered: 'Email delivered', bounce: 'Email bounced', open: 'Email open recorded, possibly automated' };
  for (const item of data.activity.filter(item => item.kind !== 'saved')) { const row = el('li', `${labels[item.kind] || item.kind.replaceAll('_', ' ')}${item.details.recipient ? ` · ${item.details.recipient}` : ''}`); row.append(el('time', new Date(item.at).toLocaleString())); timeline.append(row); }
  reports.append(timeline);
  for (const job of data.jobs.filter(j => ['failed', 'ambiguous'].includes(j.state))) reports.append(el('p', `${job.kind}: ${job.error || job.state}`, { role: 'status' }));
  const refresh = button('Refresh status', load); reports.append(refresh); layout.append(actions, reports); app.append(layout);
}
async function load() { try { data = await api(); render(); } catch (failure) { error(failure.message); } }
load();
