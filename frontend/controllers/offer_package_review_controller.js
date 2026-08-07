import PdfPaneController from "./pdf_pane_controller";

export default class extends PdfPaneController {
  static targets = [
    "termsForm",
    "buyerNames",
    "offerPrice",
    "headerTitle",
    "confirmBtn",
    "draftBtn",
    "errorBanner",
    "extractionBanner",
    "extractionLabel",
    "docList",
    "docButton",
    "findingsList",
  ];

  static values = {
    pdfUrl: String,
    confirmUrl: String,
    liveUrl: String,
    returnUrl: String,
  };

  connect() {
    this._pollTimer = null;
    this._busy = false;
    this.connectPdfPane();
    this.#highlightActiveDoc();
    this.#startPolling();
  }

  disconnect() {
    this.#stopPolling();
    this.disconnectPdfPane();
  }

  goBack(event) {
    if (event) event.preventDefault();
    window.location.href = this.returnUrlValue || "/transactions";
  }

  selectDoc(event) {
    const button = event.currentTarget;
    if (!button) return;
    const url = button.dataset.pdfUrl || "";
    if (!url) {
      this.#showError("No PDF available for that document yet.");
      return;
    }
    this.pdfUrlValue = url;
    this.docButtonTargets.forEach((el) => {
      el.classList.toggle("bg-orange-50/60", el === button);
    });
    this.loadPdf();
  }

  async confirm(event) {
    if (event) event.preventDefault();
    await this.#submit({ draft: false });
  }

  async saveDraft(event) {
    if (event) event.preventDefault();
    await this.#submit({ draft: true });
  }

  #collectTerms() {
    const form = this.hasTermsFormTarget ? this.termsFormTarget : null;
    const data = { draft: false };
    if (!form) return data;
    const fields = new FormData(form);
    for (const [key, value] of fields.entries()) {
      data[key] = typeof value === "string" ? value.trim() : value;
    }
    return data;
  }

  async #submit({ draft }) {
    if (this._busy) return;
    this._busy = true;
    this.#clearError();
    const payload = this.#collectTerms();
    payload.draft = draft;
    if (this.hasConfirmBtnTarget) this.confirmBtnTarget.disabled = true;
    if (this.hasDraftBtnTarget) this.draftBtnTarget.disabled = true;

    try {
      const response = await fetch(this.confirmUrlValue, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.success === false) {
        throw new Error(data.error || "Could not save offer package.");
      }
      if (draft) {
        this.#applyLivePayload(data.review || {});
        return;
      }
      window.location.href = data.redirect_url || this.returnUrlValue;
    } catch (error) {
      this.#showError(error.message || "Could not save offer package.");
    } finally {
      this._busy = false;
      if (this.hasConfirmBtnTarget) this.confirmBtnTarget.disabled = false;
      if (this.hasDraftBtnTarget) this.draftBtnTarget.disabled = false;
    }
  }

  #startPolling() {
    if (!this.liveUrlValue) return;
    this._pollTimer = window.setInterval(() => this.#pollLive(), 4000);
    this.#pollLive();
  }

  #stopPolling() {
    if (this._pollTimer) {
      window.clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  }

  async #pollLive() {
    if (!this.liveUrlValue || this._busy) return;
    try {
      const response = await fetch(this.liveUrlValue, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.review) return;
      this.#applyLivePayload(data.review);
      if (data.review.extraction && data.review.extraction.done) {
        this.#stopPolling();
      }
    } catch (_error) {
      /* ignore transient poll errors */
    }
  }

  #applyLivePayload(review) {
    const terms = review.terms || {};
    const form = this.hasTermsFormTarget ? this.termsFormTarget : null;
    if (form) {
      for (const [key, value] of Object.entries(terms)) {
        const input = form.elements.namedItem(key);
        if (!input || input === document.activeElement) continue;
        if (value === null || value === undefined) continue;
        // Only fill empty fields so user edits win.
        if (String(input.value || "").trim() === "") {
          input.value = value;
        }
      }
    }

    if (this.hasHeaderTitleTarget) {
      const buyers = review.buyer_names || terms.buyer_names || "Unnamed buyer";
      const price = review.offer_price;
      this.headerTitleTarget.innerHTML =
        price != null
          ? `${this.#escape(buyers)} <span class="font-normal text-slate-400">·</span> <span class="font-medium text-slate-700">$${Number(price).toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>`
          : this.#escape(buyers);
    }

    const extraction = review.extraction || {};
    if (this.hasExtractionBannerTarget && this.hasExtractionLabelTarget) {
      if (extraction.pending > 0) {
        this.extractionBannerTarget.classList.remove("hidden");
        this.extractionLabelTarget.textContent =
          `Extracting ${extraction.pending} of ${extraction.total} document${extraction.total === 1 ? "" : "s"}…`;
      } else {
        this.extractionBannerTarget.classList.add("hidden");
      }
    }

    // Refresh extraction status labels on doc buttons.
    const byId = {};
    (review.documents || []).forEach((doc) => {
      byId[String(doc.document_id)] = doc;
    });
    this.docButtonTargets.forEach((btn) => {
      const doc = byId[btn.dataset.documentId];
      if (!doc) return;
      const statusEl = btn.querySelector("[data-doc-status]");
      if (statusEl) {
        statusEl.textContent = String(doc.extraction_status || "pending")
          .replace(/_/g, " ")
          .replace(/\b\w/g, (c) => c.toUpperCase());
      }
      if (doc.pdf_url) btn.dataset.pdfUrl = doc.pdf_url;
    });
  }

  #highlightActiveDoc() {
    const activeUrl = this.pdfUrlValue;
    if (!activeUrl) return;
    this.docButtonTargets.forEach((el) => {
      el.classList.toggle("bg-orange-50/60", el.dataset.pdfUrl === activeUrl);
    });
  }

  #showError(message) {
    if (!this.hasErrorBannerTarget) return;
    this.errorBannerTarget.textContent = message;
    this.errorBannerTarget.classList.remove("hidden");
  }

  #clearError() {
    if (!this.hasErrorBannerTarget) return;
    this.errorBannerTarget.textContent = "";
    this.errorBannerTarget.classList.add("hidden");
  }

  #escape(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
}
