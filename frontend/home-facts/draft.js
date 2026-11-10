const equal = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export class Draft {
  constructor(snapshot, saved = {}) {
    this.server = snapshot;
    this.base = saved.base || snapshot.answers;
    this.pending = saved.pending || {};
    this.flight = saved.flight || null;
    this.conflicts = {};
    this.position = saved.position || { section: 0, group: 0 };
    this.reconcile(snapshot);
  }
  get answers() { return { ...this.server.answers, ...this.pending }; }
  edit(key, value) { this.pending[key] = value; delete this.conflicts[key]; }
  reconcile(snapshot) {
    this.conflicts = {};
    for (const [key, value] of Object.entries(this.pending)) {
      if (equal(snapshot.answers[key], value)) delete this.pending[key];
      else if (!equal(this.base[key], snapshot.answers[key]) && !equal(this.flight?.changes[key], snapshot.answers[key])) {
        this.conflicts[key] = { mine: value, theirs: snapshot.answers[key] ?? null };
      }
    }
    this.server = snapshot;
    this.base = snapshot.answers;
  }
  resolve(key, useMine) {
    if (!useMine) delete this.pending[key];
    delete this.conflicts[key];
  }
  begin() {
    if (this.flight) return this.flight;
    if (!Object.keys(this.pending).length || Object.keys(this.conflicts).length) return null;
    this.flight = { expected_revision: this.server.revision, mutation_id: crypto.randomUUID(), changes: { ...this.pending } };
    return this.flight;
  }
  acknowledge(snapshot) {
    for (const [key, sent] of Object.entries(this.flight?.changes || {})) {
      if (equal(this.pending[key], sent)) delete this.pending[key];
    }
    // A replay can return newer edits from another device. Compare later
    // keystrokes against what this request saved before accepting that state.
    this.base = { ...this.base, ...(this.flight?.changes || {}) };
    this.flight = null;
    this.reconcile(snapshot);
  }
  persisted() {
    return { base: this.base, pending: this.pending, flight: this.flight, position: this.position,
      schema_version: this.server.schema_version, template_sha256: this.server.manifest.template_sha256 };
  }
}
