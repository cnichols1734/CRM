import { Controller } from "@hotwired/stimulus";

const COPIED_MS = 1400;

export default class extends Controller {
  static values = { text: String };
  static targets = ["icon"];

  connect() {
    this._defaultLabel = this.element.getAttribute("aria-label") || "Copy";
  }

  disconnect() {
    clearTimeout(this._resetTimer);
  }

  async copy() {
    const text = (this.textValue || "").trim();
    if (!text) return;

    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        this._legacyCopy(text);
      }
      this._markCopied();
    } catch (err) {
      try {
        this._legacyCopy(text);
        this._markCopied();
      } catch (legacyErr) {
        return;
      }
    }
  }

  _legacyCopy(text) {
    const field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.left = "-9999px";
    document.body.appendChild(field);
    field.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(field);
    if (!ok) throw new Error("copy failed");
  }

  _markCopied() {
    clearTimeout(this._resetTimer);
    this.element.classList.add("is-copied");
    this.element.setAttribute("aria-label", "Copied");
    this.element.setAttribute("title", "Copied");
    if (this.hasIconTarget) {
      this.iconTarget.className = "fas fa-check";
    }
    this._resetTimer = setTimeout(() => this._reset(), COPIED_MS);
  }

  _reset() {
    this.element.classList.remove("is-copied");
    this.element.setAttribute("aria-label", this._defaultLabel);
    this.element.setAttribute("title", "Copy");
    if (this.hasIconTarget) {
      this.iconTarget.className = "far fa-copy";
    }
  }
}
