import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static values = { url: String, running: Boolean };
  static targets = ["queued", "sent", "delivered", "bounced", "skipped", "status"];

  connect() {
    if (!this.runningValue) return;
    this.timer = setInterval(() => this.refresh(), 4000);
  }

  disconnect() {
    if (this.timer) clearInterval(this.timer);
  }

  async refresh() {
    try {
      const response = await fetch(this.urlValue, { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const data = await response.json();
      this._set("queued", data.queued);
      this._set("sent", data.sent);
      this._set("delivered", data.delivered);
      this._set("bounced", data.bounced);
      this._set("skipped", data.skipped);
      this._set("status", data.status);
      if (!["sending", "active", "scheduled"].includes(data.status) && this.timer) {
        clearInterval(this.timer);
      }
    } catch {
      /* keep last numbers */
    }
  }

  _set(name, value) {
    if (this[`has${name.charAt(0).toUpperCase()}${name.slice(1)}Target`]) {
      this[`${name}Target`].textContent = value;
    }
  }
}
