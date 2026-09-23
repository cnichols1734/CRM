function clearConfirmedSave() {
  const receipt = document.querySelector('[data-marketing-saved-draft]');
  if (!receipt) return;
  try { localStorage.removeItem(receipt.dataset.marketingSavedDraft); } catch { /* Storage unavailable. */ }
}
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', clearConfirmedSave, {once: true});
  else clearConfirmedSave();
}

// Recovery is scoped to the signed-in user, draft and server revision.
export class DraftRecovery {
  constructor(root, form, restored, structure = {}) {
    this.key = root.dataset.draftKey;
    this.version = root.dataset.draftVersion || 'new';
    this.form = form;
    this.structure = structure;
    this.excluded = new Set(['_draft_recovery_key', 'csrf_token', 'campaign', 'step', 'flow', 'template_id', 'step_template_id', ...(structure.excluded || [])]);
    this.status = root.querySelector('[data-draft-status]');
    if (!this.key || !form) return;
    clearConfirmedSave();
    const receipt = document.createElement('input');
    receipt.type = 'hidden'; receipt.name = '_draft_recovery_key'; receipt.value = this.key;
    form.append(receipt);
    try {
      const saved = JSON.parse(localStorage.getItem(this.key) || 'null');
      if (root.dataset.draftRestore !== 'false' && saved && saved.version === this.version && Date.now() - saved.at < 7 * 86400000) {
        structure.restore?.(saved.structure);
        for (const element of form.elements) {
          if (!(element.name in saved.fields) || this.excluded.has(element.name) || ['file', 'submit', 'button'].includes(element.type)) continue;
          const values = saved.fields[element.name];
          if (element.type === 'checkbox' || element.type === 'radio') element.checked = values.includes(element.value);
          else element.value = values.shift() || '';
        }
        this.dirty = true;
        if (this.status) this.status.textContent = 'Recovered your unsaved changes on this device.';
        restored?.();
      } else if (saved && saved.version !== this.version) {
        // A newer server revision confirms that the saved draft supersedes recovery.
        localStorage.removeItem(this.key);
      }
    } catch { /* Storage can be disabled by the browser. */ }
    this.changed = () => this.save();
    form.addEventListener('input', this.changed);
    form.addEventListener('change', this.changed);
    this.leaving = event => {
      if (!this.dirty) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', this.leaving);
  }
  save() {
    this.dirty = true;
    const fields = {};
    for (const el of this.form.elements) {
      if (!el.name || this.excluded.has(el.name) || ['file', 'submit', 'button'].includes(el.type)) continue;
      fields[el.name] ||= [];
      if (['checkbox', 'radio'].includes(el.type) && !el.checked) continue;
      fields[el.name].push(el.value);
    }
    try {
      localStorage.setItem(this.key, JSON.stringify({at: Date.now(), version: this.version, fields, structure: this.structure.capture?.()}));
      if (this.status) this.status.textContent = 'Changes kept on this device. Save draft to keep them in your account.';
    } catch {
      if (this.status) this.status.textContent = 'Unsaved changes. Save your draft before leaving.';
    }
  }
  submitting() {
    // Keep recovery until a response confirms a newer server revision.
    this.save();
    this.dirty = false;
  }
  disconnect() {
    this.form?.removeEventListener('input', this.changed);
    this.form?.removeEventListener('change', this.changed);
    window.removeEventListener('beforeunload', this.leaving);
  }
}
