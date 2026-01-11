/**
 * Loading Overlay Component
 * 
 * A reusable premium loading overlay that displays during async operations
 * like document generation, preview loading, or sending for signature.
 * 
 * Usage:
 * 1. Include this JS file in your template
 * 2. Include loading_overlay.css for styles
 * 3. Call initLoadingOverlay() to inject the overlay HTML
 * 4. Call showLoadingOverlay(message, submessage) to display
 * 5. Call hideLoadingOverlay() to hide
 * 
 * Example:
 *   initLoadingOverlay();
 *   showLoadingOverlay('Generating Documents...', 'This may take a few moments');
 */

(function () {
    'use strict';

    // Default messages
    const DEFAULT_MESSAGE = 'Loading...';
    const DEFAULT_SUBMESSAGE = 'Please wait';

    /**
     * Initialize the loading overlay by injecting the HTML into the page
     */
    window.initLoadingOverlay = function () {
        // Check if already initialized
        if (document.getElementById('loadingOverlay')) {
            return;
        }

        // Create overlay HTML
        const overlayHTML = `
            <div id="loadingOverlay" class="loading-overlay">
                <div class="loading-content">
                    <div class="spinner-ring"></div>
                    <div class="loading-text" id="loadingText">${DEFAULT_MESSAGE}</div>
                    <div class="loading-subtext" id="loadingSubtext">${DEFAULT_SUBMESSAGE}</div>
                </div>
            </div>
        `;

        // Inject into body
        document.body.insertAdjacentHTML('beforeend', overlayHTML);
    };

    /**
     * Show the loading overlay with optional custom messages
     * @param {string} message - Main message to display (e.g., "Generating Documents...")
     * @param {string} submessage - Secondary message (e.g., "This may take a few moments")
     */
    window.showLoadingOverlay = function (message, submessage) {
        const overlay = document.getElementById('loadingOverlay');
        if (!overlay) {
            initLoadingOverlay();
        }

        // Update messages if provided
        if (message) {
            const textEl = document.getElementById('loadingText');
            if (textEl) textEl.textContent = message;
        }
        if (submessage) {
            const subtextEl = document.getElementById('loadingSubtext');
            if (subtextEl) subtextEl.textContent = submessage;
        }

        // Show overlay
        document.getElementById('loadingOverlay').classList.add('show');
    };

    /**
     * Hide the loading overlay
     */
    window.hideLoadingOverlay = function () {
        const overlay = document.getElementById('loadingOverlay');
        if (overlay) {
            overlay.classList.remove('show');
        }
    };

    /**
     * Attach loading overlay to a form submit button
     * @param {string} buttonSelector - CSS selector for the button
     * @param {string} message - Loading message to display
     * @param {string} submessage - Secondary message
     */
    window.attachLoadingToButton = function (buttonSelector, message, submessage) {
        const button = document.querySelector(buttonSelector);
        if (button) {
            button.addEventListener('click', function () {
                showLoadingOverlay(
                    message || 'Processing...',
                    submessage || 'This may take a few moments'
                );
            });
        }
    };

    /**
     * Attach loading overlay to form submission
     * Useful for forms that submit via form action (not fetch)
     * @param {string} formSelector - CSS selector for the form
     * @param {string} submitButtonValue - Value of the submit button to trigger on
     * @param {string} message - Loading message
     * @param {string} submessage - Secondary message
     */
    window.attachLoadingToFormSubmit = function (formSelector, submitButtonValue, message, submessage) {
        const form = document.querySelector(formSelector);
        if (!form) return;

        // Find button with matching value (could be inside or outside form)
        const button = document.querySelector(`button[form="${form.id}"][value="${submitButtonValue}"]`) ||
            form.querySelector(`button[value="${submitButtonValue}"]`);

        if (button) {
            button.addEventListener('click', function () {
                showLoadingOverlay(
                    message || 'Processing...',
                    submessage || 'This may take a few moments'
                );
            });
        }
    };

})();
