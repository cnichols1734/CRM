// Native form navigation replaces this state with the result or server error.
export class FormSubmission {
  constructor(content, indicator) {
    this.content = content;
    this.indicator = indicator;
    this.pending = false;
    this.restore = () => this.reset();
    window.addEventListener("pageshow", this.restore);
  }

  start(event) {
    if (this.pending) {
      event.preventDefault();
      return;
    }
    if (event.defaultPrevented) return;

    this.pending = true;
    this.wasInert = this.content.inert;
    this.buttons = [...this.content.querySelectorAll('button[type="submit"], input[type="submit"]')]
      .filter((button) => !button.disabled);
    this.buttons.forEach((button) => { button.disabled = true; });
    // Inert prevents edits while keeping all fields in the native POST body.
    this.content.inert = true;
    this.content.setAttribute("aria-busy", "true");
    this.indicator.hidden = false;
  }

  reset() {
    if (!this.pending) return;
    this.buttons.forEach((button) => { button.disabled = false; });
    this.content.inert = this.wasInert;
    this.content.removeAttribute("aria-busy");
    this.indicator.hidden = true;
    this.pending = false;
  }

  disconnect() {
    window.removeEventListener("pageshow", this.restore);
    this.reset();
  }
}
