/**
 * Base JavaScript functionality for CRM application
 * Handles: sidebar toggle, flash messages, user dropdown, tooltips, mobile menu, session expiry
 */

/**
 * Global fetch wrapper to detect session expiry
 * Intercepts all fetch responses and redirects to login if session has expired
 */
(function() {
    const originalFetch = window.fetch;
    
    window.fetch = async function(...args) {
        try {
            const response = await originalFetch.apply(this, args);
            
            // Check if we were redirected to the login page (session expired)
            if (response.redirected && response.url.includes('/login')) {
                handleSessionExpiry();
                // Return a rejected promise to stop further processing
                return Promise.reject(new Error('Session expired'));
            }
            
            // Check if response is HTML containing login form (fallback detection)
            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('text/html')) {
                // Clone response to check content without consuming it
                const clone = response.clone();
                const text = await clone.text();
                
                // Check for login form indicators
                if (text.includes('action="/login"') || text.includes('name="csrf_token"') && text.includes('Sign in to access')) {
                    handleSessionExpiry();
                    return Promise.reject(new Error('Session expired'));
                }
            }
            
            return response;
        } catch (error) {
            // Re-throw the error but check if it's a session expiry we already handled
            if (error.message === 'Session expired') {
                throw error;
            }
            throw error;
        }
    };
    
    function handleSessionExpiry() {
        // Don't redirect on public pages (login, register, password reset, etc.)
        const publicPaths = ['/login', '/register', '/auth/register', '/forgot-password', '/reset-password', '/accept-invite'];
        const currentPath = window.location.pathname;
        
        if (publicPaths.some(path => currentPath.startsWith(path))) {
            return;
        }
        
        // Store current URL for redirect after login
        sessionStorage.setItem('returnUrl', window.location.href);
        
        // Show session expired notification
        showSessionExpiredToast();
        
        // Redirect to login after a brief delay so user sees the message
        setTimeout(() => {
            window.location.href = '/login';
        }, 1500);
    }
    
    function showSessionExpiredToast() {
        // Remove any existing session expired toasts
        const existingToast = document.getElementById('session-expired-toast');
        if (existingToast) {
            existingToast.remove();
        }
        
        // Create toast notification
        const toast = document.createElement('div');
        toast.id = 'session-expired-toast';
        toast.className = 'fixed top-4 left-1/2 transform -translate-x-1/2 z-[9999] bg-amber-100 text-amber-800 px-6 py-4 rounded-lg shadow-lg flex items-center space-x-3';
        toast.innerHTML = `
            <i class="fas fa-clock text-amber-600"></i>
            <span class="font-medium">Your session has expired. Redirecting to login...</span>
        `;
        
        document.body.appendChild(toast);
    }
})();

