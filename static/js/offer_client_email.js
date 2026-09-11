/**
 * Client offer email composer.
 *
 * Shared by the Offers panel and Compare. The preview is the email itself,
 * rendered server-side and dropped into an iframe, so what the agent
 * approves is what the client gets.
 */

const OCE_COPY_FIELDS = ['subject', 'greeting', 'intro', 'note', 'closing'];
const OCE_FIGURE_LABELS = {
    offer_price: 'Price',
    financing_type: 'Financing',
    earnest_money: 'Earnest money',
    option_period: 'Option period',
    proposed_close_date: 'Closing date',
    seller_concessions_amount: 'Seller contributions',
    buyer_agent_commission: "Commission to buyer's agent",
    survey_responsibility: 'Who pays for the survey',
    residential_service_contract: 'Home warranty',
    title_policy_payer: 'Title policy paid by',
    sale_of_other_property: 'Contingent on buyer selling another property',
    non_realty_items: 'Non-realty items the buyer is asking for'
};
const OCE_LONG_FIGURES = ['non_realty_items'];

const offerClientEmail = {
    offerIds: [],
    dirty: {},
    terms: {},
    timer: null,
    sending: false,
    bound: false,
    paintedFor: null
};

function oceTransactionId() {
    return window.TX_CONFIG && window.TX_CONFIG.transactionId;
}

function oceOpenSheet(id) {
    if (typeof openTxSheet === 'function') {
        openTxSheet(id);
        return;
    }
    const shell = document.getElementById(id);
    if (!shell) return;
    if (window.TMotion) TMotion.openSheet(shell, shell.querySelector('.t-panel-slide'));
    else shell.classList.remove('hidden');
}

function oceCloseSheet(id, after) {
    if (typeof closeTxSheet === 'function') {
        closeTxSheet(id, after);
        return;
    }
    const shell = document.getElementById(id);
    if (!shell) return;
    if (window.TMotion) TMotion.closeSheet(shell, shell.querySelector('.t-panel-slide'), after);
    else {
        shell.classList.add('hidden');
        if (after) after();
    }
}

function oceToast(message, type) {
    if (typeof showToast === 'function') {
        showToast(message, type);
        return;
    }
    if (typeof window.showCrmToast === 'function') {
        window.showCrmToast(message, type);
    }
}

function oceRoot() {
    return document.getElementById('offerClientEmailModal');
}

function oceEl(name) {
    const root = oceRoot();
    return root ? root.querySelector(`[data-oce-${name}]`) : null;
}

function oceOfferIds(offerId) {
    if (Array.isArray(offerId)) {
        return offerId.map(Number).filter((id) => Number.isFinite(id));
    }
    return offerId ? [Number(offerId)] : [];
}

function openOfferClientEmail(offerId) {
    const root = oceRoot();
    if (!root) return;

    offerClientEmail.offerIds = oceOfferIds(offerId);
    offerClientEmail.dirty = {};
    offerClientEmail.terms = {};
    offerClientEmail.sending = false;
    offerClientEmail.paintedFor = null;

    if (root.parentElement !== document.body) {
        document.body.appendChild(root);
    }

    ['to', 'cc'].forEach((name) => {
        const field = oceEl(name);
        if (field) field.value = '';
    });
    const status = oceEl('status');
    if (status) status.textContent = '';

    oceOpenSheet('offerClientEmailModal');
    document.body.classList.add('overflow-hidden');
    oceBindEvents();
    oceRefresh({ initial: true });
}

function closeOfferClientEmail() {
    const root = oceRoot();
    if (!root) return;
    oceCloseSheet('offerClientEmailModal', function () {
        document.body.classList.remove('overflow-hidden');
        if (offerClientEmail.timer) {
            clearTimeout(offerClientEmail.timer);
            offerClientEmail.timer = null;
        }
    });
}

