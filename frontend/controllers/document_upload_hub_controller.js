import { Controller } from "@hotwired/stimulus";

/**
 * Accessible upload dialog for package-scoped PDF intake.
 * Posts one or more PDFs to upload-completed and wakes transaction-live polling.
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
  ];

  static FILE_HINT = "or choose files · 20 max, 25 MB each";

  static values = {
    uploadUrl: String,
    hasBaseline: Boolean,
    side: String,
  };

  connect() {
    this._onCancel = (event) => {
      // Escape (and other cancel) must go through close() so focus restores
      // and the dialog does not stay open after preventDefault.
      if (event.target !== this.dialogTarget) return;
      event.preventDefault();
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
    if (this._statusTimer) {
      window.clearTimeout(this._statusTimer);
      this._statusTimer = null;
    }
  }

  open(event) {
    event?.preventDefault?.();
    const params = event?.params || {};
    this.#clearMessages();
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
    this.#setFileLabel(this.constructor.FILE_HINT);
    this.scopeChanged();
    this.createNewOfferChanged();

    if (this.hasDialogTarget && typeof this.dialogTarget.showModal === "function") {
      this.dialogTarget.showModal();
      this.fileTarget?.focus?.();
    }
  }

  close(event) {
    event?.preventDefault?.();
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
      this.#setFileLabel(this.constructor.FILE_HINT);
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

    if (this.hasSubmitTarget) {
      this.submitTarget.disabled = true;
      this.submitTarget.textContent = "Uploading…";
    }
    this.#showStatus(
      files.length === 1
        ? "Uploading…"
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

      const count = data.uploaded_count || files.length;
      this.#showStatus(
        count === 1
          ? "Uploaded. Identifying the document…"
          : `Uploaded ${count} PDFs. Identifying each one…`,
      );
      document.dispatchEvent(
        new CustomEvent("transaction-document-uploaded", {
          detail: {
            documentId: data.document_id || data.id,
            documentIds: data.document_ids || [],
            scope: data.scope || scope,
            offerId: data.offer_id,
          },
        }),
      );

      this._statusTimer = window.setTimeout(() => {
        this.close();
        if (scope === "offer" && data.offer_review_url) {
          window.location.href = data.offer_review_url;
          return;
        }
        window.location.reload();
      }, 900);
    } catch (error) {
      this.#showError(error.message || "Upload failed. Try again.");
      if (this.hasSubmitTarget) {
        this.submitTarget.disabled = false;
        this.submitTarget.textContent = "Upload PDFs";
      }
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

  #showStatus(message) {
    if (!this.hasStatusTarget) return;
    this.statusTarget.textContent = message;
    this.statusTarget.classList.remove("hidden");
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
