import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static targets = ["busy"];

  busy() {
    if (this.hasBusyTarget) this.busyTarget.hidden = false;
  }
}
