import PdfPaneController from "./pdf_pane_controller";

export default class extends PdfPaneController {
  static targets = [
    "row",
    "checkbox",
    "applyBtn",
    "applyCount",
    "rejectBtn",
    "resultPane",
    "changesPane",
    "errorBanner",
  ];

  static values = {
    acceptUrl: String,
    rejectUrl: String,
    returnUrl: String,
  };

  connect() {
    this.inFlight = false;
    this.syncApplyUi();
    this.connectPdfPane();
  }

  disconnect() {
    this.disconnectPdfPane();
  }

  goBack(event) {
    if (event) event.preventDefault();
    window.location.href = this.returnUrlValue;
  }

  toggleSelected() {
    this.syncApplyUi();
  }

  selectedMap() {
    const selected = {};
    this.checkboxTargets.forEach((input) => {
      const key = input.dataset.termKey;
      if (!key) return;
      selected[key] = Boolean(input.checked);
    });
    return selected;
  }

  selectedCount() {
    return Object.values(this.selectedMap()).filter(Boolean).length;
  }

  syncApplyUi() {
    if (!this.hasApplyBtnTarget) return;
    const count = this.selectedCount();
    if (this.hasApplyCountTarget) {
      this.applyCountTarget.textContent = String(count);
    }
    this.applyBtnTarget.disabled = this.inFlight || count === 0;
    if (this.hasRejectBtnTarget) {
      this.rejectBtnTarget.disabled = this.inFlight;
    }
  }

  showError(message) {
    if (!this.hasErrorBannerTarget) return;
    this.errorBannerTarget.textContent = message || "Something went wrong. Please try again.";
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

  formatDeadlineDate(value) {
    if (!value) return "—";
    const text = String(value).slice(0, 10);
    const parts = text.split("-");
    if (parts.length !== 3) return String(value);
    const months = [
      "Jan", "Feb", "Mar", "Apr", "May", "Jun",
      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    const monthIndex = Number(parts[1]) - 1;
    const day = Number(parts[2]);
    if (monthIndex < 0 || monthIndex > 11 || !Number.isFinite(day)) return String(value);
    return `${months[monthIndex]} ${day}, ${parts[0]}`;
  }

  showResult(data) {
    if (this.hasChangesPaneTarget) {
      this.changesPaneTarget.classList.add("hidden");
    }
    if (!this.hasResultPaneTarget) return;

    const summary = this.resultPaneTarget.querySelector('[data-role="result-summary"]');
    const list = this.resultPaneTarget.querySelector('[data-role="deadline-list"]');
    const applied = (data.applied_keys || []).length;
    const updated = (data.recompute && data.recompute.updated) || [];

    if (summary) {
      summary.textContent =
        applied === 0
          ? "No term changes were applied."
          : `${applied} term${applied === 1 ? "" : "s"} applied to the accepted contract.`;
    }

    if (list) {
      list.replaceChildren();
      if (updated.length) {
        const heading = document.createElement("p");
        heading.className = "text-xs font-medium text-slate-600";
        heading.textContent = "Updated deadlines";
        list.appendChild(heading);

        updated.forEach((row) => {
          const item = document.createElement("div");
          item.className = "amr-deadline";
          const title = document.createElement("p");
          title.className = "text-sm font-medium text-slate-900";
          title.textContent = row.title || row.requirement_key || "Deadline";
          const change = document.createElement("p");
          change.className = "mt-1 font-mono text-sm text-slate-600";
          change.innerHTML = `<span class="amr-current">${this.formatDeadlineDate(row.prior_due_at)}</span>
            <span class="text-slate-400" aria-hidden="true"> → </span>
            <span class="font-medium text-slate-950">${this.formatDeadlineDate(row.due_at)}</span>`;
          item.appendChild(title);
          item.appendChild(change);
          list.appendChild(item);
        });
      }

      const created = (data.recompute && data.recompute.created) || [];
      if (created.length) {
        const heading = document.createElement("p");
        heading.className = "mt-4 text-xs font-medium text-slate-600";
        heading.textContent = `New deadlines added (${created.length})`;
        list.appendChild(heading);

        const note = document.createElement("p");
        note.className = "mt-1 text-sm text-slate-600";
        note.textContent =
          "The revised terms opened requirements that were not tracked yet. " +
          "They are on the transaction now.";
        list.appendChild(note);
      }

      const skipped = (data.recompute && data.recompute.skipped_completed) || [];
      if (skipped.length) {
        const note = document.createElement("p");
        note.className = "mt-4 text-sm text-slate-600";
        note.textContent =
          `${skipped.length} already-completed requirement` +
          `${skipped.length === 1 ? " was" : "s were"} left untouched.`;
        list.appendChild(note);
      }

      if (!updated.length && !created.length) {
        const note = document.createElement("p");
        note.className = "text-sm text-slate-600";
        note.textContent = "No open deadlines needed updating.";
        list.appendChild(note);
      }
    }

    this.resultPaneTarget.classList.remove("hidden");
    if (this.hasApplyBtnTarget) this.applyBtnTarget.disabled = true;
    if (this.hasRejectBtnTarget) this.rejectBtnTarget.disabled = true;
  }

  async accept() {
    if (this.inFlight || !this.acceptUrlValue) return;
    const selected = this.selectedMap();
    if (!Object.values(selected).some(Boolean)) {
      this.showError("Select at least one change to apply.");
      return;
    }

    this.inFlight = true;
    this.syncApplyUi();
    this.clearError();

    try {
      const response = await fetch(this.acceptUrlValue, {
        method: "POST",
        credentials: "same-origin",
        headers: this.csrfHeaders(),
        body: JSON.stringify({ selected }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) {
        throw new Error(data.error || "Could not apply this amendment.");
      }
      this.showResult(data);
    } catch (error) {
      this.showError(error.message || "Could not apply this amendment.");
    } finally {
      this.inFlight = false;
      if (this.hasResultPaneTarget && !this.resultPaneTarget.classList.contains("hidden")) {
        if (this.hasApplyBtnTarget) this.applyBtnTarget.disabled = true;
        if (this.hasRejectBtnTarget) this.rejectBtnTarget.disabled = true;
      } else {
        this.syncApplyUi();
      }
    }
  }

  async reject() {
    if (this.inFlight || !this.rejectUrlValue) return;
    this.inFlight = true;
    this.syncApplyUi();
    this.clearError();

    try {
      const response = await fetch(this.rejectUrlValue, {
        method: "POST",
        credentials: "same-origin",
        headers: this.csrfHeaders(),
        body: JSON.stringify({}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) {
        throw new Error(data.error || "Could not reject this amendment.");
      }
      window.location.href = this.returnUrlValue;
    } catch (error) {
      this.inFlight = false;
      this.syncApplyUi();
      this.showError(error.message || "Could not reject this amendment.");
    }
  }
}
