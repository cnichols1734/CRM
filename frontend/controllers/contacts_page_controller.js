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
    "errorList"
  ];

  connect() {
    this.searchTimeout = null;
    this.toggleClearButton();
  }

  disconnect() {
    if (this.searchTimeout) {
      window.clearTimeout(this.searchTimeout);
    }
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
