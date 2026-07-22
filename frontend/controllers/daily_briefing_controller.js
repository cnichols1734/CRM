import { Controller } from "@hotwired/stimulus";

const ACTION_ICONS = {
  call: "fa-phone",
  text: "fa-comment-sms",
  email: "fa-envelope",
  other: "fa-circle-check",
};

const PRIORITY_BADGE = {
  high: "crm-badge crm-badge-warning",
  medium: "crm-badge",
  low: "crm-badge crm-badge-info",
};

export default class extends Controller {
  static targets = [
    "layout",
    "skeleton",
    "content",
    "statusLabel",
    "skeletonLines",
    "priorities",
    "reconnect",
    "pipeline",
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

    // Update page description if present
    const desc = this.element.querySelector(".crm-page-description");
    if (desc && data.headline) desc.textContent = data.headline;

    this.renderPriorities(data.priorities || []);
    this.renderReconnect(data.reconnect || []);
    this.renderPipeline(data.pipeline_watch || []);
    this._setChatReady(true);
  }

  renderPriorities(items) {
    if (!this.hasPrioritiesTarget) return;
    if (!items.length) {
      this.prioritiesTarget.innerHTML = this._empty(
        "No priorities today",
        "You're clear on urgent CRM work. Check reconnects below."
      );
      return;
    }
    this.prioritiesTarget.innerHTML = items
      .map((item, i) => this._priorityCard(item, i))
      .join("");
  }

  renderReconnect(items) {
    if (!this.hasReconnectTarget) return;
    if (!items.length) {
      this.reconnectTarget.innerHTML = this._empty(
        "Sphere looks warm",
        "No contacts are going cold right now."
      );
      return;
    }
    this.reconnectTarget.innerHTML = items
      .map((item) => this._reconnectCard(item))
      .join("");
  }

  renderPipeline(items) {
    if (!this.hasPipelineTarget) return;
    if (!items.length) {
      this.pipelineTarget.innerHTML = this._empty(
        "Nothing to watch",
        "No active deals or commission notes need attention."
      );
      return;
    }
    this.pipelineTarget.innerHTML = items
      .map((item) => this._pipelineCard(item))
      .join("");
  }

