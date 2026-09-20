import { Controller } from "@hotwired/stimulus";
import { FormSubmission } from "../form_submission";

export default class extends Controller {
  static targets = ["busy", "content"];

  connect() {
    this.submission = new FormSubmission(this.contentTarget, this.busyTarget);
  }

  disconnect() {
    this.submission.disconnect();
  }

  busy(event) {
    this.submission.start(event);
  }
}
