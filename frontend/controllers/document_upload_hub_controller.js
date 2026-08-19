import { Controller } from "@hotwired/stimulus";

const FILE_HINT = "or choose files · 20 max, 25 MB each";
const GENERIC_SLUGS = new Set(["", "completed", "other", "unknown"]);
const ACTIVE_STATUSES = new Set(["pending", "processing"]);
const POLL_MS = 800;
const MIN_WAIT_MS = 900;
const TIMEOUT_MS = 25000;
const DONE_PAUSE_MS = 500;

/**
 * Accessible upload dialog for package-scoped PDF intake.
 * Posts one or more PDFs to upload-completed, then holds the dialog
 * until identification and extraction settle.
 */
export default class extends Controller {
  static targets = [
    "dialog",
    "form",
    "file",
    "fileLabel",
    "dropzone",
    "scope",
    "slug",
    "offerFields",
    "offerSelect",
    "createNewOffer",
    "error",
    "status",
    "submit",
    "title",
    "lede",
    "fields",
    "footer",
    "closeButton",
    "processing",
    "processingStatus",
  ];

  static values = {
    uploadUrl: String,
    liveUrl: String,
    hasBaseline: Boolean,
    side: String,
  };

  connect() {
    this._busy = false;
    this._idleTitle = this.hasTitleTarget
      ? this.titleTarget.textContent
      : "Upload documents";
    this._idleLede = this.hasLedeTarget
      ? this.ledeTarget.textContent
      : "PDFs are identified after upload.";
    this._onCancel = (event) => {
      if (event.target !== this.dialogTarget) return;
      event.preventDefault();
      if (this._busy) return;
      this.#clearMessages();
      this.close();
    };
    if (this.hasDialogTarget) {
      this.dialogTarget.addEventListener("cancel", this._onCancel);
    }
    this._bindDropzone();
    this._lastFocused = null;
  }

  disconnect() {
    if (this.hasDialogTarget && this._onCancel) {
      this.dialogTarget.removeEventListener("cancel", this._onCancel);
    }
    this._unbindDropzone();
    this.#stopWatching();
  }

  open(event) {
    event?.preventDefault?.();
    if (this._busy) return;
    const params = event?.params || {};
    this.#clearMessages();
    this.#setBusy(false);
    this._lastFocused = document.activeElement;

    if (this.hasScopeTarget) {
      const scope = params.scope || "other";
      if ([...this.scopeTarget.options].some((o) => o.value === scope)) {
        this.scopeTarget.value = scope;
      } else {
        this.scopeTarget.value = "other";
      }
    }
    if (this.hasSlugTarget) {
      this.slugTarget.value = params.slug || "";
    }
    if (this.hasOfferSelectTarget) {
      this.offerSelectTarget.value = params.scopeId ? String(params.scopeId) : "";
    }
    if (this.hasCreateNewOfferTarget) {
      this.createNewOfferTarget.checked = Boolean(params.createNewOffer);
    }
    if (this.hasFileTarget) {
      this.fileTarget.value = "";
    }
    this.#setFileLabel(FILE_HINT);
    this.scopeChanged();
    this.createNewOfferChanged();

    if (this.hasDialogTarget && typeof this.dialogTarget.showModal === "function") {
      this.dialogTarget.showModal();
      this.fileTarget?.focus?.();
    }
  }

  close(event) {
    event?.preventDefault?.();
    if (this._busy) return;
    if (this.hasDialogTarget && this.dialogTarget.open) {
      this.dialogTarget.close();
    }
    if (this._lastFocused && typeof this._lastFocused.focus === "function") {
      this._lastFocused.focus();
    }
  }

  scopeChanged() {
    const scope = this.hasScopeTarget ? this.scopeTarget.value : "other";
    if (this.hasOfferFieldsTarget) {
      this.offerFieldsTarget.classList.toggle("hidden", scope !== "offer");
    }
    if (scope === "amendment" && !this.hasBaselineValue) {
      this.#showError("Amendments are available after a controlling contract is established.");
      if (this.hasSubmitTarget) this.submitTarget.disabled = true;
    } else if (this.hasSubmitTarget) {
      this.submitTarget.disabled = false;
      this.#clearError();
    }
  }

