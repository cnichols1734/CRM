/**
 * Transaction Detail - Core functionality
 * Handles: tabs, status, modals, participants, document management, toast notifications
 */

const transactionId = TX_CONFIG.transactionId;
const SELLER_WORKSPACE_TAB_KEY = `sellerWorkspaceTab:${transactionId}`;

function setSellerWorkspaceReloadTab(tabName) {
    if (!tabName) return;
    sessionStorage.setItem(SELLER_WORKSPACE_TAB_KEY, tabName);
}

function getActiveSellerWorkspaceTab() {
    const activeTab = document.querySelector('[id^="seller-tab-"].is-active');
    return activeTab ? activeTab.id.replace('seller-tab-', '') : null;
}

function restoreSellerWorkspaceTab() {
    let savedTab = sessionStorage.getItem(SELLER_WORKSPACE_TAB_KEY);
    if (savedTab === 'overview') savedTab = 'listing';
    if (savedTab && document.getElementById(`seller-panel-${savedTab}`)) {
        sellerWorkspaceTab(savedTab, { persist: false });
    }
}

// =============================================================================
// TAB SWITCHING (Buyer Transactions)
// =============================================================================

function switchTab(tabName) {
    // Update tab buttons (crm-segment items use is-active modifier)
    document.querySelectorAll('.crm-segment__item').forEach(btn => btn.classList.remove('is-active'));
    const tabBtn = document.getElementById('tab-' + tabName);
    if (tabBtn) tabBtn.classList.add('is-active');

    // Update tab content panels (still use .tab-content / .active toggled by inline CSS)
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    const content = document.getElementById('content-' + tabName);
    if (content) content.classList.add('active');
}

// =============================================================================
// PAGE LOAD ACTIONS
// =============================================================================

// Check for prompt_add_participant flag (after creating contact from add participant modal)
document.addEventListener('DOMContentLoaded', function() {
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('prompt_add_participant') === '1') {
        // Show toast after a brief delay
        setTimeout(() => {
            showToast('Contact created! You can now add them as a participant.', 'success');
        }, 500);

        // Clean up URL without reloading
        const cleanUrl = window.location.pathname;
        window.history.replaceState({}, document.title, cleanUrl);
    }

    restoreSellerWorkspaceTab();
    startSellerOfferExtractionPolling();
});

// =============================================================================
// STATUS DROPDOWN
// =============================================================================

function toggleStatusDropdown(event) {
    event.stopPropagation();
    const menu = document.getElementById('statusDropdownMenu');
    if (menu) menu.classList.toggle('hidden');
}

// Close dropdown when clicking outside
document.addEventListener('click', function(event) {
    const dropdown = document.getElementById('statusDropdown');
    const menu = document.getElementById('statusDropdownMenu');
    if (dropdown && menu && !dropdown.contains(event.target)) {
        menu.classList.add('hidden');
    }
});

// =============================================================================
// STATUS MANAGEMENT
// =============================================================================

