/**
 * Transaction Detail - Upload Modals
 * Handles: scanned doc upload, e-sign upload, completed upload, static upload, signature upload
 */

// =============================================================================
// SCROLL POSITION PERSISTENCE
// =============================================================================

const DOCUMENTS_SECTION_ID = 'transaction-documents-card';

function reloadPreservingScroll(targetId) {
    // Set the hash on the current URL, then reload.
    // The browser reloads the full page from the server and then scrolls
    // to the hash element natively — no JS timing issues.
    const target = targetId || DOCUMENTS_SECTION_ID;
    history.replaceState(null, '', '#' + target);
    window.location.reload();
}

// After the reload, clean the hash from the URL so it doesn't persist.
window.addEventListener('load', function () {
    const hash = window.location.hash;
    if (hash === '#' + DOCUMENTS_SECTION_ID || hash.startsWith('#transaction-document-')) {
        history.replaceState(null, '', window.location.pathname + window.location.search);
    }
});

// =============================================================================
// SHARED HELPER
// =============================================================================

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// =============================================================================
// UPLOAD SCANNED DOCUMENT MODAL
// =============================================================================

let currentUploadDocId = null;

function showUploadScanModal(docId, docName) {
    currentUploadDocId = docId;
    document.getElementById('uploadDocId').value = docId;
    document.getElementById('uploadDocName').textContent = docName;

    // Reset form state
    document.getElementById('scanFileInput').value = '';
    document.getElementById('dropZoneContent').classList.remove('hidden');
    document.getElementById('selectedFileInfo').classList.add('hidden');
    document.getElementById('uploadProgress').classList.add('hidden');
    document.getElementById('uploadError').classList.add('hidden');
    document.getElementById('uploadScanBtn').disabled = true;

    document.getElementById('uploadScanModal').classList.remove('hidden');
}

function closeUploadScanModal() {
    document.getElementById('uploadScanModal').classList.add('hidden');
    currentUploadDocId = null;
}

function handleFileSelect(input) {
    const file = input.files[0];
    if (!file) return;

    // Validate file type
    if (!file.name.toLowerCase().endsWith('.pdf')) {
        showUploadError('Please select a PDF file.');
        return;
    }

    // Validate file size (25MB max)
    const maxSize = 25 * 1024 * 1024;
    if (file.size > maxSize) {
        showUploadError('File too large. Maximum size is 25MB.');
        return;
    }

    // Show selected file info
    document.getElementById('dropZoneContent').classList.add('hidden');
    document.getElementById('selectedFileInfo').classList.remove('hidden');
    document.getElementById('selectedFileName').textContent = file.name;
    document.getElementById('selectedFileSize').textContent = formatFileSize(file.size);
    document.getElementById('uploadError').classList.add('hidden');
    document.getElementById('uploadScanBtn').disabled = false;
}

function showUploadError(message) {
    document.getElementById('uploadError').classList.remove('hidden');
    document.getElementById('uploadErrorText').textContent = message;
    document.getElementById('uploadScanBtn').disabled = true;
}

// Handle drag and drop
const dropZone = document.getElementById('dropZone');
if (dropZone) {
    dropZone.addEventListener('dragover', function(e) {
        e.preventDefault();
        this.classList.add('border-orange-400', 'bg-orange-50/30');
    });

    dropZone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        this.classList.remove('border-orange-400', 'bg-orange-50/30');
    });

    dropZone.addEventListener('drop', function(e) {
        e.preventDefault();
        this.classList.remove('border-orange-400', 'bg-orange-50/30');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const fileInput = document.getElementById('scanFileInput');
            fileInput.files = files;
            handleFileSelect(fileInput);
        }
    });
}

// Handle form submission
const uploadForm = document.getElementById('uploadScanForm');
if (uploadForm) {
    uploadForm.addEventListener('submit', function(e) {
        e.preventDefault();

        const fileInput = document.getElementById('scanFileInput');
        const file = fileInput.files[0];
        if (!file) {
            showUploadError('Please select a file first.');
            return;
        }

        const formData = new FormData();
        formData.append('file', file);

        // Show progress
        document.getElementById('uploadProgress').classList.remove('hidden');
        document.getElementById('uploadScanBtn').disabled = true;

        // Use XMLHttpRequest for progress tracking
        const xhr = new XMLHttpRequest();

        xhr.upload.addEventListener('progress', function(e) {
            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                document.getElementById('uploadPercent').textContent = percent + '%';
                document.getElementById('uploadProgressBar').style.width = percent + '%';
            }
        });

        xhr.addEventListener('load', function() {
            if (xhr.status === 200) {
                const data = JSON.parse(xhr.responseText);
                if (data.success) {
                    showToast('Scanned document uploaded successfully!', 'success');
                    closeUploadScanModal();
                    reloadPreservingScroll(`transaction-document-${currentUploadDocId}`);
                } else {
                    showUploadError(data.error || 'Upload failed');
                    document.getElementById('uploadProgress').classList.add('hidden');
                    document.getElementById('uploadScanBtn').disabled = false;
                }
            } else {
                try {
                    const data = JSON.parse(xhr.responseText);
                    showUploadError(data.error || 'Upload failed');
                } catch (err) {
                    showUploadError('Upload failed. Please try again.');
                }
                document.getElementById('uploadProgress').classList.add('hidden');
                document.getElementById('uploadScanBtn').disabled = false;
            }
        });

        xhr.addEventListener('error', function() {
            showUploadError('Network error. Please try again.');
            document.getElementById('uploadProgress').classList.add('hidden');
            document.getElementById('uploadScanBtn').disabled = false;
        });

        xhr.open('POST', `/transactions/${transactionId}/documents/${currentUploadDocId}/upload-scan`);
        xhr.send(formData);
    });
}

