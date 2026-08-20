import { Controller } from "@hotwired/stimulus";

// srcdoc iframes inside a scaled stage inherit the app's dark color-scheme
// and often paint blank. Re-assign srcdoc after connect and lock light.
export default class extends Controller {
  static targets = ["frame"];

  connect() {
    this.paint();
  }

  paint() {
    if (!this.hasFrameTarget) return;
    const html = this.frameTarget.getAttribute("srcdoc") || "";
    if (!html.trim()) return;
    this.frameTarget.style.colorScheme = "light";
    this.frameTarget.srcdoc = html;
  }
}
