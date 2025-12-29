// static/js/agent-resources.js
(function () {
  'use strict';

  const STORAGE_KEY = 'sidebar.agentResources.expanded';

  const LINKS = [
    { label: 'BackAgent', url: 'https://origen.workspace.lwolf.com/' },
    { label: 'Zipforms', url: 'https://www.zipformplus.com/default.aspx' },
    { label: 'Canva', url: 'https://www.canva.com' },
    { label: 'HAR', url: 'https://www.har.com/memberonlyarea#member' },
    { label: 'SABOR', url: 'https://sabor.mysolidearth.com/authenticate?redirect_to=eyJwYXJhbXMiOnt9LCJuYW1lIjoic2FtbC5hdXRoIiwicXVlcnkiOnsiU0FNTFJlcXVlc3QiOiJqWkpSYjlzZ0ZJWGZKKzAvSU40SmhHU0pnK0pVV2FOcWtib3RhdHc5N0dYQ21DVkk5c1hqNG5iOTl5WDJJcldhR3BVM0x1ZkNkdzUzZWZXM3FjbUREZWc4NUhROEVwUllNTDV5Y01qcGZYSERNbnExV3FKdTZsYXR1M2lFTy91bnN4aEo2Z05VL1VGT3V3REthM1NvUURjV1ZUUnF2LzU2cStSSXFEYjQ2STJ2NmRCeVdhd1JiWWlKaFpMdEpxZS9Nam5SVlZhS2VaYk5Nakd6eHNoU3lNbFUyNFV1eFhnK3JzUmNsTE5GT2Fma3g5bUZQTG5ZSW5aMkN4ZzF4RlFTOGhNVEN5YW1oWlJxT2xGQy9xUms5dy90czRQQjhDVzBjaENoK2xJVU83Yjd2aThvV1o5eHJ6MWcxOWl3dCtIQkdYdC9kNXZUWTR3dEtzNVJsejZNakc4NEpac1VuUU1kZTg3WGd1WUpmZTBxcTBNODl1cFR0bHluek9ud0FhcTNGRjRrLzk0c1YvK2pQTGJNZUlnV0ltL3I3dUFBZWVQQSthRGhZTm5wY2lZRncyUzRUbHQzQU9hQkwva0xqdk5VZkVzUGJ6ZTd4RzZleUxxdS9lTjFzRHJhbk1iUVdVcHVmR2gwdkl4NnFyaUsvZTZsS2lZSWRBbU5mdnhBM2xoOE5jQzhuc3JWTXc9PSIsIlJlbGF5U3RhdGUiOiJ3THIzSjJCbXJieVBjNGFGNzc1bTI5TU9GbVJrTmxabDlkWitCU1VEb2pzTlpjQzI1MEg0d2x3ZVU5T09RQWd3IiwiU2lnQWxnIjoiaHR0cDovL3d3dy53My5vcmcvMjAwMS8wNC94bWxkc2lnLW1vcmUjcnNhLXNoYTI1NiIsIlNpZ25hdHVyZSI6Ik5MWHhGQVpIRWQwbW9Mc25jaG5UWkI2KzBPc2dmVWRDNkVrZjRtbEJTOHZESlgzcThveXFiVVpmYXZLYzdEV0o1RTUrejZBSXBJNFA0MUIzNzd3MXczSE1BT3FpSnZYRFhjbnJKRFk5T0J6MmJoSkJNUWZCN2hpaEpFTUF4UGlXTkxhNklGT3dkbk5UaUdUYjdNcmNlb05YM2FsMmRlWFFtdml1NXY0YjM5TlB3Vm9xYlVBWXZTYzhMTi9ja2ltNDFlMWVSdHZvMnM5aEZJQkhCblNETS91cXhyOGh0UzJsWVRhME1udVZGT0ozTFBGL0xlTENaWUNFMTNQQmloREdjd25zQ2lnYjcydnZBdmR2SXlqdXBXa2ZJUW9KeHJkQWpoay9TV1BoYUFkcGRzcFQ3dnZOY21DZzZXTG9kbHJUZjRrcEhXWGR4Znp0TjJPL3VsT2RyQT09In19' },
    { label: 'ShowingSmart', url: 'https://www.showingsmart.com/agent' },
    { label: 'OG Google Drive', url: 'https://drive.google.com/drive/u/0/folders/17CfhrIqA0j4W68vjY7yTATjzjNNLZodW' },
    { label: 'OG Training Videos', url: 'https://drive.google.com/drive/folders/12CPuRX4cOi3GiCT0XdQW1MDFuFZJqZ5J?usp=sharing' },
    { label: 'Inman News', url: 'https://www.inman.com/' }
  ];

  function $(id) {
    return document.getElementById(id);
  }

  function renderLinks(listElement) {
    if (!listElement) return;
    listElement.innerHTML = '';
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

  function setupAccordion() {
    const button = $('agentResourcesBtn');
    const panel = $('agentResourcesPanel');
    const list = $('agentResourcesList');
    const chevron = $('agentResourcesChevron');
    const sidebar = document.getElementById('sidebar');
    const mainContent = document.getElementById('mainContent');
    const sidebarToggle = document.getElementById('sidebarToggle');

    if (!button || !panel || !list || !chevron) return;

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