// =============================================================================
// UPLOAD FOR E-SIGN (UNIFIED MODAL VIEW)
// =============================================================================

function resetEsignForm() {
    const esignDocName = document.getElementById('esignDocName');
    const esignFileInput = document.getElementById('esignFileInput');
    if (esignDocName) esignDocName.value = '';
    if (esignFileInput) esignFileInput.value = '';

    const esignDropContent = document.getElementById('esignDropContent');
    const esignFileInfo = document.getElementById('esignFileInfo');
    const esignProgress = document.getElementById('esignProgress');
    const esignError = document.getElementById('esignError');
    const uploadEsignBtn = document.getElementById('uploadEsignBtn');

    if (esignDropContent) esignDropContent.classList.remove('hidden');
    if (esignFileInfo) esignFileInfo.classList.add('hidden');
    if (esignProgress) esignProgress.classList.add('hidden');
    if (esignError) esignError.classList.add('hidden');
    if (uploadEsignBtn) uploadEsignBtn.disabled = true;
}

function handleEsignFileSelect(input) {
    const file = input.files[0];
    if (!file) return;

    // Validate file type
    if (!file.name.toLowerCase().endsWith('.pdf')) {
        showEsignError('Please select a PDF file.');
        return;
    }

    // Validate file size (25MB max)
    const maxSize = 25 * 1024 * 1024;
    if (file.size > maxSize) {
        showEsignError('File too large. Maximum size is 25MB.');
        return;
    }

    // Show selected file info
    document.getElementById('esignDropContent').classList.add('hidden');
    document.getElementById('esignFileInfo').classList.remove('hidden');
    document.getElementById('esignFileName').textContent = file.name;
    document.getElementById('esignFileSize').textContent = formatFileSize(file.size);
    document.getElementById('esignError').classList.add('hidden');
    document.getElementById('uploadEsignBtn').disabled = false;

    // Auto-fill document name if empty
    const docNameInput = document.getElementById('esignDocName');
    if (!docNameInput.value.trim()) {
        const nameWithoutExt = file.name.replace(/\.pdf$/i, '');
        docNameInput.value = nameWithoutExt;
    }
}

function showEsignError(message) {
    document.getElementById('esignError').classList.remove('hidden');
    document.getElementById('esignErrorText').textContent = message;
    document.getElementById('uploadEsignBtn').disabled = true;
}

// Handle drag and drop for e-sign upload
const esignDropZone = document.getElementById('esignDropZone');
if (esignDropZone) {
    esignDropZone.addEventListener('dragover', function(e) {
        e.preventDefault();
        this.classList.add('border-orange-400', 'bg-orange-50/30');
    });

    esignDropZone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        this.classList.remove('border-orange-400', 'bg-orange-50/30');
    });

    esignDropZone.addEventListener('drop', function(e) {
        e.preventDefault();
        this.classList.remove('border-orange-400', 'bg-orange-50/30');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const fileInput = document.getElementById('esignFileInput');
            fileInput.files = files;
            handleEsignFileSelect(fileInput);
        }
    });
}

