import { Controller } from "@hotwired/stimulus";

const POLL_DELAY = 5000;
const REQUEST_TIMEOUT = 15000;

export default class extends Controller {
  static targets = ["threads", "conversation", "history", "composer", "body", "send", "error", "syncStatus", "newMessages"];

  connect() {
    this.alive = true;
    this.drafts = new Map();
    this.sequence = 0;
    this.writeRevision = 0;
    this.failures = 0;
    this.currentURL = this.cleanURL(window.location.href);
    this.currentKey = this.currentURL.searchParams.get("thread") || "";
    this.rememberDraft();
    this.jumpLatest();
    this.onPopState = () => this.load(window.location.href, { navigation: true });
    this.onVisibility = () => {
      clearTimeout(this.pollTimer);
      if (document.hidden) this.readAbort?.abort();
      else this.refresh();
    };
    this.onOnline = () => this.refresh();
    this.onOffline = () => this.setStatus("Offline · reconnecting", "offline");
    window.addEventListener("popstate", this.onPopState);
    window.addEventListener("online", this.onOnline);
    window.addEventListener("offline", this.onOffline);
    document.addEventListener("visibilitychange", this.onVisibility);
    this.setStatus("Connecting…", "loading");
    this.schedule(250);
  }

  disconnect() {
    this.alive = false;
    this.sequence += 1;
    clearTimeout(this.pollTimer);
    clearTimeout(this.searchTimer);
    this.readAbort?.abort();
    window.removeEventListener("popstate", this.onPopState);
    window.removeEventListener("online", this.onOnline);
    window.removeEventListener("offline", this.onOffline);
    document.removeEventListener("visibilitychange", this.onVisibility);
  }

  cleanURL(value) {
    const url = new URL(value, window.location.origin);
    url.searchParams.delete("format");
    url.searchParams.delete("mark_read");
    return url;
  }

