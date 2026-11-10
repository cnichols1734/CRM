const openPage = document.querySelector('[data-home-facts-open]');
if (openPage) {
  const token = new URLSearchParams(location.hash.slice(1)).get('token');
  if (token) {
    document.getElementById('hf-link-token').value = token;
    openPage.querySelector('button').disabled = false;
    document.getElementById('hf-open-status').textContent = 'Your link opens only the questionnaire or documents shared with you.';
    history.replaceState(null, '', location.pathname);
  }
}
if (document.querySelector('[data-home-facts-client]')) import('./home-facts/client.js');
if (document.querySelector('[data-home-facts-agent]')) import('./home-facts/agent.js');
