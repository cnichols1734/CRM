/**
 * Transaction Detail - Core functionality
 * Handles: tabs, status, modals, participants, document management, toast notifications
 */

const transactionId = TX_CONFIG.transactionId;

// =============================================================================
// TAB SWITCHING (Buyer Transactions)
// =============================================================================

function switchTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.getElementById('tab-' + tabName).classList.add('active');

    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    document.getElementById('content-' + tabName).classList.add('active');
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
});

// =============================================================================
// STATUS DROPDOWN
// =============================================================================

function toggleStatusDropdown(event) {
    event.stopPropagation();
    const menu = document.getElementById('statusDropdownMenu');
    menu.classList.toggle('show');
}

// Close dropdown when clicking outside
document.addEventListener('click', function(event) {
    const dropdown = document.getElementById('statusDropdown');
    const menu = document.getElementById('statusDropdownMenu');
    if (dropdown && !dropdown.contains(event.target)) {
        menu.classList.remove('show');
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
    // Reset form and state
    document.getElementById('addParticipantForm').reset();
    document.getElementById('participantContactId').value = '';
    document.getElementById('participantContactSearch').value = '';
    document.getElementById('selectedContactPreview').classList.add('hidden');
    document.getElementById('participantSearchResults').classList.add('hidden');
    document.getElementById('addParticipantBtn').disabled = true;
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
    }
});

// =============================================================================
// PARTICIPANT MANAGEMENT
// =============================================================================

// Contact search for participant modal
let participantSearchTimeout;
const participantContactSearch = document.getElementById('participantContactSearch');
const participantSearchResults = document.getElementById('participantSearchResults');

participantContactSearch.addEventListener('input', function() {
    clearTimeout(participantSearchTimeout);
    const query = this.value.trim();

    if (query.length < 2) {
        participantSearchResults.classList.add('hidden');
        return;
    }

    participantSearchTimeout = setTimeout(() => {
        fetch(`/transactions/api/contacts/search?q=${encodeURIComponent(query)}`)
            .then(res => res.json())
            .then(contacts => {
                if (contacts.length === 0) {
                    participantSearchResults.innerHTML = '<div class="p-4 text-slate-500 text-sm text-center">No contacts found</div>';
                } else {
                    participantSearchResults.innerHTML = contacts.map(c => {
                        const hasRequired = c.first_name && c.last_name && c.email;
                        const missingBadge = !hasRequired ? '<span class="text-xs text-amber-600 ml-2"><i class="fas fa-exclamation-triangle"></i> Missing info</span>' : '';
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
    // Validate contact has required fields
    if (!contact.first_name || !contact.last_name) {
        showToast('This contact is missing a name. Please update the contact first.', 'error');
        return;
    }
    if (!contact.email) {
        showToast('This contact is missing an email address. Please update the contact first.', 'error');
        return;
    }

    // Set the contact ID
    document.getElementById('participantContactId').value = contact.id;

    // Show the preview
    document.getElementById('selectedContactInitials').textContent =
        (contact.first_name[0] + contact.last_name[0]).toUpperCase();
    document.getElementById('selectedContactName').textContent = contact.name;
    document.getElementById('selectedContactEmail').textContent = contact.email || '';
    document.getElementById('selectedContactPhone').textContent = contact.phone || '';

    document.getElementById('selectedContactPreview').classList.remove('hidden');
    document.getElementById('participantContactSearch').classList.add('hidden');
    participantSearchResults.classList.add('hidden');

    // Enable submit button
    updateAddParticipantButton();
}

function clearSelectedContact() {
    document.getElementById('participantContactId').value = '';
    document.getElementById('selectedContactPreview').classList.add('hidden');
    document.getElementById('participantContactSearch').classList.remove('hidden');
    document.getElementById('participantContactSearch').value = '';
    document.getElementById('addParticipantBtn').disabled = true;
}

function updateAddParticipantButton() {
    const hasContact = document.getElementById('participantContactId').value !== '';
    const hasRole = document.getElementById('participantRole').value !== '';
    document.getElementById('addParticipantBtn').disabled = !(hasContact && hasRole);
}

// Update button state when role changes
document.getElementById('participantRole').addEventListener('change', updateAddParticipantButton);

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

document.getElementById('docTemplateSelect').addEventListener('change', function() {
    const customField = document.getElementById('customDocNameField');
    if (this.value === 'custom') {
        customField.classList.remove('hidden');
        customField.querySelector('input').required = true;
    } else {
        customField.classList.add('hidden');
        customField.querySelector('input').required = false;
    }
});

document.getElementById('addDocumentForm').addEventListener('submit', function(e) {
    e.preventDefault();
    const formData = new FormData(this);

    const select = document.getElementById('docTemplateSelect');
    const selectedOption = select.options[select.selectedIndex];
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
