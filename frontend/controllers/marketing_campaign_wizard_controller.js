import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static values = {
    estimateUrl: String,
    contactsUrl: String,
    previewAsUrl: String,
  };
  static targets = [
    "count",
    "breakdown",
    "drip",
    "preview",
    "previewEmpty",
    "previewMeta",
    "templateInput",
    "pick",
    "kind",
    "step",
    "panel",
    "source",
    "sourcePanel",
    "useButton",
    "contactQuery",
    "contactResults",
    "picked",
    "stepList",
    "stepTemplate",
    "previewAs",
  ];

  connect() {
    this._previewedId = this.hasTemplateInputTarget ? this.templateInputTarget.value : "";
    this.estimate();
    this._syncKind();
    this._syncPreview();
    this._syncUseButton();
    this._syncSource();
  }

  kind() {
    this._syncKind();
  }

  setKind(event) {
    const value = event.currentTarget.dataset.kind;
    const form = this.element.querySelector("form");
    if (form && form.kind) form.kind.value = value;
    this._syncKind();
  }

  showSource(event) {
    const source = event.currentTarget.dataset.source;
    this.sourceTargets.forEach((el) => {
      el.classList.toggle("is-active", el.dataset.source === source);
    });
    this._syncSource(source);
  }

  previewPick(event) {
    const button = event.currentTarget;
    this._previewedId = button.dataset.templateId || "";
    this.pickTargets.forEach((el) => {
      const on = el === button;
      el.classList.toggle("is-previewed", on);
      if (!on) el.classList.remove("is-selected");
    });
    this._paintCover(button);
    this._syncUseButton();
  }

  usePreviewed() {
    const id = this._previewedId;
    if (!id || !this.hasTemplateInputTarget) return;
    this.templateInputTarget.value = id;
    this.pickTargets.forEach((el) => {
      const on = el.dataset.templateId === id;
      el.classList.toggle("is-selected", on);
      el.classList.toggle("is-previewed", on);
      el.setAttribute("aria-pressed", on ? "true" : "false");
    });
    this._syncPreview();
    this._syncUseButton();
  }

  showStep(event) {
    const index = Number(event.currentTarget.dataset.step);
    if (!Number.isFinite(index)) return;
    this.panelTargets.forEach((panel) => {
      const match = Number(panel.dataset.step) === index;
      panel.classList.toggle("is-current", match);
    });
    this.stepTargets.forEach((step) => {
      const match = Number(step.dataset.step) === index;
      step.classList.toggle("is-active", match);
      step.setAttribute("aria-current", match ? "step" : "false");
    });
    const panel = this.panelTargets.find((el) => Number(el.dataset.step) === index);
    if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  searchContacts() {
    clearTimeout(this._searchTimer);
    const q = this.hasContactQueryTarget ? this.contactQueryTarget.value.trim() : "";
    if (!this.contactsUrlValue || q.length < 1) {
      if (this.hasContactResultsTarget) {
        this.contactResultsTarget.hidden = true;
        this.contactResultsTarget.innerHTML = "";
      }
      return;
    }
    this._searchTimer = setTimeout(() => {
      const url = `${this.contactsUrlValue}?q=${encodeURIComponent(q)}`;
      fetch(url, { headers: { Accept: "application/json" } })
        .then((r) => r.json())
        .then((rows) => this._renderResults(rows))
        .catch(() => {});
    }, 200);
  }

  addContact(event) {
    const button = event.currentTarget;
    const id = button.dataset.contactId;
    if (!id || !this.hasPickedTarget) return;
    if (this.pickedTarget.querySelector(`input[name="contact_id"][value="${id}"]`)) return;
    const chip = document.createElement("span");
    chip.className = "mkt-picked__chip";
    chip.innerHTML = `
      <input type="hidden" name="contact_id" value="${id}">
      ${button.dataset.contactName || "Contact"}
      <button type="button" class="mkt-picked__remove" data-action="click->marketing-campaign-wizard#removeContact" aria-label="Remove">×</button>
    `;
    this.pickedTarget.appendChild(chip);
    this._addPreviewAsOption(id, button.dataset.contactName || "Contact");
    if (this.hasContactResultsTarget) {
      this.contactResultsTarget.hidden = true;
      this.contactResultsTarget.innerHTML = "";
    }
    if (this.hasContactQueryTarget) this.contactQueryTarget.value = "";
    this.estimate();
  }

  removeContact(event) {
    const chip = event.currentTarget.closest(".mkt-picked__chip");
    if (!chip) return;
    const input = chip.querySelector('input[name="contact_id"]');
    const id = input ? input.value : "";
    chip.remove();
    if (id && this.hasPreviewAsTarget) {
      [...this.previewAsTarget.options].forEach((opt) => {
        if (opt.value === id) opt.remove();
      });
    }
    this.estimate();
  }

  addStep() {
    if (!this.hasStepListTarget || !this.hasStepTemplateTarget) return;
    const node = this.stepTemplateTarget.content.cloneNode(true);
    this.stepListTarget.appendChild(node);
  }

  removeStep(event) {
    const row = event.currentTarget.closest(".mkt-drip__row");
    if (row) row.remove();
  }

  estimate() {
    const form = this.element.querySelector("form");
    if (!form || !this.estimateUrlValue) return;
    const groups = [
      ...form.querySelectorAll("select[name='groups'] option:checked"),
      ...form.querySelectorAll("input[name='groups']:checked"),
    ].map((el) => Number(el.value));
    const contactIds = [...form.querySelectorAll("input[name='contact_id']")]
      .map((el) => Number(el.value))
      .filter(Boolean);
    const body = {
      groups,
      contact_ids: contactIds,
      zips: this._csv(form.zips && form.zips.value),
      cities: this._csv(form.cities && form.cities.value),
      states: this._csv(form.states && form.states.value),
      whole_org: Boolean(form.whole_org && form.whole_org.checked),
      require_consent: Boolean(form.require_consent && form.require_consent.checked),
    };
    fetch(this.estimateUrlValue, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.error) {
          this.countTargets.forEach((el) => { el.textContent = data.error; });
          return;
        }
        const line = `${data.sendable} will receive this (${data.matched} in the filter).`;
        this.countTargets.forEach((el) => { el.textContent = line; });
        const parts = Object.entries(data.breakdown || {}).map(([k, v]) => `${v} ${k.replace("_", " ")}`);
        const excluded = parts.length ? `Excluded: ${parts.join(", ")}.` : "";
        this.breakdownTargets.forEach((el) => { el.textContent = excluded; });
      })
      .catch(() => {});
  }

  previewAs() {
    if (!this.hasPreviewAsTarget || !this.hasTemplateInputTarget || !this.previewAsUrlValue) return;
    const templateId = this.templateInputTarget.value;
    const contactId = this.previewAsTarget.value;
    if (!templateId || !contactId) {
      this._syncPreview();
      return;
    }
    fetch(this.previewAsUrlValue, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ template_id: templateId, contact_id: contactId }),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.error || !data.html) return;
        this._showHtml(data.html);
      })
      .catch(() => {});
  }

  _renderResults(rows) {
    if (!this.hasContactResultsTarget) return;
    const list = this.contactResultsTarget;
    list.innerHTML = "";
    (rows || []).forEach((row) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "mkt-search__item";
      button.dataset.contactId = String(row.id);
      button.dataset.contactName = row.name || row.email || "Contact";
      button.dataset.action = "click->marketing-campaign-wizard#addContact";
      button.textContent = row.email ? `${row.name} · ${row.email}` : row.name;
      item.appendChild(button);
      list.appendChild(item);
    });
    list.hidden = list.children.length === 0;
  }

  _addPreviewAsOption(id, name) {
    if (!this.hasPreviewAsTarget) return;
    if ([...this.previewAsTarget.options].some((opt) => opt.value === String(id))) return;
    const option = document.createElement("option");
    option.value = String(id);
    option.textContent = name;
    this.previewAsTarget.appendChild(option);
  }

  _syncKind() {
    if (!this.hasDripTarget) return;
    const value = this._kindValue();
    this.dripTarget.hidden = value !== "drip";
    this.dripTarget.classList.toggle("hidden", value !== "drip");
    if (this.hasKindTarget) {
      this.kindTargets.forEach((el) => {
        el.classList.toggle("is-active", el.dataset.kind === value);
      });
    }
  }

  _kindValue() {
    const form = this.element.querySelector("form");
    if (!form || !form.kind) return "one_time";
    if (form.kind.value) return form.kind.value;
    const checked = form.querySelector("[name='kind']:checked");
    return checked ? checked.value : "one_time";
  }

  _syncSource(source) {
    const current = source || (
      this.hasSourceTarget
        ? (this.sourceTargets.find((el) => el.classList.contains("is-active")) || {}).dataset?.source
        : "mine"
    ) || "mine";
    if (!this.hasSourcePanelTarget) return;
    this.sourcePanelTargets.forEach((panel) => {
      const match = panel.dataset.source === current;
      panel.hidden = !match;
      panel.classList.toggle("is-current", match);
    });
  }

  _syncUseButton() {
    if (!this.hasUseButtonTarget) return;
    this.useButtonTarget.disabled = !this._previewedId;
  }

  _syncPreview() {
    if (!this.hasPreviewTarget) return;
    const selected = this.pickTargets.find((el) => (
      el.classList.contains("is-selected")
      || (this.hasTemplateInputTarget && el.dataset.templateId === this.templateInputTarget.value)
    ));
    if (selected) this._paintCover(selected);
    else this._showHtml("");
  }

  _paintCover(button) {
    const frame = button && button.querySelector("iframe");
    const html = frame ? frame.getAttribute("srcdoc") || frame.srcdoc : "";
    this._showHtml(html);
    if (this.hasPreviewMetaTarget) {
      this.previewMetaTarget.textContent = button
        ? button.dataset.templateName || ""
        : "";
    }
  }

  _showHtml(html) {
    if (!this.hasPreviewTarget) return;
    if (html) {
      this.previewTarget.srcdoc = html;
      this.previewTarget.hidden = false;
      if (this.hasPreviewEmptyTarget) this.previewEmptyTarget.hidden = true;
    } else {
      this.previewTarget.removeAttribute("srcdoc");
      this.previewTarget.hidden = true;
      if (this.hasPreviewEmptyTarget) this.previewEmptyTarget.hidden = false;
    }
  }

  _csv(value) {
    return String(value || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }
}
