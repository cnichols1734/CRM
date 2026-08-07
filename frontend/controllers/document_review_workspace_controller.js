import PdfPaneController from "./pdf_pane_controller";

export default class extends PdfPaneController {
  static targets = [
    "reviewPane",
    "fieldList",
    "fieldCard",
    "applyBtn",
    "applyLabel",
    "applyCount",
    "markReviewedBtn",
    "errorBanner",
    "filePanel",
    "fileSlug",
    "fileScope",
    "fileOfferFields",
    "fileOffer",
    "fileCreateOffer",
    "fileExplicitContract",
    "fileExplicitContractInput",
    "fileError",
    "fileStatus",
    "filedNotice",
  ];

  static values = {
    transactionId: Number,
    documentId: Number,
    proposalId: String,
    approveUrl: String,
    resolveUrl: String,
    returnUrl: String,
    downloadUrl: String,
    confirmUrl: String,
    routingContext: Object,
  };

  connect() {
    this.selectedIndex = -1;
    this.selected = {};
    this.corrections = {};
    this.applying = false;
    this.resolving = false;
    this._onKeyDown = (event) => this.handleKeydown(event);

    // Fields BOB flagged stay undecided until the agent accepts them explicitly.
    this.fieldCardTargets.forEach((card) => {
      if (card.dataset.proposed !== "true") return;
      if (card.dataset.flagged !== "true") {
        this.selected[card.dataset.fieldKey] = true;
      }
    });
    this.syncApplyUi();
    this.syncFieldStates();

    document.addEventListener("keydown", this._onKeyDown);
    this.connectPdfPane();
    this.fileScopeChanged();
  }

  disconnect() {
    document.removeEventListener("keydown", this._onKeyDown);
    this.disconnectPdfPane();
  }

  goBack(event) {
    if (event) event.preventDefault();
    window.location.href = this.returnUrlValue;
  }

  selectFieldCard(event) {
    const card = event.currentTarget;
    if (!card) return;
    const index = this.fieldCardTargets.indexOf(card);
    this.selectIndex(index);
  }

  selectIndex(index) {
    if (index < 0 || index >= this.fieldCardTargets.length) return;
    this.selectedIndex = index;
    const card = this.fieldCardTargets[index];

    this.fieldCardTargets.forEach((el) => el.classList.remove("drw-field-selected"));
    card.classList.add("drw-field-selected");
    card.focus({ preventScroll: true });
    card.scrollIntoView({ block: "nearest" });

    const page = Number(card.dataset.page);
    const quote = (card.dataset.quote || "").trim();
    if (Number.isFinite(page) && page > 0) {
      this.goToPage(page, { pulse: !quote });
      if (quote) {
        this.highlightQuote(page, quote).catch(() => this.pulsePage(this.pageEls[page]));
      }
    }
  }

  async highlightQuote(pageNum, quote) {
    const pageEl = this.pageEls[pageNum];
    if (!pageEl || !this.pdfDoc || !quote) {
      this.pulsePage(pageEl);
      return;
    }

    pageEl.querySelectorAll(".drw-quote-box").forEach((node) => node.remove());

    try {
      const page = await this.pdfDoc.getPage(pageNum);
      const viewport = page.getViewport({ scale: this.scale });
      const textContent = await page.getTextContent();
      const needle = this._normalizeText(quote);
      if (!needle) {
        this.pulsePage(pageEl);
        return;
      }

      const items = textContent.items || [];
      let joined = "";
      const map = [];
      items.forEach((item, index) => {
        const text = item.str || "";
        for (let i = 0; i < text.length; i += 1) {
          map.push({ index, offset: i });
        }
        joined += text;
        if (item.hasEOL) {
          joined += " ";
          map.push({ index, offset: text.length });
        }
      });

      const haystack = this._normalizeText(joined);
      const matchAt = haystack.indexOf(needle);
      if (matchAt < 0) {
        this.pulsePage(pageEl);
        return;
      }

      // Map normalized match start back approximately via raw joined index.
      let rawStart = 0;
      let normCount = 0;
      const joinedLower = joined.toLowerCase();
      const needleRaw = quote.replace(/\s+/g, " ").trim().toLowerCase();
      const rawMatch = joinedLower.indexOf(needleRaw);
      rawStart = rawMatch >= 0 ? rawMatch : matchAt;

      const startMeta = map[rawStart];
      const endMeta = map[Math.min(map.length - 1, rawStart + needleRaw.length - 1)];
      if (!startMeta || !endMeta) {
        this.pulsePage(pageEl);
        return;
      }

      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (let i = startMeta.index; i <= endMeta.index; i += 1) {
        const item = items[i];
        if (!item || !item.transform) continue;
        const tx = window.pdfjsLib.Util.transform(viewport.transform, item.transform);
        const x = tx[4];
        const y = tx[5];
        const width = (item.width || 0) * this.scale;
        const height = (item.height || Math.abs(tx[3]) || 10) * (this.scale > 1 ? 1 : 1);
        const h = item.height ? item.height * this.scale : Math.max(10, Math.abs(tx[0]) * 0.1 + 12);
        minX = Math.min(minX, x);
        maxX = Math.max(maxX, x + width);
        minY = Math.min(minY, y - h);
        maxY = Math.max(maxY, y);
      }

      if (!Number.isFinite(minX) || !Number.isFinite(minY)) {
        this.pulsePage(pageEl);
        return;
      }

      pageEl.scrollIntoView({ behavior: "smooth", block: "start" });
      const box = document.createElement("div");
      box.className = "drw-quote-box";
      box.style.left = `${Math.max(0, minX - 2)}px`;
      box.style.top = `${Math.max(0, minY - 2)}px`;
      box.style.width = `${Math.max(8, maxX - minX + 4)}px`;
      box.style.height = `${Math.max(8, maxY - minY + 4)}px`;
      pageEl.appendChild(box);
      window.setTimeout(() => box.remove(), 2400);
    } catch (_error) {
      this.pulsePage(pageEl);
    }
  }

