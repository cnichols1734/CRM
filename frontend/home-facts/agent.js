const root = document.querySelector('[data-home-facts-agent]');
const app = document.getElementById('hf-app');
const status = document.getElementById('hf-save-status');
const errorBox = document.getElementById('hf-error');
const base = `/transactions/${root.dataset.transactionId}/home-facts`;
let data, inFlight = false;
const pendingKey = `home-facts:agent:${root.dataset.orgId}:${root.dataset.ownerId}:${root.dataset.transactionId}`;
let pending;
try { pending = JSON.parse(sessionStorage.getItem(pendingKey)); } catch { pending = null; }
const savedInputs = {};
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

function input(label, id, multiline = false) {
  const wrapper = el('label', label); const field = el(multiline ? 'textarea' : 'input', null, { id, type: 'text' });
  field.value = savedInputs[id] || ''; field.addEventListener('input', () => { savedInputs[id] = field.value; }); wrapper.append(field); return [wrapper, field];
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
  actions.append(el('h2', 'Send to the seller'), el('p', 'The seller receives a private email link and a ClientFlow notification. Every questionnaire answer is optional.'));
  const participants = el('div');
  for (const participant of data.participants) { const label = el('label', null, { class: 'hf-choice' }); const check = el('input', null, { type: 'checkbox', value: participant.id }); check.checked = data.participants.length === 1; check.disabled = !participant.email; label.append(check, document.createTextNode(`${participant.name} · ${participant.email || 'Add an email to invite'}`)); participants.append(label); }
  actions.append(participants);
  const selectedParticipants = () => [...participants.querySelectorAll('input:checked')].map(n => Number(n.value));
  const invite = button('Send questionnaire', () => action('invite', { participant_ids: selectedParticipants() }, invite), true); invite.disabled = !!pending || inFlight || !data.participants.some(p => p.email); actions.append(invite);
  actions.append(el('p', 'Email includes the property address, one clear action button, browser and ClientFlow instructions, and your reply-to address. A resend creates a new private link.'));
  if (data.state === 'submitted') {
    const [wrapper, note] = input('Changes needed', 'reopen-note', true); actions.append(el('h2', 'Request corrections'), wrapper);
    const reopen = button('Ask seller to update answers', () => action('reopen', { note: note.value }, reopen)); actions.append(reopen);
  }
  actions.append(el('h2', 'Seller answers'));
  for (const section of data.manifest.sections) {
    const details = el('details'); const fields = section.groups.flatMap(g => g.fields).filter(f => data.answers[f.id] != null);
    details.append(el('summary', `${section.title} · ${fields.length} answers`));
    const list = el('dl');
    for (const field of fields) { list.append(el('dt', field.label), el('dd', Array.isArray(data.answers[field.id]) ? data.answers[field.id].join(', ') : data.answers[field.id])); }
    details.append(list); actions.append(details);
  }
  reports.append(el('h2', 'Documents and report review'));
  const current = data.artifacts.filter(a => a.kind === 'attachment' || a.revision === data.submitted_revision);
  const selected = el('div', null, { class: 'hf-files' });
  for (const artifact of current) {
    const row = el('div', null, { class: 'hf-file' }); const label = el('label');
    const check = el('input', null, { type: 'checkbox', value: artifact.id }); check.disabled = artifact.kind === 'questionnaire';
    label.append(check, document.createTextNode(` ${artifact.filename}`));
    row.append(label, el('a', 'Open', { href: `${base}/files/${artifact.id}`, target: '_blank', rel: 'noopener' })); selected.append(row);
  }
  reports.append(selected);
  reports.append(el('p', 'The completed questionnaire is private to your transaction. Buyer guides exclude improvement cost fields and showing details. Review attachments separately for private information.'));
  if (data.artifact_hash && data.state === 'submitted') {
    reports.append(el('p', `${data.agent_reviewed ? 'Agent approved' : 'Agent approval needed'} · ${data.seller_reviewed ? 'Seller review recorded' : 'Seller review needed'}`));
    const sellerReview = button('Request seller review', () => action('request_review', { participant_ids: selectedParticipants() }, sellerReview)); reports.append(sellerReview);
    const [wrapper, offline] = input('If reviewed together outside the app, record when and how', 'offline-review', true); reports.append(wrapper);
    const approve = button('Approve this report version', () => action('review', { revision: data.submitted_revision, artifact_hash: data.artifact_hash, ...(offline.value.trim() ? { offline_note: offline.value.trim() } : {}) }, approve)); reports.append(approve);
    reports.append(el('h2', 'Email selected documents'));
    const [toWrapper, to] = input('Recipients, separated by commas', 'share-recipients'); const [messageWrapper, message] = input('Personal message', 'share-message', true); reports.append(toWrapper, messageWrapper);
    const attachmentLabel = el('label'); const attachmentsReviewed = el('input', null, { type: 'checkbox' }); attachmentLabel.append(attachmentsReviewed, document.createTextNode(' I reviewed selected attachments for private information and improvement costs.')); reports.append(attachmentLabel);
    const preview = el('div', null, { class: 'hf-conflict', hidden: '' });
    const reviewSend = button('Review email', () => {
      const ids = [...selected.querySelectorAll('input:checked')].map(n => n.value); const recipients = to.value.split(',').map(v => v.trim()).filter(Boolean);
      if (!ids.length || !recipients.length) { error('Select documents and add at least one recipient.'); return; }
      preview.replaceChildren(el('h3', 'Confirm this email'), el('p', `Subject: Your property documents are ready | ${data.property_address}`), el('p', `Separate private email to: ${recipients.join(', ')}`), el('p', current.filter(a => ids.includes(a.id)).map(a => a.filename).join(' · ')), el('p', message.value), el('p', 'The email uses your Origen template with a secure View property documents button. Link and download activity is recorded; email opens are not proof someone read it.'));
      const payload = { recipients, message: message.value, artifact_ids: ids, artifact_hash: data.artifact_hash, attachments_reviewed: attachmentsReviewed.checked };
      const send = button('Send these documents', () => action('share', payload, send), true); preview.append(send); preview.hidden = false;
    }, true); reviewSend.disabled = !!pending || inFlight || !(data.agent_reviewed && data.seller_reviewed); reports.append(reviewSend, preview);
  } else if (data.state === 'submitted') {
    reports.append(el('p', 'Reports are being prepared.'));
    const retry = button('Retry report generation', () => action('retry_generation', {}, retry)); reports.append(retry);
  }
  if (data.grants.length) {
    reports.append(el('h2', 'Private links'));
    for (const grant of data.grants) {
      const row = el('div', null, { class: 'hf-file' }); row.append(el('span', `${grant.email} · ${grant.audience === 'seller' ? 'Seller' : 'Recipient'}${grant.revoked ? ' · Revoked' : ''}`));
      if (!grant.revoked) { const revoke = button('Revoke', () => action('revoke', { grant_id: grant.id }, revoke)); row.append(revoke); } reports.append(row);
    }
  }
  reports.append(el('h2', 'Activity'));
  const timeline = el('ol', null, { class: 'hf-activity' });
  const labels = { created: 'Questionnaire set up', saved: 'Answers saved', submitted: 'Seller submitted', email_queued: 'Email queued', email_accepted: 'Email accepted by provider', reports_ready: 'Reports generated', reports_reviewed: 'Report review recorded', download_requested: 'Document access requested', opened: 'Private link activated', changes_requested: 'Changes requested', access_revoked: 'Link revoked', sharing_approved: 'Documents approved for sharing', delivered: 'Email delivered', bounce: 'Email bounced', open: 'Email open recorded, possibly automated' };
  for (const item of data.activity) { const row = el('li', `${labels[item.kind] || item.kind.replaceAll('_', ' ')}${item.details.recipient ? ` · ${item.details.recipient}` : ''}`); row.append(el('time', new Date(item.at).toLocaleString())); timeline.append(row); }
  reports.append(timeline);
  for (const job of data.jobs.filter(j => ['failed', 'ambiguous'].includes(j.state))) reports.append(el('p', `${job.kind}: ${job.error || job.state}`, { role: 'status' }));
  const refresh = button('Refresh status', load); reports.append(refresh); layout.append(actions, reports); app.append(layout);
}
async function load() { try { data = await api(); render(); } catch (failure) { error(failure.message); } }
load();
