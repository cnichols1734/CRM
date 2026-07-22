import { Controller } from "@hotwired/stimulus";

// Label for the drafted script block, keyed by action type / channel.
const QUOTE_LABELS = {
  call: "Call script",
  text: "Text draft",
  email: "Email draft",
  other: "Suggested script",
};

export default class extends Controller {
  static targets = [
    "layout",
    "skeleton",
    "content",
    "statusLabel",
    "skeletonLines",
    "headline",
    "priorities",
    "prioritiesCount",
    "reconnect",
    "reconnectCount",
    "pipeline",
    "pipelineCount",
    "seed",
  ];
  static values = {
    status: String,
    pollUrl: String,
    generateUrl: String,
  };

  connect() {
    this.pollTimer = null;
    this.itemStates = {};

    if (this.hasSeedTarget) {
      try {
        const data = JSON.parse(this.seedTarget.textContent);
        this.renderBriefing(data);
      } catch (e) {
        console.error("Failed to parse briefing seed", e);
      }
    }

    if (this.statusValue === "missing" || this.statusValue === "generating") {
      this.ensureGenerating();
    } else if (this.statusValue === "failed") {
      // stay on failed UI; user can retry
    }
  }

  disconnect() {
    this.stopPolling();
  }

  async ensureGenerating() {
    this.showSkeleton("Building today's briefing…");
    try {
      const resp = await fetch(this.generateUrlValue, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ force: false }),
      });
      if (!resp.ok && resp.status !== 202) {
        this.showSkeleton("Couldn't start today's briefing", true);
        return;
      }
      const data = await resp.json();
      if (data.status === "ready") {
        this.renderBriefing(data);
        return;
      }
      this.startPolling();
    } catch (e) {
      console.error(e);
      this.showSkeleton("Couldn't start today's briefing", true);
    }
  }

  async refresh(event) {
    if (event) event.preventDefault();
    this.showSkeleton("Rebuilding today's briefing…");
    this.stopPolling();
    try {
      const resp = await fetch(this.generateUrlValue, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ force: true }),
      });
      const data = await resp.json();
      if (data.status === "ready") {
        this.renderBriefing(data);
      } else {
        this.startPolling();
      }
    } catch (e) {
      console.error(e);
      this.showSkeleton("Couldn't rebuild today's briefing", true);
    }
  }

  startPolling() {
    this.stopPolling();
    this.pollTimer = setInterval(() => this.pollOnce(), 2000);
  }

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  async pollOnce() {
    try {
      const resp = await fetch(this.pollUrlValue, {
        headers: { Accept: "application/json" },
      });
      if (resp.status === 404) return;
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.status === "ready") {
        this.stopPolling();
        this.renderBriefing(data);
      } else if (data.status === "failed") {
        this.stopPolling();
        this.showSkeleton("Couldn't build today's briefing", true);
      }
    } catch (e) {
      console.error(e);
    }
  }

  showSkeleton(label, failed = false) {
    if (this.hasSkeletonTarget) this.skeletonTarget.classList.remove("hidden");
    if (this.hasContentTarget) this.contentTarget.classList.add("hidden");
    if (this.hasStatusLabelTarget) {
      this.statusLabelTarget.textContent = label;
    }
    if (this.hasSkeletonLinesTarget) {
      this.skeletonLinesTarget.classList.toggle("opacity-40", failed);
    }
    this._setChatReady(false);
  }

  renderBriefing(data) {
    this.itemStates = data.item_states || {};
    this.statusValue = "ready";

    if (this.hasSkeletonTarget) this.skeletonTarget.classList.add("hidden");
    if (this.hasContentTarget) this.contentTarget.classList.remove("hidden");

    if (this.hasHeadlineTarget) {
      this.headlineTarget.textContent = data.headline || "";
      this.headlineTarget.classList.toggle("hidden", !data.headline);
    }

    this.renderPriorities(data.priorities || []);
    this.renderReconnect(data.reconnect || []);
    this.renderPipeline(data.pipeline_watch || []);
    this._setChatReady(true);
  }

  renderPriorities(items) {
    if (!this.hasPrioritiesTarget) return;
    this._setCount(this.hasPrioritiesCountTarget && this.prioritiesCountTarget, items.length);
    if (!items.length) {
      this.prioritiesTarget.innerHTML = this._empty(
        "Nothing urgent today. The reconnect list below is the best use of the morning."
      );
      return;
    }
    this.prioritiesTarget.innerHTML = items
      .map((item) => this._priorityRow(item))
      .join("");
  }

  renderReconnect(items) {
    if (!this.hasReconnectTarget) return;
    this._setCount(this.hasReconnectCountTarget && this.reconnectCountTarget, items.length);
    if (!items.length) {
      this.reconnectTarget.innerHTML = this._empty(
        "Sphere looks warm. No one's going cold right now."
      );
      return;
    }
    this.reconnectTarget.innerHTML = items
      .map((item) => this._reconnectRow(item))
      .join("");
  }

  renderPipeline(items) {
    if (!this.hasPipelineTarget) return;
    this._setCount(this.hasPipelineCountTarget && this.pipelineCountTarget, items.length);
    if (!items.length) {
      this.pipelineTarget.innerHTML = this._empty(
        "No active deals need attention right now."
      );
      return;
    }
    this.pipelineTarget.innerHTML = items
      .map((item) => this._pipelineRow(item))
      .join("");
  }

  async toggleDone(event) {
    const btn = event.currentTarget;
    const itemId = btn.dataset.itemId;
    const currentlyDone = this.itemStates[itemId] === "done";
    const next = currentlyDone ? "" : "done";
    await this._setState(itemId, next);
    const row = btn.closest(".crm-briefing-row");
    if (row) row.classList.toggle("is-done", !currentlyDone);
    btn.setAttribute("aria-pressed", String(!currentlyDone));
  }

  async createTask(event) {
    const btn = event.currentTarget;
    btn.disabled = true;
    const payload = {
      item_id: btn.dataset.itemId,
      contact_id: Number(btn.dataset.contactId),
      subject: btn.dataset.subject,
      description: btn.dataset.description || "",
      action_type: btn.dataset.actionType || "call",
      priority: btn.dataset.priority || "medium",
    };
    try {
      const resp = await fetch("/api/daily-briefing/create-task", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "Failed");
      btn.classList.add("is-added");
      btn.innerHTML = '<i class="fas fa-check"></i> Added to tasks';
      this.itemStates[payload.item_id] = "done";
      const row = btn.closest(".crm-briefing-row");
      if (row) {
        row.classList.add("is-done");
        const check = row.querySelector(".crm-check");
        if (check) check.setAttribute("aria-pressed", "true");
      }
    } catch (e) {
      console.error(e);
      btn.disabled = false;
      window.alert("Couldn't add that task. Try again.");
    }
  }

  async copyMessage(event) {
    const text = event.currentTarget.dataset.message || "";
    if (!text) return;
    const btn = event.currentTarget;
    const label = btn.querySelector("[data-label]");
    try {
      await navigator.clipboard.writeText(text);
      if (label) {
        const prev = label.textContent;
        label.textContent = "Copied";
        setTimeout(() => {
          label.textContent = prev;
        }, 1400);
      }
    } catch (e) {
      window.alert("Couldn't copy — select the text manually.");
    }
  }

  async _setState(itemId, state) {
    try {
      const resp = await fetch("/api/daily-briefing/item-state", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ item_id: itemId, state }),
      });
      if (resp.ok) {
        const data = await resp.json();
        this.itemStates = data.item_states || {};
      }
    } catch (e) {
      console.error(e);
    }
  }

  _setChatReady(ready) {
    const chat = this.element.querySelector('[data-controller~="briefing-chat"]');
    if (!chat) return;
    chat.dataset.briefingChatReadyValue = ready ? "true" : "false";
    const input = chat.querySelector('[data-briefing-chat-target="input"]');
    const sendBtn = chat.querySelector('[data-briefing-chat-target="sendBtn"]');
    if (input) input.disabled = !ready;
    if (sendBtn) sendBtn.disabled = !ready;
  }

  _setCount(el, count) {
    if (!el) return;
    el.textContent = count > 0 ? String(count) : "";
  }

  _empty(text) {
    return `<p class="crm-briefing-empty">${this._esc(text)}</p>`;
  }

  _check(itemId, done) {
    return `<button type="button" class="crm-check"
              data-action="daily-briefing#toggleDone"
              data-item-id="${this._esc(itemId)}"
              aria-pressed="${done}"
              aria-label="Mark done"
              title="Mark done">
        <i class="fas fa-check"></i>
      </button>`;
  }

  _quote(kind, text) {
    if (!text) return "";
    const label = QUOTE_LABELS[kind] || QUOTE_LABELS.other;
    return `<div class="crm-briefing-quote">
        <div class="crm-briefing-quote__top">
          <span class="crm-briefing-quote__label">${this._esc(label)}</span>
          <button type="button" class="crm-briefing-quote__copy"
                  data-action="daily-briefing#copyMessage"
                  data-message="${this._esc(text)}">
            <i class="far fa-copy"></i> <span data-label>Copy</span>
          </button>
        </div>
        <p class="crm-briefing-quote__text">${this._esc(text)}</p>
      </div>`;
  }

  _addTaskLink(item, subject, actionType, priority) {
    if (!item.contact_id) return "";
    return `<button type="button" class="crm-briefing-rowlink"
              data-action="daily-briefing#createTask"
              data-item-id="${this._esc(item.id)}"
              data-contact-id="${item.contact_id}"
              data-subject="${this._esc(subject)}"
              data-description="${this._esc(item.why || item.reason || "")}"
              data-action-type="${this._esc(actionType)}"
              data-priority="${this._esc(priority)}">
        <i class="fas fa-plus"></i> Add to tasks
      </button>`;
  }

  _priorityRow(item) {
    const done = this.itemStates[item.id] === "done";
    const badge =
      item.priority === "high"
        ? '<span class="crm-badge crm-badge-warning">High</span>'
        : "";
    const title = item.contact_id
      ? `<a href="/contact/${item.contact_id}">${this._esc(item.title)}</a>`
      : this._esc(item.title);
    const foot = item.task_id
      ? `<a href="/tasks" class="crm-briefing-rowlink">View in tasks <i class="fas fa-arrow-right"></i></a>`
      : this._addTaskLink(
          item,
          item.title,
          item.action_type || "call",
          item.priority || "medium"
        );

    return `<article class="crm-briefing-row ${done ? "is-done" : ""}" data-item-id="${this._esc(
      item.id
    )}">
      ${this._check(item.id, done)}
      <div class="crm-briefing-row__body">
        <div class="crm-briefing-row__top">
          <h3 class="crm-briefing-row__title">${title}</h3>
          ${badge}
        </div>
        <p class="crm-briefing-row__why">${this._esc(item.why)}</p>
        ${this._quote(item.action_type, item.suggested_script)}
        ${foot ? `<div class="crm-briefing-row__foot">${foot}</div>` : ""}
      </div>
    </article>`;
  }

  _reconnectRow(item) {
    const done = this.itemStates[item.id] === "done";
    const foot = this._addTaskLink(
      item,
      `Reconnect: ${item.contact_name}`,
      item.channel || "text",
      "medium"
    );

    return `<article class="crm-briefing-row ${done ? "is-done" : ""}" data-item-id="${this._esc(
      item.id
    )}">
      ${this._check(item.id, done)}
      <div class="crm-briefing-row__body">
        <div class="crm-briefing-row__top">
          <h3 class="crm-briefing-row__title">
            <a href="/contact/${item.contact_id}">${this._esc(item.contact_name)}</a>
          </h3>
          <span class="crm-briefing-row__aside">${item.days_since_touch}d cold</span>
        </div>
        <p class="crm-briefing-row__why">${this._esc(item.reason)}</p>
        ${this._quote(item.channel, item.suggested_message)}
        ${foot ? `<div class="crm-briefing-row__foot">${foot}</div>` : ""}
      </div>
    </article>`;
  }

  _pipelineRow(item) {
    const amount =
      item.amount != null ? `$${Number(item.amount).toLocaleString()}` : "";
    const link = item.contact_id
      ? `<a href="/contact/${item.contact_id}" class="crm-briefing-rowlink">${this._esc(
          item.contact_name || "Open contact"
        )} <i class="fas fa-arrow-right"></i></a>`
      : item.transaction_id
        ? `<a href="/transactions/${item.transaction_id}" class="crm-briefing-rowlink">Open deal <i class="fas fa-arrow-right"></i></a>`
        : "";

    return `<article class="crm-briefing-row crm-briefing-row--flat">
      <div class="crm-briefing-row__body">
        <div class="crm-briefing-row__top">
          <h3 class="crm-briefing-row__title">${this._esc(item.title)}</h3>
          ${amount ? `<span class="crm-briefing-row__aside">${amount}</span>` : ""}
        </div>
        <p class="crm-briefing-row__why">${this._esc(item.insight)}</p>
        ${link ? `<div class="crm-briefing-row__foot">${link}</div>` : ""}
      </div>
    </article>`;
  }

  _esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
}
