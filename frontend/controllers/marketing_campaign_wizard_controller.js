import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static values = { estimateUrl: String };
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
  ];

  connect() {
    this.estimate();
    this._syncKind();
    this._syncPreview();
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

  pick(event) {
    const button = event.currentTarget;
    const id = button.dataset.templateId;
    if (this.hasTemplateInputTarget) this.templateInputTarget.value = id;
    this.pickTargets.forEach((el) => {
      const on = el === button;
      el.classList.toggle("is-selected", on);
      el.setAttribute("aria-pressed", on ? "true" : "false");
    });
    this._syncPreview();
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

  estimate() {
    const form = this.element.querySelector("form");
    if (!form || !this.estimateUrlValue) return;
    const groups = [
      ...form.querySelectorAll("select[name='groups'] option:checked"),
      ...form.querySelectorAll("input[name='groups']:checked"),
    ].map((el) => Number(el.value));
    const body = {
      groups,
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

  _syncPreview() {
    if (!this.hasPreviewTarget) return;
    const selected = this.pickTargets.find((el) => el.classList.contains("is-selected"));
    const frame = selected && selected.querySelector("iframe");
    const html = frame ? frame.getAttribute("srcdoc") || frame.srcdoc : "";
    if (html) {
      this.previewTarget.srcdoc = html;
      this.previewTarget.hidden = false;
      if (this.hasPreviewEmptyTarget) this.previewEmptyTarget.hidden = true;
    } else {
      this.previewTarget.removeAttribute("srcdoc");
      this.previewTarget.hidden = true;
      if (this.hasPreviewEmptyTarget) this.previewEmptyTarget.hidden = false;
    }
    if (this.hasPreviewMetaTarget) {
      this.previewMetaTarget.textContent = selected
        ? selected.dataset.templateName || ""
        : "";
    }
  }

  _csv(value) {
    return String(value || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }
}