  async toggleDone(event) {
    const btn = event.currentTarget;
    const itemId = btn.dataset.itemId;
    const currentlyDone = this.itemStates[itemId] === "done";
    const next = currentlyDone ? "" : "done";
    await this._setState(itemId, next);
    const card = btn.closest("[data-item-id]");
    if (card) card.classList.toggle("is-done", !currentlyDone);
    btn.setAttribute("aria-pressed", String(!currentlyDone));
    btn.innerHTML = !currentlyDone
      ? '<i class="fas fa-check"></i> Done'
      : '<i class="far fa-circle"></i> Mark done';
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
      btn.innerHTML = '<i class="fas fa-check"></i> Task added';
      const card = btn.closest("[data-item-id]");
      if (card) card.classList.add("is-done");
      this.itemStates[payload.item_id] = "done";
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

  _empty(title, description) {
    return `<div class="crm-empty">
      <h3 class="text-sm font-semibold text-slate-900">${this._esc(title)}</h3>
      <p class="mt-1 text-sm text-slate-500">${this._esc(description)}</p>
    </div>`;
  }

  _priorityCard(item, index) {
    const done = this.itemStates[item.id] === "done";
    const icon = ACTION_ICONS[item.action_type] || ACTION_ICONS.other;
    const badgeClass = PRIORITY_BADGE[item.priority] || "crm-badge";
    const contactLink = item.contact_id
      ? `<a href="/contact/${item.contact_id}" class="crm-briefing-link">${this._esc(
          item.contact_name || "Contact"
        )}</a>`
      : this._esc(item.contact_name || "");

    return `<article class="crm-briefing-item ${done ? "is-done" : ""}" data-item-id="${this._esc(
      item.id
    )}">
      <div class="crm-briefing-item__index">${index + 1}</div>
      <div class="crm-briefing-item__body">
        <div class="crm-briefing-item__meta">
          <span class="${badgeClass}">${this._esc(item.priority || "medium")}</span>
          <span class="crm-briefing-item__action"><i class="fas ${icon}"></i> ${this._esc(
            item.action_type || "other"
          )}</span>
          ${contactLink ? `<span class="crm-briefing-item__who">${contactLink}</span>` : ""}
        </div>
        <h3 class="crm-briefing-item__title">${this._esc(item.title)}</h3>
        <p class="crm-briefing-item__why">${this._esc(item.why)}</p>
        ${
          item.suggested_script
            ? `<blockquote class="crm-briefing-script">${this._esc(
                item.suggested_script
              )}</blockquote>`
            : ""
        }
        <div class="crm-briefing-item__actions">
          <button type="button" class="crm-btn crm-btn-sm"
                  data-action="daily-briefing#toggleDone"
                  data-item-id="${this._esc(item.id)}"
                  aria-pressed="${done}">
            ${
              done
                ? '<i class="fas fa-check"></i> Done'
                : '<i class="far fa-circle"></i> Mark done'
            }
          </button>
          ${
            item.contact_id
              ? `<button type="button" class="crm-btn crm-btn-sm crm-btn-accent"
                        data-action="daily-briefing#createTask"
                        data-item-id="${this._esc(item.id)}"
                        data-contact-id="${item.contact_id}"
                        data-subject="${this._esc(item.title)}"
                        data-description="${this._esc(item.why || "")}"
                        data-action-type="${this._esc(item.action_type || "call")}"
                        data-priority="${this._esc(item.priority || "medium")}">
                  <i class="fas fa-plus"></i> Add as task
                </button>`
              : ""
          }
          ${
            item.task_id
              ? `<a href="/tasks" class="crm-btn crm-btn-sm crm-btn-ghost">
                  <i class="fas fa-arrow-right"></i> Open tasks
                </a>`
              : ""
          }
        </div>
      </div>
    </article>`;
  }

  _reconnectCard(item) {
    const done = this.itemStates[item.id] === "done";
    const channelIcon = ACTION_ICONS[item.channel] || ACTION_ICONS.text;
    return `<article class="crm-briefing-item ${done ? "is-done" : ""}" data-item-id="${this._esc(
      item.id
    )}">
      <div class="crm-briefing-item__index"><i class="fas ${channelIcon}"></i></div>
      <div class="crm-briefing-item__body">
        <div class="crm-briefing-item__meta">
          <a href="/contact/${item.contact_id}" class="crm-briefing-link">${this._esc(
            item.contact_name
          )}</a>
          <span class="crm-badge">${item.days_since_touch}d cold</span>
        </div>
        <p class="crm-briefing-item__why">${this._esc(item.reason)}</p>
        ${
          item.suggested_message
            ? `<blockquote class="crm-briefing-script">${this._esc(
                item.suggested_message
              )}</blockquote>`
            : ""
        }
        <div class="crm-briefing-item__actions">
          <button type="button" class="crm-btn crm-btn-sm"
                  data-action="daily-briefing#copyMessage"
                  data-message="${this._esc(item.suggested_message || "")}">
            <i class="fas fa-copy"></i> <span data-label>Copy message</span>
          </button>
          <button type="button" class="crm-btn crm-btn-sm crm-btn-accent"
                  data-action="daily-briefing#createTask"
                  data-item-id="${this._esc(item.id)}"
                  data-contact-id="${item.contact_id}"
                  data-subject="Reconnect: ${this._esc(item.contact_name)}"
                  data-description="${this._esc(item.reason || "")}"
                  data-action-type="${this._esc(item.channel || "text")}"
                  data-priority="medium">
            <i class="fas fa-plus"></i> Add as task
          </button>
          <button type="button" class="crm-btn crm-btn-sm crm-btn-ghost"
                  data-action="daily-briefing#toggleDone"
                  data-item-id="${this._esc(item.id)}">
            ${
              done
                ? '<i class="fas fa-check"></i> Done'
                : '<i class="far fa-circle"></i> Mark done'
            }
          </button>
        </div>
      </div>
    </article>`;
  }

  _pipelineCard(item) {
    const amount =
      item.amount != null
        ? `$${Number(item.amount).toLocaleString()}`
        : null;
    const link = item.contact_id
      ? `<a href="/contact/${item.contact_id}" class="crm-briefing-link">${this._esc(
          item.contact_name || "Contact"
        )}</a>`
      : item.transaction_id
        ? `<a href="/transactions/${item.transaction_id}" class="crm-briefing-link">Deal #${item.transaction_id}</a>`
        : "";

    return `<article class="crm-briefing-item">
      <div class="crm-briefing-item__index"><i class="fas fa-chart-line"></i></div>
      <div class="crm-briefing-item__body">
        <div class="crm-briefing-item__meta">
          ${link}
          ${amount ? `<span class="crm-badge crm-badge-accent">${amount}</span>` : ""}
        </div>
        <h3 class="crm-briefing-item__title">${this._esc(item.title)}</h3>
        <p class="crm-briefing-item__why">${this._esc(item.insight)}</p>
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