  _normalizeText(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();
  }

  jumpFindingPage(event) {
    const page = Number(event.currentTarget?.dataset?.page);
    if (Number.isFinite(page) && page > 0) this.goToPage(page);
  }

  acceptField(event) {
    const card = event.currentTarget.closest("[data-field-key]");
    if (!card || card.dataset.proposed !== "true") return;
    this.selected[card.dataset.fieldKey] = true;
    this.syncFieldStates();
    this.syncApplyUi();
  }

  rejectField(event) {
    const card = event.currentTarget.closest("[data-field-key]");
    if (!card || card.dataset.proposed !== "true") return;
    this.selected[card.dataset.fieldKey] = false;
    delete this.corrections[card.dataset.fieldKey];
    const edit = card.querySelector('[data-role="value-edit"]');
    const display = card.querySelector('[data-role="value-display"]');
    if (edit) edit.classList.add("hidden");
    if (display) display.classList.remove("hidden");
    this.syncFieldStates();
    this.syncApplyUi();
  }

  editField(event) {
    const card = event.currentTarget.closest("[data-field-key]");
    if (!card || card.dataset.proposed !== "true") return;
    const edit = card.querySelector('[data-role="value-edit"]');
    const display = card.querySelector('[data-role="value-display"]');
    const input = card.querySelector('[data-role="value-input"]');
    if (!edit || !display || !input) return;
    display.classList.add("hidden");
    edit.classList.remove("hidden");
    input.focus();
    input.select();
    input.oninput = () => {
      this.corrections[card.dataset.fieldKey] = input.value;
      this.selected[card.dataset.fieldKey] = true;
      this.syncFieldStates();
      this.syncApplyUi();
    };
  }

  syncFieldStates() {
    this.fieldCardTargets.forEach((card) => {
      if (card.dataset.proposed !== "true") return;
      const key = card.dataset.fieldKey;
      const accepted = this.selected[key] === true;
      const rejected = this.selected[key] === false;
      const acceptState = card.querySelector('[data-role="accept-state"]');
      const rejectState = card.querySelector('[data-role="reject-state"]');
      const undecidedState = card.querySelector('[data-role="undecided-state"]');
      if (acceptState) acceptState.classList.toggle("hidden", !accepted);
      if (rejectState) rejectState.classList.toggle("hidden", !rejected);
      if (undecidedState) {
        undecidedState.classList.toggle("hidden", accepted || rejected);
      }
    });
  }

  syncApplyUi() {
    if (!this.hasApplyBtnTarget) return;
    const count = Object.values(this.selected).filter(Boolean).length;
    if (this.hasApplyCountTarget) {
      this.applyCountTarget.textContent = `(${count})`;
    }
    this.applyBtnTarget.disabled = this.applying || count === 0 || !this.approveUrlValue;
  }