document.addEventListener('DOMContentLoaded', function() {
    const flashMessages = document.querySelectorAll('.toast-message');
    const userIcon = document.getElementById('userIcon');
    const userDropdown = document.getElementById('userDropdown');
    const sidebar = document.getElementById('sidebar');
    const mainContent = document.getElementById('mainContent');
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebarTexts = document.querySelectorAll('.sidebar-text');
    const tooltipLabels = document.querySelectorAll('.tooltip-label');
    const iconGroups = document.querySelectorAll('aside .group');
    const contentWrapper = document.querySelector('.content-wrapper');
    const pageTransition = document.querySelector('.page-transition');
    const mobileNav = document.querySelector('.mobile-nav');

    let tooltipTimeouts = new Map();
    let isExpanded = localStorage.getItem('sidebarExpanded') === 'true';

    function applyInitialState() {
        requestAnimationFrame(() => {
            // Remove the initial style element if it exists
            const initialStyle = document.querySelector('style[data-initial-sidebar]');
            if (initialStyle) {
                initialStyle.remove();
            }

            if (sidebar) {
                sidebar.style.transition = 'none';
            }
            if (mainContent) {
                mainContent.style.transition = 'none';
            }
            sidebarTexts.forEach(text => text.style.transition = 'none');

            if (isExpanded && sidebar && mainContent) {
                sidebar.classList.remove('w-16');
                sidebar.classList.add('w-44');
                mainContent.classList.remove('md:ml-16');
                mainContent.classList.add('md:ml-44');

                sidebarTexts.forEach(text => {
                    text.classList.remove('opacity-0', 'w-0');
                    text.classList.add('opacity-100');
                    text.style.width = 'auto';
                });

                tooltipLabels.forEach(tooltip => {
                    tooltip.style.display = 'none';
                });
            }

            // Force reflow
            if (sidebar) {
                sidebar.offsetHeight;
            }

            requestAnimationFrame(() => {
                if (sidebar) {
                    sidebar.style.transition = '';
                }
                if (mainContent) {
                    mainContent.style.transition = '';
                }
                sidebarTexts.forEach(text => text.style.transition = '');
            });
        });
    }

    applyInitialState();

    function toggleSidebar() {
        if (!sidebar || !mainContent) return;
        
        isExpanded = !isExpanded;
        localStorage.setItem('sidebarExpanded', isExpanded);

        if (isExpanded) {
            document.documentElement.style.setProperty('--sidebar-width', 'var(--sidebar-expanded-width)');
            document.documentElement.classList.add('sidebar-expanded');
            sidebar.classList.remove('w-16');
            sidebar.classList.add('w-44');
            mainContent.classList.remove('md:ml-16');
            mainContent.classList.add('md:ml-44');

            sidebarTexts.forEach(text => {
                text.classList.remove('opacity-0', 'w-0');
                text.classList.add('opacity-100');
                text.style.width = 'auto';
            });

            tooltipLabels.forEach(tooltip => {
                tooltip.style.display = 'none';
            });
        } else {
            document.documentElement.style.setProperty('--sidebar-width', '4rem');
            document.documentElement.classList.remove('sidebar-expanded');
            sidebar.classList.remove('w-44');
            sidebar.classList.add('w-16');
            mainContent.classList.remove('md:ml-44');
            mainContent.classList.add('md:ml-16');

            sidebarTexts.forEach(text => {
                text.classList.remove('opacity-100');
                text.classList.add('opacity-0', 'w-0');
                text.style.width = '0';
            });

            tooltipLabels.forEach(tooltip => {
                tooltip.style.display = '';
            });
        }
    }

    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', toggleSidebar);
    }

    // Flash message auto-dismiss
    flashMessages.forEach(function(message) {
        setTimeout(function() {
            message.classList.add('fade-out');
            setTimeout(function() {
                message.remove();
            }, 500);
        }, 4000);
    });

    // User dropdown functionality - click-based
    if (userIcon && userDropdown) {
        userIcon.addEventListener('click', (e) => {
            e.stopPropagation();
            const isVisible = !userDropdown.classList.contains('hidden');
            
            if (isVisible) {
                closeUserDropdown();
            } else {
                openUserDropdown();
            }
        });

        // Close dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (!userDropdown.classList.contains('hidden') && 
                !userDropdown.contains(e.target) && 
                !userIcon.contains(e.target)) {
                closeUserDropdown();
            }
        });

        // Close dropdown on Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !userDropdown.classList.contains('hidden')) {
                closeUserDropdown();
                userIcon.focus();
            }
        });
    }

    function openUserDropdown() {
        if (!userDropdown || !userIcon) return;
        userDropdown.classList.remove('hidden');
        userIcon.setAttribute('aria-expanded', 'true');
        requestAnimationFrame(() => {
            userDropdown.classList.add('opacity-100');
        });
    }

    function closeUserDropdown() {
        if (!userDropdown || !userIcon) return;
        userDropdown.classList.remove('opacity-100');
        userIcon.setAttribute('aria-expanded', 'false');
        setTimeout(() => {
            userDropdown.classList.add('hidden');
        }, 200);
    }

    // Tooltip functionality
    iconGroups.forEach(group => {
        const tooltip = group.querySelector('.tooltip-label');
        if (!tooltip) return;

        group.addEventListener('mouseenter', () => {
            if (!isExpanded) {
                const timeoutId = setTimeout(() => {
                    tooltip.classList.remove('hidden');
                    requestAnimationFrame(() => {
                        tooltip.classList.add('opacity-100');
                    });
                }, 500);
                tooltipTimeouts.set(group, timeoutId);
            }
        });

        group.addEventListener('mouseleave', () => {
            const timeoutId = tooltipTimeouts.get(group);
            if (timeoutId) {
                clearTimeout(timeoutId);
                tooltipTimeouts.delete(group);
            }
            tooltip.classList.remove('opacity-100');
            tooltip.classList.add('hidden');
        });
    });

    // Mobile menu initialization
    initializeMobileMenu();
});

/**
 * Initialize mobile menu state
 */
function initializeMobileMenu() {
    const menu = document.getElementById('mobileUserMenu');
    if (menu) {
        menu.classList.add('-translate-y-full', 'opacity-0', 'pointer-events-none');
        document.body.style.overflow = '';
    }
}

/**
 * Toggle mobile user menu visibility
 * Called from onclick handlers in base.html
 */
function toggleMobileUserMenu() {
    const menu = document.getElementById('mobileUserMenu');
    if (!menu) return;

    const isHidden = menu.classList.contains('-translate-y-full');

    if (isHidden) {
        // Show menu
        menu.classList.remove('-translate-y-full', 'opacity-0', 'pointer-events-none');
        menu.classList.add('translate-y-0', 'opacity-100', 'pointer-events-auto');
        document.body.style.overflow = 'hidden';
    } else {
        // Hide menu
        menu.classList.remove('translate-y-0', 'opacity-100', 'pointer-events-auto');
        menu.classList.add('-translate-y-full', 'opacity-0', 'pointer-events-none');
        document.body.style.overflow = '';
    }
}

/**
 * Hide mobile menu (utility function)
 */
function hideMobileMenu(menu) {
    if (!menu) menu = document.getElementById('mobileUserMenu');
    if (!menu) return;

    menu.classList.remove('translate-y-0', 'opacity-100', 'pointer-events-auto');
    menu.classList.add('-translate-y-full', 'opacity-0', 'pointer-events-none');
    document.body.style.overflow = '';
}

/**
 * Toggle mobile navigation menu visibility
 */
function toggleMobileNavMenu() {
    const menu = document.getElementById('mobileNavMenu');
    if (!menu) return;

    const isHidden = menu.classList.contains('-translate-x-full');

    if (isHidden) {
        // Show menu
        menu.classList.remove('-translate-x-full');
        menu.classList.add('translate-x-0');
        document.body.style.overflow = 'hidden';
    } else {
        // Hide menu
        menu.classList.remove('translate-x-0');
        menu.classList.add('-translate-x-full');
        document.body.style.overflow = '';
    }
}

