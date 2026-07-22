import { Controller } from "@hotwired/stimulus";

/**
 * Dashboard banner for Daily Briefing.
 * On connect: fetch today's status; kick generation if missing; poll until ready.
 */
export default class extends Controller {
  static targets = ["root", "label", "teaser", "actions", "review", "later", "retry"];
  static values = {
    pollUrl: String,
    generateUrl: String,
    laterUrl: String,
    briefingUrl: String,
  };

  connect() {
    this.pollTimer = null;
    this.hiddenForSession = false;
    this.bootstrap();
  }

  disconnect() {
    this.stopPolling();
  }

  async bootstrap() {
    try {
      const resp = await fetch(this.pollUrlValue, {
        headers: { Accept: "application/json" },
      });
      if (resp.status === 404) {
        await this.kickoff();
        return;
      }
      if (!resp.ok) {
        this.hide();
        return;
      }
      const data = await resp.json();
      this.apply(data);
    } catch (e) {
      console.error(e);
      this.hide();
    }
  }

  async kickoff() {
    this.showGenerating();
    try {
      const resp = await fetch(this.generateUrlValue, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ force: false }),
      });
      if (!resp.ok && resp.status !== 202) {
        this.showFailed();
        return;
      }
      const data = await resp.json();
      this.apply(data);
    } catch (e) {
      console.error(e);
      this.showFailed();
    }
  }

  apply(data) {
    if (!data) return this.hide();
    if (data.viewed_at || data.banner_dismissed) {
      this.hide();
      return;
    }
    if (data.status === "ready") {
      this.showReady(data);
      this.stopPolling();
      return;
    }
    if (data.status === "failed") {
      this.showFailed();
      this.stopPolling();
      return;
    }
    // generating
    this.showGenerating();
    this.startPolling();
  }

  showGenerating() {
    this.element.classList.remove("hidden");
    if (this.hasLabelTarget) this.labelTarget.textContent = "Today · Daily Briefing";
    if (this.hasTeaserTarget) {
      this.teaserTarget.textContent = "Building today's briefing…";
    }
    this._setActions({ review: false, later: true, retry: false, shimmer: true });
  }

  showReady(data) {
    this.element.classList.remove("hidden");
    if (this.hasLabelTarget) this.labelTarget.textContent = "Today · Daily Briefing";
    if (this.hasTeaserTarget) {
      this.teaserTarget.textContent =
        data.teaser || data.headline || "Your plan for today is ready.";
    }
    this._setActions({ review: true, later: true, retry: false, shimmer: false });
  }

  showFailed() {
    this.element.classList.remove("hidden");
    if (this.hasLabelTarget) this.labelTarget.textContent = "Today · Daily Briefing";
    if (this.hasTeaserTarget) {
      this.teaserTarget.textContent = "Couldn't build today's briefing.";
    }
    this._setActions({ review: false, later: true, retry: true, shimmer: false });
  }

  hide() {
    this.stopPolling();
    this.element.classList.add("hidden");
  }

  startPolling() {
    this.stopPolling();
    this.pollTimer = setInterval(() => this.pollOnce(), 2500);
  }

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  async pollOnce() {
    try {
      const resp = await fetch(this.pollUrlValue, {
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) return;
      const data = await resp.json();
      this.apply(data);
    } catch (e) {
      console.error(e);
    }
  }

  async later(event) {
    event.preventDefault();
    try {
      await fetch(this.laterUrlValue, {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: "{}",
      });
    } catch (e) {
      /* still hide locally */
    }
    this.hide();
  }

  async retry(event) {
    event.preventDefault();
    await this.kickoff();
  }

  _setActions({ review, later, retry, shimmer }) {
    if (this.hasReviewTarget) {
      this.reviewTarget.classList.toggle("hidden", !review);
    }
    if (this.hasLaterTarget) {
      this.laterTarget.classList.toggle("hidden", !later);
    }
    if (this.hasRetryTarget) {
      this.retryTarget.classList.toggle("hidden", !retry);
    }
    this.element.classList.toggle("is-generating", !!shimmer);
  }
}
