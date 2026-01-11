/**
 * Document Mapper v2 - Complete Redesign
 *
 * Features:
 * - Combined field support (multiple sources → one DocuSeal field)
 * - Proper modal-based field selection
 * - Multi-select for combining fields
 * - Drag-drop with visual feedback
 * - Template presets and custom separators
 * - Live preview for combined fields
 */

(function() {
    'use strict';

    // =============================================================================
    // STATE
    // =============================================================================
    const state = {
        mappings: [],           // All mappings (single + combined)
        roles: [],              // Role configurations
        selectedHtmlFields: [], // Multi-selected HTML fields for combining
        yamlContent: '',        // Generated YAML
        isValid: false,         // Schema validation result
        draggedField: null,     // Currently dragged field
        combineMode: false      // Whether we're in "combine fields" mode
    };

    // Separator presets
    const SEPARATORS = {
        'comma_space': { label: ', ', value: ', ', display: 'Comma + Space' },
        'space': { label: ' ', value: ' ', display: 'Space' },
        'dash': { label: ' - ', value: ' - ', display: 'Dash' },
        'newline': { label: '\\n', value: '\n', display: 'New Line' }
    };

    // Template presets
    const TEMPLATE_PRESETS = [
        { name: 'Address', template: '{0}, {1}, {2} {3}', fields: 4, desc: 'Street, City, State Zip' },
        { name: 'Full Name', template: '{0} {1}', fields: 2, desc: 'First Last' },
        { name: 'Name & Phone', template: '{0} - {1}', fields: 2, desc: 'Name - Phone' },
        { name: 'City, State', template: '{0}, {1}', fields: 2, desc: 'City, State' }
    ];

    // =============================================================================
    // DOM ELEMENTS
    // =============================================================================
    const elements = {
        htmlFieldsList: null,
        docusealFieldsList: null,
        mappingsList: null,
        mappingCount: null,
        btnAutoMap: null,
        btnCombine: null,
        btnPreviewYaml: null,
        btnSave: null,
        btnClearMappings: null,
        searchHtml: null,
        searchDocuseal: null,
        yamlModal: null,
        fieldPickerModal: null,
        combineModal: null,
        rolesGrid: null
    };

    // =============================================================================
    // INITIALIZATION
    // =============================================================================
    function init() {
        cacheElements();
        initializeRoles();
        setupDragDrop();
        setupEventListeners();
        setupSearch();
        setupMultiSelect();
        renderMappings();
        updateUI();
    }

    function cacheElements() {
        elements.htmlFieldsList = document.getElementById('htmlFieldsList');
        elements.docusealFieldsList = document.getElementById('docusealFieldsList');
        elements.mappingsList = document.getElementById('mappingsList');
        elements.mappingCount = document.getElementById('mappingCount');
        elements.btnAutoMap = document.getElementById('btnAutoMap');
        elements.btnCombine = document.getElementById('btnCombine');
        elements.btnPreviewYaml = document.getElementById('btnPreviewYaml');
        elements.btnSave = document.getElementById('btnSave');
        elements.btnClearMappings = document.getElementById('btnClearMappings');
        elements.searchHtml = document.getElementById('searchHtml');
        elements.searchDocuseal = document.getElementById('searchDocuseal');
        elements.yamlModal = document.getElementById('yamlModal');
        elements.fieldPickerModal = document.getElementById('fieldPickerModal');
        elements.combineModal = document.getElementById('combineModal');
        elements.rolesGrid = document.getElementById('rolesGrid');
    }

    function initializeRoles() {
        if (!window.mapperData?.docusealSubmitters) return;

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

    // =============================================================================
    // MULTI-SELECT FOR COMBINING FIELDS
    // =============================================================================
    function setupMultiSelect() {
        document.querySelectorAll('.html-field').forEach(field => {
            // Add checkbox for multi-select
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'field-checkbox';
            checkbox.dataset.name = field.dataset.name;
            checkbox.addEventListener('change', handleFieldCheckbox);
            field.insertBefore(checkbox, field.firstChild);

            // Cmd/Ctrl+click for quick selection
            field.addEventListener('click', (e) => {
                if (e.metaKey || e.ctrlKey) {
                    e.preventDefault();
                    checkbox.checked = !checkbox.checked;
                    handleFieldCheckbox({ target: checkbox });
                }
            });
        });
    }

    function handleFieldCheckbox(e) {
        const fieldName = e.target.dataset.name;
        const isChecked = e.target.checked;
        const fieldEl = e.target.closest('.html-field');

        if (isChecked) {
            if (!state.selectedHtmlFields.includes(fieldName)) {
                state.selectedHtmlFields.push(fieldName);
            }
            fieldEl.classList.add('selected');
        } else {
            state.selectedHtmlFields = state.selectedHtmlFields.filter(f => f !== fieldName);
            fieldEl.classList.remove('selected');
        }

        updateCombineButton();
    }

    function updateCombineButton() {
        const count = state.selectedHtmlFields.length;

        if (!elements.btnCombine) {
            // Create combine button if it doesn't exist
            const toolbar = document.querySelector('.panel-html .panel-toolbar');
            if (toolbar && !document.getElementById('btnCombine')) {
                const btn = document.createElement('button');
                btn.id = 'btnCombine';
                btn.className = 'btn-toolbar btn-combine';
                btn.innerHTML = '<i class="fas fa-link"></i> Combine (<span class="combine-count">0</span>)';
                btn.style.display = 'none';
                btn.addEventListener('click', openCombineModal);
                toolbar.appendChild(btn);
                elements.btnCombine = btn;
            }
        }

        if (elements.btnCombine) {
            const countSpan = elements.btnCombine.querySelector('.combine-count');
            if (countSpan) countSpan.textContent = count;
            elements.btnCombine.style.display = count >= 2 ? 'inline-flex' : 'none';
        }
    }

    function clearSelection() {
        state.selectedHtmlFields = [];
        document.querySelectorAll('.html-field.selected').forEach(el => {
            el.classList.remove('selected');
        });
        document.querySelectorAll('.field-checkbox:checked').forEach(cb => {
            cb.checked = false;
        });
        updateCombineButton();
    }

    // =============================================================================
    // DRAG AND DROP
    // =============================================================================
    function setupDragDrop() {
        // HTML fields are draggable
        document.querySelectorAll('.html-field').forEach(field => {
            field.addEventListener('dragstart', handleDragStart);
            field.addEventListener('dragend', handleDragEnd);
        });

        // Mappings panel is a drop zone
        if (elements.mappingsList) {
            elements.mappingsList.addEventListener('dragover', handleDragOver);
            elements.mappingsList.addEventListener('dragleave', handleDragLeave);
            elements.mappingsList.addEventListener('drop', handleDrop);
        }

        // DocuSeal fields can be clicked to open picker
        document.querySelectorAll('.docuseal-field').forEach(field => {
            field.addEventListener('click', () => handleDocuSealClick(field));
        });
    }

    function handleDragStart(e) {
        const fieldName = e.target.dataset.name;
        state.draggedField = fieldName;
        e.dataTransfer.setData('text/plain', fieldName);
        e.dataTransfer.effectAllowed = 'copy';
        e.target.classList.add('dragging');

        // Create custom drag image
        const ghost = e.target.cloneNode(true);
        ghost.classList.add('drag-ghost');
        ghost.style.position = 'absolute';
        ghost.style.top = '-1000px';
        document.body.appendChild(ghost);
        e.dataTransfer.setDragImage(ghost, 0, 0);
        setTimeout(() => ghost.remove(), 0);
    }

    function handleDragEnd(e) {
        e.target.classList.remove('dragging');
        state.draggedField = null;
        document.querySelector('.drop-zone')?.classList.remove('drag-over');
    }

    function handleDragOver(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        const dropZone = document.getElementById('dropZone');
        if (dropZone) dropZone.classList.add('drag-over');
    }

    function handleDragLeave(e) {
        const dropZone = document.getElementById('dropZone');
        if (dropZone && !elements.mappingsList.contains(e.relatedTarget)) {
            dropZone.classList.remove('drag-over');
        }
    }

    function handleDrop(e) {
        e.preventDefault();
        document.getElementById('dropZone')?.classList.remove('drag-over');

        const htmlFieldName = e.dataTransfer.getData('text/plain');
        if (!htmlFieldName) return;

        // Open field picker modal for selecting DocuSeal target
        openFieldPickerModal(htmlFieldName);
    }

    function handleDocuSealClick(fieldElement) {
        const dsName = fieldElement.dataset.name;
        const dsRole = fieldElement.dataset.role;
        const dsType = fieldElement.dataset.type;

        // Check if already mapped
        if (isDocuSealFieldMapped(dsName)) {
            showToast('This DocuSeal field is already mapped', 'warning');
            return;
        }

        // If we have selected HTML fields, open combine modal
        if (state.selectedHtmlFields.length >= 2) {
            openCombineModalWithTarget(dsName, dsRole, dsType);
            return;
        }

        // Otherwise, try to find best matching HTML field
        const bestMatch = findBestHtmlMatch(dsName);

        if (bestMatch && !isHtmlFieldMapped(bestMatch.name)) {
            addSingleMapping({
                html_field: bestMatch.name,
                docuseal_field: dsName,
                docuseal_role: dsRole,
                html_type: bestMatch.html_type,
                docuseal_type: dsType,
                source: `form.${bestMatch.name}`,
                transform: suggestTransform(bestMatch.html_type, dsType),
                confidence: 100
            });
        } else {
            // Open field picker for HTML field selection
            openHtmlPickerModal(dsName, dsRole, dsType);
        }
    }

    // =============================================================================
    // FIELD PICKER MODAL (replaces prompt())
    // =============================================================================
    function openFieldPickerModal(htmlFieldName) {
        // Get unmapped DocuSeal fields
        const unmappedFields = getUnmappedDocuSealFields();

        if (unmappedFields.length === 0) {
            showToast('All DocuSeal fields are already mapped', 'warning');
            return;
        }

        const htmlField = window.mapperData.htmlFields.find(f => f.name === htmlFieldName);

        const modalHtml = `
            <div class="picker-modal" id="fieldPickerModal">
                <div class="picker-modal-content">
                    <div class="picker-modal-header">
                        <h3><i class="fas fa-crosshairs"></i> Select Target Field</h3>
                        <button class="btn-close-modal" onclick="closePickerModal()"><i class="fas fa-times"></i></button>
                    </div>
                    <div class="picker-modal-body">
                        <div class="picker-source">
                            <span class="picker-label">Source:</span>
                            <span class="picker-field picker-field-html">
                                ${htmlFieldName}
                                <span class="field-type">${htmlField?.html_type || 'text'}</span>
                            </span>
                        </div>
                        <div class="picker-search">
                            <input type="text" id="pickerSearch" placeholder="Search DocuSeal fields..." autofocus>
                        </div>
                        <div class="picker-fields" id="pickerFieldsList">
                            ${renderPickerFields(unmappedFields, htmlFieldName)}
                        </div>
                    </div>
                </div>
            </div>
        `;

        // Remove existing modal
        document.getElementById('fieldPickerModal')?.remove();

        // Add modal to body
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        // Setup search
        document.getElementById('pickerSearch').addEventListener('input', (e) => {
            filterPickerFields(e.target.value, unmappedFields, htmlFieldName);
        });

        // Setup field clicks
        document.querySelectorAll('.picker-field-option').forEach(opt => {
            opt.addEventListener('click', () => {
                const dsName = opt.dataset.name;
                const dsRole = opt.dataset.role;
                const dsType = opt.dataset.type;

                addSingleMapping({
                    html_field: htmlFieldName,
                    docuseal_field: dsName,
                    docuseal_role: dsRole,
                    html_type: htmlField?.html_type || 'text',
                    docuseal_type: dsType,
                    source: `form.${htmlFieldName}`,
                    transform: suggestTransform(htmlField?.html_type, dsType),
                    confidence: 80
                });

                closePickerModal();
            });
        });

        // Close on backdrop click
        document.getElementById('fieldPickerModal').addEventListener('click', (e) => {
            if (e.target.id === 'fieldPickerModal') closePickerModal();
        });
    }

    function openHtmlPickerModal(dsName, dsRole, dsType) {
        const unmappedFields = getUnmappedHtmlFields();

        if (unmappedFields.length === 0) {
            showToast('All HTML fields are already mapped', 'warning');
            return;
        }

        const modalHtml = `
            <div class="picker-modal" id="fieldPickerModal">
                <div class="picker-modal-content">
                    <div class="picker-modal-header">
                        <h3><i class="fas fa-crosshairs"></i> Select Source Field</h3>
                        <button class="btn-close-modal" onclick="closePickerModal()"><i class="fas fa-times"></i></button>
                    </div>
                    <div class="picker-modal-body">
                        <div class="picker-source">
                            <span class="picker-label">Target:</span>
                            <span class="picker-field picker-field-docuseal">
                                ${dsName}
                                <span class="field-role">${dsRole}</span>
                            </span>
                        </div>
                        <div class="picker-search">
                            <input type="text" id="pickerSearch" placeholder="Search HTML fields..." autofocus>
                        </div>
                        <div class="picker-fields" id="pickerFieldsList">
                            ${renderHtmlPickerFields(unmappedFields)}
                        </div>
                    </div>
                </div>
            </div>
        `;

        document.getElementById('fieldPickerModal')?.remove();
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        document.getElementById('pickerSearch').addEventListener('input', (e) => {
            filterHtmlPickerFields(e.target.value, unmappedFields);
        });

        document.querySelectorAll('.picker-field-option').forEach(opt => {
            opt.addEventListener('click', () => {
                const htmlFieldName = opt.dataset.name;
                const htmlField = window.mapperData.htmlFields.find(f => f.name === htmlFieldName);

                addSingleMapping({
                    html_field: htmlFieldName,
                    docuseal_field: dsName,
                    docuseal_role: dsRole,
                    html_type: htmlField?.html_type || 'text',
                    docuseal_type: dsType,
                    source: `form.${htmlFieldName}`,
                    transform: suggestTransform(htmlField?.html_type, dsType),
                    confidence: 80
                });

                closePickerModal();
            });
        });

        document.getElementById('fieldPickerModal').addEventListener('click', (e) => {
            if (e.target.id === 'fieldPickerModal') closePickerModal();
        });
    }

    function renderPickerFields(fields, sourceHtmlField) {
        // Group by role
        const byRole = {};
        fields.forEach(f => {
            if (!byRole[f.role]) byRole[f.role] = [];
            byRole[f.role].push(f);
        });

        let html = '';
        for (const [role, roleFields] of Object.entries(byRole)) {
            html += `<div class="picker-role-group">
                <div class="picker-role-header"><i class="fas fa-user"></i> ${role}</div>`;

            roleFields.forEach(f => {
                const similarity = calculateSimilarity(
                    normalizeFieldName(sourceHtmlField),
                    normalizeFieldName(f.name)
                );
                const matchClass = similarity > 60 ? 'good-match' : similarity > 30 ? 'partial-match' : '';

                html += `
                    <div class="picker-field-option ${matchClass}"
                         data-name="${f.name}" data-role="${f.role}" data-type="${f.type}">
                        <span class="picker-field-name">${f.name}</span>
                        <span class="picker-field-meta">
                            <span class="field-type">${f.type}</span>
                            ${similarity > 30 ? `<span class="match-score">${Math.round(similarity)}%</span>` : ''}
                        </span>
                    </div>
                `;
            });

            html += '</div>';
        }
        return html;
    }

    function renderHtmlPickerFields(fields) {
        return fields.map(f => `
            <div class="picker-field-option" data-name="${f.name}">
                <span class="picker-field-name">${f.name}</span>
                <span class="field-type">${f.html_type || 'text'}</span>
            </div>
        `).join('');
    }

    function filterPickerFields(query, fields, sourceHtmlField) {
        const q = query.toLowerCase();
        const filtered = fields.filter(f => f.name.toLowerCase().includes(q));
        document.getElementById('pickerFieldsList').innerHTML = renderPickerFields(filtered, sourceHtmlField);

        // Re-attach click handlers
        document.querySelectorAll('.picker-field-option').forEach(opt => {
            opt.addEventListener('click', () => {
                const htmlField = window.mapperData.htmlFields.find(f => f.name === sourceHtmlField);
                addSingleMapping({
                    html_field: sourceHtmlField,
                    docuseal_field: opt.dataset.name,
                    docuseal_role: opt.dataset.role,
                    html_type: htmlField?.html_type || 'text',
                    docuseal_type: opt.dataset.type,
                    source: `form.${sourceHtmlField}`,
                    transform: suggestTransform(htmlField?.html_type, opt.dataset.type),
                    confidence: 80
                });
                closePickerModal();
            });
        });
    }

    function filterHtmlPickerFields(query, fields) {
        const q = query.toLowerCase();
        const filtered = fields.filter(f => f.name.toLowerCase().includes(q));
        document.getElementById('pickerFieldsList').innerHTML = renderHtmlPickerFields(filtered);
    }

    window.closePickerModal = function() {
        document.getElementById('fieldPickerModal')?.remove();
    };

    // =============================================================================
    // COMBINED FIELD MODAL
    // =============================================================================
    function openCombineModal() {
        if (state.selectedHtmlFields.length < 2) {
            showToast('Select at least 2 fields to combine', 'warning');
            return;
        }

        const unmappedDocuSeal = getUnmappedDocuSealFields();

        const modalHtml = createCombineModalHtml(unmappedDocuSeal);

        document.getElementById('combineModal')?.remove();
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        setupCombineModal();
    }

    function openCombineModalWithTarget(dsName, dsRole, dsType) {
        const unmappedDocuSeal = getUnmappedDocuSealFields();

        const modalHtml = createCombineModalHtml(unmappedDocuSeal, { name: dsName, role: dsRole, type: dsType });

        document.getElementById('combineModal')?.remove();
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        setupCombineModal();
    }

    function createCombineModalHtml(docusealFields, preselectedTarget = null) {
        const fieldsHtml = state.selectedHtmlFields.map((name, idx) => `
            <div class="combine-field-item" data-name="${name}" data-index="${idx}">
                <span class="combine-field-handle"><i class="fas fa-grip-vertical"></i></span>
                <span class="combine-field-index">${idx + 1}</span>
                <span class="combine-field-name">${name}</span>
                <button class="combine-field-remove" onclick="removeCombineField('${name}')">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `).join('');

        const targetOptionsHtml = docusealFields.map(f => `
            <option value="${f.name}" data-role="${f.role}" data-type="${f.type}"
                    ${preselectedTarget?.name === f.name ? 'selected' : ''}>
                ${f.name} (${f.role})
            </option>
        `).join('');

        const presetButtonsHtml = TEMPLATE_PRESETS.map(p => `
            <button class="preset-btn" data-template="${p.template}" title="${p.desc}">
                ${p.name}
            </button>
        `).join('');

        const separatorButtonsHtml = Object.entries(SEPARATORS).map(([key, sep]) => `
            <button class="separator-btn" data-separator="${key}" data-value="${sep.value}">
                ${sep.display}
            </button>
        `).join('');

        const defaultTemplate = generateDefaultTemplate(state.selectedHtmlFields.length);

        return `
            <div class="combine-modal" id="combineModal">
                <div class="combine-modal-content">
                    <div class="combine-modal-header">
                        <h3><i class="fas fa-object-group"></i> Create Combined Field</h3>
                        <button class="btn-close-modal" onclick="closeCombineModal()">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="combine-modal-body">
                        <div class="combine-section">
                            <label class="combine-label">Source Fields (drag to reorder)</label>
                            <div class="combine-fields-list" id="combineFieldsList">
                                ${fieldsHtml}
                            </div>
                        </div>

                        <div class="combine-section">
                            <label class="combine-label">Template Presets</label>
                            <div class="preset-buttons">
                                ${presetButtonsHtml}
                            </div>
                        </div>

                        <div class="combine-section">
                            <label class="combine-label">Quick Separators</label>
                            <div class="separator-buttons">
                                ${separatorButtonsHtml}
                            </div>
                        </div>

                        <div class="combine-section">
                            <label class="combine-label">Custom Template</label>
                            <input type="text" id="combineTemplate" class="combine-input"
                                   value="${defaultTemplate}" placeholder="{0}, {1}, {2}">
                            <div class="template-help">
                                Use <code>{0}</code>, <code>{1}</code>, etc. for positional, or <code>{field_name}</code> for named placeholders
                            </div>
                        </div>

                        <div class="combine-section">
                            <label class="combine-label">Preview</label>
                            <div class="combine-preview" id="combinePreview">
                                ${generatePreview(state.selectedHtmlFields, defaultTemplate)}
                            </div>
                        </div>

                        <div class="combine-section">
                            <label class="combine-label">Target DocuSeal Field</label>
                            <select id="combineTarget" class="combine-select">
                                <option value="">Select target field...</option>
                                ${targetOptionsHtml}
                            </select>
                        </div>
                    </div>
                    <div class="combine-modal-footer">
                        <button class="btn-action btn-cancel" onclick="closeCombineModal()">Cancel</button>
                        <button class="btn-action btn-save" id="btnCreateCombined" onclick="createCombinedMapping()">
                            <i class="fas fa-link"></i> Create Combined Field
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    function setupCombineModal() {
        // Setup template input
        const templateInput = document.getElementById('combineTemplate');
        templateInput?.addEventListener('input', () => updateCombinePreview());

        // Setup separator buttons
        document.querySelectorAll('.separator-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const template = generateTemplateFromSeparator(btn.dataset.value, state.selectedHtmlFields.length);
                templateInput.value = template;
                updateCombinePreview();
            });
        });

        // Setup preset buttons
        document.querySelectorAll('.preset-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                templateInput.value = btn.dataset.template;
                updateCombinePreview();
            });
        });

        // Setup drag-drop reordering
        setupCombineFieldSorting();

        // Close on backdrop click
        document.getElementById('combineModal')?.addEventListener('click', (e) => {
            if (e.target.id === 'combineModal') closeCombineModal();
        });
    }

    function setupCombineFieldSorting() {
        const list = document.getElementById('combineFieldsList');
        if (!list) return;

        let draggedItem = null;

        list.querySelectorAll('.combine-field-item').forEach(item => {
            item.setAttribute('draggable', 'true');

            item.addEventListener('dragstart', (e) => {
                draggedItem = item;
                item.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
            });

            item.addEventListener('dragend', () => {
                item.classList.remove('dragging');
                draggedItem = null;
                updateFieldOrder();
            });

            item.addEventListener('dragover', (e) => {
                e.preventDefault();
                if (draggedItem && draggedItem !== item) {
                    const rect = item.getBoundingClientRect();
                    const midY = rect.top + rect.height / 2;
                    if (e.clientY < midY) {
                        item.parentNode.insertBefore(draggedItem, item);
                    } else {
                        item.parentNode.insertBefore(draggedItem, item.nextSibling);
                    }
                }
            });
        });
    }

    function updateFieldOrder() {
        const newOrder = [];
        document.querySelectorAll('.combine-field-item').forEach((item, idx) => {
            const name = item.dataset.name;
            newOrder.push(name);
            item.querySelector('.combine-field-index').textContent = idx + 1;
            item.dataset.index = idx;
        });
        state.selectedHtmlFields = newOrder;
        updateCombinePreview();
    }

    window.removeCombineField = function(name) {
        state.selectedHtmlFields = state.selectedHtmlFields.filter(f => f !== name);
        document.querySelector(`.combine-field-item[data-name="${name}"]`)?.remove();
        updateFieldOrder();

        // Update checkboxes in HTML fields list
        const checkbox = document.querySelector(`.field-checkbox[data-name="${name}"]`);
        if (checkbox) {
            checkbox.checked = false;
            checkbox.closest('.html-field')?.classList.remove('selected');
        }

        if (state.selectedHtmlFields.length < 2) {
            closeCombineModal();
            showToast('Combined field requires at least 2 fields', 'warning');
        }
    };

    function updateCombinePreview() {
        const template = document.getElementById('combineTemplate')?.value || '';
        const preview = generatePreview(state.selectedHtmlFields, template);
        const previewEl = document.getElementById('combinePreview');
        if (previewEl) previewEl.innerHTML = preview;
    }

    function generatePreview(fields, template) {
        // Use sample values for preview
        const sampleValues = fields.map((name, idx) => {
            if (name.toLowerCase().includes('address') || name.toLowerCase().includes('street')) return '123 Main St';
            if (name.toLowerCase().includes('city')) return 'Austin';
            if (name.toLowerCase().includes('state')) return 'TX';
            if (name.toLowerCase().includes('zip')) return '78701';
            if (name.toLowerCase().includes('phone')) return '(555) 123-4567';
            if (name.toLowerCase().includes('name')) return 'John Doe';
            if (name.toLowerCase().includes('email')) return 'john@example.com';
            return `[${name}]`;
        });

        let result = template;

        // Replace positional
        sampleValues.forEach((val, idx) => {
            result = result.replace(new RegExp(`\\{${idx}\\}`, 'g'), val);
        });

        // Replace named
        fields.forEach((name, idx) => {
            result = result.replace(new RegExp(`\\{${name}\\}`, 'g'), sampleValues[idx]);
        });

        return `<code>${escapeHtml(result)}</code>`;
    }

    function generateDefaultTemplate(count) {
        return Array.from({ length: count }, (_, i) => `{${i}}`).join(', ');
    }

    function generateTemplateFromSeparator(separator, count) {
        return Array.from({ length: count }, (_, i) => `{${i}}`).join(separator);
    }

    window.createCombinedMapping = function() {
        const template = document.getElementById('combineTemplate')?.value;
        const targetSelect = document.getElementById('combineTarget');
        const targetName = targetSelect?.value;

        if (!template) {
            showToast('Please enter a template', 'error');
            return;
        }

        if (!targetName) {
            showToast('Please select a target DocuSeal field', 'error');
            return;
        }

        const selectedOption = targetSelect.options[targetSelect.selectedIndex];
        const targetRole = selectedOption.dataset.role;
        const targetType = selectedOption.dataset.type;

        // Create sources array
        const sources = state.selectedHtmlFields.map(name => `form.${name}`);

        // Generate field key from first field
        const fieldKey = state.selectedHtmlFields[0].replace(/-/g, '_') + '_combined';

        addCombinedMapping({
            field_key: fieldKey,
            html_fields: [...state.selectedHtmlFields],
            docuseal_field: targetName,
            docuseal_role: targetRole,
            docuseal_type: targetType,
            sources: sources,
            template: template,
            transform: null,
            confidence: 100
        });

        closeCombineModal();
        clearSelection();
        showToast('Combined field created!', 'success');
    };

    window.closeCombineModal = function() {
        document.getElementById('combineModal')?.remove();
    };

    // =============================================================================
    // MAPPING MANAGEMENT
    // =============================================================================
    function addSingleMapping(mapping) {
        // Check for duplicates
        if (isHtmlFieldMapped(mapping.html_field)) {
            showToast(`HTML field "${mapping.html_field}" is already mapped`, 'warning');
            return;
        }

        if (isDocuSealFieldMapped(mapping.docuseal_field)) {
            showToast(`DocuSeal field "${mapping.docuseal_field}" is already mapped`, 'warning');
            return;
        }

        mapping.type = 'single';
        mapping.field_key = mapping.html_field.replace(/-/g, '_');

        state.mappings.push(mapping);
        renderMappings();
        updateFieldStatus();
        updateUI();
    }

    function addCombinedMapping(mapping) {
        // Check if DocuSeal field is already mapped
        if (isDocuSealFieldMapped(mapping.docuseal_field)) {
            showToast(`DocuSeal field "${mapping.docuseal_field}" is already mapped`, 'warning');
            return;
        }

        mapping.type = 'combined';

        state.mappings.push(mapping);
        renderMappings();
        updateFieldStatus();
        updateUI();
    }

    function removeMapping(index) {
        state.mappings.splice(index, 1);
        renderMappings();
        updateFieldStatus();
        updateUI();
    }

    function isHtmlFieldMapped(fieldName) {
        return state.mappings.some(m => {
            if (m.type === 'combined') {
                return m.html_fields.includes(fieldName);
            }
            return m.html_field === fieldName;
        });
    }

    function isDocuSealFieldMapped(fieldName) {
        return state.mappings.some(m => m.docuseal_field === fieldName);
    }

    function getUnmappedDocuSealFields() {
        if (!window.mapperData?.docusealFields) return [];
        return window.mapperData.docusealFields.filter(f => !isDocuSealFieldMapped(f.name));
    }

    function getUnmappedHtmlFields() {
        if (!window.mapperData?.htmlFields) return [];
        return window.mapperData.htmlFields.filter(f => !isHtmlFieldMapped(f.name));
    }

    // =============================================================================
    // RENDER MAPPINGS
    // =============================================================================
    function renderMappings() {
        if (state.mappings.length === 0) {
            elements.mappingsList.innerHTML = `
                <div class="drop-zone" id="dropZone">
                    <i class="fas fa-arrows-alt"></i>
                    <p>Drag HTML fields here or click DocuSeal fields</p>
                    <p class="drop-zone-hint">Select multiple fields (Cmd/Ctrl+click) and use "Combine" for combined fields</p>
                </div>
            `;
            return;
        }

        elements.mappingsList.innerHTML = state.mappings.map((m, index) => {
            if (m.type === 'combined') {
                return renderCombinedMapping(m, index);
            }
            return renderSingleMapping(m, index);
        }).join('');

        // Re-attach event listeners
        attachMappingEventListeners();
    }

    function renderSingleMapping(m, index) {
        return `
            <div class="mapping-item mapping-single" data-index="${index}">
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
        `;
    }

    function renderCombinedMapping(m, index) {
        const sourcesHtml = m.html_fields.map((name, i) => `
            <span class="combined-source-pill">${i + 1}. ${name}</span>
        `).join('');

        return `
            <div class="mapping-item mapping-combined" data-index="${index}">
                <div class="mapping-row">
                    <div class="mapping-html mapping-html-combined">
                        <div class="combined-sources">
                            ${sourcesHtml}
                        </div>
                        <div class="combined-template-display">
                            <i class="fas fa-code"></i> ${escapeHtml(m.template)}
                        </div>
                    </div>
                    <i class="fas fa-arrow-right mapping-arrow"></i>
                    <div class="mapping-docuseal">
                        ${m.docuseal_field}
                        <span class="mapping-role">${m.docuseal_role}</span>
                    </div>
                </div>
                <div class="mapping-config">
                    <select class="mapping-transform" data-index="${index}">
                        <option value="">No transform</option>
                        ${window.mapperData.availableTransforms.map(t =>
                            `<option value="${t}" ${m.transform === t ? 'selected' : ''}>${t}</option>`
                        ).join('')}
                    </select>
                    <div class="mapping-actions">
                        <button class="mapping-btn btn-edit-combined" data-index="${index}" title="Edit Template">
                            <i class="fas fa-edit"></i>
                        </button>
                        <button class="mapping-btn btn-remove" data-index="${index}" title="Remove">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    function attachMappingEventListeners() {
        // Transform selects
        document.querySelectorAll('.mapping-transform').forEach(select => {
            select.addEventListener('change', (e) => {
                const index = parseInt(e.target.dataset.index);
                state.mappings[index].transform = e.target.value || null;
            });
        });

        // Remove buttons
        document.querySelectorAll('.btn-remove').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const index = parseInt(e.target.closest('button').dataset.index);
                removeMapping(index);
            });
        });

        // Edit buttons for single mappings
        document.querySelectorAll('.btn-edit').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const index = parseInt(e.target.closest('button').dataset.index);
                openEditModal(index);
            });
        });

        // Edit buttons for combined mappings
        document.querySelectorAll('.btn-edit-combined').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const index = parseInt(e.target.closest('button').dataset.index);
                openEditCombinedModal(index);
            });
        });
    }

    function getConfidenceClass(confidence) {
        if (confidence >= 80) return 'confidence-high';
        if (confidence >= 50) return 'confidence-medium';
        return 'confidence-low';
    }

    // =============================================================================
    // EDIT MODALS
    // =============================================================================
    function openEditModal(index) {
        const mapping = state.mappings[index];
        if (!mapping) return;

        const modalHtml = `
            <div class="edit-modal" id="editModal">
                <div class="edit-modal-content">
                    <div class="edit-modal-header">
                        <h3><i class="fas fa-edit"></i> Edit Mapping</h3>
                        <button class="btn-close-modal" onclick="closeEditModal()"><i class="fas fa-times"></i></button>
                    </div>
                    <div class="edit-modal-body">
                        <div class="form-group">
                            <label>Field Key</label>
                            <input type="text" id="editFieldKey" class="form-input" value="${mapping.field_key || mapping.html_field}" readonly>
                        </div>
                        <div class="form-group">
                            <label>DocuSeal Field</label>
                            <input type="text" id="editDocusealField" class="form-input" value="${mapping.docuseal_field}" readonly>
                        </div>
                        <div class="form-group">
                            <label>Source Path</label>
                            <select id="editSource" class="form-input">
                                ${renderSourceOptions(mapping.source || `form.${mapping.html_field}`)}
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Transform</label>
                            <select id="editTransform" class="form-input">
                                <option value="">None</option>
                                ${window.mapperData.availableTransforms.map(t =>
                                    `<option value="${t}" ${mapping.transform === t ? 'selected' : ''}>${t}</option>`
                                ).join('')}
                            </select>
                        </div>
                        <div class="form-group">
                            <label>
                                <input type="checkbox" id="editHasCondition" ${mapping.condition_field ? 'checked' : ''}>
                                Conditional field
                            </label>
                        </div>
                        <div id="conditionFields" style="display: ${mapping.condition_field ? 'block' : 'none'}">
                            <div class="form-group">
                                <label>Condition Field</label>
                                <input type="text" id="editConditionField" class="form-input"
                                       value="${mapping.condition_field || ''}" placeholder="e.g., form.doc_responsibility">
                            </div>
                            <div class="form-group">
                                <label>Condition Equals</label>
                                <input type="text" id="editConditionEquals" class="form-input"
                                       value="${mapping.condition_equals || ''}" placeholder="e.g., seller">
                            </div>
                        </div>
                    </div>
                    <div class="edit-modal-footer">
                        <button class="btn-action btn-danger" onclick="deleteEditMapping(${index})">
                            <i class="fas fa-trash"></i> Remove
                        </button>
                        <button class="btn-action btn-save" onclick="saveEditMapping(${index})">
                            <i class="fas fa-check"></i> Apply
                        </button>
                    </div>
                </div>
            </div>
        `;

        document.getElementById('editModal')?.remove();
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        document.getElementById('editHasCondition').addEventListener('change', (e) => {
            document.getElementById('conditionFields').style.display = e.target.checked ? 'block' : 'none';
        });

        document.getElementById('editModal').addEventListener('click', (e) => {
            if (e.target.id === 'editModal') closeEditModal();
        });
    }

    function openEditCombinedModal(index) {
        const mapping = state.mappings[index];
        if (!mapping || mapping.type !== 'combined') return;

        const modalHtml = `
            <div class="edit-modal" id="editModal">
                <div class="edit-modal-content">
                    <div class="edit-modal-header">
                        <h3><i class="fas fa-edit"></i> Edit Combined Field</h3>
                        <button class="btn-close-modal" onclick="closeEditModal()"><i class="fas fa-times"></i></button>
                    </div>
                    <div class="edit-modal-body">
                        <div class="form-group">
                            <label>Source Fields</label>
                            <div class="edit-sources-list">
                                ${mapping.html_fields.map((f, i) => `<span class="source-pill">${i + 1}. ${f}</span>`).join('')}
                            </div>
                        </div>
                        <div class="form-group">
                            <label>DocuSeal Field</label>
                            <input type="text" class="form-input" value="${mapping.docuseal_field}" readonly>
                        </div>
                        <div class="form-group">
                            <label>Template</label>
                            <input type="text" id="editCombinedTemplate" class="form-input" value="${escapeHtml(mapping.template)}">
                        </div>
                        <div class="form-group">
                            <label>Preview</label>
                            <div class="combine-preview" id="editCombinePreview">
                                ${generatePreview(mapping.html_fields, mapping.template)}
                            </div>
                        </div>
                        <div class="form-group">
                            <label>Transform</label>
                            <select id="editCombinedTransform" class="form-input">
                                <option value="">None</option>
                                ${window.mapperData.availableTransforms.map(t =>
                                    `<option value="${t}" ${mapping.transform === t ? 'selected' : ''}>${t}</option>`
                                ).join('')}
                            </select>
                        </div>
                    </div>
                    <div class="edit-modal-footer">
                        <button class="btn-action btn-danger" onclick="deleteEditMapping(${index})">
                            <i class="fas fa-trash"></i> Remove
                        </button>
                        <button class="btn-action btn-save" onclick="saveCombinedEditMapping(${index})">
                            <i class="fas fa-check"></i> Apply
                        </button>
                    </div>
                </div>
            </div>
        `;

        document.getElementById('editModal')?.remove();
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        // Update preview on template change
        document.getElementById('editCombinedTemplate').addEventListener('input', (e) => {
            document.getElementById('editCombinePreview').innerHTML =
                generatePreview(mapping.html_fields, e.target.value);
        });

        document.getElementById('editModal').addEventListener('click', (e) => {
            if (e.target.id === 'editModal') closeEditModal();
        });
    }

    function renderSourceOptions(currentSource) {
        const options = [
            { group: 'Form Data', options: [
                { value: '', label: 'form.[field_name]' }
            ]},
            { group: 'Transaction', options: [
                { value: 'transaction.full_address', label: 'transaction.full_address' },
                { value: 'transaction.street_address', label: 'transaction.street_address' },
                { value: 'transaction.city', label: 'transaction.city' },
                { value: 'transaction.state', label: 'transaction.state' },
                { value: 'transaction.zip_code', label: 'transaction.zip_code' }
            ]},
            { group: 'User/Agent', options: [
                { value: 'user.email', label: 'user.email' },
                { value: 'user.full_name', label: 'user.full_name' },
                { value: 'user.phone', label: 'user.phone' }
            ]}
        ];

        let html = `<option value="${currentSource}">${currentSource}</option>`;

        options.forEach(group => {
            html += `<optgroup label="${group.group}">`;
            group.options.forEach(opt => {
                if (opt.value !== currentSource) {
                    html += `<option value="${opt.value}">${opt.label}</option>`;
                }
            });
            html += '</optgroup>';
        });

        return html;
    }

    window.saveEditMapping = function(index) {
        const hasCondition = document.getElementById('editHasCondition').checked;

        state.mappings[index] = {
            ...state.mappings[index],
            source: document.getElementById('editSource').value || `form.${state.mappings[index].html_field}`,
            transform: document.getElementById('editTransform').value || null,
            condition_field: hasCondition ? document.getElementById('editConditionField').value : null,
            condition_equals: hasCondition ? document.getElementById('editConditionEquals').value : null
        };

        renderMappings();
        closeEditModal();
        showToast('Mapping updated', 'success');
    };

    window.saveCombinedEditMapping = function(index) {
        state.mappings[index] = {
            ...state.mappings[index],
            template: document.getElementById('editCombinedTemplate').value,
            transform: document.getElementById('editCombinedTransform').value || null
        };

        renderMappings();
        closeEditModal();
        showToast('Combined field updated', 'success');
    };

    window.deleteEditMapping = function(index) {
        removeMapping(index);
        closeEditModal();
    };

    window.closeEditModal = function() {
        document.getElementById('editModal')?.remove();
    };

    // =============================================================================
    // FIELD STATUS UPDATES
    // =============================================================================
    function updateFieldStatus() {
        // Get all mapped HTML field names
        const mappedHtml = new Set();
        state.mappings.forEach(m => {
            if (m.type === 'combined') {
                m.html_fields.forEach(f => mappedHtml.add(f));
            } else {
                mappedHtml.add(m.html_field);
            }
        });

        const mappedDocuSeal = new Set(state.mappings.map(m => m.docuseal_field));

        // Update HTML fields
        document.querySelectorAll('.html-field').forEach(field => {
            const name = field.dataset.name;
            if (mappedHtml.has(name)) {
                field.classList.add('mapped');
                field.querySelector('.status-mapped').style.display = 'inline-block';
                field.querySelector('.status-unmapped').style.display = 'none';
            } else {
                field.classList.remove('mapped');
                field.querySelector('.status-mapped').style.display = 'none';
                field.querySelector('.status-unmapped').style.display = 'inline-block';
            }
        });

        // Update DocuSeal fields
        document.querySelectorAll('.docuseal-field').forEach(field => {
            const name = field.dataset.name;
            if (mappedDocuSeal.has(name)) {
                field.classList.add('mapped');
                field.querySelector('.status-mapped').style.display = 'inline-block';
                field.querySelector('.status-unmapped').style.display = 'none';
            } else {
                field.classList.remove('mapped');
                field.querySelector('.status-mapped').style.display = 'none';
                field.querySelector('.status-unmapped').style.display = 'inline-block';
            }
        });
    }

    function updateUI() {
        if (elements.mappingCount) {
            elements.mappingCount.textContent = state.mappings.length;
        }
        if (elements.btnSave) {
            elements.btnSave.disabled = state.mappings.length === 0;
        }
    }

    // =============================================================================
    // EVENT LISTENERS
    // =============================================================================
    function setupEventListeners() {
        elements.btnAutoMap?.addEventListener('click', handleAutoMap);
        elements.btnPreviewYaml?.addEventListener('click', handlePreviewYaml);
        elements.btnSave?.addEventListener('click', handleSave);
        elements.btnClearMappings?.addEventListener('click', handleClearMappings);
        elements.rolesGrid?.addEventListener('change', handleRoleChange);

        // YAML modal buttons
        document.getElementById('btnCopyYaml')?.addEventListener('click', handleCopyYaml);
        document.getElementById('btnCloseModal')?.addEventListener('click', () => {
            elements.yamlModal.style.display = 'none';
        });
        document.getElementById('btnSaveFromModal')?.addEventListener('click', handleSave);

        // Close modals on backdrop click
        elements.yamlModal?.addEventListener('click', (e) => {
            if (e.target === elements.yamlModal) {
                elements.yamlModal.style.display = 'none';
            }
        });
    }

    function setupSearch() {
        elements.searchHtml?.addEventListener('input', (e) => {
            filterFields('.html-field', e.target.value);
        });

        elements.searchDocuseal?.addEventListener('input', (e) => {
            filterFields('.docuseal-field', e.target.value);
        });
    }

    function filterFields(selector, query) {
        const normalizedQuery = query.toLowerCase();
        document.querySelectorAll(selector).forEach(field => {
            const name = field.dataset.name.toLowerCase();
            field.style.display = name.includes(normalizedQuery) ? '' : 'none';
        });
    }

    // =============================================================================
    // AUTO-MAP
    // =============================================================================
    async function handleAutoMap() {
        elements.btnAutoMap.disabled = true;
        elements.btnAutoMap.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Mapping...';

        try {
            const response = await fetch('/api/mapper/auto-map', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    html_fields: window.mapperData.htmlFields,
                    docuseal_fields: window.mapperData.docusealFields
                })
            });

            const data = await response.json();

            if (data.success && data.mappings) {
                let addedCount = 0;
                data.mappings.forEach(mapping => {
                    if (!isHtmlFieldMapped(mapping.html_field) && !isDocuSealFieldMapped(mapping.docuseal_field)) {
                        addSingleMapping({
                            ...mapping,
                            source: `form.${mapping.html_field}`
                        });
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

    // =============================================================================
    // YAML GENERATION & SAVE
    // =============================================================================
    async function handlePreviewYaml() {
        const config = buildConfig();

        try {
            const response = await fetch('/api/mapper/generate-yaml', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });

            const data = await response.json();

            if (data.success) {
                state.yamlContent = data.yaml;
                state.isValid = data.valid;

                document.getElementById('yamlPreview').textContent = data.yaml;
                hljs.highlightElement(document.getElementById('yamlPreview'));

                const validationStatus = document.getElementById('validationStatus');
                const validationErrors = document.getElementById('validationErrors');

                if (data.valid) {
                    validationStatus.className = 'validation-status valid';
                    validationStatus.innerHTML = '<i class="fas fa-check-circle"></i> Valid';
                    validationErrors.textContent = '';
                } else {
                    validationStatus.className = 'validation-status invalid';
                    validationStatus.innerHTML = '<i class="fas fa-exclamation-circle"></i> Invalid';
                    validationErrors.textContent = data.errors.join('\n');
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
        const fields = state.mappings.map(m => {
            const field = {
                field_key: m.field_key || m.html_field?.replace(/-/g, '_'),
                docuseal_field: m.docuseal_field,
                role_key: m.docuseal_role.toLowerCase().replace(/\s+/g, '_'),
                transform: m.transform || null,
                condition_field: m.condition_field || null,
                condition_equals: m.condition_equals || null
            };

            if (m.type === 'combined') {
                // Combined field - use sources and template
                field.sources = m.sources;
                field.template = m.template;
            } else {
                // Single field - use source
                field.source = m.source || `form.${m.html_field}`;
            }

            return field;
        });

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

    async function handleSave() {
        if (!state.yamlContent) {
            await handlePreviewYaml();
        }

        if (!state.isValid) {
            showToast('Cannot save invalid YAML. Please fix the errors first.', 'error');
            return;
        }

        try {
            const response = await fetch('/api/mapper/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
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

    function handleClearMappings() {
        if (confirm('Are you sure you want to clear all mappings?')) {
            state.mappings = [];
            renderMappings();
            updateFieldStatus();
            updateUI();
            showToast('All mappings cleared', 'info');
        }
    }

    function handleCopyYaml() {
        navigator.clipboard.writeText(state.yamlContent).then(() => {
            showToast('YAML copied to clipboard!', 'success');
        }).catch(err => {
            console.error('Copy failed:', err);
            showToast('Failed to copy to clipboard', 'error');
        });
    }

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

    // =============================================================================
    // UTILITY FUNCTIONS
    // =============================================================================
    function findBestHtmlMatch(dsFieldName) {
        const dsNormalized = normalizeFieldName(dsFieldName);
        let bestMatch = null;
        let bestScore = 0;

        window.mapperData.htmlFields.forEach(htmlField => {
            if (isHtmlFieldMapped(htmlField.name)) return;

            const htmlNormalized = normalizeFieldName(htmlField.name);

            if (htmlNormalized === dsNormalized) {
                bestMatch = htmlField;
                bestScore = 100;
                return;
            }

            const similarity = calculateSimilarity(htmlNormalized, dsNormalized);
            if (similarity > bestScore && similarity > 60) {
                bestScore = similarity;
                bestMatch = htmlField;
            }
        });

        return bestMatch;
    }

    function normalizeFieldName(name) {
        return name.toLowerCase()
            .replace(/[^a-z0-9]/g, '_')
            .replace(/_+/g, '_')
            .replace(/^_|_$/g, '');
    }

    function calculateSimilarity(a, b) {
        const aWords = new Set(a.split('_'));
        const bWords = new Set(b.split('_'));
        const intersection = new Set([...aWords].filter(x => bWords.has(x)));
        const union = new Set([...aWords, ...bWords]);
        return (intersection.size / union.size) * 100;
    }

    function suggestTransform(htmlType, dsType) {
        if (htmlType === 'checkbox' || dsType === 'checkbox') return 'checkbox';
        if (htmlType === 'date' || dsType === 'date') return 'date_short';
        if (htmlType === 'money' || htmlType === 'number') return 'currency';
        return null;
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;

        const icon = type === 'success' ? 'check-circle' :
                     type === 'error' ? 'exclamation-circle' :
                     type === 'warning' ? 'exclamation-triangle' : 'info-circle';

        const bg = type === 'success' ? '#10b981' :
                   type === 'error' ? '#ef4444' :
                   type === 'warning' ? '#f59e0b' : '#3b82f6';

        toast.innerHTML = `<i class="fas fa-${icon}"></i><span>${message}</span>`;
        toast.style.cssText = `
            position: fixed;
            bottom: 20px;
            right: 20px;
            padding: 12px 20px;
            border-radius: 8px;
            background: ${bg};
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

    // =============================================================================
    // INITIALIZATION
    // =============================================================================
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
