import { Controller } from "@hotwired/stimulus";
import { FormSubmission } from "../form_submission";

export default class extends Controller {
  static targets = ["busy", "content", "category", "collection", "search", "ai", "prompt"];

  connect() {
    this.submission = new FormSubmission(this.contentTarget, this.busyTarget);
    const category = new URL(window.location.href).searchParams.get("collection");
    this.selectedCategory = this.categoryTargets.some(button => button.dataset.category === category) ? category : "base-templates";
    this.filter();
  }

  disconnect() {
    this.submission.disconnect();
  }

  chooseCategory(event) {
    this.selectedCategory = event.currentTarget.dataset.category;
    const url = new URL(window.location.href);
    url.searchParams.set("collection", this.selectedCategory);
    window.history.replaceState(null, "", url);
    this.filter();
  }

  filter() {
    const query = this.searchTarget.value.trim().toLowerCase();
    this.categoryTargets.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.category === this.selectedCategory)));
    this.collectionTargets.forEach(section => {
      section.hidden = section.dataset.category !== this.selectedCategory;
      const cards = [...section.querySelectorAll("[data-template-name]")];
      cards.forEach(card => { card.hidden = !card.dataset.templateName.includes(query); });
      section.querySelector("[data-template-empty]").hidden = !query || cards.some(card => !card.hidden);
    });
  }

  openAi() {
    if (!this.hasAiTarget) return;
    this.aiTarget.hidden = false;
    this.aiTarget.open = true;
    if (this.hasPromptTarget) this.promptTarget.focus();
  }

  busy(event) {
    this.submission.start(event);
  }
}
