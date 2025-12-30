/**
 * OG Action Plan - Multi-step Wizard JavaScript
 * Handles form navigation, validation, and submission
 */

document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('actionPlanForm');
    const steps = document.querySelectorAll('.form-step');
    const prevBtn = document.getElementById('prevBtn');
    const nextBtn = document.getElementById('nextBtn');
    const submitBtn = document.getElementById('submitBtn');
    const progressBar = document.getElementById('progressBar');
    const currentStepSpan = document.getElementById('currentStep');
    const sectionTitle = document.getElementById('sectionTitle');
    const loadingOverlay = document.getElementById('loadingOverlay');
    const planDisplay = document.getElementById('planDisplay');
    const questionnaireForm = document.getElementById('questionnaireForm');
    const planContent = document.getElementById('planContent');
    const retakeBtn = document.getElementById('retakeBtn');

    const totalSteps = steps.length;
    let currentStep = 1;
    let isInitialLoad = true;  // Flag to prevent scroll on initial page load

    const sectionTitles = [
        'Your Natural Tendencies',
        'Time & Consistency',
        'What You Actually Enjoy',
        'Your Strengths',
        'Lead Source Reality Check',
        'Your 2026 Lead-Gen Pillars',
        'Tracking & Success'
    ];

    // Initialize (without scrolling)
    updateUI();
    isInitialLoad = false;

    // Event Listeners
    if (prevBtn) {
        prevBtn.addEventListener('click', goToPrevStep);
    }

    if (nextBtn) {
        nextBtn.addEventListener('click', goToNextStep);
    }

    if (form) {
        form.addEventListener('submit', handleSubmit);
    }

    if (retakeBtn) {
        retakeBtn.addEventListener('click', handleRetake);
    }

    /**
     * Update the UI based on current step
     */
    function updateUI() {
        // Update step visibility
        steps.forEach((step, index) => {
            if (index + 1 === currentStep) {
                step.classList.remove('hidden');
            } else {
                step.classList.add('hidden');
            }
        });

        // Update progress bar
        const progress = (currentStep / totalSteps) * 100;
        if (progressBar) {
            progressBar.style.width = `${progress}%`;
        }

        // Update step counter
        if (currentStepSpan) {
            currentStepSpan.textContent = currentStep;
        }

        // Update section title
        if (sectionTitle) {
            sectionTitle.textContent = sectionTitles[currentStep - 1];
        }

        // Update button visibility
        if (prevBtn) {
            if (currentStep === 1) {
                prevBtn.classList.add('hidden');
            } else {
                prevBtn.classList.remove('hidden');
            }
        }

        if (nextBtn && submitBtn) {
            if (currentStep === totalSteps) {
                nextBtn.classList.add('hidden');
                submitBtn.classList.remove('hidden');
            } else {
                nextBtn.classList.remove('hidden');
                submitBtn.classList.add('hidden');
            }
        }

        // Only scroll to form when navigating between steps (not on initial page load)
        if (!isInitialLoad) {
            const formCard = form ? form.closest('.bg-white') : null;
            if (formCard) {
                formCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }
    }

    /**
     * Validate the current step
     */
    function validateCurrentStep() {
        const currentStepEl = steps[currentStep - 1];
        const requiredInputs = currentStepEl.querySelectorAll('[required]');
        let isValid = true;

        requiredInputs.forEach(input => {
            if (input.type === 'radio') {
                // For radio buttons, check if any in the group is selected
                const name = input.name;
                const checked = currentStepEl.querySelector(`input[name="${name}"]:checked`);
                if (!checked) {
                    isValid = false;
                    highlightError(input.closest('.space-y-2'));
                }
            } else if (input.type === 'checkbox') {
                // For required checkboxes, at least one should be checked
                const name = input.name;
                const checked = currentStepEl.querySelector(`input[name="${name}"]:checked`);
                if (!checked) {
                    isValid = false;
                    highlightError(input.closest('.space-y-2, .grid'));
                }
            } else {
                if (!input.value.trim()) {
                    isValid = false;
                    highlightError(input);
                }
            }
        });

        return isValid;
    }

    /**
     * Highlight an error on an element
     */
    function highlightError(element) {
        if (!element) return;

        element.classList.add('ring-2', 'ring-red-500', 'rounded-lg');
        
        setTimeout(() => {
            element.classList.remove('ring-2', 'ring-red-500', 'rounded-lg');
        }, 2000);
    }

    /**
     * Go to the previous step
     */
    function goToPrevStep() {
        if (currentStep > 1) {
            currentStep--;
            updateUI();
        }
    }

    /**
     * Go to the next step
     */
    function goToNextStep() {
        if (validateCurrentStep()) {
            if (currentStep < totalSteps) {
                currentStep++;
                updateUI();
            }
        }
    }

    /**
     * Collect all form data
     */
    function collectFormData() {
        const formData = new FormData(form);
        const responses = {};

        // Handle regular inputs
        const inputs = form.querySelectorAll('input:not([type="checkbox"]):not([type="radio"]), textarea');
        inputs.forEach(input => {
            if (input.name && input.value) {
                responses[input.name] = input.value;
            }
        });

        // Handle radio buttons
        const radios = form.querySelectorAll('input[type="radio"]:checked');
        radios.forEach(radio => {
            responses[radio.name] = radio.value;
        });

        // Handle checkboxes (group by name)
        const checkboxes = form.querySelectorAll('input[type="checkbox"]:checked');
        checkboxes.forEach(checkbox => {
            if (!responses[checkbox.name]) {
                responses[checkbox.name] = [];
            }
            responses[checkbox.name].push(checkbox.value);
        });

        return responses;
    }

    /**
     * Handle form submission
     */
    async function handleSubmit(e) {
        e.preventDefault();

        if (!validateCurrentStep()) {
            return;
        }

        const responses = collectFormData();

        // Show loading overlay
        if (loadingOverlay) {
            loadingOverlay.classList.remove('hidden');
        }

        try {
            const response = await fetch('/api/action-plan/submit', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ responses }),
            });

            const data = await response.json();

            if (data.success) {
                // Render the plan
                renderPlan(data.plan);
                
                // Show plan display, hide form and intro
                if (questionnaireForm) {
                    questionnaireForm.classList.add('hidden');
                }
                const introSection = document.getElementById('introSection');
                if (introSection) {
                    introSection.classList.add('hidden');
                }
                if (planDisplay) {
                    planDisplay.classList.remove('hidden');
                }
                // Scroll to top to see the plan
                window.scrollTo({ top: 0, behavior: 'smooth' });
            } else {
                throw new Error(data.error || 'Failed to generate action plan');
            }
        } catch (error) {
            console.error('Error submitting form:', error);
            alert('There was an error generating your action plan. Please try again.');
        } finally {
            // Hide loading overlay
            if (loadingOverlay) {
                loadingOverlay.classList.add('hidden');
            }
        }
    }

    /**
     * Render the AI-generated plan with markdown
     */
    function renderPlan(planText) {
        if (!planContent) return;

        // Use marked.js to parse markdown
        if (typeof marked !== 'undefined') {
            planContent.innerHTML = marked.parse(planText);
        } else {
            // Fallback: basic formatting
            planContent.innerHTML = planText
                .replace(/## (.*)/g, '<h2 class="text-xl font-bold text-gray-900 mt-6 mb-3">$1</h2>')
                .replace(/### (.*)/g, '<h3 class="text-lg font-semibold text-gray-800 mt-4 mb-2">$1</h3>')
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                .replace(/\n- /g, '<br>• ')
                .replace(/\n/g, '<br>');
        }

        // Enhance the rendered content
        enhancePlanDisplay();
    }

    /**
     * Enhance the plan display with professional styling
     */
    function enhancePlanDisplay() {
        if (!planContent) return;

        // Define section mappings with icons and colors
        const sectionConfig = {
            'Your Three Lead-Gen Pillars': { icon: 'fa-bullseye', class: 'section-pillars', color: '#f97316' },
            'High-Level Strategy Overview': { icon: 'fa-compass', class: 'section-overview', color: '#6366f1' },
            'Monthly Action Plan': { icon: 'fa-calendar-alt', class: 'section-monthly', color: '#f97316' },
            'Weekly Action Plan': { icon: 'fa-calendar-week', class: 'section-weekly', color: '#10b981' },
            'Optional High-Impact Bonus Ideas': { icon: 'fa-lightbulb', class: 'section-bonus', color: '#8b5cf6' },
            'Your Next Steps This Week': { icon: 'fa-rocket', class: 'section-next', color: '#0ea5e9' }
        };

        // Build table of contents
        const toc = document.createElement('div');
        toc.className = 'plan-toc';
        toc.innerHTML = `
            <div class="plan-toc-title">Quick Navigation</div>
            <div class="plan-toc-links" id="tocLinks"></div>
        `;

        // Get all H2 elements
        const h2Elements = planContent.querySelectorAll('h2');
        let tocLinks = [];

        h2Elements.forEach((h2, index) => {
            const title = h2.textContent.trim();
            const sectionId = `section-${index}`;
            
            // Find matching config
            let config = null;
            for (const [key, val] of Object.entries(sectionConfig)) {
                if (title.toLowerCase().includes(key.toLowerCase()) || key.toLowerCase().includes(title.toLowerCase().substring(0, 15))) {
                    config = val;
                    break;
                }
            }
            
            // Default config if no match
            if (!config) {
                config = { icon: 'fa-bookmark', class: 'section-default', color: '#64748b' };
            }

            // Create section wrapper
            const section = document.createElement('div');
            section.className = `plan-section ${config.class}`;
            section.id = sectionId;

            // Create section header
            const header = document.createElement('div');
            header.className = 'plan-section-header';
            header.innerHTML = `
                <div class="plan-section-icon">
                    <i class="fas ${config.icon}"></i>
                </div>
                <h2 class="plan-section-title">${title}</h2>
                <i class="fas fa-chevron-down plan-section-toggle"></i>
            `;

            // Create section content
            const content = document.createElement('div');
            content.className = 'plan-section-content';

            // Collect all content until next H2 or end
            let nextElement = h2.nextElementSibling;
            while (nextElement && nextElement.tagName !== 'H2') {
                const clone = nextElement.cloneNode(true);
                content.appendChild(clone);
                nextElement = nextElement.nextElementSibling;
            }

            // Enhance H3 subsections with pillar colors
            content.querySelectorAll('h3').forEach(h3 => {
                const h3Text = h3.textContent.toLowerCase();
                let pillarClass = '';
                if (h3Text.includes('pillar 1') || h3Text.includes('pillar #1')) pillarClass = 'pillar-1-sub';
                else if (h3Text.includes('pillar 2') || h3Text.includes('pillar #2')) pillarClass = 'pillar-2-sub';
                else if (h3Text.includes('pillar 3') || h3Text.includes('pillar #3')) pillarClass = 'pillar-3-sub';
                
                // Wrap h3 and following content in subsection
                const subsection = document.createElement('div');
                subsection.className = `plan-subsection ${pillarClass}`;
                
                let nextEl = h3.nextElementSibling;
                const elementsToWrap = [h3.cloneNode(true)];
                
                while (nextEl && nextEl.tagName !== 'H3' && nextEl.tagName !== 'H2') {
                    elementsToWrap.push(nextEl.cloneNode(true));
                    nextEl = nextEl.nextElementSibling;
                }
                
                elementsToWrap.forEach(el => subsection.appendChild(el));
                
                // Replace original h3 with subsection
                h3.parentNode.insertBefore(subsection, h3);
                
                // Remove original elements
                let toRemove = h3.nextElementSibling;
                while (toRemove && toRemove !== subsection && toRemove.tagName !== 'H3' && toRemove.tagName !== 'H2') {
                    const next = toRemove.nextElementSibling;
                    toRemove.remove();
                    toRemove = next;
                }
                h3.remove();
            });

            section.appendChild(header);
            section.appendChild(content);

            // Add click handler for collapse
            header.addEventListener('click', () => {
                section.classList.toggle('collapsed');
            });

            // Replace H2 with section
            h2.parentNode.insertBefore(section, h2);
            
            // Remove original H2 and its content (already cloned)
            let toRemove = h2.nextElementSibling;
            while (toRemove && toRemove.tagName !== 'H2' && !toRemove.classList.contains('plan-section')) {
                const next = toRemove.nextElementSibling;
                toRemove.remove();
                toRemove = next;
            }
            h2.remove();

            // Add to TOC
            tocLinks.push(`<a href="#${sectionId}" class="plan-toc-link"><i class="fas ${config.icon}"></i>${title.substring(0, 20)}${title.length > 20 ? '...' : ''}</a>`);
        });

        // Insert TOC at the top (after H1 if exists)
        const h1 = planContent.querySelector('h1');
        if (h1) {
            toc.querySelector('#tocLinks').innerHTML = tocLinks.join('');
            h1.insertAdjacentElement('afterend', toc);
        } else {
            toc.querySelector('#tocLinks').innerHTML = tocLinks.join('');
            planContent.insertBefore(toc, planContent.firstChild);
        }

        // Enhance pillar cards if they exist (look for the pillars section)
        enhancePillarCards();
        
        // Smooth scroll for TOC links
        toc.querySelectorAll('.plan-toc-link').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const targetId = link.getAttribute('href').substring(1);
                const target = document.getElementById(targetId);
                if (target) {
                    // Expand if collapsed
                    if (target.classList.contains('collapsed')) {
                        target.classList.remove('collapsed');
                    }
                    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            });
        });
    }

    /**
     * Enhance pillar cards with visual styling
     */
    function enhancePillarCards() {
        if (!planContent) return;

        // Find the pillars section and convert bullet points to cards
        const pillarSection = planContent.querySelector('.section-pillars .plan-section-content');
        if (!pillarSection) return;

        const ul = pillarSection.querySelector('ul');
        if (!ul) return;

        const items = ul.querySelectorAll('li');
        if (items.length === 0) return;

        // Create pillar grid
        const grid = document.createElement('div');
        grid.className = 'pillar-grid';

        items.forEach((item, index) => {
            const card = document.createElement('div');
            card.className = `pillar-card pillar-${index + 1}`;
            
            // Parse the content - usually "**Pillar Name** — description"
            const html = item.innerHTML;
            const strongMatch = html.match(/<strong>(.*?)<\/strong>/);
            const title = strongMatch ? strongMatch[1] : `Pillar ${index + 1}`;
            const desc = html.replace(/<strong>.*?<\/strong>/, '').replace(/^[\s—–-]+/, '').trim();
            
            card.innerHTML = `
                <div class="pillar-card-number">${index + 1}</div>
                <div class="pillar-card-title">${title}</div>
                <div class="pillar-card-desc">${desc}</div>
            `;
            
            grid.appendChild(card);
        });

        // Replace ul with grid
        ul.parentNode.replaceChild(grid, ul);
    }

    /**
     * Handle retake button click
     */
    async function handleRetake() {
        if (!confirm('Are you sure you want to retake the questionnaire? Your current action plan will be deleted.')) {
            return;
        }

        try {
            const response = await fetch('/api/action-plan/retake', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
            });

            const data = await response.json();

            if (data.success) {
                // Reset form
                if (form) {
                    form.reset();
                }
                currentStep = 1;
                isInitialLoad = true;  // Prevent scroll when resetting
                updateUI();
                isInitialLoad = false;

                // Show form and intro, hide plan
                if (planDisplay) {
                    planDisplay.classList.add('hidden');
                }
                if (questionnaireForm) {
                    questionnaireForm.classList.remove('hidden');
                }
                // Show intro section
                const introSection = document.getElementById('introSection');
                if (introSection) {
                    introSection.classList.remove('hidden');
                }
                // Scroll to top of page
                window.scrollTo({ top: 0, behavior: 'smooth' });
            } else {
                throw new Error(data.error || 'Failed to reset action plan');
            }
        } catch (error) {
            console.error('Error retaking questionnaire:', error);
            alert('There was an error. Please try again.');
        }
    }

    // If plan exists on page load, render it with markdown
    if (planContent && planContent.textContent.trim()) {
        const existingPlan = planContent.textContent.trim();
        if (existingPlan && typeof marked !== 'undefined') {
            renderPlan(existingPlan);
        }
    }
});