  filesChanged() {
    const files = this.hasFileTarget ? Array.from(this.fileTarget.files || []) : [];
    if (!files.length) {
      this.#setFileLabel(FILE_HINT);
      return;
    }
    this.#setFileLabel(
      files.length === 1
        ? files[0].name
        : `${files.length} PDFs selected`,
    );
  }

  createNewOfferChanged() {
    if (!this.hasCreateNewOfferTarget || !this.hasOfferSelectTarget) return;
    const createNew = this.createNewOfferTarget.checked;
    this.offerSelectTarget.disabled = createNew;
    if (createNew) this.offerSelectTarget.value = "";
  }

  async submit(event) {
    event?.preventDefault?.();
    if (this._busy) return;
    this.#clearMessages();

    const files = this.hasFileTarget
      ? Array.from(this.fileTarget.files || [])
      : [];
    if (!files.length) {
      this.#showError("Choose at least one PDF to upload.");
      return;
    }
    if (files.length > 20) {
      this.#showError("Upload up to 20 PDFs at a time.");
      return;
    }
    for (const file of files) {
      if (
        !file.name.toLowerCase().endsWith(".pdf")
        && file.type !== "application/pdf"
      ) {
        this.#showError(`${file.name}: upload a PDF.`);
        return;
      }
    }

    const scope = this.hasScopeTarget ? this.scopeTarget.value : "other";
    if (scope === "amendment" && !this.hasBaselineValue) {
      this.#showError("Amendments are available after a controlling contract is established.");
      return;
    }
    if (scope === "offer") {
      const createNew = this.hasCreateNewOfferTarget && this.createNewOfferTarget.checked;
      const offerId = this.hasOfferSelectTarget ? this.offerSelectTarget.value : "";
      if (!createNew && !offerId) {
        this.#showError("Select an offer or start a new offer package.");
        return;
      }
    }

    const body = new FormData();
    for (const file of files) {
      body.append("files", file);
    }
    body.append("scope", scope === "buyer_transaction" ? "other" : scope);
    const slug = this.hasSlugTarget ? (this.slugTarget.value || "completed") : "completed";
    body.append("template_slug", slug);
    if (this.hasSlugTarget && this.slugTarget.value && files.length === 1) {
      body.append("document_name", this.slugTarget.selectedOptions?.[0]?.text || "");
    }
    if (scope === "offer") {
      if (this.hasCreateNewOfferTarget && this.createNewOfferTarget.checked) {
        body.append("create_new_offer", "true");
      } else if (this.hasOfferSelectTarget && this.offerSelectTarget.value) {
        body.append("offer_id", this.offerSelectTarget.value);
      }
    }

    this.#setBusy(true);
    this.#setProcessingStatus(
      files.length === 1
        ? "Uploading the PDF…"
        : `Uploading ${files.length} PDFs…`,
    );

    try {
      const response = await fetch(this.uploadUrlValue, {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.success === false) {
        throw new Error(data.error || "Upload failed. Try again.");
      }

      const documentIds = this.#collectIds(data);
      document.dispatchEvent(
        new CustomEvent("transaction-document-uploaded", {
          detail: {
            documentId: data.document_id || data.id || documentIds[0],
            documentIds,
            scope: data.scope || scope,
            offerId: data.offer_id,
          },
        }),
      );

      this.#setProcessingStatus("Figuring out what this is…");
      this.#startWatching({
        documentIds,
        scope: data.scope || scope,
        offerReviewUrl: data.offer_review_url || "",
      });
    } catch (error) {
      this.#setBusy(false);
      this.#showError(error.message || "Upload failed. Try again.");
      if (this.hasSubmitTarget) {
        this.submitTarget.disabled = false;
        this.submitTarget.textContent = "Upload PDFs";
      }
    }
  }

  #collectIds(data) {
    const ids = [];
    for (const value of data.document_ids || []) {
      const id = Number(value);
      if (id) ids.push(id);
    }
    const fallback = Number(data.document_id || data.id);
    if (fallback && !ids.includes(fallback)) ids.push(fallback);
    return ids;
  }

  #startWatching({ documentIds, scope, offerReviewUrl }) {
    this.#stopWatching();
    this._watch = {
      ids: documentIds,
      scope,
      offerReviewUrl,
      startedAt: Date.now(),
      errors: 0,
    };
    this._pollTimer = window.setInterval(() => this.#tickWatch(), POLL_MS);
    this.#tickWatch();
  }

  #stopWatching() {
    if (this._pollTimer) {
      window.clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
    if (this._finishTimer) {
      window.clearTimeout(this._finishTimer);
      this._finishTimer = null;
    }
    this._tickInFlight = false;
  }

  async #tickWatch() {
    if (!this._watch || this._tickInFlight) return;
    const elapsed = Date.now() - this._watch.startedAt;
    if (elapsed >= TIMEOUT_MS) {
      this.#finishWatch("timeout");
      return;
    }
    if (!this.hasLiveUrlValue || !this.liveUrlValue) {
      if (elapsed >= 8000) this.#finishWatch("timeout");
      return;
    }

    this._tickInFlight = true;
    try {
      const response = await fetch(this.liveUrlValue, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) {
        this._watch.errors += 1;
        if (this._watch.errors >= 8) this.#finishWatch("timeout");
        return;
      }
      this._watch.errors = 0;
      const payload = await response.json();
      if (!this._watch) return;
      const docs = payload?.extraction?.documents || [];
      const watchIds = this.#expandWatchIds(this._watch.ids, docs);
      const watched = docs.filter((doc) => watchIds.has(doc.id));
      this.#updateCopyFromDocs(watched);

      const active = watched.some((doc) => ACTIVE_STATUSES.has(doc.status));
      const minWaitOver = elapsed >= MIN_WAIT_MS;
      const seenSeeds = this._watch.ids.length === 0
        || this._watch.ids.every((id) => docs.some((doc) => doc.id === id));
      if (
        minWaitOver
        && seenSeeds
        && !active
        && !payload.in_flight
      ) {
        this.#finishWatch("done");
      }
    } catch (_error) {
      if (this._watch) this._watch.errors += 1;
      if (this._watch && this._watch.errors >= 8) this.#finishWatch("timeout");
    } finally {
      this._tickInFlight = false;
    }
  }

  #expandWatchIds(seedIds, docs) {
    const watch = new Set(seedIds);
    let added = true;
    while (added) {
      added = false;
      for (const doc of docs) {
        if (doc.parent_id && watch.has(doc.parent_id) && !watch.has(doc.id)) {
          watch.add(doc.id);
          added = true;
        }
      }
    }
    return watch;
  }

  #updateCopyFromDocs(watched) {
    const named = watched.map((doc) => this.#docLabel(doc)).find(Boolean);
    const processing = watched.some((doc) => doc.status === "processing");
    const pending = watched.some((doc) => doc.status === "pending");
    if ((processing || pending) && named) {
      this.#setProcessingStatus(`Reading the ${named}…`);
      return;
    }
    if (processing) {
      this.#setProcessingStatus("Reading the file…");
      return;
    }
    if (pending) {
      this.#setProcessingStatus("Figuring out what this is…");
    }
  }

  #docLabel(doc) {
    const slug = String(doc.template_slug || "").toLowerCase();
    if (GENERIC_SLUGS.has(slug)) return "";
    const raw = String(doc.template_name || slug.replace(/-/g, " ")).trim();
    if (!raw) return "";
    const stripped = raw.replace(/^the\s+/i, "");
    return stripped.charAt(0).toLowerCase() + stripped.slice(1);
  }

  #finishWatch(reason) {
    if (!this._watch) return;
    const offerReviewUrl = this._watch.offerReviewUrl;
    const scope = this._watch.scope;
    this.#stopWatching();
    this._watch = null;
    this.#setProcessingStatus(
      reason === "done"
        ? "Done. Loading the transaction…"
        : "Uploaded. Still working in the background.",
    );
    this._finishTimer = window.setTimeout(() => {
      if (scope === "offer" && offerReviewUrl) {
        window.location.href = offerReviewUrl;
        return;
      }
      window.location.reload();
    }, DONE_PAUSE_MS);
  }

  #setBusy(busy) {
    this._busy = Boolean(busy);
    if (this.hasDialogTarget) {
      this.dialogTarget.classList.toggle("is-busy", this._busy);
      this.dialogTarget.setAttribute("aria-busy", this._busy ? "true" : "false");
    }
    if (this.hasFieldsTarget) {
      this.fieldsTarget.classList.toggle("hidden", this._busy);
    }
    if (this.hasFooterTarget) {
      this.footerTarget.classList.toggle("hidden", this._busy);
    }
    if (this.hasCloseButtonTarget) {
      this.closeButtonTarget.classList.toggle("hidden", this._busy);
      this.closeButtonTarget.disabled = this._busy;
    }
    if (this.hasProcessingTarget) {
      this.processingTarget.classList.toggle("hidden", !this._busy);
    }
    if (this.hasTitleTarget) {
      this.titleTarget.textContent = this._busy ? "Working on it" : this._idleTitle;
    }
    if (this.hasLedeTarget) {
      this.ledeTarget.textContent = this._busy
        ? "This usually takes a few seconds."
        : this._idleLede;
    }
    if (!this._busy) this.#stopWatching();
  }

  #setProcessingStatus(message) {
    if (this.hasProcessingStatusTarget) {
      this.processingStatusTarget.textContent = message;
    }
  }

  #clearMessages() {
    this.#clearError();
    if (this.hasStatusTarget) {
      this.statusTarget.textContent = "";
      this.statusTarget.classList.add("hidden");
    }
    if (this.hasSubmitTarget) {
      this.submitTarget.disabled = false;
      this.submitTarget.textContent = "Upload PDFs";
    }
  }

  #clearError() {
    if (this.hasErrorTarget) {
      this.errorTarget.textContent = "";
      this.errorTarget.classList.add("hidden");
    }
  }

  #showError(message) {
    if (!this.hasErrorTarget) return;
    this.errorTarget.textContent = message;
    this.errorTarget.classList.remove("hidden");
  }

  #setFileLabel(text) {
    if (this.hasFileLabelTarget) this.fileLabelTarget.textContent = text;
  }

  _bindDropzone() {
    if (!this.hasDropzoneTarget) return;
    this._onDragOver = (event) => {
      event.preventDefault();
      this.dropzoneTarget.classList.add("is-active");
    };
    this._onDragLeave = (event) => {
      if (this.dropzoneTarget.contains(event.relatedTarget)) return;
      this.dropzoneTarget.classList.remove("is-active");
    };
    this._onDrop = (event) => {
      event.preventDefault();
      this.dropzoneTarget.classList.remove("is-active");
      const files = event.dataTransfer?.files;
      if (!files?.length || !this.hasFileTarget) return;
      const transfer = new DataTransfer();
      Array.from(files).forEach((file) => transfer.items.add(file));
      this.fileTarget.files = transfer.files;
      this.filesChanged();
    };
    this.dropzoneTarget.addEventListener("dragover", this._onDragOver);
    this.dropzoneTarget.addEventListener("dragleave", this._onDragLeave);
    this.dropzoneTarget.addEventListener("drop", this._onDrop);
  }

  _unbindDropzone() {
    if (!this.hasDropzoneTarget) return;
    if (this._onDragOver) this.dropzoneTarget.removeEventListener("dragover", this._onDragOver);
    if (this._onDragLeave) this.dropzoneTarget.removeEventListener("dragleave", this._onDragLeave);
    if (this._onDrop) this.dropzoneTarget.removeEventListener("drop", this._onDrop);
  }
}
