import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static values = { url: String, running: Boolean, status: String, revision: String, engagement: String };
  static targets = ["queued", "sent", "delivered", "bounced", "failed", "skipped", "status"];

  connect() {
    this.timer = setInterval(() => this.refresh(), 15000);
  }

  disconnect() {
    clearInterval(this.timer);
    this.request?.abort();
  }

  async refresh() {
    if (document.hidden || this.refreshing) return;
    this.refreshing = true;
    this.request = new AbortController();
    try {
      const response = await fetch(this.urlValue, {
        headers: { Accept: "application/json" }, cache: "no-store", signal: this.request.signal,
      });
      if (!response.ok) return;
      const data = await response.json();
      if (data.revision === this.revisionValue && data.engagement_revision === this.engagementValue) return;
      const page = await fetch(window.location.href, { cache: "no-store", signal: this.request.signal });
      if (!page.ok || page.redirected) return;
      const doc = new DOMParser().parseFromString(await page.text(), "text/html");
      const incoming = doc.querySelector('[data-controller="marketing-campaign-monitor"]');
      if (!incoming || !this.element.isConnected) return;
      // Preserve open previews and activity panels when the surrounding counts change.
      for (const region of this.element.querySelectorAll("[data-live-region]")) {
        const replacement = incoming.querySelector(`[data-live-region="${region.dataset.liveRegion}"]`);
        if (!replacement) continue;
        for (const detail of region.querySelectorAll("details[open][id]")) {
          const next = replacement.querySelector(`#${CSS.escape(detail.id)}`);
          if (next) next.replaceWith(detail);
        }
        const focusedId = region.contains(document.activeElement) ? document.activeElement.id : null;
        const scroll = region.querySelector(".crm-table-wrap")?.scrollLeft || 0;
        region.replaceChildren(...replacement.childNodes);
        if (focusedId) document.getElementById(focusedId)?.focus({ preventScroll: true });
        const table = region.querySelector(".crm-table-wrap");
        if (table) table.scrollLeft = scroll;
      }
      // Status changes also affect the Pause/Resume controls in the page header.
      if (data.status !== this.statusValue) {
        const header = this.element.querySelector(".crm-page-header");
        const nextHeader = incoming.querySelector(".crm-page-header");
        if (header && nextHeader) header.replaceChildren(...nextHeader.childNodes);
      }
      this.statusValue = data.status;
      this.revisionValue = incoming.dataset.marketingCampaignMonitorRevisionValue;
      this.engagementValue = incoming.dataset.marketingCampaignMonitorEngagementValue;
      for (const detail of this.element.querySelectorAll("details[data-activity-url][open]")) {
        this.fetchActivity(detail);
      }
    } catch {
      // Leave the last successful counts visible while the connection recovers.
    } finally {
      this.refreshing = false;
    }
  }

  showActivity(event) {
    event.preventDefault();
    const detail = document.getElementById(event.currentTarget.hash.slice(1));
    if (!detail) return;
    detail.open = true;
    detail.querySelector("summary")?.focus({ preventScroll: true });
  }

  loadActivity(event) {
    if (event.currentTarget.open) this.fetchActivity(event.currentTarget);
  }

  async fetchActivity(detail) {
    if (detail.dataset.loading === "true") return;
    detail.dataset.loading = "true";
    const content = detail.querySelector("[data-activity-content]");
    if (!content.children.length) content.textContent = "Loading activity…";
    try {
      const response = await fetch(detail.dataset.activityUrl, { cache: "no-store" });
      if (!response.ok || response.redirected) throw new Error("Activity unavailable");
      content.innerHTML = await response.text();
    } catch {
      content.textContent = "Activity could not load. Close and reopen to try again.";
    } finally {
      delete detail.dataset.loading;
    }
  }
}
