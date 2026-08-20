import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static values = { previewUrl: String, uploadUrl: String, testUrl: String };
  static targets = [
    "frame", "subject", "preheader", "blocksField", "fileInput",
    "uploadStatus", "imageList", "keepImages", "busy", "sample",
    "filledSubject", "testTo", "testButton", "testStatus",
    "links", "linkList", "linkStatus", "saveForm", "sampleToggle",
  ];

  connect() {
    this.lastFocus = null;
    this.lastInput = null;
    this.iframeRange = null;
    this.renderImageList();
    this.syncKeptImages();
    this.renderLinks();
    this.highlightUsedSamples();
    this.updateFilledSubject("");
    if (this.hasFrameTarget) {
      this.frameTarget.addEventListener("load", () => this.bindEditors());
      this.bindEditors();
    }
  }

  busy() {
    if (this.hasBusyTarget) this.busyTarget.hidden = false;
  }

  desktop(event) {
    this.frameTarget.classList.remove("is-phone");
    this.markSegment(event.currentTarget);
  }

  mobile(event) {
    this.frameTarget.classList.add("is-phone");
    this.markSegment(event.currentTarget);
  }

  markSegment(button) {
    const group = button.closest(".crm-segment");
    if (!group) return;
    group.querySelectorAll(".crm-segment__item").forEach((item) => {
      item.classList.toggle("is-active", item === button);
    });
  }

  rememberInput(event) {
    this.lastFocus = "input";
    this.lastInput = event.currentTarget;
    this.highlightUsedSamples();
    if (this.samplePreviewOn()) this.samplesChanged();
  }

  samplesChanged() {
    if (!this.samplePreviewOn()) return;
    clearTimeout(this.sampleTimer);
    this.sampleTimer = setTimeout(() => this.preview(), 280);
  }

  toggleSamples() {
    this.preview();
  }

  upload() {
    if (!this.uploadUrlValue || !this.hasFileInputTarget) return;
    const file = this.fileInputTarget.files && this.fileInputTarget.files[0];
    if (!file) return;
    this.setStatus("Uploading…");
    const body = new FormData();
    body.append("file", file);
    const headers = { Accept: "application/json" };
    const csrf = this.csrfToken();
    if (csrf) headers["X-CSRFToken"] = csrf;
    fetch(this.uploadUrlValue, { method: "POST", headers, body })
      .then(async (r) => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok || data.error) {
          throw new Error(data.error || "Could not upload that photo.");
        }
        return data;
      })
      .then((data) => {
        const blocks = this.readBlocks();
        this.insertImage(blocks, {
          type: "image",
          image_url: data.url,
          alt: file.name.replace(/\.[^.]+$/, "") || "Photo",
        });
        this.writeBlocks(blocks);
        this.fileInputTarget.value = "";
        this.setStatus("Photo added to the email. Save the template to keep it.");
        this.renderImageList();
        this.syncKeptImages();
        this.renderLinks();
        this.preview();
      })
      .catch((err) => {
        this.setStatus(err.message || "Could not upload that photo.");
      });
  }

  removeImage(event) {
    const index = Number(event.currentTarget.dataset.index);
    const blocks = this.readBlocks();
    let seen = -1;
    const next = blocks.filter((block) => {
      if (block.type !== "image") return true;
      seen += 1;
      return seen !== index;
    });
    this.writeBlocks(next);
    this.setStatus("Photo removed. Save the template to keep the change.");
    this.renderImageList();
    this.syncKeptImages();
    this.renderLinks();
    this.preview();
  }

  insertMerge(event) {
    // Mouse already inserted on mousedown so the iframe caret is still alive.
    if (event.type === "click" && event.detail > 0) return;
    event.preventDefault();
    const key = event.currentTarget.dataset.key;
    const fallback = event.currentTarget.dataset.fallback || "";
    const label = event.currentTarget.dataset.label || key;
    const token = fallback ? `{{${key}|${fallback}}}` : `{{${key}}}`;
    const visible = this.samplePreviewOn()
      ? (this.sampleValue(key) || this.samplePlaceholder(key) || label)
      : label;

    if (this.lastFocus === "input" && this.lastInput && document.contains(this.lastInput)) {
      this.insertInInput(this.lastInput, token);
      this.highlightUsedSamples();
      if (this.samplePreviewOn()) this.preview();
      return;
    }
    if (this.insertInFrame(key, fallback, token, visible)) {
      this.highlightUsedSamples();
      return;
    }
    if (this.hasSubjectTarget) {
      this.insertInInput(this.subjectTarget, token);
      this.highlightUsedSamples();
      if (this.samplePreviewOn()) this.preview();
    }
  }

  insertInInput(input, token) {
    input.focus();
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? start;
    const next = `${input.value.slice(0, start)}${token}${input.value.slice(end)}`;
    input.value = next;
    const pos = start + token.length;
    input.setSelectionRange(pos, pos);
    this.lastFocus = "input";
    this.lastInput = input;
  }

  insertInFrame(key, fallback, token, sample) {
    if (!this.hasFrameTarget) return false;
    const doc = this.frameTarget.contentDocument;
    if (!doc || !doc.body) return false;
    const sel = doc.getSelection();
    let range = (sel && sel.rangeCount && sel.getRangeAt(0)) || this.iframeRange;
    const editable = this.editableFromRange(doc, range);
    if (!editable) return false;
    if (!range || !this.rangeInside(editable, range)) {
      range = doc.createRange();
      range.selectNodeContents(editable);
      range.collapse(false);
    }
    const chipAtCaret = this.mergeChipFromNode(range.startContainer);
    if (chipAtCaret) {
      range = doc.createRange();
      range.setStartAfter(chipAtCaret);
      range.collapse(true);
    }
    const chip = doc.createElement("span");
    chip.contentEditable = "false";
    chip.setAttribute("data-mkt-merge", key);
    if (fallback) chip.setAttribute("data-mkt-fallback", fallback);
    if (this.samplePreviewOn()) {
      chip.setAttribute("data-mkt-filled", "1");
    } else {
      chip.setAttribute(
        "style",
        "background-color:#fff7ed;color:#c2410c;border:1px solid #fdba74;border-radius:4px;padding:1px 6px;font-weight:700;white-space:nowrap",
      );
    }
    chip.textContent = sample;
    range.insertNode(chip);
    const after = doc.createRange();
    after.setStartAfter(chip);
    after.collapse(true);
    if (sel) {
      sel.removeAllRanges();
      sel.addRange(after);
    }
    this.iframeRange = after.cloneRange();
    this.lastFocus = "iframe";
    this.captureEdit(editable);
    return true;
  }

  editableFromRange(doc, range) {
    let node = range && range.startContainer;
    if (node && node.nodeType === Node.TEXT_NODE) node = node.parentElement;
    const hit = node && node.closest && node.closest("[data-mkt-edit]");
    if (hit) return hit;
    return this.lastEditable || doc.querySelector("[data-mkt-edit]");
  }

  mergeChipFromNode(node) {
    if (!node) return null;
    const el = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    return el && el.closest ? el.closest("[data-mkt-merge]") : null;
  }

  rangeInside(editable, range) {
    if (!editable || !range) return false;
    try {
      const node = range.startContainer;
      return node === editable || editable.contains(node);
    } catch {
      return false;
    }
  }

  preview() {
    if (!this.previewUrlValue || !this.hasBlocksFieldTarget) return;
    const blocks = this.readBlocks();
    if (blocks === null) return;
    const headers = { "Content-Type": "application/json", Accept: "application/json" };
    const csrf = this.csrfToken();
    if (csrf) headers["X-CSRFToken"] = csrf;
    fetch(this.previewUrlValue, {
      method: "POST",
      headers,
      body: JSON.stringify({
        subject: this.hasSubjectTarget ? this.subjectTarget.value : "",
        preheader: this.hasPreheaderTarget ? this.preheaderTarget.value : "",
        blocks,
        samples: this.readSamples(),
        fill_samples: this.samplePreviewOn(),
      }),
    })
      .then(async (r) => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok || data.error) {
          throw new Error(data.error || "Could not refresh the preview.");
        }
        return data;
      })
      .then((data) => {
        if (data.html && this.hasFrameTarget) {
          this.frameTarget.srcdoc = data.html;
        }
        this.updateFilledSubject(this.samplePreviewOn() ? (data.subject || "") : "");
        this.highlightUsedSamples(data.used_keys);
      })
      .catch((err) => {
        if (err && err.message) this.setStatus(err.message);
      });
  }

  sendTest() {
    if (!this.testUrlValue || !this.hasBlocksFieldTarget) return;
    const blocks = this.readBlocks();
    if (blocks === null) return;
    this.setTestStatus("Sending…");
    if (this.hasTestButtonTarget) this.testButtonTarget.disabled = true;
    const headers = { "Content-Type": "application/json", Accept: "application/json" };
    const csrf = this.csrfToken();
    if (csrf) headers["X-CSRFToken"] = csrf;
    fetch(this.testUrlValue, {
      method: "POST",
      headers,
      body: JSON.stringify({
        subject: this.hasSubjectTarget ? this.subjectTarget.value : "",
        preheader: this.hasPreheaderTarget ? this.preheaderTarget.value : "",
        blocks,
        samples: this.readSamples(),
        to: this.hasTestToTarget ? this.testToTarget.value : "",
      }),
    })
      .then(async (r) => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok || data.error) {
          throw new Error(data.error || "Could not send the test email.");
        }
        return data;
      })
      .then((data) => {
        const sent = Array.isArray(data.sent) ? data.sent : [];
        if (sent.length === 1) {
          this.setTestStatus(`Sent to ${sent[0]}.`);
        } else {
          this.setTestStatus(`Sent to ${sent.length} addresses.`);
        }
      })
      .catch((err) => {
        this.setTestStatus(err.message || "Could not send the test email.");
      })
      .finally(() => {
        if (this.hasTestButtonTarget) this.testButtonTarget.disabled = false;
      });
  }

  bindEditors() {
    if (!this.hasFrameTarget) return;
    const doc = this.frameTarget.contentDocument;
    if (!doc || !doc.body) return;
    doc.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", (event) => event.preventDefault());
    });
    doc.addEventListener("selectionchange", () => this.rememberIframeSelection());
    doc.addEventListener("mousedown", () => {
      this.lastFocus = "iframe";
    });
    doc.querySelectorAll("[data-mkt-edit]").forEach((el) => {
      el.addEventListener("input", () => this.captureEdit(el));
      el.addEventListener("blur", () => this.captureEdit(el));
      el.addEventListener("keyup", () => this.rememberIframeSelection());
      el.addEventListener("mouseup", () => this.rememberIframeSelection());
    });
  }

  rememberIframeSelection() {
    if (!this.hasFrameTarget) return;
    const doc = this.frameTarget.contentDocument;
    if (!doc) return;
    const sel = doc.getSelection();
    if (!sel || !sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    const editable = this.editableFromRange(doc, range);
    if (!editable) return;
    this.iframeRange = range.cloneRange();
    this.lastEditable = editable;
    this.lastFocus = "iframe";
  }

  captureEdit(el) {
    const blocks = this.readBlocks();
    if (!blocks) return;
    const index = Number(el.dataset.mktBlock);
    const field = el.dataset.mktField;
    const item = el.dataset.mktItem;
    const key = el.dataset.mktKey;
    const value = this.editValue(el);
    if (!blocks[index]) return;
    if (field === "items" && item != null) {
      blocks[index].items = blocks[index].items || [];
      blocks[index].items[Number(item)] = value;
    } else if (field === "steps" && item != null) {
      const steps = blocks[index].steps || [];
      if (steps[Number(item)]) {
        steps[Number(item)][key || "title"] = value;
      }
      blocks[index].steps = steps;
    } else if (field === "stats" && item != null) {
      const stats = blocks[index].stats || [];
      if (stats[Number(item)]) {
        stats[Number(item)][key || "value"] = value;
      }
      blocks[index].stats = stats;
    } else {
      blocks[index][field] = value;
    }
    this.writeBlocks(blocks);
    this.highlightUsedSamples();
    if (blocks[index].type === "button" && field === "label") {
      this.renderLinks();
    }
  }

  guardSave(event) {
    const message = this.missingButtonUrl();
    if (!message) return;
    event.preventDefault();
    this.setLinkStatus(message);
    if (this.hasLinksTarget) {
      this.linksTarget.hidden = false;
      this.linksTarget.scrollIntoView({ block: "nearest" });
    }
  }

  linkChanged(event) {
    const input = event.currentTarget;
    const index = Number(input.dataset.index);
    const field = input.dataset.field || "url";
    const blocks = this.readBlocks();
    if (!blocks || !blocks[index]) return;
    blocks[index][field] = input.value.trim();
    this.writeBlocks(blocks);
    this.setLinkStatus("");
    clearTimeout(this.linkTimer);
    this.linkTimer = setTimeout(() => this.preview(), 280);
  }

  renderLinks() {
    if (!this.hasLinksTarget || !this.hasLinkListTarget) return;
    const blocks = this.readBlocks() || [];
    const rows = [];
    blocks.forEach((block, index) => {
      if (block.type === "button") {
        rows.push({
          index,
          field: "url",
          caption: `"${block.label || "Button"}" button`,
          url: block.url || "",
        });
      }
      if (block.type === "listing_card") {
        rows.push({
          index,
          field: "url",
          caption: "Listing card link",
          url: block.url || "",
        });
      }
    });
    this.linksTarget.hidden = rows.length === 0;
    this.linkListTarget.innerHTML = rows.map((row) => `
      <label class="mkt-link-row">
        <span>${this.escapeHtml(row.caption)}</span>
        <input class="crm-input" type="text"
               inputmode="url"
               data-index="${row.index}" data-field="${row.field}"
               value="${this.escapeAttr(row.url)}"
               placeholder="https://"
               autocomplete="off"
               data-action="input->marketing-template-studio#linkChanged">
      </label>
    `).join("");
  }

  missingButtonUrl() {
    const blocks = this.readBlocks() || [];
    for (const block of blocks) {
      if (block.type !== "button") continue;
      const url = String(block.url || "").trim();
      const label = String(block.label || "button").trim() || "button";
      if (!this.isRealUrl(url)) {
        return `Add a URL for the "${label}" button before saving.`;
      }
    }
    return "";
  }

  isRealUrl(value) {
    const url = String(value || "").trim().toLowerCase();
    return url.startsWith("http://") || url.startsWith("https://")
      || url.startsWith("mailto:") || url.startsWith("tel:");
  }

  setLinkStatus(message) {
    if (!this.hasLinkStatusTarget) return;
    this.linkStatusTarget.hidden = !message;
    this.linkStatusTarget.textContent = message || "";
  }

  editValue(el) {
    if (el.dataset.mktField === "text" && el.tagName === "DIV") {
      const paras = [...el.querySelectorAll(":scope > p")];
      if (paras.length) {
        return paras
          .map((p) => this.serializeNode(p).replace(/\n/g, " ").trim())
          .filter(Boolean)
          .join("\n\n");
      }
    }
    return this.serializeNode(el).replace(/\u00a0/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  }

  serializeNode(node) {
    if (!node) return "";
    if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || "";
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    if (node.hasAttribute && node.hasAttribute("data-mkt-merge")) {
      const key = node.getAttribute("data-mkt-merge");
      const fallback = node.getAttribute("data-mkt-fallback");
      return fallback ? `{{${key}|${fallback}}}` : `{{${key}}}`;
    }
    if (node.tagName === "BR") return "\n";
    let text = "";
    for (const child of node.childNodes) {
      text += this.serializeNode(child);
    }
    return text;
  }

  readBlocks() {
    if (!this.hasBlocksFieldTarget) return [];
    try {
      const parsed = JSON.parse(this.blocksFieldTarget.value || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      this.setStatus("Could not read this template. Try again.");
      return null;
    }
  }

  writeBlocks(blocks) {
    this.blocksFieldTarget.value = JSON.stringify(blocks);
  }

  insertImage(blocks, image) {
    const index = [...blocks].map((block) => block.type).lastIndexOf("signature");
    if (index >= 0) {
      blocks.splice(index, 0, image);
    } else {
      blocks.push(image);
    }
  }

  renderImageList() {
    if (!this.hasImageListTarget) return;
    const blocks = this.readBlocks() || [];
    const images = blocks.filter((block) => block.type === "image" && block.image_url);
    if (!images.length) {
      this.imageListTarget.hidden = true;
      this.imageListTarget.innerHTML = "";
      return;
    }
    this.imageListTarget.hidden = false;
    this.imageListTarget.innerHTML = images.map((image, index) => `
      <div class="crm-photo-list__row">
        <img class="crm-photo-list__thumb" src="${this.escapeAttr(image.image_url)}" alt="">
        <div>
          <div class="text-sm" style="color: var(--ink);">${this.escapeHtml(image.alt || "Photo")}</div>
          <button type="button" class="crm-btn mt-1" data-action="click->marketing-template-studio#removeImage" data-index="${index}">Remove</button>
        </div>
      </div>
    `).join("");
  }

  syncKeptImages() {
    const blocks = this.readBlocks() || [];
    const images = blocks
      .filter((block) => block.type === "image" && block.image_url)
      .map((block) => ({ image_url: block.image_url, alt: block.alt || "Photo" }));
    const payload = JSON.stringify(images);
    this.keepImagesTargets.forEach((el) => {
      el.value = payload;
    });
  }

  readSamples() {
    const out = {};
    this.sampleTargets.forEach((input) => {
      out[input.dataset.key] = input.value;
    });
    return out;
  }

  sampleValue(key) {
    const hit = Array.from(this.sampleTargets).find((input) => input.dataset.key === key);
    return hit ? hit.value.trim() : "";
  }

  samplePlaceholder(key) {
    const hit = Array.from(this.sampleTargets).find((input) => input.dataset.key === key);
    return hit ? (hit.getAttribute("placeholder") || "").trim() : "";
  }

  samplePreviewOn() {
    return this.hasSampleToggleTarget && this.sampleToggleTarget.checked;
  }

  highlightUsedSamples(keys) {
    const used = keys ? new Set(keys) : this.usedMergeKeys();
    this.sampleTargets.forEach((input) => {
      const row = input.closest(".mkt-sample-row");
      if (row) row.classList.toggle("is-used", used.has(input.dataset.key));
    });
  }

  usedMergeKeys() {
    const blob = [
      this.hasSubjectTarget ? this.subjectTarget.value : "",
      this.hasPreheaderTarget ? this.preheaderTarget.value : "",
      this.hasBlocksFieldTarget ? this.blocksFieldTarget.value : "",
    ].join("\n");
    const keys = new Set();
    const re = /\{\{\s*([a-z][a-z_]*\.[a-z][a-z_]*)/g;
    let match;
    while ((match = re.exec(blob))) keys.add(match[1]);
    return keys;
  }

  updateFilledSubject(text) {
    if (!this.hasFilledSubjectTarget) return;
    const raw = this.hasSubjectTarget ? this.subjectTarget.value : "";
    const filled = this.samplePreviewOn() ? (text || "").trim() : "";
    const show = Boolean(filled && filled !== raw);
    this.filledSubjectTarget.textContent = show ? filled : "";
    this.filledSubjectTarget.hidden = !show;
  }

  setStatus(message) {
    if (!this.hasUploadStatusTarget) return;
    this.uploadStatusTarget.hidden = !message;
    this.uploadStatusTarget.textContent = message || "";
  }

  setTestStatus(message) {
    if (!this.hasTestStatusTarget) return;
    this.testStatusTarget.hidden = !message;
    this.testStatusTarget.textContent = message || "";
  }

  csrfToken() {
    return document.querySelector('input[name="csrf_token"]')?.value;
  }

  escapeAttr(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
}
