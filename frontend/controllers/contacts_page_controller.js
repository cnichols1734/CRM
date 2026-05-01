import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static targets = [
    "searchInput",
    "mobileSearchInput",
    "mobileSearchBar",
    "filterPanel",
    "clearSearchButton",
    "importFile",
    "importButton",
    "importLoading",
    "importStatus",
    "statusIcon",
    "statusMessage",
    "errorDetails",
    "errorList",
    "layout",
    "previewRail",
    "previewBody",
    "row"
  ];

  connect() {
    this.searchTimeout = null;
    this.previewAbortController = null;
    this.previewClearTimer = null;
    this.activeRow = null;
    this.handleKeydown = this.handleKeydown.bind(this);
    document.addEventListener("keydown", this.handleKeydown);
    this.toggleClearButton();
  }

  disconnect() {
    if (this.searchTimeout) {
      window.clearTimeout(this.searchTimeout);
    }
    if (this.previewAbortController) {
      this.previewAbortController.abort();
    }
    if (this.previewClearTimer) {
      window.clearTimeout(this.previewClearTimer);
    }
    document.removeEventListener("keydown", this.handleKeydown);
  }

  // ── Slide-over preview rail ──────────────────────────────────
  // Click a row -> fetch /contact/<id>/preview, inject the partial
  // into the rail, animate the layout grid open. Esc / close button
  // both dismiss. Mobile (<md) keeps native <a> navigation since the
  // mobile card stack is unaffected.
  openPreview(event) {
    if (!this.hasLayoutTarget) return;

    // Don't hijack clicks on inner links/buttons (lets email/phone-style
    // anchors inside future rows work normally).
    const interactive = event.target.closest("a, button, input, select, textarea, [role='button']");
    if (interactive && this.element.contains(interactive)) return;

    const row = event.currentTarget;
    const contactId = row.dataset.contactId;
    if (!contactId) return;

    // Already open on this row -> close (toggle behavior).
    if (this.activeRow === row && this.layoutTarget.classList.contains("is-open")) {
      this.closePreview();
      return;
    }

    const isWarmSwap = this.layoutTarget.classList.contains("is-open");

    this.markActiveRow(row);

    if (isWarmSwap) {
      // Rail is already open -- keep the card in place and cross-fade the
      // inner content. No skeleton flash.
      this.fetchPreview(contactId, { warmSwap: true });
    } else {
      // Cold open -- pop the card in, show a skeleton, then swap in real
      // content as soon as the fetch returns.
      this.openRail();
      this.renderSkeleton();
      this.fetchPreview(contactId, { warmSwap: false });
    }
  }

  closePreview(event) {
    if (event && typeof event.preventDefault === "function") event.preventDefault();
    if (!this.hasLayoutTarget) return;
    if (!this.layoutTarget.classList.contains("is-open")) return;

    this.layoutTarget.classList.remove("is-open");
    if (this.hasPreviewRailTarget) {
      this.previewRailTarget.setAttribute("aria-hidden", "true");
    }
    this.clearActiveRow();

    if (this.previewAbortController) {
      this.previewAbortController.abort();
      this.previewAbortController = null;
    }

    // Clear the body after the slide-out finishes so the next cold open
    // starts from a clean state. Matches the 540ms grid transition.
    if (this.previewClearTimer) window.clearTimeout(this.previewClearTimer);
    this.previewClearTimer = window.setTimeout(() => {
      if (this.hasPreviewBodyTarget) this.previewBodyTarget.innerHTML = "";
    }, 600);
  }

  handleKeydown(event) {
    if (event.key === "Escape" && this.hasLayoutTarget && this.layoutTarget.classList.contains("is-open")) {
      this.closePreview();
    }
  }

  openRail() {
    if (this.previewClearTimer) {
      window.clearTimeout(this.previewClearTimer);
      this.previewClearTimer = null;
    }
    this.layoutTarget.classList.add("is-open");
    if (this.hasPreviewRailTarget) {
      this.previewRailTarget.setAttribute("aria-hidden", "false");
    }
  }

  markActiveRow(row) {
    if (this.activeRow && this.activeRow !== row) {
      this.activeRow.classList.remove("is-selected");
    }
    row.classList.add("is-selected");
    this.activeRow = row;
  }

  clearActiveRow() {
    if (this.activeRow) {
      this.activeRow.classList.remove("is-selected");
      this.activeRow = null;
    }
  }

  renderSkeleton() {
    if (!this.hasPreviewBodyTarget) return;
    this.previewBodyTarget.innerHTML = `
      <div class="crm-rail__skeleton">
        <div class="crm-rail__skeleton-line is-short"></div>
        <div class="crm-rail__skeleton-line"></div>
        <div class="crm-rail__skeleton-line is-mid"></div>
        <div class="crm-rail__skeleton-line"></div>
        <div class="crm-rail__skeleton-line is-mid"></div>
      </div>
    `;
  }

  async fetchPreview(contactId, { warmSwap = false } = {}) {
    if (this.previewAbortController) this.previewAbortController.abort();
    this.previewAbortController = new AbortController();
    const requestedRow = this.activeRow;

    try {
      const response = await fetch(`/contact/${contactId}/preview`, {
        signal: this.previewAbortController.signal,
        headers: { "X-Requested-With": "XMLHttpRequest" }
      });
      if (!response.ok) {
        throw new Error(`Preview request failed: ${response.status}`);
      }
      const html = await response.text();

      // If the user moved on (different row, or closed the rail), drop this response.
      if (this.activeRow !== requestedRow || !this.layoutTarget.classList.contains("is-open")) return;

      await this.swapPreviewContent(html, { warmSwap });
    } catch (error) {
      if (error.name === "AbortError") return;
      if (this.hasPreviewBodyTarget && this.activeRow === requestedRow) {
        const fallbackHref = requestedRow ? requestedRow.dataset.contactHref : "";
        const fallbackHtml = `
          <div class="crm-rail__panel">
            <header class="crm-rail__header">
              <div class="crm-eyebrow">Preview unavailable</div>
              <button type="button" class="crm-rail__close" aria-label="Close preview"
                      data-action="click->contacts-page#closePreview">
                <i class="fas fa-times text-xs"></i>
              </button>
            </header>
            <p class="crm-rail__notes">We couldn't load this contact's preview.${fallbackHref ? ` <a href="${fallbackHref}">Open the full view instead.</a>` : ""}</p>
          </div>
        `;
        await this.swapPreviewContent(fallbackHtml, { warmSwap });
      }
    }
  }

  // Swaps the rail body content. On a warm swap (rail already open with
  // existing content) we fade the current panel out first, then inject the
  // new HTML which fades itself in via the .crm-rail__inner > * keyframe.
  async swapPreviewContent(html, { warmSwap = false } = {}) {
    if (!this.hasPreviewBodyTarget) return;

    if (warmSwap) {
      const current = this.previewBodyTarget.firstElementChild;
      if (current) {
        current.classList.add("is-leaving");
        await this.wait(160);
      }
    }

    this.previewBodyTarget.innerHTML = html;
  }

  wait(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  toggleFilters(event) {
    if (event) event.preventDefault();
    if (!this.hasFilterPanelTarget) return;
    this.filterPanelTarget.classList.toggle("hidden");
  }

  toggleMobileSearch(event) {
    if (event) event.preventDefault();
    if (!this.hasMobileSearchBarTarget) return;

    this.mobileSearchBarTarget.classList.toggle("hidden");
    if (!this.mobileSearchBarTarget.classList.contains("hidden") && this.hasMobileSearchInputTarget) {
      this.mobileSearchInputTarget.focus();
    }
  }

  queueSearch(event) {
    if (!this.hasSearchInputTarget) return;

    const value = event.target.value.trim();
    this.toggleClearButton();

    if (this.searchTimeout) {
      window.clearTimeout(this.searchTimeout);
    }

    this.searchTimeout = window.setTimeout(() => {
      const url = new URL(window.location.href);
      if (value) {
        url.searchParams.set("q", value);
      } else {
        url.searchParams.delete("q");
      }
      url.searchParams.delete("page");
      window.location.href = url.toString();
    }, 250);
  }

  clearSearch(event) {
    if (event) event.preventDefault();
    if (!this.hasSearchInputTarget) return;

    this.searchInputTarget.value = "";
    this.toggleClearButton();

    const url = new URL(window.location.href);
    url.searchParams.delete("q");
    url.searchParams.delete("page");
    window.location.href = url.toString();
  }

  sort(event) {
    event.preventDefault();

    const column = event.currentTarget.dataset.column;
    if (!column) return;

    const url = new URL(window.location.href);
    const currentSort = url.searchParams.get("sort") || "name";
    const currentDir = url.searchParams.get("dir") || "asc";
    const newDir = currentSort === column && currentDir === "asc" ? "desc" : "asc";

    url.searchParams.set("sort", column);
    url.searchParams.set("dir", newDir);
    window.location.href = url.toString();
  }

  clearSort(event) {
    event.preventDefault();

    const url = new URL(window.location.href);
    url.searchParams.delete("sort");
    url.searchParams.delete("dir");
    window.location.href = url.toString();
  }

  changePerPage(event) {
    const value = event.currentTarget.value;
    const url = new URL(window.location.href);
    url.searchParams.set("per_page", value);
    url.searchParams.set("page", 1);
    window.location.href = url.toString();
  }

  openImportDialog(event) {
    event.preventDefault();
    if (this.hasImportFileTarget) {
      this.importFileTarget.click();
    }
  }

  hideImportStatus(event) {
    if (event) event.preventDefault();
    if (this.hasImportStatusTarget) {
      this.importStatusTarget.classList.add("hidden");
    }
  }

  async uploadImport(event) {
    const file = event.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    const ownerSelect = this.element.querySelector("#importUserId");
    if (ownerSelect && ownerSelect.value) {
      formData.append("user_id", ownerSelect.value);
    }

    this.setImportState(true);

    try {
      const response = await fetch("/import-contacts", {
        method: "POST",
        body: formData
      });
      const data = await response.json();
      this.renderImportStatus(data);
    } catch (error) {
      console.error(error);
      this.renderImportStatus({
        status: "error",
        message: "Error uploading file"
      });
    } finally {
      this.setImportState(false);
      event.target.value = "";
    }
  }

  setImportState(loading) {
    if (this.hasImportButtonTarget) {
      this.importButtonTarget.disabled = loading;
      this.importButtonTarget.classList.toggle("opacity-50", loading);
      this.importButtonTarget.classList.toggle("cursor-not-allowed", loading);
    }
    if (this.hasImportLoadingTarget) {
      this.importLoadingTarget.classList.toggle("hidden", !loading);
    }
  }

  renderImportStatus(data) {
    if (!this.hasImportStatusTarget) return;

    const iconClasses = {
      success: "fas fa-check-circle text-emerald-500 text-xl",
      partial_success: "fas fa-exclamation-triangle text-amber-500 text-xl",
      error: "fas fa-times-circle text-red-500 text-xl"
    };

    if (this.hasStatusIconTarget) {
      this.statusIconTarget.className = iconClasses[data.status] || iconClasses.error;
    }

    if (this.hasStatusMessageTarget) {
      this.statusMessageTarget.textContent =
        data.message ||
        (data.status === "success"
          ? `Imported ${data.success_count || 0} contacts successfully.`
          : data.status === "partial_success"
            ? `Imported ${data.success_count || 0} contacts with a few issues.`
            : "Import failed.");
    }

    if (this.hasErrorListTarget) {
      this.errorListTarget.innerHTML = "";
      const details = data.error_details || [];
      details.forEach((detail) => {
        const item = document.createElement("li");
        item.textContent = detail;
        this.errorListTarget.appendChild(item);
      });
    }

    if (this.hasErrorDetailsTarget) {
      this.errorDetailsTarget.classList.toggle(
        "hidden",
        !(data.error_details && data.error_details.length)
      );
    }

    this.importStatusTarget.classList.remove("hidden");
  }

  toggleClearButton() {
    if (!this.hasClearSearchButtonTarget || !this.hasSearchInputTarget) return;
    const hasValue = this.searchInputTarget.value.trim().length > 0;
    this.clearSearchButtonTarget.classList.toggle("hidden", !hasValue);
  }
}