// Handle e-sign document form submission
const esignForm = document.getElementById('uploadEsignForm');
if (esignForm) {
    esignForm.addEventListener('submit', function(e) {
        e.preventDefault();

        const fileInput = document.getElementById('esignFileInput');
        const file = fileInput.files[0];
        if (!file) {
            showEsignError('Please select a file first.');
            return;
        }

        const documentName = document.getElementById('esignDocName').value.trim();

        const formData = new FormData();
        formData.append('file', file);
        formData.append('document_name', documentName);

        // Show progress
        document.getElementById('esignProgress').classList.remove('hidden');
        document.getElementById('uploadEsignBtn').disabled = true;

        // Use XMLHttpRequest for progress tracking
        const xhr = new XMLHttpRequest();

        xhr.upload.addEventListener('progress', function(e) {
            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                document.getElementById('esignPercent').textContent = percent + '%';
                document.getElementById('esignProgressBar').style.width = percent + '%';
            }
        });

        xhr.addEventListener('load', function() {
            if (xhr.status === 200) {
                const data = JSON.parse(xhr.responseText);
                if (data.success) {
                    showToast('Document uploaded! Redirecting to field editor...', 'success');
                    closeAddDocumentModal();
                    // Redirect to field editor
                    if (data.redirect_url) {
                        window.location.href = data.redirect_url;
                    } else {
                        reloadPreservingScroll(`transaction-document-${currentSignatureDocId}`);
                    }
                } else {
                    showEsignError(data.error || 'Upload failed');
                    document.getElementById('esignProgress').classList.add('hidden');
                    document.getElementById('uploadEsignBtn').disabled = false;
                }
            } else {
                try {
                    const data = JSON.parse(xhr.responseText);
                    showEsignError(data.error || 'Upload failed');
                } catch (err) {
                    showEsignError('Upload failed. Please try again.');
                }
                document.getElementById('esignProgress').classList.add('hidden');
                document.getElementById('uploadEsignBtn').disabled = false;
            }
        });

        xhr.addEventListener('error', function() {
            showEsignError('Network error. Please try again.');
            document.getElementById('esignProgress').classList.add('hidden');
            document.getElementById('uploadEsignBtn').disabled = false;
        });

        xhr.open('POST', `/transactions/${transactionId}/documents/upload-external`);
        xhr.send(formData);
    });
}

// =============================================================================
// UPLOAD COMPLETED DOCUMENT (UNIFIED MODAL VIEW)
// =============================================================================

function resetCompletedForm() {
    const completedDocName = document.getElementById('completedDocName');
    const completedFileInput = document.getElementById('completedFileInput');
    if (completedDocName) completedDocName.value = '';
    if (completedFileInput) completedFileInput.value = '';

    const completedDropContent = document.getElementById('completedDropContent');
    const completedFileInfo = document.getElementById('completedFileInfo');
    const completedProgress = document.getElementById('completedProgress');
    const completedError = document.getElementById('completedError');
    const uploadCompletedBtn = document.getElementById('uploadCompletedBtn');

    if (completedDropContent) completedDropContent.classList.remove('hidden');
    if (completedFileInfo) completedFileInfo.classList.add('hidden');
    if (completedProgress) completedProgress.classList.add('hidden');
    if (completedError) completedError.classList.add('hidden');
    if (uploadCompletedBtn) uploadCompletedBtn.disabled = true;
}

function handleCompletedFileSelect(input) {
    const file = input.files[0];
    if (!file) return;

    // Validate file type
    if (!file.name.toLowerCase().endsWith('.pdf')) {
        showCompletedError('Please select a PDF file.');
        return;
    }

    // Validate file size (25MB max)
    const maxSize = 25 * 1024 * 1024;
    if (file.size > maxSize) {
        showCompletedError('File too large. Maximum size is 25MB.');
        return;
    }

    // Show selected file info
    document.getElementById('completedDropContent').classList.add('hidden');
    document.getElementById('completedFileInfo').classList.remove('hidden');
    document.getElementById('completedFileName').textContent = file.name;
    document.getElementById('completedFileSize').textContent = formatFileSize(file.size);
    document.getElementById('completedError').classList.add('hidden');
    document.getElementById('uploadCompletedBtn').disabled = false;

    // Auto-fill document name if empty
    const docNameInput = document.getElementById('completedDocName');
    if (!docNameInput.value.trim()) {
        const nameWithoutExt = file.name.replace(/\.pdf$/i, '');
        docNameInput.value = nameWithoutExt;
    }
}

function showCompletedError(message) {
    document.getElementById('completedError').classList.remove('hidden');
    document.getElementById('completedErrorText').textContent = message;
    document.getElementById('uploadCompletedBtn').disabled = true;
}

// Handle drag and drop for completed upload
const completedDropZone = document.getElementById('completedDropZone');
if (completedDropZone) {
    completedDropZone.addEventListener('dragover', function(e) {
        e.preventDefault();
        this.classList.add('border-orange-400', 'bg-orange-50/30');
    });

    completedDropZone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        this.classList.remove('border-orange-400', 'bg-orange-50/30');
    });

    completedDropZone.addEventListener('drop', function(e) {
        e.preventDefault();
        this.classList.remove('border-orange-400', 'bg-orange-50/30');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const fileInput = document.getElementById('completedFileInput');
            fileInput.files = files;
            handleCompletedFileSelect(fileInput);
        }
    });
}

