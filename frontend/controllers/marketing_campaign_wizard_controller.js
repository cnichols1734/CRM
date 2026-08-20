import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static values = { estimateUrl: String };
  static targets = ["count", "breakdown", "drip"];

  connect() {
    this.estimate();
  }

  kind(event) {
    if (!this.hasDripTarget) return;
    this.dripTarget.classList.toggle("hidden", event.target.value !== "drip");
  }

  estimate() {
    const form = this.element.querySelector("form");
    if (!form || !this.estimateUrlValue) return;
    const body = {
      groups: [...form.querySelectorAll("select[name='groups'] option:checked")].map((o) => Number(o.value)),
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
          this.countTarget.textContent = data.error;
          return;
        }
        this.countTarget.textContent = `${data.sendable} will receive this (${data.matched} in the filter).`;
        const parts = Object.entries(data.breakdown || {}).map(([k, v]) => `${v} ${k.replace("_", " ")}`);
        this.breakdownTarget.textContent = parts.length ? `Excluded: ${parts.join(", ")}.` : "";
      })
      .catch(() => {});
  }

  _csv(value) {
    return String(value || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }
}
