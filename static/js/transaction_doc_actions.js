/**
 * Transaction Detail - Document Actions
 * Handles: signature operations, document viewing/downloading, status checks
 */

function sendForSignature(docId) {
    if (!confirm('Send this document for signature?')) return;

    showToast('Sending document...', 'info');

    fetch(`/transactions/${transactionId}/documents/${docId}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            showToast(`Document sent to ${data.submitters} signer(s)!`, 'success');
            setTimeout(() => location.reload(), 2000);
        } else {
            showToast('Error: ' + data.error, 'error');
        }
    })
    .catch(err => showToast('Error: ' + err.message, 'error'));
}

function sendAdhocDocument(docId) {
    if (!confirm('Send this document for signature?')) return;

    showToast('Sending document...', 'info');

    fetch(`/transactions/${transactionId}/documents/${docId}/send-adhoc`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            showToast('Document sent for signature!', 'success');
            setTimeout(() => location.reload(), 2000);
        } else {
            showToast('Error: ' + data.error, 'error');
        }
    })
    .catch(err => showToast('Error: ' + err.message, 'error'));
}

function convertToHybrid(docId) {
    if (!confirm('Convert this wet-signed document for additional e-signatures? You will place signature fields for the remaining signers.')) return;

    showToast('Preparing document...', 'info');

    fetch(`/transactions/${transactionId}/documents/${docId}/convert-hybrid`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            showToast('Redirecting to field editor...', 'success');
            window.location.href = data.redirect_url;
        } else {
            showToast('Error: ' + data.error, 'error');
        }
    })
    .catch(err => showToast('Error: ' + err.message, 'error'));
}

function checkSignatureStatus(docId) {
    fetch(`/transactions/${transactionId}/documents/${docId}/status`)
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            const signers = data.signers || [];
            const statuses = signers.map(s => s.status).join(', ');
            showToast(`Status: ${data.overall_status}. Signers: ${statuses}`, 'info');
        } else {
            showToast('Error: ' + data.error, 'error');
        }
    });
}

function voidDocument(docId) {
    if (!confirm('Void this document? This will clear the current signature request and allow you to edit and resend.')) return;

    showToast('Voiding document...', 'info');

    fetch(`/transactions/${transactionId}/documents/${docId}/void`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            showToast('Document voided! You can now edit and resend.', 'success');
            setTimeout(() => location.reload(), 1500);
        } else {
            showToast('Error: ' + data.error, 'error');
        }
    })
    .catch(err => showToast('Error: ' + err.message, 'error'));
}

function resendDocument(docId) {
    if (!confirm('Resend signature request emails to signers who haven\'t completed signing?')) return;

    showToast('Resending emails...', 'info');

    fetch(`/transactions/${transactionId}/documents/${docId}/resend`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            showToast(`Reminder emails sent to ${data.resent_count} signer(s)!`, 'success');
        } else {
            showToast('Error: ' + data.error, 'error');
        }
    })
    .catch(err => showToast('Error: ' + err.message, 'error'));
}

function simulateSignature(docId) {
    if (!confirm('Simulate signature completion?')) return;

    fetch(`/transactions/${transactionId}/documents/${docId}/simulate-sign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            showToast('Signature simulated!', 'success');
            setTimeout(() => location.reload(), 1500);
        } else {
            showToast('Error: ' + data.error, 'error');
        }
    });
}

function downloadDocument(docId) {
    fetch(`/transactions/${transactionId}/documents/${docId}/download`)
    .then(res => res.json())
    .then(data => {
        if (data.success && data.documents && data.documents.length > 0) {
            if (data.mock_mode) {
                showToast('Download URL: ' + data.documents[0].url, 'info');
            } else {
                window.open(data.documents[0].url, '_blank');
            }
        } else {
            showToast('Error: ' + (data.error || 'No documents'), 'error');
        }
    });
}

function viewSignedDocument(docId) {
    fetch(`/transactions/${transactionId}/documents/${docId}/download`)
    .then(res => res.json())
    .then(data => {
        if (data.success && data.documents && data.documents.length > 0) {
            if (data.mock_mode) {
                showToast('Mock Mode: Signed document URL would be ' + data.documents[0].url, 'info');
            } else {
                window.open(data.documents[0].url, '_blank');
            }
        } else {
            showToast('Error: ' + (data.error || 'No signed documents available'), 'error');
        }
    });
}

function viewStoredDocument(docId) {
    // View locally stored signed document from Supabase
    fetch(`/transactions/${transactionId}/documents/${docId}/view-signed`)
    .then(res => res.json())
    .then(data => {
        if (data.success && data.url) {
            window.open(data.url, '_blank');
        } else {
            showToast(data.error || 'Failed to get document URL', 'error');
        }
    })
    .catch(error => {
        showToast('Failed to view document. Please try again.', 'error');
        console.error('View stored document error:', error);
    });
}

// View static document function
function viewStaticDocument(docId) {
    fetch(`/transactions/${transactionId}/documents/${docId}/view-static`)
    .then(res => res.json())
    .then(data => {
        if (data.success && data.url) {
            window.open(data.url, '_blank');
        } else {
            showToast(data.error || 'Failed to get document URL', 'error');
        }
    })
    .catch(error => {
        showToast('Failed to view document. Please try again.', 'error');
        console.error('View static document error:', error);
    });
}
