import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static targets = [
    "errorBanner",
    "hbBackdrop",
    "hbPanel",
    "hbForm",
    "hbConfirm",
    "hbConfirmText",
    "deadlineInput",
    "messageInput",
    "offerCheckbox",
    "submitHbBtn",
    "openHbBtn",
  ];

  static values = {
    highestBestUrl: String,
    returnUrl: String,
  };

  connect() {
    this.inFlight = false;
    this._onKeydown = (event) => {
      if (event.key === "Escape") this.closeHighestBest();
    };
    document.addEventListener("keydown", this._onKeydown);
  }

  disconnect() {
    document.removeEventListener("keydown", this._onKeydown);
  }

  openHighestBest(event) {
    if (event) event.preventDefault();
    this.clearError();
    if (this.hasHbFormTarget) this.hbFormTarget.classList.remove("hidden");
    if (this.hasHbConfirmTarget) this.hbConfirmTarget.classList.add("hidden");
    if (this.hasHbBackdropTarget) this.hbBackdropTarget.classList.remove("hidden");
    if (this.hasHbPanelTarget) this.hbPanelTarget.classList.remove("hidden");
    if (this.hasDeadlineInputTarget) this.deadlineInputTarget.focus();
  }

  closeHighestBest(event) {
    if (event) event.preventDefault();
    if (this.hasHbBackdropTarget) this.hbBackdropTarget.classList.add("hidden");
    if (this.hasHbPanelTarget) this.hbPanelTarget.classList.add("hidden");
  }

  selectedOfferIds() {
    if (!this.hasOfferCheckboxTarget) return [];
    return this.offerCheckboxTargets
      .filter((input) => input.checked)
      .map((input) => Number(input.value))
      .filter((id) => Number.isFinite(id));
  }

  async submitHighestBest(event) {
    if (event) event.preventDefault();
    if (this.inFlight) return;

    this.clearError();
    const deadline = this.hasDeadlineInputTarget
      ? (this.deadlineInputTarget.value || "").trim()
      : "";
    if (!deadline) {
      this.showError("Choose a deadline before recording highest and best.");
      return;
    }

    const offerIds = this.selectedOfferIds();
    if (!offerIds.length) {
      this.showError("Select at least one offer to include.");
      return;
    }

    const message = this.hasMessageInputTarget
      ? (this.messageInputTarget.value || "").trim() || null
      : null;

    this.inFlight = true;
    if (this.hasSubmitHbBtnTarget) this.submitHbBtnTarget.disabled = true;

    try {
      const response = await fetch(this.highestBestUrlValue, {
        method: "POST",
        headers: this.csrfHeaders(),
        body: JSON.stringify({
          deadline_at: deadline,
          message,
          offer_ids: offerIds,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) {
        this.showError(
          data.error || "Could not record highest and best. Nothing was saved.",
        );
        return;
      }

      if (this.hasHbFormTarget) this.hbFormTarget.classList.add("hidden");
      if (this.hasHbConfirmTarget) this.hbConfirmTarget.classList.remove("hidden");
      if (this.hasHbConfirmTextTarget) {
        const when = data.deadline_at
          ? this.formatDeadline(data.deadline_at)
          : "the deadline you set";
        const count = (data.offer_ids || []).length;
        this.hbConfirmTextTarget.textContent =
          `The CRM saved a highest-and-best deadline of ${when} ` +
          `for ${count} offer${count === 1 ? "" : "s"}. ` +
          "Send the request to the buyer agents yourself — nothing was sent from AgentFlow.";
      }
    } catch (_err) {
      this.showError("Could not record highest and best. Nothing was saved.");
    } finally {
      this.inFlight = false;
      if (this.hasSubmitHbBtnTarget) this.submitHbBtnTarget.disabled = false;
    }
  }

  async acceptOffer(event) {
    if (event) event.preventDefault();
    if (this.inFlight) return;

    const button = event.currentTarget;
    const url = button.dataset.url;
    const position = button.dataset.position || "primary";
    if (!url) return;

    const label =
      position === "backup"
        ? "Accept this offer as backup?"
        : "Accept this offer as the primary contract?";
    if (!window.confirm(label)) return;

    this.inFlight = true;
    this.clearError();
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: this.csrfHeaders(),
        body: JSON.stringify({ position }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) {
        this.showError(data.error || "Could not accept this offer.");
        return;
      }
      window.location.href = this.returnUrlValue || window.location.href;
    } catch (_err) {
      this.showError("Could not accept this offer.");
    } finally {
      this.inFlight = false;
    }
  }

  showError(message) {
    if (!this.hasErrorBannerTarget) return;
    this.errorBannerTarget.textContent =
      message || "Something went wrong. Please try again.";
    this.errorBannerTarget.classList.remove("hidden");
  }

  clearError() {
    if (!this.hasErrorBannerTarget) return;
    this.errorBannerTarget.textContent = "";
    this.errorBannerTarget.classList.add("hidden");
  }

  csrfHeaders() {
    const headers = {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    };
    const token =
      document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") ||
      document.querySelector('input[name="csrf_token"]')?.value;
    if (token) {
      headers["X-CSRFToken"] = token;
    }
    return headers;
  }

  formatDeadline(value) {
    if (!value) return "—";
    const text = String(value);
    const datePart = text.slice(0, 10);
    const timePart = text.includes("T") ? text.slice(11, 16) : "";
    const parts = datePart.split("-");
    if (parts.length !== 3) return text;
    const months = [
      "Jan", "Feb", "Mar", "Apr", "May", "Jun",
      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    const monthIndex = Number(parts[1]) - 1;
    const day = Number(parts[2]);
    if (monthIndex < 0 || monthIndex > 11 || !Number.isFinite(day)) return text;
    const dateLabel = `${months[monthIndex]} ${day}, ${parts[0]}`;
    return timePart ? `${dateLabel} at ${timePart}` : dateLabel;
  }
}