  async navigate(event) {
    if (event.button > 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    clearTimeout(this.searchTimer);
    this.element.querySelectorAll('.messages-filter-menu[open]').forEach(menu => { menu.open = false; });
    await this.load(event.currentTarget.href, { navigation: true, push: true });
  }

  search(event) {
    event.preventDefault();
    clearTimeout(this.searchTimer);
    this.searchForm(event.currentTarget);
  }

  queueSearch(event) {
    clearTimeout(this.searchTimer);
    const form = event.target.form;
    this.searchTimer = setTimeout(() => this.searchForm(form), 300);
  }

  searchForm(form) {
    if (!form) return;
    const url = this.cleanURL(form.getAttribute("action") || this.currentURL);
    url.search = new URLSearchParams(new FormData(form)).toString();
    if (this.currentKey) url.searchParams.set("thread", this.currentKey);
    this.load(url, { navigation: true, push: true });
  }

  schedule(delay = POLL_DELAY) {
    clearTimeout(this.pollTimer);
    if (this.alive && !document.hidden && !this.sessionExpired) {
      this.pollTimer = setTimeout(() => this.refresh(), delay);
    }
  }

  refresh(event) {
    event?.preventDefault();
    if (!this.alive || document.hidden || this.sessionExpired) return;
    if (this.mutation) return this.schedule();
    return this.load(this.currentURL, { manual: Boolean(event) });
  }

  async load(value, { navigation = false, push = false, manual = false } = {}) {
    const url = this.cleanURL(value);
    if (url.origin !== window.location.origin || url.pathname !== this.currentURL.pathname) return;
    this.rememberDraft();
    this.readAbort?.abort();
    const abort = new AbortController();
    this.readAbort = abort;
    const sequence = ++this.sequence;
    const writeRevision = this.writeRevision || 0;
    clearTimeout(this.pollTimer);
    if (navigation || manual) this.setStatus("Updating…", "loading");
    try {
      const data = await this.request(url, { signal: abort.signal });
      if (!this.alive || sequence !== this.sequence) return;
      if (writeRevision !== (this.writeRevision || 0)) return await this.load(url, { navigation, push, manual });
      this.rememberDraft();
      const changedThread = this.currentKey !== (data.selected_key || "");
      this.currentURL = url;
      this.currentKey = data.selected_key || "";
      if (this.currentKey) this.currentURL.searchParams.set("thread", this.currentKey);
      else this.currentURL.searchParams.delete("thread");
      if (push && window.location.href !== this.currentURL.href) window.history.pushState({}, "", this.currentURL);
      this.applySnapshot(data, { changedThread });
      this.failures = 0;
      this.setStatus("Updates on", "connected");
    } catch (error) {
      if (!this.alive || sequence !== this.sequence || abort.signal.aborted) return;
      this.failures += 1;
      if (error.status === 401) {
        this.expireSession();
      } else if ([403, 404].includes(error.status) && url.searchParams.has("thread")) {
        const listURL = this.cleanURL(url);
        listURL.searchParams.delete("thread");
        if (url.searchParams.get("thread") === this.currentKey) {
          this.conversationTarget.replaceChildren();
          this.drafts.delete(this.currentKey);
          this.currentKey = "";
        }
        await this.load(listURL, { navigation: true, push: true });
        this.showError("This conversation is no longer available. Choose another from the inbox.");
      } else {
        this.setStatus("Connection interrupted · retrying", "offline");
        if (navigation || manual) this.showError("Couldn't load messages. Your current conversation is still here. Try again.");
      }
    } finally {
      if (sequence === this.sequence) this.schedule(Math.min(POLL_DELAY * 2 ** this.failures, 30000));
    }
  }

  async request(value, options = {}) {
    const url = this.cleanURL(value);
    url.searchParams.set("format", "json");
    const timeout = new AbortController();
    const cancel = () => timeout.abort();
    options.signal?.addEventListener("abort", cancel, { once: true });
    if (options.signal?.aborted) cancel();
    const timer = setTimeout(cancel, REQUEST_TIMEOUT);
    try {
      const response = await fetch(url, {
        ...options,
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json", ...options.headers },
        signal: timeout.signal,
      });
      if (response.redirected || response.status === 401) {
        throw Object.assign(new Error("Your session expired. Sign in again to continue."), { status: 401 });
      }
      if (!(response.headers.get("content-type") || "").includes("application/json")) {
        throw new Error("The server returned an unexpected response.");
      }
      const data = await response.json();
      if (!response.ok || data.ok === false) {
        throw Object.assign(new Error(data.error || "Couldn't update this conversation."), { status: response.status });
      }
      if (!data || Array.isArray(data) || !["threads_html", "conversation_html", "selected_key", "csrf_token"].every(key => typeof data[key] === "string")) {
        throw new Error("The server returned an incomplete conversation.");
      }
      return data;
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", cancel);
    }
  }

  applySnapshot(data, { changedThread = false, sent = false, forceContext = false } = {}) {
    if (typeof data.threads_html === "string" && data.threads_html !== this.threadsHTML) {
      const scrollTop = this.threadsTarget.scrollTop;
      const focusedKey = this.threadsTarget.contains(document.activeElement) ? document.activeElement.closest("[data-thread-key]")?.dataset.threadKey : null;
      this.threadsTarget.innerHTML = data.threads_html;
      this.threadsTarget.scrollTop = scrollTop;
      this.threadsHTML = data.threads_html;
      if (focusedKey) Array.from(this.threadsTarget.querySelectorAll("[data-thread-key]")).find(row => row.dataset.threadKey === focusedKey)?.focus({ preventScroll: true });
    }
    if (typeof data.conversation_html === "string") {
      if (changedThread || !this.conversationTarget.querySelector("[data-thread-key]")) {
        this.conversationTarget.innerHTML = data.conversation_html;
        this.element.classList.remove("is-details-open");
        this.restoreDraft();
        this.jumpLatest();
      } else {
        const template = document.createElement("template");
        template.innerHTML = data.conversation_html;
        for (const name of ["header", "context", "assignment"]) {
          const selector = `[data-client-messages-part="${name}"]`;
          const before = this.conversationTarget.querySelector(selector);
          const after = template.content.querySelector(selector);
          if (before && after && before.outerHTML !== after.outerHTML && (forceContext || !before.contains(document.activeElement))) {
            const openIds = Array.from(before.querySelectorAll("details[open]")).map(el => el.id);
            before.replaceWith(after.cloneNode(true));
            for (const id of openIds) if (id) this.conversationTarget.querySelector(`#${CSS.escape(id)}`)?.setAttribute("open", "");
          }
        }
        this.patchHistory(template.content, sent);
        const before = this.conversationTarget.querySelector('[data-client-messages-target~="composer"]');
        const after = template.content.querySelector('[data-client-messages-target~="composer"]');
        if (before && after && before.dataset.canReply !== after.dataset.canReply) {
          before.replaceWith(after.cloneNode(true));
          this.restoreDraft();
        }
      }
    }
    this.element.classList.toggle("has-conversation", Boolean(this.currentKey));
    this.element.querySelectorAll('[name="csrf_token"]').forEach(input => { input.value = data.csrf_token || input.value; });
    this.element.querySelectorAll("[data-client-messages-attention-count]").forEach(el => {
      el.textContent = data.attention_count || "";
      el.hidden = !data.attention_count;
    });
    this.element.querySelectorAll('[aria-label="Filter conversations"] a').forEach(link => {
      const active = (new URL(link.href).searchParams.get("view") || "all") === (data.view || "all");
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
      const next = new URL(link.href);
      if (data.search) next.searchParams.set("q", data.search);
      else next.searchParams.delete("q");
      link.href = next;
    });
    const search = this.element.querySelector('input[name="q"]');
    if (search && search !== document.activeElement) search.value = data.search || "";
    const view = this.element.querySelector('form[role="search"] input[name="view"]');
    if (view) view.value = data.view || "all";
    const filterSummary = this.element.querySelector('.messages-filter-menu summary');
    if (filterSummary) {
      const filtered = ["attention", "deal"].includes(data.view);
      filterSummary.classList.toggle("is-active", filtered);
      const label = filterSummary.querySelector("span");
      if (label) label.textContent = data.view === "attention" ? "Needs reply" : data.view === "deal" ? "Transactions" : "Filter";
    }
    this.updateDetailsToggle();
  }

  patchHistory(fragment, sent) {
    const history = this.conversationTarget.querySelector('[data-client-messages-target~="history"]');
    const next = fragment.querySelector('[data-client-messages-target~="history"]');
    if (!history || !next || history.innerHTML === next.innerHTML) return;
    const selection = window.getSelection();
    if (!sent && selection && !selection.isCollapsed && history.contains(selection.anchorNode)) return;
    const wasAtBottom = this.atBottom(history);
    const top = history.scrollTop;
    const ids = new Set(Array.from(history.querySelectorAll("[data-message-id]")).map(el => el.dataset.messageId));
    const hasNew = Array.from(next.querySelectorAll("[data-message-id]")).some(el => !ids.has(el.dataset.messageId));
    const anchor = Array.from(history.querySelectorAll("[data-message-id]")).find(el => el.getBoundingClientRect().bottom > history.getBoundingClientRect().top);
    const anchorId = anchor?.dataset.messageId;
    const anchorTop = anchor?.getBoundingClientRect().top;
    history.innerHTML = next.innerHTML;
    if (sent || wasAtBottom) this.jumpLatest();
    else {
      history.scrollTop = top;
      const restored = Array.from(history.querySelectorAll("[data-message-id]")).find(el => el.dataset.messageId === anchorId);
      if (restored) history.scrollTop += restored.getBoundingClientRect().top - anchorTop;
      const button = this.conversationTarget.querySelector('[data-client-messages-target~="newMessages"]');
      if (hasNew && button) button.hidden = false;
    }
  }

  atBottom(history) { return history.scrollHeight - history.scrollTop - history.clientHeight < 80; }

  historyScrolled() {
    if (this.hasHistoryTarget && this.atBottom(this.historyTarget) && this.hasNewMessagesTarget) this.newMessagesTarget.hidden = true;
  }

  jumpLatest(event) {
    event?.preventDefault();
    const history = this.conversationTarget?.querySelector('[data-client-messages-target~="history"]');
    if (history) history.scrollTop = history.scrollHeight;
    const button = this.conversationTarget?.querySelector('[data-client-messages-target~="newMessages"]');
    if (button) button.hidden = true;
  }

  rememberDraft() {
    if (!this.currentKey || !this.hasBodyTarget) return;
    const prior = this.drafts.get(this.currentKey);
    const body = this.bodyTarget.value;
    const requestId = prior?.body === body ? prior.requestId : crypto.randomUUID();
    this.drafts.set(this.currentKey, { body, requestId: requestId || crypto.randomUUID() });
  }

  restoreDraft() {
    const body = this.conversationTarget.querySelector('[data-client-messages-target~="body"]');
    if (!body) return;
    const draft = this.drafts.get(this.currentKey);
    if (draft) body.value = draft.body;
    this.resizeBody(body);
  }

  draftChanged() {
    this.rememberDraft();
    if (this.hasBodyTarget) this.resizeBody(this.bodyTarget);
    this.showError("");
  }

  resizeBody(body) {
    body.style.height = "auto";
    body.style.height = `${Math.min(Math.max(body.scrollHeight, 48), 180)}px`;
  }

  composerKeydown(event) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      event.target.form?.requestSubmit();
    }
  }

  async submit(event) {
    event.preventDefault();
    if (this.mutation || this.sessionExpired) return;
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    this.rememberDraft();
    const data = new FormData(form);
    const key = this.cleanURL(form.getAttribute("action") || this.currentURL).searchParams.get("thread");
    const reply = data.has("body");
    const body = String(data.get("body") || "");
    if (reply && !body.trim()) return this.showError("Write a message before sending.");
    if (reply) data.set("request_id", this.drafts.get(key)?.requestId || crypto.randomUUID());
    const url = this.cleanURL(this.currentURL);
    url.searchParams.set("thread", key);
    const submittedURL = this.currentURL.href;
    this.mutation = true;
    this.readAbort?.abort();
    this.sequence += 1;
    clearTimeout(this.pollTimer);
    this.showError("");
    const buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
    buttons.forEach(button => { button.disabled = true; button.setAttribute("aria-busy", "true"); });
    form.setAttribute("aria-busy", "true");
    this.setStatus(reply ? "Sending…" : "Updating assignment…", "loading");
    try {
      const snapshot = await this.request(url, { method: "POST", body: data });
      if (!this.alive) return;
      this.writeRevision = (this.writeRevision || 0) + 1;
      if (reply) {
        if (key === this.currentKey) this.rememberDraft();
        if (this.drafts.get(key)?.body === body) {
          this.drafts.delete(key);
          if (key === this.currentKey && this.hasBodyTarget) { this.bodyTarget.value = ""; this.resizeBody(this.bodyTarget); }
        }
      }
      if (this.currentURL.href === submittedURL) {
        if (snapshot.url) {
          const canonical = this.cleanURL(snapshot.url);
          if (canonical.origin === window.location.origin && canonical.pathname === this.currentURL.pathname) {
            this.currentURL = canonical;
            window.history.replaceState({}, "", canonical);
          }
        }
        this.applySnapshot(snapshot, { sent: reply, forceContext: true });
      }
      this.setStatus(reply ? "Message sent" : "Assignment updated", "connected");
    } catch (error) {
      if (!this.alive) return;
      if (error.status === 401) this.expireSession();
      else {
        const message = error.status ? error.message : (reply ? "Couldn't confirm sending. Your reply is kept. Try again." : "Couldn't update the assignment. Try again.");
        this.showError(message);
        this.setStatus("Action not confirmed", "offline");
      }
    } finally {
      this.mutation = false;
      buttons.forEach(button => { button.disabled = Boolean(this.sessionExpired); button.removeAttribute("aria-busy"); });
      form.removeAttribute("aria-busy");
      this.schedule();
    }
  }

  showError(message) {
    if (!this.hasErrorTarget) return;
    this.errorTarget.textContent = message;
    this.errorTarget.hidden = !message;
  }

  setStatus(message, state) {
    if (this.hasSyncStatusTarget) {
      this.syncStatusTarget.textContent = message;
      this.syncStatusTarget.dataset.state = state;
    }
  }

  expireSession() {
    this.sessionExpired = true;
    this.setStatus("Sign in again", "offline");
    this.showError("Your session expired. Reload this page to sign in again. Your unsent text is still here.");
    this.element.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; });
  }

  toggleDetails() {
    this.element.classList.toggle("is-details-open");
    this.updateDetailsToggle();
  }

  updateDetailsToggle() {
    const open = this.element.classList.contains("is-details-open");
    this.element.querySelectorAll("[data-client-messages-details-toggle]").forEach(button => button.setAttribute("aria-expanded", String(open)));
  }
}