function oceBindEvents() {
    if (offerClientEmail.bound) return;
    const root = oceRoot();
    if (!root) return;

    const onChange = (event) => {
        const target = event.target;
        if (!target) return;

        const copyField = OCE_COPY_FIELDS.find((name) => target.hasAttribute(`data-oce-${name}`));
        if (copyField) {
            offerClientEmail.dirty[copyField] = target.value.trim() !== '';
            oceScheduleRefresh();
            return;
        }
        if (target.hasAttribute('data-oce-figure')) {
            const offerId = target.getAttribute('data-oce-figure-offer');
            const key = target.getAttribute('data-oce-figure');
            if (!offerClientEmail.terms[offerId]) offerClientEmail.terms[offerId] = {};
            offerClientEmail.terms[offerId][key] = target.value;
            oceScheduleRefresh();
            return;
        }
        if (target.hasAttribute('data-oce-pick')) {
            offerClientEmail.offerIds = Array.from(
                root.querySelectorAll('[data-oce-pick]:checked')
            ).map((box) => Number(box.value));
            oceRefresh({});
        }
    };

    root.addEventListener('input', onChange);
    root.addEventListener('change', onChange);
    offerClientEmail.bound = true;
}

function oceScheduleRefresh() {
    if (offerClientEmail.timer) clearTimeout(offerClientEmail.timer);
    offerClientEmail.timer = setTimeout(() => oceRefresh({}), 450);
}

function oceOverrides() {
    const payload = { offer_ids: offerClientEmail.offerIds };

    OCE_COPY_FIELDS.forEach((name) => {
        if (!offerClientEmail.dirty[name]) return;
        const field = oceEl(name);
        if (field) payload[name] = field.value;
    });
    const note = oceEl('note');
    if (note) payload.note = note.value;

    const terms = {};
    Object.keys(offerClientEmail.terms).forEach((offerId) => {
        if (!offerClientEmail.offerIds.length || offerClientEmail.offerIds.includes(Number(offerId))) {
            terms[offerId] = offerClientEmail.terms[offerId];
        }
    });
    if (Object.keys(terms).length) payload.terms = terms;

    return payload;
}

