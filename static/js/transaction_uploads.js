/**
 * Transaction Detail - Upload Modals
 * Handles: scanned doc upload, e-sign upload, completed upload, static upload, signature upload
 */

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
        this.classList.add('border-teal-400', 'bg-teal-50/30');
    });

    dropZone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        this.classList.remove('border-teal-400', 'bg-teal-50/30');
    });

    dropZone.addEventListener('drop', function(e) {
        e.preventDefault();
        this.classList.remove('border-teal-400', 'bg-teal-50/30');

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
                    location.reload();
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
        this.classList.add('border-purple-400', 'bg-purple-50/30');
    });

    esignDropZone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        this.classList.remove('border-purple-400', 'bg-purple-50/30');
    });

    esignDropZone.addEventListener('drop', function(e) {
        e.preventDefault();
        this.classList.remove('border-purple-400', 'bg-purple-50/30');

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
                        location.reload();
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
        this.classList.add('border-teal-400', 'bg-teal-50/30');
    });

    completedDropZone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        this.classList.remove('border-teal-400', 'bg-teal-50/30');
    });

    completedDropZone.addEventListener('drop', function(e) {
        e.preventDefault();
        this.classList.remove('border-teal-400', 'bg-teal-50/30');

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
                    location.reload();
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
        this.classList.add('border-teal-400', 'bg-teal-50/30');
    });

    staticDropZone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        this.classList.remove('border-teal-400', 'bg-teal-50/30');
    });

    staticDropZone.addEventListener('drop', function(e) {
        e.preventDefault();
        this.classList.remove('border-teal-400', 'bg-teal-50/30');

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
                    location.reload();
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
        this.classList.add('border-purple-400', 'bg-purple-50/30');
    });

    signatureDropZone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        this.classList.remove('border-purple-400', 'bg-purple-50/30');
    });

    signatureDropZone.addEventListener('drop', function(e) {
        e.preventDefault();
        this.classList.remove('border-purple-400', 'bg-purple-50/30');

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
                        location.reload();
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