// Handle completed document form submission
const completedForm = document.getElementById('uploadCompletedForm');
if (completedForm) {
    completedForm.addEventListener('submit', function(e) {
        e.preventDefault();

        const fileInput = document.getElementById('completedFileInput');
        const file = fileInput.files[0];
        if (!file) {
            showCompletedError('Please select a file first.');
            return;
        }

        const documentName = document.getElementById('completedDocName').value.trim();

        const formData = new FormData();
        formData.append('file', file);
        formData.append('document_name', documentName);

        // Show progress
        document.getElementById('completedProgress').classList.remove('hidden');
        document.getElementById('uploadCompletedBtn').disabled = true;

        // Use XMLHttpRequest for progress tracking
        const xhr = new XMLHttpRequest();

        xhr.upload.addEventListener('progress', function(e) {
            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                document.getElementById('completedPercent').textContent = percent + '%';
                document.getElementById('completedProgressBar').style.width = percent + '%';
            }
        });

        xhr.addEventListener('load', function() {
            if (xhr.status === 200) {
                const data = JSON.parse(xhr.responseText);
                if (data.success) {
                    showToast('Document uploaded successfully!', 'success');
                    closeAddDocumentModal();
                    reloadPreservingScroll();
                } else {
                    showCompletedError(data.error || 'Upload failed');
                    document.getElementById('completedProgress').classList.add('hidden');
                    document.getElementById('uploadCompletedBtn').disabled = false;
                }
            } else {
                try {
                    const data = JSON.parse(xhr.responseText);
                    showCompletedError(data.error || 'Upload failed');
                } catch (err) {
                    showCompletedError('Upload failed. Please try again.');
                }
                document.getElementById('completedProgress').classList.add('hidden');
                document.getElementById('uploadCompletedBtn').disabled = false;
            }
        });

        xhr.addEventListener('error', function() {
            showCompletedError('Network error. Please try again.');
            document.getElementById('completedProgress').classList.add('hidden');
            document.getElementById('uploadCompletedBtn').disabled = false;
        });

        xhr.open('POST', `/transactions/${transactionId}/documents/upload-completed`);
        xhr.send(formData);
    });
}

// =============================================================================
// UPLOAD STATIC DOCUMENT MODAL (FOR PLACEHOLDERS)
// =============================================================================

let currentStaticDocId = null;

function showUploadStaticModal(docId, docName) {
    currentStaticDocId = docId;
    document.getElementById('staticDocId').value = docId;
    document.getElementById('staticDocName').textContent = docName;

    // Reset form state
    document.getElementById('staticFileInput').value = '';
    document.getElementById('staticDropContent').classList.remove('hidden');
    document.getElementById('staticFileInfo').classList.add('hidden');
    document.getElementById('staticProgress').classList.add('hidden');
    document.getElementById('staticError').classList.add('hidden');
    document.getElementById('uploadStaticBtn').disabled = true;

    document.getElementById('uploadStaticModal').classList.remove('hidden');
}

function closeUploadStaticModal() {
    document.getElementById('uploadStaticModal').classList.add('hidden');
    currentStaticDocId = null;
}

function handleStaticFileSelect(input) {
    const file = input.files[0];
    if (!file) return;

    // Validate file type
    if (!file.name.toLowerCase().endsWith('.pdf')) {
        showStaticError('Please select a PDF file.');
        return;
    }

    // Validate file size (25MB max)
    const maxSize = 25 * 1024 * 1024;
    if (file.size > maxSize) {
        showStaticError('File too large. Maximum size is 25MB.');
        return;
    }

    // Show selected file info
    document.getElementById('staticDropContent').classList.add('hidden');
    document.getElementById('staticFileInfo').classList.remove('hidden');
    document.getElementById('staticFileName').textContent = file.name;
    document.getElementById('staticFileSize').textContent = formatFileSize(file.size);
    document.getElementById('staticError').classList.add('hidden');
    document.getElementById('uploadStaticBtn').disabled = false;
}

function showStaticError(message) {
    document.getElementById('staticError').classList.remove('hidden');
    document.getElementById('staticErrorText').textContent = message;
    document.getElementById('uploadStaticBtn').disabled = true;
}

// Handle drag and drop for static upload
const staticDropZone = document.getElementById('staticDropZone');
if (staticDropZone) {
    staticDropZone.addEventListener('dragover', function(e) {
        e.preventDefault();
        this.classList.add('border-orange-400', 'bg-orange-50/30');
    });

    staticDropZone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        this.classList.remove('border-orange-400', 'bg-orange-50/30');
    });

    staticDropZone.addEventListener('drop', function(e) {
        e.preventDefault();
        this.classList.remove('border-orange-400', 'bg-orange-50/30');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const fileInput = document.getElementById('staticFileInput');
            fileInput.files = files;
            handleStaticFileSelect(fileInput);
        }
    });
}

