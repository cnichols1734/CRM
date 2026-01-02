/**
 * Base JavaScript functionality for CRM application
 * Handles: sidebar toggle, flash messages, user dropdown, tooltips, mobile menu
 */

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

    let userDropdownTimeout;
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

    // User dropdown functionality
    if (userIcon && userDropdown) {
        userIcon.parentElement.addEventListener('mouseenter', () => {
            clearTimeout(userDropdownTimeout);
            userDropdown.classList.remove('hidden');
            requestAnimationFrame(() => {
                userDropdown.classList.add('opacity-100');
            });
        });

        userIcon.parentElement.addEventListener('mouseleave', (e) => {
            if (!e.relatedTarget || !userDropdown.contains(e.relatedTarget)) {
                userDropdownTimeout = setTimeout(() => {
                    if (!userDropdown.matches(':hover')) {
                        userDropdown.classList.remove('opacity-100');
                        setTimeout(() => {
                            userDropdown.classList.add('hidden');
                        }, 300);
                    }
                }, 200);
            }
        });

        userDropdown.addEventListener('mouseenter', () => {
            clearTimeout(userDropdownTimeout);
        });

        userDropdown.addEventListener('mouseleave', () => {
            userDropdownTimeout = setTimeout(() => {
                if (!userIcon.matches(':hover')) {
                    userDropdown.classList.remove('opacity-100');
                    setTimeout(() => {
                        userDropdown.classList.add('hidden');
                    }, 300);
                }
            }, 200);
        });
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

