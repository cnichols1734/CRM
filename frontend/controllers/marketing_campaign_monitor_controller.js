import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static values = { url: String, running: Boolean, status: String, revision: String };
  static targets = ["queued", "sent", "delivered", "bounced", "failed", "skipped", "status"];

  connect() {
    if (!this.runningValue) return;
    this.timer = setInterval(() => this.refresh(), 12000);
  }

  disconnect() {
    if (this.timer) clearInterval(this.timer);
  }

  async refresh() {
    try {
      const response = await fetch(this.urlValue, { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const data = await response.json();
      if (data.status !== this.statusValue || data.revision !== this.revisionValue) {
        window.location.reload();
      }
    } catch {
      /* keep last numbers */
    }
  }

}