// Handle static document form submission
const staticForm = document.getElementById('uploadStaticForm');
if (staticForm) {
    staticForm.addEventListener('submit', function(e) {
        e.preventDefault();

        const fileInput = document.getElementById('staticFileInput');
        const file = fileInput.files[0];
        if (!file) {
            showStaticError('Please select a file first.');
            return;
        }

        const formData = new FormData();
        formData.append('file', file);

        // Show progress
        document.getElementById('staticProgress').classList.remove('hidden');
        document.getElementById('uploadStaticBtn').disabled = true;

        // Use XMLHttpRequest for progress tracking
        const xhr = new XMLHttpRequest();

        xhr.upload.addEventListener('progress', function(e) {
            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                document.getElementById('staticPercent').textContent = percent + '%';
                document.getElementById('staticProgressBar').style.width = percent + '%';
            }
        });

        xhr.addEventListener('load', function() {
            if (xhr.status === 200) {
                const data = JSON.parse(xhr.responseText);
                if (data.success) {
                    showToast('Document uploaded successfully!', 'success');
                    closeUploadStaticModal();
                    reloadPreservingScroll(`transaction-document-${currentStaticDocId}`);
                } else {
                    showStaticError(data.error || 'Upload failed');
                    document.getElementById('staticProgress').classList.add('hidden');
                    document.getElementById('uploadStaticBtn').disabled = false;
                }
            } else {
                try {
                    const data = JSON.parse(xhr.responseText);
                    showStaticError(data.error || 'Upload failed');
                } catch (err) {
                    showStaticError('Upload failed. Please try again.');
                }
                document.getElementById('staticProgress').classList.add('hidden');
                document.getElementById('uploadStaticBtn').disabled = false;
            }
        });

        xhr.addEventListener('error', function() {
            showStaticError('Network error. Please try again.');
            document.getElementById('staticProgress').classList.add('hidden');
            document.getElementById('uploadStaticBtn').disabled = false;
        });

        xhr.open('POST', `/transactions/${transactionId}/documents/${currentStaticDocId}/upload-static`);
        xhr.send(formData);
    });
}

// =============================================================================
// UPLOAD FOR SIGNATURE MODAL (PLACEHOLDER TO EXTERNAL)
// =============================================================================

let currentSignatureDocId = null;

function showUploadForSignatureModal(docId, docName) {
    currentSignatureDocId = docId;
    document.getElementById('signatureDocId').value = docId;
    document.getElementById('signatureDocName').textContent = docName;

    // Reset form state
    document.getElementById('signatureFileInput').value = '';
    document.getElementById('signatureDropContent').classList.remove('hidden');
    document.getElementById('signatureFileInfo').classList.add('hidden');
    document.getElementById('signatureProgress').classList.add('hidden');
    document.getElementById('signatureError').classList.add('hidden');
    document.getElementById('uploadForSignatureBtn').disabled = true;

    document.getElementById('uploadForSignatureModal').classList.remove('hidden');
}

function closeUploadForSignatureModal() {
    document.getElementById('uploadForSignatureModal').classList.add('hidden');
    currentSignatureDocId = null;
}

function handleSignatureFileSelect(input) {
    const file = input.files[0];
    if (!file) return;

    // Validate file type
    if (!file.name.toLowerCase().endsWith('.pdf')) {
        showSignatureError('Please select a PDF file.');
        return;
    }

    // Validate file size (25MB max)
    const maxSize = 25 * 1024 * 1024;
    if (file.size > maxSize) {
        showSignatureError('File too large. Maximum size is 25MB.');
        return;
    }

    // Show selected file info
    document.getElementById('signatureDropContent').classList.add('hidden');
    document.getElementById('signatureFileInfo').classList.remove('hidden');
    document.getElementById('signatureFileName').textContent = file.name;
    document.getElementById('signatureFileSize').textContent = formatFileSize(file.size);
    document.getElementById('signatureError').classList.add('hidden');
    document.getElementById('uploadForSignatureBtn').disabled = false;
}

function showSignatureError(message) {
    document.getElementById('signatureError').classList.remove('hidden');
    document.getElementById('signatureErrorText').textContent = message;
    document.getElementById('uploadForSignatureBtn').disabled = true;
}

// Handle drag and drop for signature upload
const signatureDropZone = document.getElementById('signatureDropZone');
if (signatureDropZone) {
    signatureDropZone.addEventListener('dragover', function(e) {
        e.preventDefault();
        this.classList.add('border-orange-400', 'bg-orange-50/30');
    });

    signatureDropZone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        this.classList.remove('border-orange-400', 'bg-orange-50/30');
    });

    signatureDropZone.addEventListener('drop', function(e) {
        e.preventDefault();
        this.classList.remove('border-orange-400', 'bg-orange-50/30');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const fileInput = document.getElementById('signatureFileInput');
            fileInput.files = files;
            handleSignatureFileSelect(fileInput);
        }
    });
}

