// static/js/agent-resources.js
// Fetches resources from API (org-specific)
(function () {
  'use strict';

  const STORAGE_KEY = 'sidebar.agentResources.expanded';

  // Resources will be fetched from API
  let LINKS = [];

  function $(id) {
    return document.getElementById(id);
  }

  async function fetchResources() {
    try {
      const response = await fetch('/api/resources');
      const data = await response.json();
      if (data.success && data.resources) {
        LINKS = data.resources.map(r => ({
          label: r.label,
          url: r.url
        }));
      }
    } catch (error) {
      console.error('Failed to fetch resources:', error);
      LINKS = [];
    }
    return LINKS;
  }

  function renderLinks(listElement) {
    if (!listElement) return;
    listElement.innerHTML = '';
    
    if (LINKS.length === 0) {
      const li = document.createElement('li');
      li.className = 'text-white/60 text-sm px-1.5 py-1 italic';
      li.textContent = 'No resources configured';
      listElement.appendChild(li);
      return;
    }
    
    const sorted = LINKS.slice().sort((a, b) => a.label.localeCompare(b.label));
    sorted.forEach(({ label, url }) => {
      const li = document.createElement('li');
      const a = document.createElement('a');
      a.href = url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = label;
      a.className = 'block text-white/90 hover:text-orange-500 text-sm px-1.5 py-1 rounded focus:outline-none focus:ring-2 focus:ring-orange-500';
      li.appendChild(a);
      listElement.appendChild(li);
    });
  }

  async function setupAccordion() {
    const button = $('agentResourcesBtn');
    const panel = $('agentResourcesPanel');
    const list = $('agentResourcesList');
    const chevron = $('agentResourcesChevron');
    const sidebar = document.getElementById('sidebar');
    const mainContent = document.getElementById('mainContent');
    const sidebarToggle = document.getElementById('sidebarToggle');

    if (!button || !panel || !list || !chevron) return;

    // Fetch resources from API first
    await fetchResources();
    renderLinks(list);

    function setExpanded(expanded) {
      button.setAttribute('aria-expanded', String(expanded));
      if (expanded) {
        panel.classList.remove('hidden');
        chevron.classList.add('rotate-180');
      } else {
        panel.classList.add('hidden');
        chevron.classList.remove('rotate-180');
      }
      localStorage.setItem(STORAGE_KEY, String(expanded));
    }

    function toggle() {
      const expanded = button.getAttribute('aria-expanded') === 'true';
      setExpanded(!expanded);
    }

    function isSidebarExpanded() {
      return localStorage.getItem('sidebarExpanded') === 'true';
    }

    function expandSidebar() {
      if (!sidebar || !mainContent) return;
      localStorage.setItem('sidebarExpanded', 'true');
      document.documentElement.style.setProperty('--sidebar-width', 'var(--sidebar-expanded-width)');
      document.documentElement.classList.add('sidebar-expanded');
      sidebar.classList.remove('w-16');
      sidebar.classList.add('w-44');
      mainContent.classList.remove('md:ml-16');
      mainContent.classList.add('md:ml-44');
      // Reveal text labels
      document.querySelectorAll('.sidebar-text').forEach(text => {
        text.classList.remove('opacity-0', 'w-0');
        text.classList.add('opacity-100');
        text.style.width = 'auto';
      });
      // Hide tooltips if any
      document.querySelectorAll('.tooltip-label').forEach(t => t.style.display = 'none');
    }

    function collapseSidebar() {
      if (!sidebar || !mainContent) return;
      localStorage.setItem('sidebarExpanded', 'false');
      document.documentElement.style.setProperty('--sidebar-width', '4rem');
      document.documentElement.classList.remove('sidebar-expanded');
      sidebar.classList.remove('w-44');
      sidebar.classList.add('w-16');
      mainContent.classList.remove('md:ml-44');
      mainContent.classList.add('md:ml-16');
      // Hide text labels
      document.querySelectorAll('.sidebar-text').forEach(text => {
        text.classList.remove('opacity-100');
        text.classList.add('opacity-0', 'w-0');
        text.style.width = '0';
      });
      // Show tooltips if any
      document.querySelectorAll('.tooltip-label').forEach(t => t.style.display = '');
    }

    // Initialize from storage
    const stored = localStorage.getItem(STORAGE_KEY);
    setExpanded(stored === 'true');
    // If sidebar is currently collapsed, force the panel closed
    if (!isSidebarExpanded()) {
      setExpanded(false);
    }

    // Click to toggle
    button.addEventListener('click', () => {
      // If sidebar collapsed, expand it before toggling panel
      if (!isSidebarExpanded()) {
        expandSidebar();
      }
      toggle();
    });

    // Keyboard: Enter/Space on button toggles (and auto-expands sidebar if needed)
    button.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        if (!isSidebarExpanded()) {
          expandSidebar();
        }
        toggle();
      }
    });

    // Keyboard: Escape inside panel collapses and returns focus to button
    panel.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setExpanded(false);
        button.focus();
      }
    });

    // If the user collapses the sidebar while resources panel is open, auto-collapse panel
    const observer = new MutationObserver(() => {
      const expanded = button.getAttribute('aria-expanded') === 'true';
      const sidebarExpanded = isSidebarExpanded();
      if (expanded && !sidebarExpanded) {
        setExpanded(false);
      }
    });
    // Observe changes to the html class that tracks sidebar state
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupAccordion);
  } else {
    setupAccordion();
  }
})();
