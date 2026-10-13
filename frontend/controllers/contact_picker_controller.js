import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static targets = ["query", "value", "results", "status"];
  static values = { url: String };

  connect() {
    this.version = 0;
    this.items = [];
    this.activeIndex = -1;
    this.validateSelection();
  }

  disconnect() {
    this.cancelSearch();
  }

  cancelSearch() {
    this.version++;
    clearTimeout(this.timer);
    this.abort?.abort();
  }

  validateSelection() {
    const needsSelection = !this.valueTarget.value &&
      (this.queryTarget.required || this.queryTarget.value.trim());
    this.queryTarget.setCustomValidity(needsSelection ? "Select a contact from the search results." : "");
  }

  search() {
    this.cancelSearch();
    this.valueTarget.value = "";
    this.validateSelection();
    this.close();
    this.items = [];
    const query = this.queryTarget.value.trim();
    this.statusTarget.textContent = query ? "Searching contacts…" : "";
    if (!query) return;
    const version = this.version;
    this.timer = setTimeout(() => this.load(query, version), 200);
  }

  async load(query, version) {
    this.abort = new AbortController();
    const url = new URL(this.urlValue, window.location.origin);
    url.searchParams.set("q", query);
    try {
      const response = await fetch(url, { signal: this.abort.signal, headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Contact search failed");
      const contacts = await response.json();
      if (version !== this.version) return;
      this.items = contacts;
      this.resultsTarget.replaceChildren();
      contacts.forEach((contact, index) => {
        const item = document.createElement("li");
        item.id = `${this.resultsTarget.id}-${index}`;
        item.dataset.index = index;
        item.dataset.contactId = contact.id;
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", "false");
        const name = document.createElement("span");
        name.textContent = contact.name || contact.email || contact.phone;
        item.append(name);
        const detail = document.createElement("small");
        detail.textContent = [contact.email, contact.phone].filter(Boolean).join(" · ");
        item.append(detail);
        this.resultsTarget.append(item);
      });
      this.resultsTarget.hidden = contacts.length === 0;
      this.queryTarget.setAttribute("aria-expanded", String(contacts.length > 0));
      this.statusTarget.textContent = contacts.length
        ? `${contacts.length} contacts found${contacts.length === 20 ? ". Keep typing to narrow results" : ""}.`
        : "No matching contacts.";
    } catch (error) {
      if (version !== this.version || error.name === "AbortError") return;
      this.statusTarget.textContent = "Could not load contacts. Type again to retry.";
    }
  }

  focus() {
    if (!this.valueTarget.value && this.queryTarget.value.trim()) this.search();
  }

  keepFocus(event) {
    event.preventDefault();
  }

  select(event) {
    const option = event.target.closest("[data-index]");
    if (option && this.resultsTarget.contains(option)) this.choose(Number(option.dataset.index));
  }

  choose(index) {
    const contact = this.items[index];
    if (!contact) return;
    this.cancelSearch();
    this.valueTarget.value = String(contact.id);
    this.queryTarget.value = contact.name || contact.email || contact.phone;
    this.validateSelection();
    this.close();
    this.statusTarget.textContent = [contact.email, contact.phone].filter(Boolean).join(" · ");
  }

  keydown(event) {
    if (event.key === "Escape" || event.key === "Tab") {
      if (event.key === "Escape") event.preventDefault();
      this.leave();
      return;
    }
    if (this.resultsTarget.hidden) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      this.activeIndex = this.activeIndex < 0
        ? (event.key === "ArrowDown" ? 0 : this.items.length - 1)
        : (this.activeIndex + (event.key === "ArrowDown" ? 1 : -1) + this.items.length) % this.items.length;
      [...this.resultsTarget.children].forEach((option, index) => {
        option.setAttribute("aria-selected", String(index === this.activeIndex));
      });
      const active = this.resultsTarget.children[this.activeIndex];
      this.queryTarget.setAttribute("aria-activedescendant", active.id);
      active.scrollIntoView({ block: "nearest" });
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (this.activeIndex >= 0) this.choose(this.activeIndex);
    }
  }

  leave(event) {
    if (event?.relatedTarget && this.element.contains(event.relatedTarget)) return;
    this.cancelSearch();
    this.close();
    if (!this.valueTarget.value) this.statusTarget.textContent = "";
  }

  close() {
    this.activeIndex = -1;
    this.resultsTarget.hidden = true;
    this.queryTarget.setAttribute("aria-expanded", "false");
    this.queryTarget.removeAttribute("aria-activedescendant");
  }
}
