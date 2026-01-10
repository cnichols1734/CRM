/**
 * Document Mapper v2 - JavaScript
 * 
 * Handles field mapping, drag-drop, auto-mapping, and YAML generation.
 */

(function() {
    'use strict';

    // State
    const state = {
        mappings: [],
        roles: [],
        yamlContent: '',
        isValid: false
    };

    // DOM Elements
    const elements = {
        htmlFieldsList: document.getElementById('htmlFieldsList'),
        docusealFieldsList: document.getElementById('docusealFieldsList'),
        mappingsList: document.getElementById('mappingsList'),
        dropZone: document.getElementById('dropZone'),
        mappingCount: document.getElementById('mappingCount'),
        btnAutoMap: document.getElementById('btnAutoMap'),
        btnPreviewYaml: document.getElementById('btnPreviewYaml'),
        btnSave: document.getElementById('btnSave'),
        btnClearMappings: document.getElementById('btnClearMappings'),
        btnFetchTemplate: document.getElementById('btnFetchTemplate'),
        templateIdInput: document.getElementById('templateIdInput'),
        searchHtml: document.getElementById('searchHtml'),
        searchDocuseal: document.getElementById('searchDocuseal'),
        yamlModal: document.getElementById('yamlModal'),
        yamlPreview: document.getElementById('yamlPreview'),
        validationStatus: document.getElementById('validationStatus'),
        validationErrors: document.getElementById('validationErrors'),
        btnCopyYaml: document.getElementById('btnCopyYaml'),
        btnCloseModal: document.getElementById('btnCloseModal'),
        btnSaveFromModal: document.getElementById('btnSaveFromModal'),
        editModal: document.getElementById('editModal'),
        rolesGrid: document.getElementById('rolesGrid')
    };

    // Initialize
    function init() {
        initializeRoles();
        setupDragDrop();
        setupEventListeners();
        setupSearch();
    }

    // Initialize roles from DocuSeal submitters
    function initializeRoles() {
        if (!window.mapperData.docusealSubmitters) return;

        state.roles = window.mapperData.docusealSubmitters.map(role => {
            const roleKey = role.toLowerCase().replace(/\s+/g, '_');
            const isAgent = roleKey.includes('agent');
            const isBroker = roleKey.includes('broker');
            const isSeller2 = roleKey.includes('seller') && (roleKey.includes('2') || roleKey.includes('co'));
            const isBuyer2 = roleKey.includes('buyer') && (roleKey.includes('2') || roleKey.includes('co'));

            return {
                role_key: roleKey,
                docuseal_role: role,
                email_source: isAgent || isBroker ? 'user.email' :
                             isSeller2 ? 'transaction.sellers[1].display_email' :
                             isBuyer2 ? 'transaction.buyers[1].display_email' :
                             roleKey.includes('buyer') ? 'transaction.primary_buyer.display_email' :
                             'transaction.primary_seller.display_email',
                name_source: isAgent || isBroker ? 'user.full_name' :
                            isSeller2 ? 'transaction.sellers[1].display_name' :
                            isBuyer2 ? 'transaction.buyers[1].display_name' :
                            roleKey.includes('buyer') ? 'transaction.primary_buyer.display_name' :
                            'transaction.primary_seller.display_name',
                optional: isSeller2 || isBuyer2,
                auto_complete: isAgent || isBroker
            };
        });
    }

    // Setup drag and drop
    function setupDragDrop() {
        // HTML fields are draggable
        document.querySelectorAll('.html-field').forEach(field => {
            field.addEventListener('dragstart', handleDragStart);
            field.addEventListener('dragend', handleDragEnd);
        });

        // Mappings panel is a drop zone
        if (elements.dropZone) {
            elements.mappingsList.addEventListener('dragover', handleDragOver);
            elements.mappingsList.addEventListener('dragleave', handleDragLeave);
            elements.mappingsList.addEventListener('drop', handleDrop);
        }

        // DocuSeal fields can be clicked to open picker
        document.querySelectorAll('.docuseal-field').forEach(field => {
            field.addEventListener('click', () => handleDocuSealClick(field));
        });
    }

    // Drag handlers
    function handleDragStart(e) {
        e.dataTransfer.setData('text/plain', e.target.dataset.name);
        e.target.classList.add('dragging');
    }

    function handleDragEnd(e) {
        e.target.classList.remove('dragging');
    }

    function handleDragOver(e) {
        e.preventDefault();
        if (elements.dropZone) {
            elements.dropZone.classList.add('drag-over');
        }
    }

    function handleDragLeave(e) {
        if (elements.dropZone) {
            elements.dropZone.classList.remove('drag-over');
        }
    }

    function handleDrop(e) {
        e.preventDefault();
        if (elements.dropZone) {
            elements.dropZone.classList.remove('drag-over');
        }

        const htmlFieldName = e.dataTransfer.getData('text/plain');
        if (!htmlFieldName) return;

        // If we have DocuSeal fields, prompt for selection
        if (window.mapperData.docusealFields && window.mapperData.docusealFields.length > 0) {
            promptForDocuSealField(htmlFieldName);
        } else {
            // No DocuSeal fields - create a manual mapping
            addManualMapping(htmlFieldName);
        }
    }

    // Handle DocuSeal field click for quick mapping
    function handleDocuSealClick(fieldElement) {
        const dsName = fieldElement.dataset.name;
        const dsRole = fieldElement.dataset.role;
        const dsType = fieldElement.dataset.type;

        // Check if already mapped
        if (state.mappings.some(m => m.docuseal_field === dsName)) {
            showToast('This DocuSeal field is already mapped', 'warning');
            return;
        }

        // Find best matching HTML field
        const bestMatch = findBestHtmlMatch(dsName);
        
        if (bestMatch) {
            addMapping({
                html_field: bestMatch.name,
                docuseal_field: dsName,
                docuseal_role: dsRole,
                html_type: bestMatch.html_type,
                docuseal_type: dsType,
                suggested_transform: suggestTransform(bestMatch.html_type, dsType),
                confidence: 100
            });
        } else {
            // Prompt for HTML field selection
            promptForHtmlField(dsName, dsRole, dsType);
        }
    }

    // Prompt user to select a DocuSeal field
    function promptForDocuSealField(htmlFieldName) {
        // Find unmapped DocuSeal fields
        const unmapped = window.mapperData.docusealFields.filter(
            f => !state.mappings.some(m => m.docuseal_field === f.name)
        );

        if (unmapped.length === 0) {
            showToast('All DocuSeal fields are already mapped', 'warning');
            return;
        }

        // Create a simple selection (in production, this could be a nice modal)
        const selected = prompt(
            `Select a DocuSeal field to map "${htmlFieldName}" to:\n\n` +
            unmapped.map((f, i) => `${i + 1}. ${f.name} (${f.role})`).join('\n') +
            '\n\nEnter the number:'
        );

        if (selected && !isNaN(selected)) {
            const index = parseInt(selected) - 1;
            if (index >= 0 && index < unmapped.length) {
                const dsField = unmapped[index];
                const htmlField = window.mapperData.htmlFields.find(f => f.name === htmlFieldName);

                addMapping({
                    html_field: htmlFieldName,
                    docuseal_field: dsField.name,
                    docuseal_role: dsField.role,
                    html_type: htmlField?.html_type || 'text',
                    docuseal_type: dsField.type,
                    suggested_transform: suggestTransform(htmlField?.html_type, dsField.type),
                    confidence: 80
                });
            }
        }
    }

    // Prompt for HTML field when clicking DocuSeal field
    function promptForHtmlField(dsName, dsRole, dsType) {
        const unmapped = window.mapperData.htmlFields.filter(
            f => !state.mappings.some(m => m.html_field === f.name)
        );

        if (unmapped.length === 0) {
            showToast('All HTML fields are already mapped', 'warning');
            return;
        }

        const selected = prompt(
            `Select an HTML field to map to "${dsName}":\n\n` +
            unmapped.map((f, i) => `${i + 1}. ${f.name}`).join('\n') +
            '\n\nEnter the number:'
        );

        if (selected && !isNaN(selected)) {
            const index = parseInt(selected) - 1;
            if (index >= 0 && index < unmapped.length) {
                const htmlField = unmapped[index];

                addMapping({
                    html_field: htmlField.name,
                    docuseal_field: dsName,
                    docuseal_role: dsRole,
                    html_type: htmlField.html_type || 'text',
                    docuseal_type: dsType,
                    suggested_transform: suggestTransform(htmlField.html_type, dsType),
                    confidence: 80
                });
            }
        }
    }

    // Find best matching HTML field for a DocuSeal field
    function findBestHtmlMatch(dsFieldName) {
        const dsNormalized = normalizeFieldName(dsFieldName);
        let bestMatch = null;
        let bestScore = 0;

        window.mapperData.htmlFields.forEach(htmlField => {
            // Skip if already mapped
            if (state.mappings.some(m => m.html_field === htmlField.name)) return;

            const htmlNormalized = normalizeFieldName(htmlField.name);
            
            // Exact match
            if (htmlNormalized === dsNormalized) {
                bestMatch = htmlField;
                bestScore = 100;
                return;
            }

            // Calculate similarity
            const similarity = calculateSimilarity(htmlNormalized, dsNormalized);
            if (similarity > bestScore && similarity > 60) {
                bestScore = similarity;
                bestMatch = htmlField;
            }
        });

        return bestMatch;
    }

    // Normalize field name
    function normalizeFieldName(name) {
        return name.toLowerCase()
            .replace(/[^a-z0-9]/g, '_')
            .replace(/_+/g, '_')
            .replace(/^_|_$/g, '');
    }

    // Calculate string similarity (simple version)
    function calculateSimilarity(a, b) {
        const aWords = new Set(a.split('_'));
        const bWords = new Set(b.split('_'));
        const intersection = new Set([...aWords].filter(x => bWords.has(x)));
        const union = new Set([...aWords, ...bWords]);
        return (intersection.size / union.size) * 100;
    }

    // Suggest transform based on types
    function suggestTransform(htmlType, dsType) {
        if (htmlType === 'checkbox' || dsType === 'checkbox') return 'checkbox';
        if (htmlType === 'date' || dsType === 'date') return 'date_short';
        if (htmlType === 'money' || htmlType === 'number') return 'currency';
        return null;
    }

    // Add a mapping
    function addMapping(mapping) {
        // Check for duplicates
        if (state.mappings.some(m => m.html_field === mapping.html_field)) {
            showToast(`HTML field "${mapping.html_field}" is already mapped`, 'warning');
            return;
        }

        // Set default source
        mapping.source = mapping.source || `form.${mapping.html_field}`;
        mapping.transform = mapping.transform || mapping.suggested_transform || null;
        mapping.condition_field = mapping.condition_field || null;
        mapping.condition_equals = mapping.condition_equals || null;

        state.mappings.push(mapping);
        renderMappings();
        updateFieldStatus();
        updateUI();
    }

    // Remove a mapping
    function removeMapping(htmlFieldName) {
        state.mappings = state.mappings.filter(m => m.html_field !== htmlFieldName);
        renderMappings();
        updateFieldStatus();
        updateUI();
    }

    // Add manual mapping (no DocuSeal field)
    function addManualMapping(htmlFieldName) {
        const dsFieldName = prompt(`Enter the DocuSeal field name for "${htmlFieldName}":`);
        if (!dsFieldName) return;

        const dsRole = prompt('Enter the DocuSeal role (e.g., Seller, Buyer, Agent):', 'Seller');
        if (!dsRole) return;

        const htmlField = window.mapperData.htmlFields.find(f => f.name === htmlFieldName);

        addMapping({
            html_field: htmlFieldName,
            docuseal_field: dsFieldName,
            docuseal_role: dsRole,
            html_type: htmlField?.html_type || 'text',
            docuseal_type: 'text',
            suggested_transform: null,
            confidence: 50
        });
    }

    // Render mappings list
    function renderMappings() {
        if (state.mappings.length === 0) {
            elements.mappingsList.innerHTML = `
                <div class="drop-zone" id="dropZone">
                    <i class="fas fa-arrows-alt"></i>
                    <p>Drag HTML fields here or use Auto-Map</p>
                </div>
            `;
            return;
        }

        elements.mappingsList.innerHTML = state.mappings.map((m, index) => `
            <div class="mapping-item" data-index="${index}" data-html="${m.html_field}">
                <div class="mapping-row">
                    <div class="mapping-html">${m.html_field}</div>
                    <i class="fas fa-arrow-right mapping-arrow"></i>
                    <div class="mapping-docuseal">
                        ${m.docuseal_field}
                        <span class="mapping-role">${m.docuseal_role}</span>
                    </div>
                    ${m.confidence ? `<span class="mapping-confidence ${getConfidenceClass(m.confidence)}">${m.confidence}%</span>` : ''}
                </div>
                <div class="mapping-config">
                    <select class="mapping-transform" data-index="${index}">
                        <option value="">No transform</option>
                        ${window.mapperData.availableTransforms.map(t => 
                            `<option value="${t}" ${m.transform === t ? 'selected' : ''}>${t}</option>`
                        ).join('')}
                    </select>
                    <div class="mapping-actions">
                        <button class="mapping-btn btn-edit" data-index="${index}" title="Edit">
                            <i class="fas fa-cog"></i>
                        </button>
                        <button class="mapping-btn btn-remove" data-index="${index}" title="Remove">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                </div>
            </div>
        `).join('');

        // Add event listeners to new elements
        elements.mappingsList.querySelectorAll('.mapping-transform').forEach(select => {
            select.addEventListener('change', (e) => {
                const index = parseInt(e.target.dataset.index);
                state.mappings[index].transform = e.target.value || null;
            });
        });

        elements.mappingsList.querySelectorAll('.btn-edit').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const index = parseInt(e.target.closest('button').dataset.index);
                openEditModal(index);
            });
        });

        elements.mappingsList.querySelectorAll('.btn-remove').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const index = parseInt(e.target.closest('button').dataset.index);
                removeMapping(state.mappings[index].html_field);
            });
        });
    }

    // Get confidence class
    function getConfidenceClass(confidence) {
        if (confidence >= 80) return 'confidence-high';
        if (confidence >= 50) return 'confidence-medium';
        return 'confidence-low';
    }

    // Update field status (mapped/unmapped)
    function updateFieldStatus() {
        const mappedHtml = new Set(state.mappings.map(m => m.html_field));
        const mappedDocuSeal = new Set(state.mappings.map(m => m.docuseal_field));

        document.querySelectorAll('.html-field').forEach(field => {
            if (mappedHtml.has(field.dataset.name)) {
                field.classList.add('mapped');
            } else {
                field.classList.remove('mapped');
            }
        });

        document.querySelectorAll('.docuseal-field').forEach(field => {
            if (mappedDocuSeal.has(field.dataset.name)) {
                field.classList.add('mapped');
            } else {
                field.classList.remove('mapped');
            }
        });
    }

    // Update UI elements
    function updateUI() {
        elements.mappingCount.textContent = state.mappings.length;
        elements.btnSave.disabled = state.mappings.length === 0;
    }

    // Setup event listeners
    function setupEventListeners() {
        // Auto-map button
        if (elements.btnAutoMap) {
            elements.btnAutoMap.addEventListener('click', handleAutoMap);
        }

        // Preview YAML button
        if (elements.btnPreviewYaml) {
            elements.btnPreviewYaml.addEventListener('click', handlePreviewYaml);
        }

        // Save button
        if (elements.btnSave) {
            elements.btnSave.addEventListener('click', handleSave);
        }

        // Clear mappings
        if (elements.btnClearMappings) {
            elements.btnClearMappings.addEventListener('click', handleClearMappings);
        }

        // Fetch template
        if (elements.btnFetchTemplate) {
            elements.btnFetchTemplate.addEventListener('click', handleFetchTemplate);
        }

        // YAML modal buttons
        if (elements.btnCopyYaml) {
            elements.btnCopyYaml.addEventListener('click', handleCopyYaml);
        }
        if (elements.btnCloseModal) {
            elements.btnCloseModal.addEventListener('click', () => elements.yamlModal.style.display = 'none');
        }
        if (elements.btnSaveFromModal) {
            elements.btnSaveFromModal.addEventListener('click', handleSave);
        }

        // Role configuration changes
        if (elements.rolesGrid) {
            elements.rolesGrid.addEventListener('change', handleRoleChange);
        }

        // Edit modal
        setupEditModal();

        // Close modals on outside click
        document.querySelectorAll('.yaml-modal, .edit-modal').forEach(modal => {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    modal.style.display = 'none';
                }
            });
        });
    }

    // Setup search
    function setupSearch() {
        if (elements.searchHtml) {
            elements.searchHtml.addEventListener('input', (e) => {
                filterFields('.html-field', e.target.value);
            });
        }

        if (elements.searchDocuseal) {
            elements.searchDocuseal.addEventListener('input', (e) => {
                filterFields('.docuseal-field', e.target.value);
            });
        }
    }

    // Filter fields
    function filterFields(selector, query) {
        const normalizedQuery = query.toLowerCase();
        document.querySelectorAll(selector).forEach(field => {
            const name = field.dataset.name.toLowerCase();
            if (name.includes(normalizedQuery)) {
                field.style.display = '';
            } else {
                field.style.display = 'none';
            }
        });
    }

    // Handle auto-map
    async function handleAutoMap() {
        elements.btnAutoMap.disabled = true;
        elements.btnAutoMap.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Mapping...';

        try {
            const response = await fetch('/api/mapper/auto-map', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    html_fields: window.mapperData.htmlFields,
                    docuseal_fields: window.mapperData.docusealFields
                })
            });

            const data = await response.json();

            if (data.success && data.mappings) {
                // Add auto-mapped fields
                let addedCount = 0;
                data.mappings.forEach(mapping => {
                    if (!state.mappings.some(m => m.html_field === mapping.html_field)) {
                        addMapping(mapping);
                        addedCount++;
                    }
                });

                showToast(`Auto-mapped ${addedCount} fields`, 'success');
            } else {
                showToast('Auto-mapping failed: ' + (data.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            console.error('Auto-map error:', error);
            showToast('Auto-mapping failed: ' + error.message, 'error');
        } finally {
            elements.btnAutoMap.disabled = false;
            elements.btnAutoMap.innerHTML = '<i class="fas fa-magic"></i> Auto-Map';
        }
    }

    // Handle preview YAML
    async function handlePreviewYaml() {
        const config = buildConfig();

        try {
            const response = await fetch('/api/mapper/generate-yaml', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(config)
            });

            const data = await response.json();

            if (data.success) {
                state.yamlContent = data.yaml;
                state.isValid = data.valid;

                // Display in modal
                elements.yamlPreview.textContent = data.yaml;
                hljs.highlightElement(elements.yamlPreview);

                // Update validation status
                if (data.valid) {
                    elements.validationStatus.className = 'validation-status valid';
                    elements.validationStatus.innerHTML = '<i class="fas fa-check-circle"></i> Valid';
                    elements.validationErrors.textContent = '';
                } else {
                    elements.validationStatus.className = 'validation-status invalid';
                    elements.validationStatus.innerHTML = '<i class="fas fa-exclamation-circle"></i> Invalid';
                    elements.validationErrors.textContent = data.errors.join('\n');
                }

                elements.yamlModal.style.display = 'flex';
            } else {
                showToast('Failed to generate YAML: ' + (data.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            console.error('Generate YAML error:', error);
            showToast('Failed to generate YAML: ' + error.message, 'error');
        }
    }

    // Build config from current state
    function buildConfig() {
        // Get role configurations from the UI
        const roles = [];
        document.querySelectorAll('.role-card').forEach(card => {
            const roleName = card.dataset.role;
            roles.push({
                role_key: roleName.toLowerCase().replace(/\s+/g, '_'),
                docuseal_role: roleName,
                email_source: card.querySelector('.role-email-source').value,
                name_source: card.querySelector('.role-name-source').value,
                optional: card.querySelector('.role-optional').checked,
                auto_complete: card.querySelector('.role-auto-complete').checked
            });
        });

        // Build fields from mappings
        const fields = state.mappings.map(m => ({
            field_key: m.html_field.replace(/-/g, '_'),
            docuseal_field: m.docuseal_field,
            role_key: m.docuseal_role.toLowerCase().replace(/\s+/g, '_'),
            source: m.source || `form.${m.html_field}`,
            transform: m.transform || null,
            condition_field: m.condition_field || null,
            condition_equals: m.condition_equals || null
        }));

        return {
            slug: window.mapperData.slug,
            name: window.mapperData.name,
            docuseal_template_id: window.mapperData.templateId,
            type: 'form-driven',
            display: {
                color: '#6B7280',
                icon: 'fas fa-file',
                sort_order: 99
            },
            form: {
                template: `${window.mapperData.slug.replace(/-/g, '_')}_form.html`,
                partial: `${window.mapperData.slug.replace(/-/g, '_')}_fields.html`
            },
            roles: roles,
            fields: fields
        };
    }

    // Handle save
    async function handleSave() {
        if (!state.yamlContent) {
            // Generate YAML first
            await handlePreviewYaml();
        }

        if (!state.isValid) {
            showToast('Cannot save invalid YAML. Please fix the errors first.', 'error');
            return;
        }

        try {
            const response = await fetch('/api/mapper/save', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    slug: window.mapperData.slug,
                    yaml_content: state.yamlContent
                })
            });

            const data = await response.json();

            if (data.success) {
                showToast('YAML saved successfully!', 'success');
                elements.yamlModal.style.display = 'none';
            } else {
                showToast('Failed to save: ' + (data.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            console.error('Save error:', error);
            showToast('Failed to save: ' + error.message, 'error');
        }
    }

    // Handle clear mappings
    function handleClearMappings() {
        if (confirm('Are you sure you want to clear all mappings?')) {
            state.mappings = [];
            renderMappings();
            updateFieldStatus();
            updateUI();
            showToast('All mappings cleared', 'info');
        }
    }

    // Handle fetch template
    async function handleFetchTemplate() {
        const templateId = elements.templateIdInput?.value;
        if (!templateId) {
            showToast('Please enter a template ID', 'warning');
            return;
        }

        try {
            const response = await fetch('/admin/document-mapping/fetch-template', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ template_id: templateId })
            });

            const data = await response.json();

            if (data.success) {
                showToast(`Fetched template: ${data.template_name}`, 'success');
                // Reload the page to show new fields
                location.reload();
            } else {
                showToast('Failed to fetch template: ' + (data.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            console.error('Fetch template error:', error);
            showToast('Failed to fetch template: ' + error.message, 'error');
        }
    }

    // Handle copy YAML
    function handleCopyYaml() {
        navigator.clipboard.writeText(state.yamlContent).then(() => {
            showToast('YAML copied to clipboard!', 'success');
        }).catch(err => {
            console.error('Copy failed:', err);
            showToast('Failed to copy to clipboard', 'error');
        });
    }

    // Handle role configuration change
    function handleRoleChange(e) {
        const card = e.target.closest('.role-card');
        if (!card) return;

        const roleName = card.dataset.role;
        const roleIndex = state.roles.findIndex(r => r.docuseal_role === roleName);
        
        if (roleIndex >= 0) {
            state.roles[roleIndex] = {
                ...state.roles[roleIndex],
                email_source: card.querySelector('.role-email-source').value,
                name_source: card.querySelector('.role-name-source').value,
                optional: card.querySelector('.role-optional').checked,
                auto_complete: card.querySelector('.role-auto-complete').checked
            };
        }
    }

    // Setup edit modal
    function setupEditModal() {
        const modal = elements.editModal;
        if (!modal) return;

        const closeBtn = modal.querySelector('.btn-close-modal');
        const saveBtn = document.getElementById('btnSaveMapping');
        const removeBtn = document.getElementById('btnRemoveMapping');
        const hasConditionCheckbox = document.getElementById('editHasCondition');
        const conditionFields = document.getElementById('conditionFields');

        if (closeBtn) {
            closeBtn.addEventListener('click', () => modal.style.display = 'none');
        }

        if (hasConditionCheckbox) {
            hasConditionCheckbox.addEventListener('change', (e) => {
                conditionFields.style.display = e.target.checked ? 'block' : 'none';
            });
        }

        if (saveBtn) {
            saveBtn.addEventListener('click', saveEditedMapping);
        }

        if (removeBtn) {
            removeBtn.addEventListener('click', () => {
                const index = parseInt(modal.dataset.index);
                if (index >= 0 && index < state.mappings.length) {
                    removeMapping(state.mappings[index].html_field);
                    modal.style.display = 'none';
                }
            });
        }
    }

    // Open edit modal
    function openEditModal(index) {
        const mapping = state.mappings[index];
        if (!mapping) return;

        const modal = elements.editModal;
        modal.dataset.index = index;

        document.getElementById('editFieldKey').value = mapping.html_field;
        document.getElementById('editDocusealField').value = mapping.docuseal_field;
        document.getElementById('editTransform').value = mapping.transform || '';

        // Set source
        const sourceSelect = document.getElementById('editSource');
        let sourceFound = false;
        for (let i = 0; i < sourceSelect.options.length; i++) {
            if (sourceSelect.options[i].value === mapping.source) {
                sourceSelect.selectedIndex = i;
                sourceFound = true;
                break;
            }
        }
        if (!sourceFound) {
            // Add custom option
            const option = document.createElement('option');
            option.value = mapping.source;
            option.textContent = mapping.source;
            option.selected = true;
            sourceSelect.appendChild(option);
        }

        // Set condition
        const hasCondition = !!mapping.condition_field;
        document.getElementById('editHasCondition').checked = hasCondition;
        document.getElementById('conditionFields').style.display = hasCondition ? 'block' : 'none';
        document.getElementById('editConditionField').value = mapping.condition_field || '';
        document.getElementById('editConditionEquals').value = mapping.condition_equals || '';

        modal.style.display = 'flex';
    }

    // Save edited mapping
    function saveEditedMapping() {
        const modal = elements.editModal;
        const index = parseInt(modal.dataset.index);
        
        if (index >= 0 && index < state.mappings.length) {
            const hasCondition = document.getElementById('editHasCondition').checked;
            
            state.mappings[index] = {
                ...state.mappings[index],
                source: document.getElementById('editSource').value || `form.${state.mappings[index].html_field}`,
                transform: document.getElementById('editTransform').value || null,
                condition_field: hasCondition ? document.getElementById('editConditionField').value : null,
                condition_equals: hasCondition ? document.getElementById('editConditionEquals').value : null
            };

            renderMappings();
            modal.style.display = 'none';
            showToast('Mapping updated', 'success');
        }
    }

    // Toast notification
    function showToast(message, type = 'info') {
        // Simple toast implementation
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.innerHTML = `
            <i class="fas fa-${type === 'success' ? 'check-circle' : type === 'error' ? 'exclamation-circle' : type === 'warning' ? 'exclamation-triangle' : 'info-circle'}"></i>
            <span>${message}</span>
        `;
        toast.style.cssText = `
            position: fixed;
            bottom: 20px;
            right: 20px;
            padding: 12px 20px;
            border-radius: 8px;
            background: ${type === 'success' ? '#10b981' : type === 'error' ? '#ef4444' : type === 'warning' ? '#f59e0b' : '#3b82f6'};
            color: white;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 8px;
            z-index: 9999;
            animation: slideIn 0.3s ease;
        `;

        document.body.appendChild(toast);

        setTimeout(() => {
            toast.style.animation = 'slideOut 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    // Add animation styles
    const style = document.createElement('style');
    style.textContent = `
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        @keyframes slideOut {
            from { transform: translateX(0); opacity: 1; }
            to { transform: translateX(100%); opacity: 0; }
        }
    `;
    document.head.appendChild(style);

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

