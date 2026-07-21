import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static targets = [
    "sortableList",
    "groupRow",
    "status",
    "modal",
    "modalTitle",
    "form",
    "groupIdInput",
    "nameInput",
    "categoryInput",
    "activeInput",
    "activeField",
    "formError",
    "submitButton",
    "restoreButton",
  ];

  static values = {
    reorderUrl: String,
    createUrl: String,
    restoreUrl: String,
  };

  connect() {
    this.sortables = [];
    this.previousFocus = null;
    this.boundEscape = this.onKeydown.bind(this);
    document.addEventListener("keydown", this.boundEscape);

    if (typeof Sortable !== "undefined") {
      this.sortableListTargets.forEach((list) => {
        const sortable = Sortable.create(list, {
          handle: "[data-groups-page-target='dragHandle']",
          animation: 150,
          ghostClass: "crm-group-row--ghost",
          onEnd: () => this.persistListOrder(list),
        });
        this.sortables.push(sortable);
      });
    }
  }

  disconnect() {
    document.removeEventListener("keydown", this.boundEscape);
    this.sortables.forEach((s) => s.destroy());
    this.sortables = [];
  }

  onKeydown(event) {
    if (event.key === "Escape" && !this.modalTarget.classList.contains("hidden")) {
      this.closeModal();
    }
  }

  openCreateModal() {
    this.modalTitleTarget.textContent = "Add group";
    this.groupIdInputTarget.value = "";
    this.nameInputTarget.value = "";
    this.categoryInputTarget.value = "";
    this.activeInputTarget.checked = true;
    this.activeFieldTarget.classList.add("hidden");
    this.clearFormError();
    this.showModal();
  }

  openEditModal(event) {
    const button = event.currentTarget;
    this.modalTitleTarget.textContent = "Edit group";
    this.groupIdInputTarget.value = button.dataset.groupId || "";
    this.nameInputTarget.value = button.dataset.groupName || "";
    this.categoryInputTarget.value = button.dataset.groupCategory || "";
    this.activeInputTarget.checked = button.dataset.groupActive === "true";
    this.activeFieldTarget.classList.remove("hidden");
    this.clearFormError();
    this.showModal();
  }

  showModal() {
    this.previousFocus = document.activeElement;
    this.modalTarget.classList.remove("hidden");
    requestAnimationFrame(() => this.nameInputTarget.focus());
  }

  closeModal() {
    this.modalTarget.classList.add("hidden");
    this.clearFormError();
    if (this.previousFocus && typeof this.previousFocus.focus === "function") {
      this.previousFocus.focus();
    }
  }

  async submitForm(event) {
    event.preventDefault();
    this.clearFormError();

    const payload = {
      name: this.nameInputTarget.value.trim(),
      category: this.categoryInputTarget.value.trim(),
    };
    if (!this.activeFieldTarget.classList.contains("hidden")) {
      payload.is_active = this.activeInputTarget.checked;
    }

    const groupId = this.groupIdInputTarget.value;
    const isEdit = Boolean(groupId);
    const url = isEdit ? `/groups/${groupId}` : this.createUrlValue;
    const method = isEdit ? "PUT" : "POST";

    this.submitButtonTarget.disabled = true;
    try {
      const data = await this.requestJson(url, method, payload);
      if (!data.success) {
        this.showFormError(data.error || "Could not save group.");
        return;
      }
      this.closeModal();
      window.location.reload();
    } catch (error) {
      this.showFormError(error.message || "Could not save group.");
    } finally {
      this.submitButtonTarget.disabled = false;
    }
  }

  async toggleActive(event) {
    const input = event.currentTarget;
    const groupId = input.dataset.groupId;
    const isActive = input.checked;
    input.disabled = true;
    try {
      const data = await this.requestJson(`/groups/${groupId}`, "PUT", {
        is_active: isActive,
      });
      if (!data.success) {
        input.checked = !isActive;
        input.disabled = false;
        this.showStatus(data.error || "Could not update group.", true);
        return;
      }
      window.location.reload();
    } catch (error) {
      input.checked = !isActive;
      input.disabled = false;
      this.showStatus(error.message || "Could not update group.", true);
    }
  }

  async deleteGroup(event) {
    const button = event.currentTarget;
    const groupId = button.dataset.groupId;
    const name = button.dataset.groupName || "this group";
    if (!window.confirm(`Delete "${name}"? This cannot be undone.`)) {
      return;
    }
    try {
      const data = await this.requestJson(`/groups/${groupId}`, "DELETE");
      if (!data.success) {
        this.showStatus(data.error || "Could not delete group.", true);
        return;
      }
      window.location.reload();
    } catch (error) {
      this.showStatus(error.message || "Could not delete group.", true);
    }
  }

  async restoreDefaults() {
    if (this.hasRestoreButtonTarget) {
      this.restoreButtonTarget.disabled = true;
    }
    try {
      const data = await this.requestJson(this.restoreUrlValue, "POST");
      if (!data.success) {
        this.showStatus(data.error || "Could not restore defaults.", true);
        return;
      }
      const count = data.created_count || 0;
      if (count === 0) {
        this.showStatus("You already have every default group.");
        return;
      }
      window.location.reload();
    } catch (error) {
      this.showStatus(error.message || "Could not restore defaults.", true);
    } finally {
      if (this.hasRestoreButtonTarget) {
        this.restoreButtonTarget.disabled = false;
      }
    }
  }

  moveUp(event) {
    const row = event.currentTarget.closest("[data-groups-page-target='groupRow']");
    const list = row?.parentElement;
    if (!row || !list || !row.previousElementSibling) return;
    list.insertBefore(row, row.previousElementSibling);
    this.persistListOrder(list);
  }

  moveDown(event) {
    const row = event.currentTarget.closest("[data-groups-page-target='groupRow']");
    const list = row?.parentElement;
    if (!row || !list || !row.nextElementSibling) return;
    list.insertBefore(row.nextElementSibling, row);
    this.persistListOrder(list);
  }

  async persistListOrder(list) {
    // Global sort_order across all categories: collect every list in page order
    const items = [];
    let order = 1;
    this.sortableListTargets.forEach((sortableList) => {
      sortableList.querySelectorAll("[data-groups-page-target='groupRow']").forEach((row) => {
        items.push({
          id: Number(row.dataset.groupId),
          sort_order: order,
        });
        order += 1;
      });
    });

    // If only one list changed, still send full page order for consistency
    if (!items.length) {
      list.querySelectorAll("[data-groups-page-target='groupRow']").forEach((row, index) => {
        items.push({
          id: Number(row.dataset.groupId),
          sort_order: index + 1,
        });
      });
    }

    try {
      const data = await this.requestJson(this.reorderUrlValue, "POST", items);
      if (!data.success) {
        this.showStatus(data.error || "Could not save order.", true);
      }
    } catch (error) {
      this.showStatus(error.message || "Could not save order.", true);
    }
  }

  async requestJson(url, method, body) {
    const options = {
      method,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
    };
    if (body !== undefined) {
      options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    let data = {};
    try {
      data = await response.json();
    } catch (_err) {
      data = {};
    }
    if (!response.ok && !data.error) {
      throw new Error(`Request failed (${response.status})`);
    }
    return data;
  }

  showStatus(message, isError = false) {
    if (!this.hasStatusTarget) return;
    this.statusTarget.textContent = message;
    this.statusTarget.classList.remove("hidden");
    this.statusTarget.classList.toggle("is-error", isError);
  }

  showFormError(message) {
    this.formErrorTarget.textContent = message;
    this.formErrorTarget.classList.remove("hidden");
  }

  clearFormError() {
    if (!this.hasFormErrorTarget) return;
    this.formErrorTarget.textContent = "";
    this.formErrorTarget.classList.add("hidden");
  }
}