function oceRefresh({ initial }) {
    const loading = oceEl('loading');
    if (loading) loading.classList.replace('hidden', 'flex');

    return fetch(`/transactions/${oceTransactionId()}/offers/client-email/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(oceOverrides())
    })
        .then((res) => res.json())
        .then((data) => {
            if (!data.success) throw new Error(data.error || 'Could not build the email');
            offerClientEmail.offerIds = data.draft.offer_ids || [];
            const key = offerClientEmail.offerIds.join(',');
            oceFillCopy(data.draft, initial);
            if (key !== offerClientEmail.paintedFor) {
                offerClientEmail.paintedFor = key;
                ocePaintPicker(data.candidates);
                ocePaintFigures(data.draft);
            }
            if (initial) oceFillRecipients(data.draft);
            oceSetTitle(data.draft);
            ocePaintSender(data.sender);
            ocePaintPreview(data.html);
            return data;
        })
        .catch((err) => {
            oceToast(err.message || 'Could not build the email', 'error');
        })
        .finally(() => {
            if (loading) loading.classList.replace('flex', 'hidden');
        });
}

function oceFillCopy(draft, initial) {
    OCE_COPY_FIELDS.forEach((name) => {
        if (name === 'note') return;
        if (!initial && offerClientEmail.dirty[name]) return;
        const field = oceEl(name);
        if (field) field.value = draft[name] || '';
    });
    if (initial) {
        const note = oceEl('note');
        if (note) note.value = draft.note || '';
    }
}

function oceFillRecipients(draft) {
    const field = oceEl('to');
    const hint = oceEl('to-hint');
    const recipients = draft.recipients || [];
    if (field) field.value = recipients.map((r) => r.email).join(', ');
    if (!hint) return;
    if (recipients.length) {
        hint.textContent = recipients
            .map((r) => r.name)
            .filter(Boolean)
            .join(', ');
    } else {
        hint.textContent = 'No client email on file for this transaction. Type one above.';
    }
}

function oceSetTitle(draft) {
    const title = oceEl('title');
    if (!title) return;
    const count = (draft.offer_ids || []).length;
    title.textContent = count > 1 ? `Comparing ${count} offers` : 'Offer summary';
}

function ocePaintSender(sender) {
    const el = oceEl('from');
    if (!el) return;
    if (!sender || !sender.from_email) {
        el.textContent = '';
        return;
    }
    if (sender.via === 'gmail') {
        el.textContent = `Sends from ${sender.from_email}`;
        return;
    }
    el.textContent = `Sends from ${sender.from_email}. Connect Gmail in Profile to send as you.`;
}

function ocePaintPicker(candidates) {
    const wrap = oceEl('picker-wrap');
    const list = oceEl('picker');
    if (!wrap || !list) return;
    if (!candidates || candidates.length < 2) {
        wrap.classList.add('hidden');
        return;
    }
    wrap.classList.remove('hidden');
    list.innerHTML = candidates
        .map((candidate) => `
            <label class="flex items-center gap-2 text-sm text-[color:var(--ink-2)]">
                <input type="checkbox" data-oce-pick value="${candidate.offer_id}" ${candidate.selected ? 'checked' : ''}>
                <span class="min-w-0 flex-1 truncate">${oceEscape(candidate.label)}</span>
                <span class="shrink-0 text-xs text-[color:var(--ink-3)]">${oceEscape(candidate.price)}</span>
            </label>`)
        .join('');
}

function ocePaintFigures(draft) {
    const list = oceEl('figures');
    if (!list) return;
    const single = (draft.offers || []).length === 1;

    list.innerHTML = (draft.offers || [])
        .map((offer) => {
            const rows = Object.keys(OCE_FIGURE_LABELS)
                .map((key) => {
                    const cell = offer.terms[key] || {};
                    if (OCE_LONG_FIGURES.includes(key)) {
                        return `
                        <label class="block sm:col-span-2">
                            <span class="mb-1 block text-xs font-medium text-slate-700">${OCE_FIGURE_LABELS[key]}</span>
                            <textarea class="crm-textarea w-full"
                                      rows="3"
                                      data-oce-figure="${key}"
                                      data-oce-figure-offer="${offer.offer_id}"
                                      placeholder="One item per line. Leave blank if there is no addendum.">${oceEscape(cell.value || '')}</textarea>
                        </label>`;
                    }
                    return `
                        <label class="block">
                            <span class="mb-1 block text-xs font-medium text-slate-700">${OCE_FIGURE_LABELS[key]}</span>
                            <input type="text"
                                   class="crm-input"
                                   data-oce-figure="${key}"
                                   data-oce-figure-offer="${offer.offer_id}"
                                   value="${oceEscape(cell.value || '')}"
                                   placeholder="Not set">
                        </label>`;
                })
                .join('');
            return `
                <details class="rounded-md px-3 py-2" style="border: 1px solid var(--hairline);" ${single ? 'open' : ''}>
                    <summary class="cursor-pointer text-sm font-medium text-[color:var(--ink-2)]">${oceEscape(offer.label)}</summary>
                    <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">${rows}</div>
                </details>`;
        })
        .join('');
}

function ocePaintPreview(html) {
    const frame = oceEl('preview');
    if (!frame) return;
    let scroll = 0;
    try {
        scroll = frame.contentWindow ? frame.contentWindow.scrollY : 0;
    } catch (err) {
        scroll = 0;
    }
    frame.onload = () => {
        try {
            if (scroll) frame.contentWindow.scrollTo(0, scroll);
        } catch (err) {
            /* cross-document access can fail during teardown */
        }
    };
    frame.srcdoc = html;
}

function sendOfferClientEmail() {
    if (offerClientEmail.sending) return;
    const button = oceEl('send');
    const status = oceEl('status');
    const to = (oceEl('to') || {}).value || '';
    if (!to.trim()) {
        oceToast('Add who this email goes to.', 'error');
        return;
    }

    offerClientEmail.sending = true;
    if (button) button.disabled = true;
    if (status) status.textContent = ' · Sending';

    const payload = Object.assign(oceOverrides(), {
        to: to,
        cc: (oceEl('cc') || {}).value || ''
    });

    fetch(`/transactions/${oceTransactionId()}/offers/client-email/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
        .then((res) => res.json())
        .then((data) => {
            if (!data.success) throw new Error(data.error || 'Could not send the email');
            oceToast(data.message || 'Email sent.', data.skipped ? 'info' : 'success');
            closeOfferClientEmail();
        })
        .catch((err) => {
            oceToast(err.message || 'Could not send the email', 'error');
            if (status) status.textContent = '';
        })
        .finally(() => {
            offerClientEmail.sending = false;
            if (button) button.disabled = false;
        });
}

function oceEscape(value) {
    return String(value === null || value === undefined ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
