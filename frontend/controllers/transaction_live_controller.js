import { Controller } from "@hotwired/stimulus";

const BACKOFF_MS = [2000, 3000, 5000, 8000, 13000, 20000];
const MAX_CONSECUTIVE_ERRORS = 5;
const MAX_ACTIVITY_MS = 15 * 60 * 1000;
const IDLE_STREAK_STOP = 3;

export default class extends Controller {
  static values = {
    url: String,
    transactionId: Number,
  };

  connect() {
    this.backoffIndex = 0;
    this.idleStreak = 0;
    this.stopped = false;
    this.errorCount = 0;
    this.lastVersion = null;
    this.timer = null;
    this.activityStartedAt = Date.now();
    this.requestController = new AbortController();

    this._onUploaded = () => this._wake();
    this._onRefresh = () => this._wake();
    this._onVisibility = () => this._handleVisibility();
    this._onPageHide = () => this._cancelPolling();
    this._onPageShow = (event) => {
      if (!event.persisted) return;
      this.requestController = new AbortController();
      this._wake();
    };

    window.addEventListener("transaction-document-uploaded", this._onUploaded);
    window.addEventListener("transaction:refresh", this._onRefresh);
    document.addEventListener("visibilitychange", this._onVisibility);
    window.addEventListener("pagehide", this._onPageHide);
    window.addEventListener("pageshow", this._onPageShow);

    this._fetchThenSchedule();
  }

  disconnect() {
    this._cancelPolling();
    window.removeEventListener("transaction-document-uploaded", this._onUploaded);
    window.removeEventListener("transaction:refresh", this._onRefresh);
    document.removeEventListener("visibilitychange", this._onVisibility);
    window.removeEventListener("pagehide", this._onPageHide);
    window.removeEventListener("pageshow", this._onPageShow);
  }

  _cancelPolling() {
    this.stopped = true;
    this._clearTimer();
    this.requestController?.abort();
  }

  _clearTimer() {
    if (this.timer != null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  _stop(reason) {
    this._clearTimer();
    this.stopped = true;
    if (reason === "error") {
      window.dispatchEvent(new CustomEvent("transaction:live:error"));
    } else if (reason === "paused") {
      window.dispatchEvent(new CustomEvent("transaction:live:paused"));
    } else {
      window.dispatchEvent(new CustomEvent("transaction:live:stopped"));
    }
  }

  _currentDelay() {
    return BACKOFF_MS[Math.min(this.backoffIndex, BACKOFF_MS.length - 1)];
  }

  _schedule() {
    if (this.stopped) return;
    this._clearTimer();
    this.timer = setTimeout(() => this._tick(), this._currentDelay());
  }

  async _tick() {
    if (this.stopped) return;

    if (Date.now() - this.activityStartedAt >= MAX_ACTIVITY_MS) {
      this._stop("paused");
      return;
    }

    if (document.hidden) {
      this._schedule();
      return;
    }

    await this._fetchThenSchedule();
  }

  async _fetchThenSchedule() {
    const controller = this.requestController;
    const data = await this._fetchOnce();
    if (this.stopped || controller !== this.requestController || controller.signal.aborted) return;
    if (data) this._processPayload(data);
    if (!this.stopped) this._schedule();
  }

  async _fetchOnce() {
    const controller = this.requestController;
    if (controller.signal.aborted) return null;
    try {
      const resp = await fetch(this.urlValue, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "no-store",
        signal: controller.signal,
      });
      if (!resp.ok) throw new Error("bad status");
      const data = await resp.json();
      if (controller.signal.aborted) return null;
      this.errorCount = 0;
      window.dispatchEvent(new CustomEvent("transaction:live", { detail: data }));
      return data;
    } catch (_err) {
      if (controller.signal.aborted) return null;
      this.errorCount += 1;
      if (this.errorCount >= MAX_CONSECUTIVE_ERRORS) {
        this._stop("error");
      }
      return null;
    }
  }

  _processPayload(data) {
    const prevVersion = this.lastVersion;
    const hasPrev = prevVersion !== null;
    const versionChanged = hasPrev && data.version !== prevVersion;

    if (versionChanged) {
      this.backoffIndex = 0;
    } else if (hasPrev) {
      this.backoffIndex = Math.min(this.backoffIndex + 1, BACKOFF_MS.length - 1);
    }

    if (data.in_flight || versionChanged) {
      this.idleStreak = 0;
      if (this.stopped) {
        this.stopped = false;
        this.backoffIndex = 0;
        this.activityStartedAt = Date.now();
      }
    } else if (hasPrev && !data.in_flight) {
      this.idleStreak += 1;
      if (this.idleStreak >= IDLE_STREAK_STOP) {
        this.lastVersion = data.version;
        this._stop("stopped");
        return;
      }
    }

    this.lastVersion = data.version;
  }

  _wake() {
    if (this.requestController.signal.aborted) return;
    this.backoffIndex = 0;
    this.idleStreak = 0;
    this.stopped = false;
    this.errorCount = 0;
    this.activityStartedAt = Date.now();
    this._clearTimer();
    this._fetchThenSchedule();
  }

  async _handleVisibility() {
    if (document.hidden) return;
    if (!this.stopped) return;

    const prevVersion = this.lastVersion;
    const controller = this.requestController;
    const data = await this._fetchOnce();
    if (!data || controller !== this.requestController || controller.signal.aborted) return;

    const versionChanged = prevVersion !== null && data.version !== prevVersion;
    if (data.in_flight || versionChanged) {
      this.stopped = false;
      this.backoffIndex = 0;
      this.idleStreak = 0;
      this.activityStartedAt = Date.now();
      this.lastVersion = data.version;
      this._schedule();
      return;
    }

    this.lastVersion = data.version;
  }
}
