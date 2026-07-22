import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static targets = ["messages", "empty", "chips", "form", "input", "sendBtn"];
  static values = {
    streamUrl: String,
    ready: Boolean,
  };

  connect() {
    this.streaming = false;
  }

  useChip(event) {
    event.preventDefault();
    const prompt = event.currentTarget.dataset.prompt;
    if (!prompt || !this.hasInputTarget) return;
    this.inputTarget.value = prompt;
    this.inputTarget.focus();
    this.send(event);
  }

  async send(event) {
    if (event) event.preventDefault();
    if (this.streaming) return;
    if (!this.readyValue) return;

    const message = (this.inputTarget.value || "").trim();
    if (!message) return;

    this.inputTarget.value = "";
    this._hideEmpty();
    this._appendMessage("user", message);
    this.streaming = true;
    this.sendBtnTarget.disabled = true;

    const assistantEl = this._appendMessage("assistant", "", true);

    try {
      const resp = await fetch(this.streamUrlValue, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify({ message }),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        assistantEl.innerHTML = this._esc(
          err.error || "Couldn't reach B.O.B. Try again."
        );
        assistantEl.classList.remove("is-streaming");
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let full = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const part of parts) {
          const line = part
            .split("\n")
            .filter((l) => l.startsWith("data: "))
            .map((l) => l.slice(6))
            .join("\n");
          if (!line) continue;
          if (line === "[DONE]") continue;
          if (line.startsWith("[FULL_RESPONSE]") && line.endsWith("[/FULL_RESPONSE]")) {
            full = line.slice("[FULL_RESPONSE]".length, -"[/FULL_RESPONSE]".length);
            continue;
          }
          const chunk = line.replace(/\\n/g, "\n");
          full += chunk;
          this._renderMarkdown(assistantEl, full);
        }
      }

      this._renderMarkdown(assistantEl, full || "…");
      assistantEl.classList.remove("is-streaming");
    } catch (e) {
      console.error(e);
      assistantEl.textContent = "Something went wrong. Please try again.";
      assistantEl.classList.remove("is-streaming");
    } finally {
      this.streaming = false;
      this.sendBtnTarget.disabled = !this.readyValue;
      this._scrollToBottom();
    }
  }

  readyValueChanged(ready) {
    if (this.hasInputTarget) this.inputTarget.disabled = !ready;
    if (this.hasSendBtnTarget) this.sendBtnTarget.disabled = !ready || this.streaming;
  }

  _hideEmpty() {
    if (this.hasEmptyTarget) this.emptyTarget.classList.add("hidden");
  }

  _appendMessage(role, text, streaming = false) {
    const el = document.createElement("div");
    el.className = `crm-briefing-msg crm-briefing-msg--${role}${
      streaming ? " is-streaming" : ""
    }`;
    if (role === "user") {
      el.textContent = text;
    } else {
      this._renderMarkdown(el, text || "…");
    }
    this.messagesTarget.appendChild(el);
    this._scrollToBottom();
    return el;
  }

  _renderMarkdown(el, text) {
    if (window.marked && window.DOMPurify) {
      el.innerHTML = window.DOMPurify.sanitize(window.marked.parse(text || ""));
    } else {
      el.textContent = text || "";
    }
  }

  _scrollToBottom() {
    if (!this.hasMessagesTarget) return;
    this.messagesTarget.scrollTop = this.messagesTarget.scrollHeight;
  }

  _esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
}
