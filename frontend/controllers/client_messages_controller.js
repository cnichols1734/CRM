import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static targets = ["history"];

  connect() {
    if (this.hasHistoryTarget) {
      this.historyTarget.scrollTop = this.historyTarget.scrollHeight;
    }
  }
}