function updateStatus(newStatus) {
    fetch(`/transactions/${transactionId}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            location.reload();
        } else {
            showToast('Error updating status: ' + data.error, 'error');
        }
    });
}

// =============================================================================
// MODAL MANAGEMENT
// =============================================================================

function confirmDelete() {
    document.getElementById('deleteModal').classList.remove('hidden');
}

function closeDeleteModal() {
    document.getElementById('deleteModal').classList.add('hidden');
}

function showAddParticipantModal() {
    document.getElementById('addParticipantForm').reset();
    document.getElementById('participantContactId').value = '';
    document.getElementById('participantPartnerOrganizationId').value = '';
    document.getElementById('participantPartnerContactId').value = '';
    document.getElementById('participantContactSearch').value = '';
    document.getElementById('selectedContactPreview').classList.add('hidden');
    document.getElementById('participantSearchResults').classList.add('hidden');
    document.getElementById('participantSearchWrap').classList.add('hidden');
    document.getElementById('participantCreateHint').classList.add('hidden');
    document.getElementById('addParticipantBtn').disabled = true;
    _participantSource = 'partners';
    document.getElementById('addParticipantModal').classList.remove('hidden');
}

function closeParticipantModal() {
    document.getElementById('addParticipantModal').classList.add('hidden');
    clearSelectedContact();
}

// =============================================================================
// UNIFIED ADD DOCUMENT MODAL
// =============================================================================

function showAddDocumentPicker() {
    // Reset all views to initial state
    resetAddDocumentModal();
    // Show picker view
    showPickerView('picker');
    // Show modal
    document.getElementById('addDocumentModal').classList.remove('hidden');
}

function closeAddDocumentModal() {
    document.getElementById('addDocumentModal').classList.add('hidden');
    resetAddDocumentModal();
}

function resetAddDocumentModal() {
    // Reset template form
    const templateForm = document.getElementById('addDocumentForm');
    if (templateForm) templateForm.reset();
    document.getElementById('customDocNameField').classList.add('hidden');

    // Reset e-sign form
    resetEsignForm();

    // Reset completed form
    resetCompletedForm();
}

function showPickerView(view) {
    // Hide all views
    document.getElementById('pickerView').classList.add('hidden');
    document.getElementById('templateView').classList.add('hidden');
    document.getElementById('uploadEsignView').classList.add('hidden');
    document.getElementById('uploadCompletedView').classList.add('hidden');

    // Show requested view
    switch(view) {
        case 'picker':
            document.getElementById('pickerView').classList.remove('hidden');
            break;
        case 'template':
            document.getElementById('templateView').classList.remove('hidden');
            break;
        case 'upload-esign':
            document.getElementById('uploadEsignView').classList.remove('hidden');
            break;
        case 'upload-completed':
            document.getElementById('uploadCompletedView').classList.remove('hidden');
            break;
    }
}

// Legacy function aliases for compatibility
function showAddDocumentModal() {
    showAddDocumentPicker();
    showPickerView('template');
}

function closeDocumentModal() {
    closeAddDocumentModal();
}

function showAddExternalModal() {
    showAddDocumentPicker();
    showPickerView('upload-esign');
}

function closeExternalModal() {
    closeAddDocumentModal();
}

// Close modals on Escape key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        closeDeleteModal();
        closeParticipantModal();
        closeAddDocumentModal();
        closeUploadScanModal();
        closeUploadStaticModal();
        closeUploadForSignatureModal();
        closeSellerNewOfferModal();
        closeSellerOfferModal();
    }
});

// =============================================================================
// PARTICIPANT MANAGEMENT
// =============================================================================

// Role → partner type, search hint, and default source mapping
const ROLE_CONFIG = {
    seller:                  { source: 'contacts', hint: 'Search for the seller' },
    co_seller:               { source: 'contacts', hint: 'Search for the co-seller' },
    buyer:                   { source: 'contacts', hint: 'Search for the buyer' },
    co_buyer:                { source: 'contacts', hint: 'Search for the co-buyer' },
    listing_agent:           { source: 'partners', type: 'brokerage', hint: 'Search for a listing agent or brokerage' },
    buyers_agent:            { source: 'partners', type: 'brokerage', hint: 'Search for a buyer\'s agent or brokerage' },
    title_company:           { source: 'partners', type: 'title_company', hint: 'Search for a title company' },
    lender:                  { source: 'partners', type: 'lender', hint: 'Search for a lender' },
    transaction_coordinator: { source: 'partners', type: 'other', hint: 'Search for a transaction coordinator' },
};

// Current participant source state (managed in JS, not DOM)
let _participantSource = 'partners'; // 'partners' | 'contacts'

let participantSearchTimeout;
const participantContactSearch = document.getElementById('participantContactSearch');
const participantSearchResults = document.getElementById('participantSearchResults');

function _getParticipantRole() {
    return document.getElementById('participantRole').value;
}

function _isPartnerSource() {
    return _participantSource === 'partners';
}

function switchParticipantSource() {
    _participantSource = _isPartnerSource() ? 'contacts' : 'partners';
    _applyParticipantSourceUI();
    clearSelectedContact();
}

function _applyParticipantSourceUI() {
    const role = _getParticipantRole();
    const cfg = ROLE_CONFIG[role] || {};
    const isPartner = _isPartnerSource();

    // Role-aware label and placeholder
    const hint = cfg.hint || (isPartner ? 'Search Partner Directory' : 'Search my contacts');
    document.getElementById('participantSearchLabel').textContent = hint;
    participantContactSearch.placeholder =
        isPartner ? 'Type company or person name…' : 'Type contact name…';

    // Switch link — offers opposite source
    const switchBtn = document.getElementById('participantSwitchSource');
    switchBtn.textContent = isPartner ? 'Use my contacts instead' : 'Search Partner Directory instead';

    // Create hint
    const hintWrap = document.getElementById('participantCreateHint');
    const hintText = document.getElementById('participantCreateHintText');
    const createLink = document.getElementById('participantCreateLink');
    const createLinkText = document.getElementById('participantCreateLinkText');
    hintWrap.classList.remove('hidden');
    if (isPartner) {
        hintText.textContent = "Company not in the directory yet?";
        createLink.href = '/partners/';
        createLinkText.textContent = 'Add to Partner Directory';
    } else {
        hintText.textContent = "Can't find who you're looking for?";
        createLink.href = '/contacts/create';
        createLinkText.textContent = 'Create a new contact';
    }
}

participantContactSearch.addEventListener('input', function() {
    clearTimeout(participantSearchTimeout);
    const query = this.value.trim();

    if (query.length < 2) {
        participantSearchResults.classList.add('hidden');
        return;
    }

    participantSearchTimeout = setTimeout(() => {
        const role = _getParticipantRole();
        const isPartner = _isPartnerSource();
        const cfg = ROLE_CONFIG[role] || {};
        const partnerType = cfg.type || '';
        const url = isPartner
            ? `/transactions/api/partners/search?q=${encodeURIComponent(query)}&role=${encodeURIComponent(role)}&type=${encodeURIComponent(partnerType)}`
            : `/transactions/api/contacts/search?q=${encodeURIComponent(query)}`;

        fetch(url)
            .then(res => res.json())
            .then(results => {
                if (results.length === 0) {
                    participantSearchResults.innerHTML =
                        `<div class="p-4 text-sm text-slate-500 text-center">No ${isPartner ? 'partners' : 'contacts'} found</div>`;
                } else {
                    participantSearchResults.innerHTML = results.map(c => {
                        if (isPartner) {
                            // Show person name on top line, company below (when different)
                            const topLine = c.name !== c.company ? c.name : c.company;
                            const subLine = (c.name !== c.company && c.company)
                                ? `${c.company} · ${c.type_label}`
                                : c.type_label;
                            return `
                                <div class="p-3 hover:bg-orange-50 cursor-pointer transition-colors border-b border-slate-100 last:border-0"
                                     onclick='selectParticipantPartner(${JSON.stringify(c).replace(/'/g, "&#39;")})'>
                                    <div class="font-medium text-slate-800">${topLine}</div>
                                    <div class="text-sm text-slate-500">${subLine}${c.email ? ' · ' + c.email : ''}</div>
                                    ${c.address ? `<div class="text-xs text-slate-400">${c.address}</div>` : ''}
                                </div>
                            `;
                        }
                        const hasRequired = c.first_name && c.last_name && c.email;
                        const missingBadge = !hasRequired
                            ? '<span class="text-xs text-amber-600 ml-2"><i class="fas fa-exclamation-triangle"></i> Missing info</span>'
                            : '';
                        return `
                            <div class="p-3 hover:bg-orange-50 cursor-pointer transition-colors border-b border-slate-100 last:border-0"
                                 onclick='selectParticipantContact(${JSON.stringify(c).replace(/'/g, "&#39;")})'>
                                <div class="font-medium text-slate-800">${c.name}${missingBadge}</div>
                                <div class="text-sm text-slate-500">${c.email || 'No email'}</div>
                            </div>
                        `;
                    }).join('');
                }
                participantSearchResults.classList.remove('hidden');
            });
    }, 300);
});

// Close search results on outside click
document.addEventListener('click', function(e) {
    if (!participantContactSearch.contains(e.target) && !participantSearchResults.contains(e.target)) {
        participantSearchResults.classList.add('hidden');
    }
});

function selectParticipantContact(contact) {
    if (!contact.first_name || !contact.last_name) {
        showToast('This contact is missing a name. Please update the contact first.', 'error');
        return;
    }
    if (!contact.email) {
        showToast('This contact is missing an email address. Please update the contact first.', 'error');
        return;
    }

    document.getElementById('participantContactId').value = contact.id;
    document.getElementById('participantPartnerOrganizationId').value = '';
    document.getElementById('participantPartnerContactId').value = '';

    document.getElementById('selectedContactInitials').textContent =
        (contact.first_name[0] + contact.last_name[0]).toUpperCase();
    document.getElementById('selectedContactName').textContent = contact.name;
    document.getElementById('selectedContactCompany').textContent = '';
    document.getElementById('selectedContactEmail').textContent = contact.email || '';
    document.getElementById('selectedContactPhone').textContent = contact.phone || '';

    document.getElementById('selectedContactPreview').classList.remove('hidden');
    document.getElementById('participantContactSearch').classList.add('hidden');
    participantSearchResults.classList.add('hidden');

    updateAddParticipantButton();
}

function selectParticipantPartner(partner) {
    document.getElementById('participantContactId').value = '';
    document.getElementById('participantPartnerOrganizationId').value = partner.partner_organization_id;
    document.getElementById('participantPartnerContactId').value = partner.partner_contact_id || '';

    // Person name on top, company below when different
    const hasPerson = partner.name && partner.company && partner.name !== partner.company;
    const displayName = hasPerson ? partner.name : partner.company;
    const displayCompany = hasPerson ? partner.company : '';

    const initials = displayName
        .split(/\s+/).filter(Boolean).slice(0, 2).map(p => p[0]).join('').toUpperCase() || 'P';

    document.getElementById('selectedContactInitials').textContent = initials;
    document.getElementById('selectedContactName').textContent = displayName;
    document.getElementById('selectedContactCompany').textContent = displayCompany;
    document.getElementById('selectedContactEmail').textContent = partner.email || '';
    document.getElementById('selectedContactPhone').textContent = partner.phone || '';

    document.getElementById('selectedContactPreview').classList.remove('hidden');
    document.getElementById('participantContactSearch').classList.add('hidden');
    participantSearchResults.classList.add('hidden');

    updateAddParticipantButton();
}

function clearSelectedContact() {
    document.getElementById('participantContactId').value = '';
    document.getElementById('participantPartnerOrganizationId').value = '';
    document.getElementById('participantPartnerContactId').value = '';
    document.getElementById('selectedContactPreview').classList.add('hidden');
    document.getElementById('participantContactSearch').classList.remove('hidden');
    document.getElementById('participantContactSearch').value = '';
    document.getElementById('addParticipantBtn').disabled = true;
}

function updateAddParticipantButton() {
    const hasContact = document.getElementById('participantContactId').value !== '';
    const hasPartner = document.getElementById('participantPartnerOrganizationId').value !== '';
    const hasRole = _getParticipantRole() !== '';
    document.getElementById('addParticipantBtn').disabled = !((hasContact || hasPartner) && hasRole);
}

// When role changes: auto-pick the best source and show the search area
document.getElementById('participantRole').addEventListener('change', function() {
    const role = this.value;
    if (!role) {
        document.getElementById('participantSearchWrap').classList.add('hidden');
        document.getElementById('participantCreateHint').classList.add('hidden');
        return;
    }

    const cfg = ROLE_CONFIG[role] || { source: 'partners' };
    _participantSource = cfg.source;
    _applyParticipantSourceUI();
    document.getElementById('participantSearchWrap').classList.remove('hidden');
    clearSelectedContact();
    updateAddParticipantButton();
});

document.getElementById('addParticipantForm').addEventListener('submit', function(e) {
    e.preventDefault();
    const formData = new FormData(this);

    fetch(`/transactions/${transactionId}/participants`, {
        method: 'POST',
        body: formData
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            location.reload();
        } else {
            showToast('Error adding participant: ' + data.error, 'error');
        }
    });
});

function removeParticipant(participantId) {
    if (!confirm('Remove this participant?')) return;

    fetch(`/transactions/${transactionId}/participants/${participantId}`, {
        method: 'DELETE'
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            location.reload();
        } else {
            showToast('Error removing participant: ' + data.error, 'error');
        }
    });
}

// =============================================================================
// DOCUMENT MANAGEMENT
// =============================================================================

const docTemplateSelect = document.getElementById('docTemplateSelect');
if (docTemplateSelect) {
    docTemplateSelect.addEventListener('change', function() {
        const customField = document.getElementById('customDocNameField');
        if (this.value === 'custom') {
            customField.classList.remove('hidden');
            customField.querySelector('input').required = true;
        } else {
            customField.classList.add('hidden');
            customField.querySelector('input').required = false;
        }
    });
}

const addDocumentForm = document.getElementById('addDocumentForm');
if (addDocumentForm && docTemplateSelect) {
    addDocumentForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const formData = new FormData(this);

        const selectedOption = docTemplateSelect.options[docTemplateSelect.selectedIndex];
        let templateName = selectedOption.dataset.name;

        if (formData.get('template_slug') === 'custom') {
            templateName = formData.get('custom_name');
        }

        formData.append('template_name', templateName);

        fetch(`/transactions/${transactionId}/documents`, {
            method: 'POST',
            body: formData
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                if (data.placeholder_updated) {
                    // Show special message for placeholder conversion
                    showToast(data.message || 'Placeholder updated with generated document.', 'success');
                }
                location.reload();
            } else {
                showToast('Error adding document: ' + data.error, 'error');
            }
        });
    });
}

function removeDocument(docId) {
    if (!confirm('Remove this document?')) return;

    fetch(`/transactions/${transactionId}/documents/${docId}`, {
        method: 'DELETE'
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            location.reload();
        } else {
            showToast('Error removing document: ' + data.error, 'error');
        }
    });
}

// =============================================================================
// DOCUMENT ACTIONS DROPDOWN
// =============================================================================

function toggleDocActionsMenu(docId) {
    // Close all other menus first
    document.querySelectorAll('[id^="docActionsMenu-"]').forEach(menu => {
        if (menu.id !== `docActionsMenu-${docId}`) {
            menu.classList.add('hidden');
        }
    });

    const menu = document.getElementById(`docActionsMenu-${docId}`);
    menu.classList.toggle('hidden');
}

// Close dropdown when clicking outside
document.addEventListener('click', function(event) {
    if (!event.target.closest('.doc-actions-dropdown')) {
        document.querySelectorAll('[id^="docActionsMenu-"]').forEach(menu => {
            menu.classList.add('hidden');
        });
    }
});

// =============================================================================
// TOAST NOTIFICATIONS
// =============================================================================

function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    const icon = document.getElementById('toastIcon');
    document.getElementById('toastMessage').textContent = message;

    icon.className = 'fas';
    if (type === 'success') {
        icon.classList.add('fa-check-circle', 'text-green-500');
    } else if (type === 'error') {
        icon.classList.add('fa-exclamation-circle', 'text-red-500');
    } else if (type === 'warning') {
        icon.classList.add('fa-exclamation-triangle', 'text-amber-500');
    } else {
        icon.classList.add('fa-info-circle', 'text-blue-500');
    }

    toast.classList.remove('hidden');
    setTimeout(() => toast.classList.add('hidden'), 4000);
}

// =============================================================================
// SELLER WORKSPACE
// =============================================================================

function sellerWorkspaceTab(tabName, options = {}) {
    if (tabName === 'overview') tabName = 'listing';
    document.querySelectorAll('[id^="seller-tab-"]').forEach(btn => btn.classList.remove('is-active'));
    document.querySelectorAll('.seller-tab-panel').forEach(panel => panel.classList.add('hidden'));
    document.querySelectorAll('[data-seller-listing-tab-content]').forEach(element => {
        element.classList.toggle('hidden', tabName !== 'listing');
    });

    const tab = document.getElementById(`seller-tab-${tabName}`);
    const panel = document.getElementById(`seller-panel-${tabName}`);
    if (tab) tab.classList.add('is-active');
    if (panel) panel.classList.remove('hidden');
    if (tab && panel && options.persist !== false) {
        setSellerWorkspaceReloadTab(tabName);
    }
}

function sellerFormData(form) {
    const data = {};
    const terms = {};
    new FormData(form).forEach((value, key) => {
        const termMatch = key.match(/^terms_data\[(.+)\]$/);
        if (termMatch) {
            if (value !== '') terms[termMatch[1]] = value;
        } else if (value !== '') {
            data[key] = value;
        }
    });
    if (Object.keys(terms).length) data.terms_data = terms;
    return data;
}

function sellerPost(url, payload, successMessage, options = {}) {
    return fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
    })
    .then(res => res.json())
    .then(data => {
        if (!data.success) {
            throw new Error(data.error || 'Request failed');
        }
        if (successMessage) showToast(successMessage, 'success');
        const reloadTab = options.reloadTab || getActiveSellerWorkspaceTab();
        if (reloadTab) setSellerWorkspaceReloadTab(reloadTab);
        setTimeout(() => location.reload(), 500);
        return data;
    })
    .catch(err => {
        showToast(err.message || 'Something went wrong', 'error');
    });
}

function showSellerNewOfferModal() {
    const modal = document.getElementById('sellerNewOfferModal');
    if (!modal) return;
    modal.classList.remove('hidden');
    document.body.classList.add('overflow-hidden');
    const firstInput = modal.querySelector('input[name="buyer_names"]');
    if (firstInput) {
        setTimeout(() => firstInput.focus(), 100);
    }
}

function closeSellerNewOfferModal() {
    const modal = document.getElementById('sellerNewOfferModal');
    if (modal) modal.classList.add('hidden');
    if (!document.querySelector('[data-seller-offer-modal]:not(.hidden)')) {
        document.body.classList.remove('overflow-hidden');
    }
}

function openSellerOfferModal(offerId) {
    const modal = document.getElementById(`sellerOfferModal-${offerId}`);
    if (!modal) return;
    modal.classList.remove('hidden');
    document.body.classList.add('overflow-hidden');
}

function closeSellerOfferModal(offerId) {
    if (offerId) {
        const modal = document.getElementById(`sellerOfferModal-${offerId}`);
        if (modal) modal.classList.add('hidden');
    } else {
        document.querySelectorAll('[data-seller-offer-modal]').forEach(modal => modal.classList.add('hidden'));
    }
    const newOfferModal = document.getElementById('sellerNewOfferModal');
    if (!newOfferModal || newOfferModal.classList.contains('hidden')) {
        document.body.classList.remove('overflow-hidden');
    }
}

const sellerOfferForm = document.getElementById('sellerOfferForm');
if (sellerOfferForm) {
    sellerOfferForm.addEventListener('submit', function(e) {
        e.preventDefault();
        setSellerWorkspaceReloadTab('offers');
        const newOfferModal = document.getElementById('sellerNewOfferModal');
        const uploadForm = newOfferModal ? newOfferModal.querySelector('.seller-offer-upload-form') : null;
        const uploadInput = uploadForm ? uploadForm.querySelector('.seller-offer-file-input') : null;
        const selectedFiles = Array.from((uploadInput && uploadInput.files) || []);
        if (selectedFiles.length && uploadForm) {
            showToast('Uploading selected PDFs for extraction...', 'success');
            uploadForm.requestSubmit();
            return;
        }

        sellerPost(
            `/transactions/${transactionId}/offers`,
            sellerFormData(this),
            'Offer logged.'
        );
    });
}

document.querySelectorAll('.seller-offer-update-form').forEach(form => {
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        const offerId = this.dataset.offerId;
        if (!offerId) return;
        sellerPost(
            `/transactions/${transactionId}/offers/${offerId}`,
            sellerFormData(this),
            'Offer details saved.'
        );
    });
});

const OFFER_DOCUMENT_TYPE_OPTIONS = [
    ['offer_package', 'Offer package (contract + addenda)'],
    ['buyer_offer', 'Offer contract only'],
    ['buyer_counter', 'Buyer counter'],
    ['seller_counter', 'Seller counter'],
    ['final_acceptance', 'Executed contract'],
    ['sellers_disclosure', "Seller's Disclosure"],
    ['hoa_addendum', 'HOA Addendum'],
    ['pre_approval', 'Mortgage pre-approval'],
    ['third_party_financing', 'Third party financing'],
    ['backup_acceptance', 'Backup addendum']
];

function inferOfferDocumentType(filename) {
    const words = (filename || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().split(/\s+/);
    const tokens = new Set(words);
    const has = (...items) => items.every(item => tokens.has(item));

    if (has('third', 'financing')) return 'third_party_financing';
    if (tokens.has('preapproval') || has('pre', 'approval') || tokens.has('prequal') || has('pre', 'qual') || tokens.has('prequalification')) {
        return 'pre_approval';
    }
    if (tokens.has('hoa') || has('owners', 'association') || has('property', 'subject', 'mandatory') || has('mandatory', 'membership')) {
        return 'hoa_addendum';
    }
    if (has('seller', 'disclosure') || has('sellers', 'disclosure') || tokens.has('sd')) return 'sellers_disclosure';
    if (tokens.has('backup')) return 'backup_acceptance';
    if (tokens.has('executed') || tokens.has('signed') || tokens.has('acceptance')) return 'offer_package';
    if (tokens.has('counter')) return tokens.has('seller') ? 'seller_counter' : 'buyer_counter';
    if (tokens.has('contract') || tokens.has('offer') || tokens.has('resale')) return 'offer_package';
    return 'offer_package';
}

function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    }[char]));
}

function formatSellerLabel(value) {
    return String(value || '')
        .replace(/_/g, ' ')
        .replace(/\b\w/g, char => char.toUpperCase());
}

function formatSellerCurrency(value) {
    if (value === null || value === undefined || value === '') return 'Price TBD';
    const numericValue = Number(value);
    if (Number.isNaN(numericValue)) return String(value);
    return numericValue.toLocaleString(undefined, {
        style: 'currency',
        currency: 'USD',
        maximumFractionDigits: 0
    });
}

function formatSellerDateTime(value) {
    if (!value) return 'No response deadline';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit'
    });
}

function sellerDeadlineClass(state) {
    if (['critical', 'strong_warning', 'expired'].includes(state)) return 'font-medium text-red-700';
    if (state === 'warning') return 'font-medium text-orange-700';
    return 'text-slate-600';
}

function updateSellerOfferText(row, selector, value) {
    const element = row.querySelector(selector);
    if (element) element.textContent = value;
}

function renderSellerOfferExtractionStatus(offer) {
    const status = offer.extraction_status;
    if (status && status !== 'complete') {
        const isFailed = status === 'failed';
        const icon = isFailed ? 'fa-exclamation-circle' : 'fa-spinner fa-spin';
        const color = isFailed ? 'text-red-600' : 'text-sky-600';
        return {
            html: `<i class="fas ${icon} mr-0.5 text-[10px]"></i>${escapeHtml(status.replace(/_/g, ' '))}`,
            classes: `text-xs ${color}`,
        };
    }

    const versionCount = Number(offer.version_count || 0);
    return {
        html: `${versionCount} version${versionCount === 1 ? '' : 's'}`,
        classes: 'text-xs text-slate-500',
    };
}

function updateSellerOfferRow(offer) {
    const row = document.querySelector(`[data-seller-offer-row="${offer.id}"]`);
    if (!row) return;

    updateSellerOfferText(row, '[data-seller-offer-buyer]', offer.buyer_names || 'Unnamed buyer');
    const agentLabel = [
        offer.buyer_agent_name || 'No agent yet',
        offer.buyer_agent_brokerage || ''
    ].filter(Boolean).join(' · ');
    updateSellerOfferText(row, '[data-seller-offer-agent]', agentLabel);
    updateSellerOfferText(row, '[data-seller-offer-price]', formatSellerCurrency(offer.offer_price));
    updateSellerOfferText(row, '[data-seller-offer-financing]', offer.financing_type || 'Financing TBD');

    const urgency = offer.urgency || {};
    const deadlineLabel = row.querySelector('[data-seller-offer-deadline-label]');
    if (deadlineLabel) {
        deadlineLabel.className = sellerDeadlineClass(urgency.state);
        deadlineLabel.textContent = urgency.label || 'No deadline';
    }
    updateSellerOfferText(row, '[data-seller-offer-deadline-at]', formatSellerDateTime(offer.response_deadline_at));

    const documentCount = Number(offer.document_count || 0);
    updateSellerOfferText(
        row,
        '[data-seller-offer-doc-count]',
        `${documentCount} document${documentCount === 1 ? '' : 's'}`
    );

    const extractionElement = row.querySelector('[data-seller-offer-extraction-status]');
    if (extractionElement) {
        const rendered = renderSellerOfferExtractionStatus(offer);
        extractionElement.className = rendered.classes;
        extractionElement.innerHTML = rendered.html;
    }

    updateSellerOfferText(row, '[data-seller-offer-status]', formatSellerLabel(offer.status));
}

let sellerOfferPollingTimer = null;
let sellerOfferPollCount = 0;

function hasSellerOfferExtractionInProgress(offers) {
    return offers.some(offer => ['pending', 'processing'].includes(offer.extraction_status));
}

function pollSellerOfferExtractionStatus() {
    const hadWorkingRow = Array.from(document.querySelectorAll('[data-seller-offer-extraction-status]')).some(element => {
        const value = element.textContent.trim().toLowerCase();
        return value.includes('pending') || value.includes('processing');
    });

    fetch(`/transactions/${transactionId}/offers`)
    .then(res => res.json())
    .then(data => {
        if (!data.success) throw new Error(data.error || 'Unable to refresh offers');
        const offers = data.offers || [];
        offers.forEach(updateSellerOfferRow);

        sellerOfferPollCount += 1;
        const stillWorking = hasSellerOfferExtractionInProgress(offers);
        if (stillWorking && sellerOfferPollCount < 100) {
            sellerOfferPollingTimer = setTimeout(pollSellerOfferExtractionStatus, 3000);
        } else {
            sellerOfferPollingTimer = null;
            if (hadWorkingRow) {
                setSellerWorkspaceReloadTab('offers');
                setTimeout(() => location.reload(), 350);
            }
        }
    })
    .catch(() => {
        sellerOfferPollCount += 1;
        if (sellerOfferPollCount < 5) {
            sellerOfferPollingTimer = setTimeout(pollSellerOfferExtractionStatus, 5000);
        } else {
            sellerOfferPollingTimer = null;
        }
    });
}

function startSellerOfferExtractionPolling() {
    if (sellerOfferPollingTimer || !document.getElementById('seller-panel-offers')) return;
    const hasPendingRow = Array.from(document.querySelectorAll('[data-seller-offer-extraction-status]')).some(element => {
        const value = element.textContent.trim().toLowerCase();
        return value.includes('pending') || value.includes('processing');
    });
    if (!hasPendingRow) return;

    sellerOfferPollCount = 0;
    sellerOfferPollingTimer = setTimeout(pollSellerOfferExtractionStatus, 2500);
}

function updateSellerOfferDropzone(form, files) {
    const title = form.querySelector('.seller-offer-dropzone-title');
    const hint = form.querySelector('.seller-offer-dropzone-hint');
    if (!title || !hint) return;
    if (!files.length) {
        title.textContent = 'Choose PDFs to attach';
        hint.textContent = 'Multiple PDFs allowed. Types are inferred from filename.';
        return;
    }
    const totalSize = files.reduce((sum, file) => sum + (file.size || 0), 0);
    const totalLabel = totalSize ? ` · ${(totalSize / 1024 / 1024).toFixed(2)} MB total` : '';
    title.textContent = `${files.length} PDF${files.length === 1 ? '' : 's'} ready`;
    hint.textContent = `Tap to add or replace${totalLabel}`;
}

function renderSellerOfferFileList(form) {
    const input = form.querySelector('.seller-offer-file-input');
    const list = form.querySelector('.seller-offer-file-list');
    if (!input || !list) return;

    const files = Array.from(input.files || []);
    updateSellerOfferDropzone(form, files);

    if (!files.length) {
        list.classList.add('hidden');
        list.innerHTML = '';
        return;
    }

    list.innerHTML = files.map((file, index) => {
        const inferredType = inferOfferDocumentType(file.name);
        const options = OFFER_DOCUMENT_TYPE_OPTIONS.map(([value, label]) => (
            `<option value="${value}" ${value === inferredType ? 'selected' : ''}>${label}</option>`
        )).join('');
        const fileSize = file.size ? `${Math.max(file.size / 1024 / 1024, 0.01).toFixed(2)} MB` : '';
        return `
            <div class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-md border border-slate-200 bg-white px-3 py-2.5" data-offer-upload-row="${index}">
                <span class="flex h-9 w-9 items-center justify-center rounded-md bg-rose-50 text-rose-600">
                    <i class="fas fa-file-pdf text-sm"></i>
                </span>
                <div class="min-w-0">
                    <div class="truncate text-sm font-medium text-slate-900">${escapeHtml(file.name)}</div>
                    <div class="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-500">
                        ${fileSize ? `<span>${fileSize}</span><span class="text-slate-300">·</span>` : ''}
                        <span>Auto-detected type</span>
                    </div>
                </div>
                <select class="crm-select seller-offer-document-type-select h-9 w-44 px-3 py-1.5 text-xs">
                    ${options}
                </select>
                <div class="seller-offer-upload-status col-span-3 hidden border-t border-slate-100 pt-2 text-[11px] text-slate-500"></div>
            </div>
        `;
    }).join('');
    list.classList.remove('hidden');
}

document.querySelectorAll('.seller-offer-upload-form').forEach(form => {
    const input = form.querySelector('.seller-offer-file-input');
    if (input) {
        input.addEventListener('change', () => renderSellerOfferFileList(form));
    }

    form.addEventListener('submit', function(e) {
        e.preventDefault();
        const submit = this.querySelector('button[type="submit"]');
        const originalText = submit ? submit.textContent : '';
        const input = this.querySelector('.seller-offer-file-input');
        const files = Array.from((input && input.files) || []);
        if (!files.length) {
            showToast('Select at least one PDF.', 'error');
            return;
        }
        const rows = Array.from(this.querySelectorAll('[data-offer-upload-row]'));
        const formData = new FormData();
        const isNewOfferUpload = Boolean(this.closest('#sellerNewOfferModal'));
        if (isNewOfferUpload && sellerOfferForm) {
            new FormData(sellerOfferForm).forEach((value, key) => {
                if (value !== '') {
                    formData.append(key, value);
                }
            });
        }
        new FormData(this).forEach((value, key) => {
            if (key !== 'files' && key !== 'file' && key !== 'document_type') {
                formData.append(key, value);
            }
        });
        files.forEach((file, index) => {
            const row = rows[index];
            const select = row ? row.querySelector('.seller-offer-document-type-select') : null;
            const status = row ? row.querySelector('.seller-offer-upload-status') : null;
            formData.append('files', file);
            formData.append('document_type', select ? select.value : inferOfferDocumentType(file.name));
            if (status) {
                status.textContent = 'Uploading...';
                status.classList.remove('hidden', 'text-red-600');
                status.classList.add('text-sky-600');
            }
        });
        if (submit) {
            submit.disabled = true;
            submit.textContent = 'Uploading...';
        }

        fetch(`/transactions/${transactionId}/offers/upload`, {
            method: 'POST',
            body: formData
        })
        .then(res => res.json())
        .then(data => {
            if (!data.success) throw new Error(data.error || 'Upload failed');
            rows.forEach(row => {
                const status = row.querySelector('.seller-offer-upload-status');
                if (status) {
                    status.textContent = 'Uploaded. Extraction queued.';
                    status.classList.remove('hidden', 'text-red-600', 'text-sky-600');
                    status.classList.add('text-emerald-600');
                }
            });
            showToast(data.message || 'Offer document uploaded.', 'success');
            setSellerWorkspaceReloadTab('offers');
            setTimeout(() => location.reload(), 800);
        })
        .catch(err => {
            showToast(err.message || 'Offer upload failed', 'error');
            rows.forEach(row => {
                const status = row.querySelector('.seller-offer-upload-status');
                if (status) {
                    status.textContent = err.message || 'Upload failed';
                    status.classList.remove('hidden', 'text-sky-600');
                    status.classList.add('text-red-600');
                }
            });
            if (submit) {
                submit.disabled = false;
                submit.textContent = originalText;
            }
        });
    });
});

const CONTRACT_DOCUMENT_TYPE_OPTIONS = [
    ['final_acceptance', 'Executed contract'],
    ['third_party_financing', 'Third party financing'],
    ['hoa_addendum', 'HOA Addendum'],
    ['sellers_disclosure', "Seller's Disclosure"],
    ['pre_approval', 'Mortgage pre-approval'],
    ['backup_acceptance', 'Backup addendum']
];

function inferContractDocumentType(filename) {
    const words = (filename || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().split(/\s+/);
    const tokens = new Set(words);
    const has = (...items) => items.every(item => tokens.has(item));

    if (has('third', 'financing')) return 'third_party_financing';
    if (tokens.has('preapproval') || has('pre', 'approval') || tokens.has('prequal') || has('pre', 'qual') || tokens.has('prequalification')) {
        return 'pre_approval';
    }
    if (tokens.has('hoa') || has('owners', 'association') || has('property', 'subject', 'mandatory') || has('mandatory', 'membership')) {
        return 'hoa_addendum';
    }
    if (has('seller', 'disclosure') || has('sellers', 'disclosure') || tokens.has('sd')) return 'sellers_disclosure';
    if (tokens.has('backup')) return 'backup_acceptance';
    return 'final_acceptance';
}

function updateSellerContractDropzone(form, files) {
    const title = form.querySelector('.seller-contract-dropzone-title');
    const hint = form.querySelector('.seller-contract-dropzone-hint');
    if (!title || !hint) return;
    if (!files.length) {
        title.textContent = 'Choose executed PDFs';
        hint.textContent = 'Multiple PDFs allowed. Types are inferred from filename.';
        return;
    }
    const totalSize = files.reduce((sum, file) => sum + (file.size || 0), 0);
    const totalLabel = totalSize ? ` · ${(totalSize / 1024 / 1024).toFixed(2)} MB total` : '';
    title.textContent = `${files.length} PDF${files.length === 1 ? '' : 's'} ready`;
    hint.textContent = `Tap to add or replace${totalLabel}`;
}

function renderSellerContractFileList(form) {
    const input = form.querySelector('.seller-contract-file-input');
    const list = form.querySelector('.seller-contract-file-list');
    if (!input || !list) return;

    const files = Array.from(input.files || []);
    updateSellerContractDropzone(form, files);

    if (!files.length) {
        list.classList.add('hidden');
        list.innerHTML = '';
        return;
    }

    list.innerHTML = files.map((file, index) => {
        const inferredType = inferContractDocumentType(file.name);
        const options = CONTRACT_DOCUMENT_TYPE_OPTIONS.map(([value, label]) => (
            `<option value="${value}" ${value === inferredType ? 'selected' : ''}>${label}</option>`
        )).join('');
        const fileSize = file.size ? `${Math.max(file.size / 1024 / 1024, 0.01).toFixed(2)} MB` : '';
        return `
            <div class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-md border border-slate-200 bg-white px-3 py-2.5" data-contract-upload-row="${index}">
                <span class="flex h-9 w-9 items-center justify-center rounded-md bg-rose-50 text-rose-600">
                    <i class="fas fa-file-pdf text-sm"></i>
                </span>
                <div class="min-w-0">
                    <div class="truncate text-sm font-medium text-slate-900">${escapeHtml(file.name)}</div>
                    <div class="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-500">
                        ${fileSize ? `<span>${fileSize}</span><span class="text-slate-300">·</span>` : ''}
                        <span>Auto-detected type</span>
                    </div>
                </div>
                <select class="crm-select seller-contract-document-type-select h-9 w-44 px-3 py-1.5 text-xs">
                    ${options}
                </select>
                <div class="seller-contract-upload-status col-span-3 hidden border-t border-slate-100 pt-2 text-[11px] text-slate-500"></div>
            </div>
        `;
    }).join('');
    list.classList.remove('hidden');
}

document.querySelectorAll('.seller-contract-upload-form').forEach(form => {
    const input = form.querySelector('.seller-contract-file-input');
    if (input) {
        input.addEventListener('change', () => renderSellerContractFileList(form));
    }

    form.addEventListener('submit', function(e) {
        e.preventDefault();
        const contractId = this.dataset.contractId;
        const submit = this.querySelector('button[type="submit"]');
        const originalText = submit ? submit.textContent : '';
        const fileInput = this.querySelector('.seller-contract-file-input');
        const files = Array.from((fileInput && fileInput.files) || []);
        if (!contractId) {
            showToast('Unable to find contract.', 'error');
            return;
        }
        if (!files.length) {
            showToast('Select at least one PDF.', 'error');
            return;
        }

        const rows = Array.from(this.querySelectorAll('[data-contract-upload-row]'));
        const formData = new FormData();
        files.forEach((file, index) => {
            const row = rows[index];
            const select = row ? row.querySelector('.seller-contract-document-type-select') : null;
            const status = row ? row.querySelector('.seller-contract-upload-status') : null;
            formData.append('files', file);
            formData.append('document_type', select ? select.value : inferContractDocumentType(file.name));
            if (status) {
                status.textContent = 'Uploading...';
                status.classList.remove('hidden', 'text-red-600');
                status.classList.add('text-sky-600');
            }
        });

        if (submit) {
            submit.disabled = true;
            submit.textContent = 'Uploading...';
        }

        fetch(`/transactions/${transactionId}/seller/contracts/${contractId}/documents/upload`, {
            method: 'POST',
            body: formData
        })
        .then(res => res.json())
        .then(data => {
            if (!data.success) throw new Error(data.error || 'Upload failed');
            rows.forEach(row => {
                const status = row.querySelector('.seller-contract-upload-status');
                if (status) {
                    status.textContent = 'Uploaded. Extraction queued.';
                    status.classList.remove('hidden', 'text-red-600', 'text-sky-600');
                    status.classList.add('text-emerald-600');
                }
            });
            showToast(data.message || 'Contract document uploaded.', 'success');
            setSellerWorkspaceReloadTab('contract');
            setTimeout(() => location.reload(), 800);
        })
        .catch(err => {
            showToast(err.message || 'Contract upload failed', 'error');
            rows.forEach(row => {
                const status = row.querySelector('.seller-contract-upload-status');
                if (status) {
                    status.textContent = err.message || 'Upload failed';
                    status.classList.remove('hidden', 'text-sky-600');
                    status.classList.add('text-red-600');
                }
            });
            if (submit) {
                submit.disabled = false;
                submit.textContent = originalText;
            }
        });
    });
});

function acceptSellerOffer(offerId, position) {
    const label = position === 'backup' ? 'accept this offer as backup' : 'accept this offer as primary';
    if (!confirm(`Are you sure you want to ${label}?`)) return;

    const payload = { position };
    if (position === 'backup') {
        const backupPosition = prompt('Backup position number?', '1');
        if (backupPosition) payload.backup_position = backupPosition;
    }

    sellerPost(
        `/transactions/${transactionId}/offers/${offerId}/accept`,
        payload,
        position === 'backup' ? 'Backup contract created.' : 'Primary contract accepted.',
        { reloadTab: 'contract' }
    );
}

function expireSellerOffer(offerId) {
    sellerPost(
        `/transactions/${transactionId}/offers/${offerId}/expire`,
        {},
        'Offer deadline checked.'
    );
}

function openContractActionModal(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}

function closeContractActionModal(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;
    modal.classList.add('hidden');
    document.body.style.overflow = '';
}

function showTerminateContractForm() {
    closeContractActionModal('closeContractForm');
    openContractActionModal('terminateContractForm');
}

function closeTerminateContractModal() {
    closeContractActionModal('terminateContractForm');
}

function showCloseContractForm() {
    closeContractActionModal('terminateContractForm');
    openContractActionModal('closeContractForm');
}

function closeCloseContractModal() {
    closeContractActionModal('closeContractForm');
}

document.addEventListener('keydown', function(e) {
    if (e.key !== 'Escape') return;
    ['terminateContractForm', 'closeContractForm'].forEach(id => {
        const modal = document.getElementById(id);
        if (modal && !modal.classList.contains('hidden')) {
            closeContractActionModal(id);
        }
    });
});

const sellerTerminateForm = document.getElementById('sellerTerminateForm');
if (sellerTerminateForm) {
    sellerTerminateForm.addEventListener('submit', function(e) {
        e.preventDefault();
        if (!confirm('Confirm this contract termination?')) return;
        sellerPost(
            `/transactions/${transactionId}/seller/contracts/${this.dataset.contractId}/terminate`,
            sellerFormData(this),
            'Contract termination recorded.'
        );
    });
}

const sellerCloseForm = document.getElementById('sellerCloseForm');
if (sellerCloseForm) {
    sellerCloseForm.addEventListener('submit', function(e) {
        e.preventDefault();
        if (!confirm('Mark this transaction closed?')) return;
        sellerPost(
            `/transactions/${transactionId}/seller/contracts/${this.dataset.contractId}/close`,
            sellerFormData(this),
            'Transaction marked closed.'
        );
    });
}

const sellerContractDetailsForm = document.getElementById('sellerContractDetailsForm');
if (sellerContractDetailsForm) {
    const editBtn = sellerContractDetailsForm.querySelector('[data-contract-action="edit"]');
    const cancelBtn = sellerContractDetailsForm.querySelector('[data-contract-action="cancel"]');
    const saveBtn = sellerContractDetailsForm.querySelector('[data-contract-action="save"]');
    const fieldRows = sellerContractDetailsForm.querySelectorAll('[data-contract-field]');
    const executionDateInput = sellerContractDetailsForm.querySelector('[data-contract-execution-date]');

    const captureSnapshot = () => {
        const snapshot = new Map();
        sellerContractDetailsForm.querySelectorAll('input, textarea, select').forEach(el => {
            snapshot.set(el, el.value);
        });
        return snapshot;
    };
    let initialSnapshot = captureSnapshot();

    const setMode = (mode) => {
        const editing = mode === 'edit';
        sellerContractDetailsForm.dataset.contractMode = editing ? 'edit' : 'view';
        if (editBtn) editBtn.classList.toggle('hidden', editing);
        if (cancelBtn) cancelBtn.classList.toggle('hidden', !editing);
        if (saveBtn) saveBtn.classList.toggle('hidden', !editing);
        fieldRows.forEach(row => {
            const display = row.querySelector('[data-contract-display]');
            const input = row.querySelector('[data-contract-input]');
            if (display) display.classList.toggle('hidden', editing);
            if (input) input.classList.toggle('hidden', !editing);
        });
        if (editing) {
            const firstInput = sellerContractDetailsForm.querySelector('[data-contract-field] [data-contract-input]');
            if (firstInput) {
                try { firstInput.focus({ preventScroll: true }); } catch (err) { firstInput.focus(); }
            }
        }
    };

    if (editBtn) {
        editBtn.addEventListener('click', () => {
            initialSnapshot = captureSnapshot();
            setMode('edit');
        });
    }
    if (cancelBtn) {
        cancelBtn.addEventListener('click', () => {
            initialSnapshot.forEach((value, el) => { el.value = value; });
            setMode('view');
        });
    }
    if (executionDateInput) {
        executionDateInput.addEventListener('change', () => {
            if (sellerContractDetailsForm.dataset.contractMode !== 'edit') {
                setMode('edit');
            }
        });
    }

    sellerContractDetailsForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const contractId = this.dataset.contractId;
        if (!contractId) {
            showToast('Unable to find contract.', 'error');
            return;
        }
        sellerPost(
            `/transactions/${transactionId}/seller/contracts/${contractId}/details`,
            sellerFormData(this),
            'Contract details saved.'
        );
    });
}

document.querySelectorAll('.seller-milestone').forEach(row => {
    const toggleBtn = row.querySelector('.seller-milestone-toggle');
    const form = row.querySelector('.seller-milestone-form');
    const chevron = row.querySelector('.seller-milestone-chevron');
    const cancelBtn = row.querySelector('.seller-milestone-cancel');
    if (!toggleBtn || !form) return;

    const closeRow = () => {
        form.classList.add('hidden');
        if (chevron) chevron.classList.remove('rotate-180');
        toggleBtn.setAttribute('aria-expanded', 'false');
    };
    const openRow = () => {
        form.classList.remove('hidden');
        if (chevron) chevron.classList.add('rotate-180');
        toggleBtn.setAttribute('aria-expanded', 'true');
    };

    toggleBtn.setAttribute('aria-expanded', 'false');
    toggleBtn.addEventListener('click', () => {
        if (form.classList.contains('hidden')) {
            openRow();
        } else {
            closeRow();
        }
    });
    if (cancelBtn) cancelBtn.addEventListener('click', closeRow);

    form.addEventListener('submit', function(e) {
        e.preventDefault();
        const contractId = this.dataset.contractId;
        const milestoneId = this.dataset.milestoneId;
        if (!contractId || !milestoneId) {
            showToast('Unable to find milestone.', 'error');
            return;
        }
        sellerPost(
            `/transactions/${transactionId}/seller/contracts/${contractId}/milestones/${milestoneId}`,
            sellerFormData(this),
            'Milestone updated.'
        );
    });
});

const sellerManualMilestoneForm = document.getElementById('sellerManualMilestoneForm');
if (sellerManualMilestoneForm) {
    const manualToggleBtn = document.querySelector('[data-toggle="seller-manual-milestone"]');
    const manualCancelBtn = sellerManualMilestoneForm.querySelector('[data-toggle-cancel="seller-manual-milestone"]');
    const showManual = () => sellerManualMilestoneForm.classList.remove('hidden');
    const hideManual = () => {
        sellerManualMilestoneForm.classList.add('hidden');
        sellerManualMilestoneForm.reset();
    };
    if (manualToggleBtn) {
        manualToggleBtn.addEventListener('click', () => {
            if (sellerManualMilestoneForm.classList.contains('hidden')) {
                showManual();
                const firstInput = sellerManualMilestoneForm.querySelector('input[name="title"]');
                if (firstInput) firstInput.focus();
            } else {
                hideManual();
            }
        });
    }
    if (manualCancelBtn) manualCancelBtn.addEventListener('click', hideManual);

    sellerManualMilestoneForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const contractId = this.dataset.contractId;
        if (!contractId) {
            showToast('Unable to find contract.', 'error');
            return;
        }
        sellerPost(
            `/transactions/${transactionId}/seller/contracts/${contractId}/milestones`,
            sellerFormData(this),
            'Milestone added.'
        );
    });
}

// =============================================================================
// FILL ALL FORMS & CONTACT FILE DOWNLOAD
// =============================================================================

function fillAllForms() {
    // Navigate to combined fill-all-documents view
    window.location.href = TX_CONFIG.fillAllFormsUrl;
}

function downloadContactFileFromTx(contactId, fileId) {
    fetch(`/contact/${contactId}/files/${fileId}/download`)
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            window.open(data.url, '_blank');
        } else {
            showToast(data.error || 'Download failed', 'error');
        }
    })
    .catch(error => {
        showToast('Download failed. Please try again.', 'error');
        console.error('Download error:', error);
    });
}