// Handle signature document form submission
const signatureForm = document.getElementById('uploadForSignatureForm');
if (signatureForm) {
    signatureForm.addEventListener('submit', function(e) {
        e.preventDefault();

        const fileInput = document.getElementById('signatureFileInput');
        const file = fileInput.files[0];
        if (!file) {
            showSignatureError('Please select a file first.');
            return;
        }

        const formData = new FormData();
        formData.append('file', file);

        // Show progress
        document.getElementById('signatureProgress').classList.remove('hidden');
        document.getElementById('uploadForSignatureBtn').disabled = true;

        // Use XMLHttpRequest for progress tracking
        const xhr = new XMLHttpRequest();

        xhr.upload.addEventListener('progress', function(e) {
            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                document.getElementById('signaturePercent').textContent = percent + '%';
                document.getElementById('signatureProgressBar').style.width = percent + '%';
            }
        });

        xhr.addEventListener('load', function() {
            if (xhr.status === 200) {
                const data = JSON.parse(xhr.responseText);
                if (data.success) {
                    showToast('Document uploaded! Redirecting to field editor...', 'success');
                    closeUploadForSignatureModal();
                    // Redirect to field editor
                    if (data.redirect_url) {
                        window.location.href = data.redirect_url;
                    } else {
                        reloadPreservingScroll();
                    }
                } else {
                    showSignatureError(data.error || 'Upload failed');
                    document.getElementById('signatureProgress').classList.add('hidden');
                    document.getElementById('uploadForSignatureBtn').disabled = false;
                }
            } else {
                try {
                    const data = JSON.parse(xhr.responseText);
                    showSignatureError(data.error || 'Upload failed');
                } catch (err) {
                    showSignatureError('Upload failed. Please try again.');
                }
                document.getElementById('signatureProgress').classList.add('hidden');
                document.getElementById('uploadForSignatureBtn').disabled = false;
            }
        });

        xhr.addEventListener('error', function() {
            showSignatureError('Network error. Please try again.');
            document.getElementById('signatureProgress').classList.add('hidden');
            document.getElementById('uploadForSignatureBtn').disabled = false;
        });

        xhr.open('POST', `/transactions/${transactionId}/documents/${currentSignatureDocId}/upload-for-signature`);
        xhr.send(formData);
    });
}


// =============================================================================
// FULFILL PLACEHOLDER MODAL (placeholder_upload_only workflow)
// =============================================================================

let currentFulfillDocId = null;

function showFulfillPlaceholderModal(docId, docName, isReplace) {
    currentFulfillDocId = docId;
    document.getElementById('fulfillDocId').value = docId;
    document.getElementById('fulfillDocName').textContent = docName;

    document.getElementById('fulfillFileInput').value = '';
    document.getElementById('fulfillDropContent').classList.remove('hidden');
    document.getElementById('fulfillFileInfo').classList.add('hidden');
    document.getElementById('fulfillProgress').classList.add('hidden');
    document.getElementById('fulfillError').classList.add('hidden');
    document.getElementById('fulfillUploadBtn').disabled = true;

    const infoBanner = document.getElementById('fulfillInfoBanner');
    const replaceBanner = document.getElementById('fulfillReplaceBanner');
    const uploadBtn = document.getElementById('fulfillUploadBtn');

    if (isReplace) {
        infoBanner.classList.add('hidden');
        replaceBanner.classList.remove('hidden');
        uploadBtn.innerHTML = '<i class="fas fa-sync-alt mr-2"></i>Replace & Delete Old';
    } else {
        infoBanner.classList.remove('hidden');
        replaceBanner.classList.add('hidden');
        uploadBtn.innerHTML = '<i class="fas fa-upload mr-2"></i>Upload';
    }

    document.getElementById('fulfillPlaceholderModal').classList.remove('hidden');
}

function closeFulfillPlaceholderModal() {
    document.getElementById('fulfillPlaceholderModal').classList.add('hidden');
    currentFulfillDocId = null;
}

function handleFulfillFileSelect(input) {
    const file = input.files[0];
    if (!file) return;

    if (!file.name.toLowerCase().endsWith('.pdf')) {
        showFulfillError('Please select a PDF file.');
        return;
    }

    const maxSize = 25 * 1024 * 1024;
    if (file.size > maxSize) {
        showFulfillError('File too large. Maximum size is 25MB.');
        return;
    }

    document.getElementById('fulfillDropContent').classList.add('hidden');
    document.getElementById('fulfillFileInfo').classList.remove('hidden');
    document.getElementById('fulfillFileName').textContent = file.name;
    document.getElementById('fulfillFileSize').textContent = formatFileSize(file.size);
    document.getElementById('fulfillError').classList.add('hidden');
    document.getElementById('fulfillUploadBtn').disabled = false;
}

