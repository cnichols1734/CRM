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

        // Style the rendered markdown
        styleRenderedPlan();
    }

    /**
     * Apply styles to the rendered plan
     */
    function styleRenderedPlan() {
        if (!planContent) return;

        // Style headers
        planContent.querySelectorAll('h1, h2').forEach(h => {
            h.classList.add('text-xl', 'font-bold', 'text-gray-900', 'mt-6', 'mb-3', 'border-b', 'border-gray-200', 'pb-2');
        });

        planContent.querySelectorAll('h3').forEach(h => {
            h.classList.add('text-lg', 'font-semibold', 'text-gray-800', 'mt-4', 'mb-2');
        });

        // Style lists
        planContent.querySelectorAll('ul').forEach(ul => {
            ul.classList.add('list-disc', 'list-inside', 'space-y-1', 'text-gray-700', 'ml-4');
        });

        planContent.querySelectorAll('ol').forEach(ol => {
            ol.classList.add('list-decimal', 'list-inside', 'space-y-1', 'text-gray-700', 'ml-4');
        });

        // Style paragraphs
        planContent.querySelectorAll('p').forEach(p => {
            p.classList.add('text-gray-700', 'mb-3');
        });

        // Style strong/bold
        planContent.querySelectorAll('strong').forEach(s => {
            s.classList.add('text-gray-900');
        });
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