  handleKeydown(event) {
    const tag = (event.target?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || event.target?.isContentEditable) {
      return;
    }

    const key = event.key;
    if (key === "j" || key === "ArrowDown") {
      event.preventDefault();
      this.selectIndex(
        this.selectedIndex < 0 ? 0 : Math.min(this.fieldCardTargets.length - 1, this.selectedIndex + 1),
      );
      return;
    }
    if (key === "k" || key === "ArrowUp") {
      event.preventDefault();
      this.selectIndex(
        this.selectedIndex < 0
          ? this.fieldCardTargets.length - 1
          : Math.max(0, this.selectedIndex - 1),
      );
      return;
    }
    if (key === "a" || key === "A") {
      const card = this.fieldCardTargets[this.selectedIndex];
      if (card) this.acceptField({ currentTarget: card });
      return;
    }
    if (key === "r" || key === "R") {
      const card = this.fieldCardTargets[this.selectedIndex];
      if (card) this.rejectField({ currentTarget: card });
      return;
    }
    if (key === "Escape") {
      if (this.hasReviewPaneTarget) this.reviewPaneTarget.focus();
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

  async applySelected() {
    if (this.applying || !this.approveUrlValue) return;
    const selected = { ...this.selected };
    if (!Object.values(selected).some(Boolean)) {
      this.showError("Accept at least one field to apply.");
      return;
    }

    this.applying = true;
    this.syncApplyUi();
    this.clearError();
    if (this.hasApplyLabelTarget) this.applyLabelTarget.textContent = "Applying…";

    try {
      const response = await fetch(this.approveUrlValue, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({
          selected,
          corrections: this.corrections,
          flash_on_success: true,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not apply selected fields.");
      }
      window.location.href = data.next_url || this.returnUrlValue;
    } catch (error) {
      this.applying = false;
      if (this.hasApplyLabelTarget) this.applyLabelTarget.textContent = "Apply selected";
      this.syncApplyUi();
      this.showError(error.message || "Could not apply selected fields.");
    }
  }

  async markReviewed() {
    if (this.resolving || !this.resolveUrlValue) return;
    this.resolving = true;
    this.clearError();
    if (this.hasMarkReviewedBtnTarget) {
      this.markReviewedBtnTarget.disabled = true;
      this.markReviewedBtnTarget.textContent = "Saving…";
    }
    try {
      const response = await fetch(this.resolveUrlValue, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not mark this document reviewed.");
      }
      window.location.href = data.next_url || this.returnUrlValue;
    } catch (error) {
      this.resolving = false;
      if (this.hasMarkReviewedBtnTarget) {
        this.markReviewedBtnTarget.disabled = false;
        this.markReviewedBtnTarget.textContent = "Mark reviewed";
      }
      this.showError(error.message || "Could not mark this document reviewed.");
    }
  }

  fileScopeChanged() {
    if (!this.hasFileScopeTarget) return;
    const scope = this.fileScopeTarget.value;
    const showOffer = scope === "offer";
    if (this.hasFileOfferFieldsTarget) {
      this.fileOfferFieldsTarget.classList.toggle("hidden", !showOffer);
    }
    if (this.hasFileExplicitContractTarget) {
      const showExplicit = scope === "contract";
      this.fileExplicitContractTarget.classList.toggle("hidden", !showExplicit);
      this.fileExplicitContractTarget.classList.toggle("flex", showExplicit);
    }
  }

  showFileError(message) {
    if (!this.hasFileErrorTarget) return;
    this.fileErrorTarget.textContent = message || "Could not file this document.";
    this.fileErrorTarget.classList.remove("hidden");
  }

  clearFileError() {
    if (!this.hasFileErrorTarget) return;
    this.fileErrorTarget.textContent = "";
    this.fileErrorTarget.classList.add("hidden");
  }

  showFileStatus(message) {
    if (!this.hasFileStatusTarget) return;
    this.fileStatusTarget.textContent = message || "";
    this.fileStatusTarget.classList.toggle("hidden", !message);
  }

  async confirmFiling(event) {
    if (event) event.preventDefault();
    if (this.confirming || !this.confirmUrlValue) return;
    if (!this.hasFileSlugTarget || !this.hasFileScopeTarget) return;

    const templateSlug = this.fileSlugTarget.value;
    const scope = this.fileScopeTarget.value;
    const createNewOffer =
      this.hasFileCreateOfferTarget && this.fileCreateOfferTarget.checked;
    const offerIdRaw =
      this.hasFileOfferTarget && !createNewOffer ? this.fileOfferTarget.value : "";
    const explicitControlling =
      this.hasFileExplicitContractInputTarget &&
      this.fileExplicitContractInputTarget.checked;

    this.clearFileError();
    this.showFileStatus("");
    if (!templateSlug) {
      this.showFileError("Choose a form type.");
      return;
    }
    if (!scope) {
      this.showFileError("Choose a destination.");
      return;
    }
    if (scope === "offer" && !createNewOffer && !offerIdRaw) {
      this.showFileError("Select an offer, or start a new offer package.");
      return;
    }

    this.confirming = true;
    try {
      const response = await fetch(this.confirmUrlValue, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({
          template_slug: templateSlug,
          scope,
          offer_id: offerIdRaw ? Number(offerIdRaw) : null,
          create_new_offer: createNewOffer,
          explicit_controlling_confirmation: explicitControlling,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.success === false) {
        throw new Error(data.error || "Could not confirm filing.");
      }

      // Stay in review when term fields remain; only leave for amendment-only paths
      // or when there is nothing left to apply.
      const hasProposalFields = Boolean(this.proposalIdValue);
      if (data.amendment_id && data.next_url) {
        window.location.href = data.next_url;
        return;
      }
      if (hasProposalFields) {
        this.showFileStatus("Filed. Now approve selected terms below — filing alone does not change them.");
        window.setTimeout(() => {
          window.location.reload();
        }, 450);
        return;
      }
      if (data.next_url) {
        window.location.href = data.next_url;
        return;
      }
      window.location.reload();
    } catch (error) {
      this.confirming = false;
      this.showFileError(error.message || "Could not confirm filing.");
    }
  }
}