function showFulfillError(message) {
    document.getElementById('fulfillError').classList.remove('hidden');
    document.getElementById('fulfillErrorText').textContent = message;
    document.getElementById('fulfillUploadBtn').disabled = true;
}

// Drag and drop for fulfill placeholder
const fulfillDropZone = document.getElementById('fulfillDropZone');
if (fulfillDropZone) {
    fulfillDropZone.addEventListener('dragover', function(e) {
        e.preventDefault();
        this.classList.add('border-orange-400', 'bg-orange-50/30');
    });

    fulfillDropZone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        this.classList.remove('border-orange-400', 'bg-orange-50/30');
    });

    fulfillDropZone.addEventListener('drop', function(e) {
        e.preventDefault();
        this.classList.remove('border-orange-400', 'bg-orange-50/30');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const fileInput = document.getElementById('fulfillFileInput');
            fileInput.files = files;
            handleFulfillFileSelect(fileInput);
        }
    });
}

// Fulfill placeholder form submission
const fulfillForm = document.getElementById('fulfillPlaceholderForm');
if (fulfillForm) {
    fulfillForm.addEventListener('submit', function(e) {
        e.preventDefault();

        const fileInput = document.getElementById('fulfillFileInput');
        const file = fileInput.files[0];
        if (!file) {
            showFulfillError('Please select a file first.');
            return;
        }

        const formData = new FormData();
        formData.append('file', file);

        document.getElementById('fulfillProgress').classList.remove('hidden');
        document.getElementById('fulfillUploadBtn').disabled = true;

        const xhr = new XMLHttpRequest();
        const transactionId = TX_CONFIG.transactionId;

        xhr.upload.addEventListener('progress', function(e) {
            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                document.getElementById('fulfillPercent').textContent = percent + '%';
                document.getElementById('fulfillProgressBar').style.width = percent + '%';
            }
        });

        xhr.addEventListener('load', function() {
            if (xhr.status === 200) {
                const data = JSON.parse(xhr.responseText);
                if (data.success) {
                    showToast('Document uploaded successfully!', 'success');
                    closeFulfillPlaceholderModal();
                    reloadPreservingScroll(`transaction-document-${currentFulfillDocId}`);
                } else {
                    showFulfillError(data.error || 'Upload failed');
                    document.getElementById('fulfillProgress').classList.add('hidden');
                    document.getElementById('fulfillUploadBtn').disabled = false;
                }
            } else {
                try {
                    const data = JSON.parse(xhr.responseText);
                    showFulfillError(data.error || 'Upload failed');
                } catch (err) {
                    showFulfillError('Upload failed. Please try again.');
                }
                document.getElementById('fulfillProgress').classList.add('hidden');
                document.getElementById('fulfillUploadBtn').disabled = false;
            }
        });

        xhr.addEventListener('error', function() {
            showFulfillError('Network error. Please try again.');
            document.getElementById('fulfillProgress').classList.add('hidden');
            document.getElementById('fulfillUploadBtn').disabled = false;
        });

        xhr.open('POST', `/transactions/${transactionId}/documents/${currentFulfillDocId}/fulfill`);
        xhr.send(formData);
    });
}


// =============================================================================
// ADD CUSTOM PLACEHOLDER MODAL (placeholder_upload_only workflow)
// =============================================================================

function showAddPlaceholderModal() {
    document.getElementById('placeholderDocName').value = '';
    document.getElementById('addPlaceholderModal').classList.remove('hidden');
}

function closeAddPlaceholderModal() {
    document.getElementById('addPlaceholderModal').classList.add('hidden');
}

const addPlaceholderForm = document.getElementById('addPlaceholderForm');
if (addPlaceholderForm) {
    addPlaceholderForm.addEventListener('submit', function(e) {
        e.preventDefault();

        const docName = document.getElementById('placeholderDocName').value.trim();
        if (!docName) return;

        const transactionId = TX_CONFIG.transactionId;
        const formData = new FormData();
        formData.append('document_name', docName);

        fetch(`/transactions/${transactionId}/documents/add-placeholder`, {
            method: 'POST',
            body: formData
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast('Placeholder added successfully!', 'success');
                closeAddPlaceholderModal();
                reloadPreservingScroll();
            } else {
                showToast(data.error || 'Failed to add placeholder', 'error');
            }
        })
        .catch(() => {
            showToast('Network error. Please try again.', 'error');
        });
    });
}


// =============================================================================
// EXTRACTION STATUS POLLING (listing info auto-populate)
// =============================================================================

(function() {
    const card = document.getElementById('listing-info-card');
    if (!card) return;

    const status = card.dataset.extractionStatus;
    if (status !== 'pending' && status !== 'processing') return;

    const txId = card.dataset.transactionId;
    const contentEl = document.getElementById('listing-info-content');
    if (!txId || !contentEl) return;

    const POLL_INTERVAL = 4000;
    const MAX_POLLS = 8;
    let polls = 0;

    const timer = setInterval(function() {
        polls++;

        fetch(`/transactions/${txId}/extraction-status`)
            .then(r => r.json())
            .then(data => {
                if (data.ready) {
                    clearInterval(timer);
                    if (data.listing_info) {
                        renderListingInfo(contentEl, data.listing_info);
                    } else if (data.extraction_status === 'failed') {
                        renderStatusMessage(contentEl, 'bg-red-50', 'fas fa-exclamation-triangle text-red-400', 'Data extraction failed. Try re-uploading the document.');
                    } else {
                        renderStatusMessage(contentEl, 'bg-amber-50', 'fas fa-exclamation-triangle text-amber-400', 'Could not extract listing data from this document. Try re-uploading a clearer copy.');
                    }
                } else if (polls >= MAX_POLLS) {
                    clearInterval(timer);
                    renderStatusMessage(contentEl, 'bg-amber-50', 'fas fa-clock text-amber-400', 'Extraction is taking longer than expected.');
                }
            })
            .catch(function() {
                if (polls >= MAX_POLLS) clearInterval(timer);
            });
    }, POLL_INTERVAL);

    function renderStatusMessage(el, bgClass, iconClass, message) {
        el.textContent = '';
        var outer = document.createElement('div');
        outer.className = 'text-center py-6 mb-4';
        var iconWrap = document.createElement('div');
        iconWrap.className = 'w-12 h-12 ' + bgClass + ' rounded-xl flex items-center justify-center mx-auto mb-3';
        var icon = document.createElement('i');
        icon.className = iconClass;
        iconWrap.appendChild(icon);
        outer.appendChild(iconWrap);
        var msg = document.createElement('p');
        msg.className = 'text-sm text-slate-500';
        msg.textContent = message;
        outer.appendChild(msg);
        el.appendChild(outer);
    }

    function renderListingInfo(el, info) {
        el.textContent = '';

        function makeRow(label, value, extraClasses) {
            var row = document.createElement('div');
            row.className = 'info-row';
            var labelEl = document.createElement('span');
            labelEl.className = 'info-label';
            labelEl.textContent = label;
            var valueEl = document.createElement('span');
            valueEl.className = 'info-value' + (extraClasses ? ' ' + extraClasses : '');
            valueEl.textContent = value || '\u2014';
            row.appendChild(labelEl);
            row.appendChild(valueEl);
            return row;
        }

        var wrapper = document.createElement('div');
        wrapper.className = 'space-y-0';

        var price = info.list_price || '\u2014';
        wrapper.appendChild(makeRow('List Price', price, 'text-emerald-600 font-semibold'));
        wrapper.appendChild(makeRow('Listing Start Date', info.listing_start_date));
        wrapper.appendChild(makeRow('Listing Expiration Date', info.listing_end_date));

        if (info.commission_type === '5b') {
            wrapper.appendChild(makeRow("Broker's Fee (Origen Realty)", info.broker_fee));
            wrapper.appendChild(makeRow('Buyer Side Commission', 'N/A \u2014 Listing Broker Only (Section 5B)', 'text-slate-400 italic'));
        } else {
            wrapper.appendChild(makeRow('Total Commission', info.total_commission));
            wrapper.appendChild(makeRow('Buyer Side Commission', info.buyer_commission));
        }

        var protectionVal = info.protection_period_days ? info.protection_period_days + ' days' : null;
        wrapper.appendChild(makeRow('Protection Period', protectionVal));
        wrapper.appendChild(makeRow('Accepted Financing', info.financing_types));
        wrapper.appendChild(makeRow('HOA Required', info.has_hoa));

        if (info.special_provisions) {
            var divider = document.createElement('div');
            divider.className = 'pt-3 mt-3 border-t border-slate-100';
            var spLabel = document.createElement('span');
            spLabel.className = 'info-label block mb-1';
            spLabel.textContent = 'Special Provisions';
            var spText = document.createElement('p');
            spText.className = 'text-sm text-slate-700 leading-relaxed';
            spText.textContent = info.special_provisions;
            divider.appendChild(spLabel);
            divider.appendChild(spText);
            wrapper.appendChild(divider);
        }

        el.appendChild(wrapper);
    }
})();
