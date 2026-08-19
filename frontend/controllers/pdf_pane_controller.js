import { Controller } from "@hotwired/stimulus";

const MIN_SCALE = 0.6;
const MAX_SCALE = 2.4;
const SCALE_STEP = 0.15;
const PULSE_MS = 900;

/**
 * Shared PDF pane behaviour for the full-page review workspaces.
 *
 * Subclasses own the right-hand review pane and must call connectPdfPane()
 * from connect() and disconnectPdfPane() from disconnect().
 */
export default class PdfPaneController extends Controller {
  static targets = [
    "pdfPane",
    "pageStack",
    "pdfLoading",
    "pdfError",
    "pageLabel",
    "pageTotal",
  ];

  static values = {
    pdfUrl: String,
  };

  connectPdfPane() {
    this.pdfDoc = null;
    this.scale = 1.15;
    this.pendingFit = Boolean(this.pendingFit);
    this.renderToken = 0;
    this.pageEls = [];
    this._onScroll = () => this.updatePageFromScroll();

    if (this.hasPdfPaneTarget) {
      this.pdfPaneTarget.addEventListener("scroll", this._onScroll, { passive: true });
    }
    this.loadPdf();
  }

  disconnectPdfPane() {
    if (this.hasPdfPaneTarget && this._onScroll) {
      this.pdfPaneTarget.removeEventListener("scroll", this._onScroll);
    }
    this.renderToken += 1;
    this.pdfDoc = null;
    this.pageEls = [];
  }

  // The PDF.js CDN tag sits at the end of the template, so it can land after connect().
  waitForPdfJs(timeoutMs = 10000) {
    if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib);
    return new Promise((resolve, reject) => {
      const startedAt = Date.now();
      const poll = () => {
        if (window.pdfjsLib) return resolve(window.pdfjsLib);
        if (Date.now() - startedAt >= timeoutMs) {
          return reject(new Error("pdfjsLib unavailable"));
        }
        window.setTimeout(poll, 60);
      };
      poll();
    });
  }

  async loadPdf() {
    if (!this.pdfUrlValue) {
      this.showPdfError();
      return;
    }

    const token = ++this.renderToken;
    this.hidePdfError();
    try {
      await this.waitForPdfJs();
    } catch (error) {
      if (token === this.renderToken) this.showPdfError();
      return;
    }
    if (token !== this.renderToken) return;

    try {
      const loadingTask = window.pdfjsLib.getDocument(this.pdfUrlValue);
      this.pdfDoc = await loadingTask.promise;
      if (token !== this.renderToken) return;
      if (this.hasPageTotalTarget) {
        this.pageTotalTarget.textContent = String(this.pdfDoc.numPages);
      }
      if (this.pendingFit) {
        await this.waitForPaneWidth();
        await this.applyFitScale();
        this.pendingFit = false;
      }
      await this.renderAllPages(token);
    } catch (error) {
      console.error("PDF load failed", error);
      if (token === this.renderToken) this.showPdfError();
    }
  }

  hidePdfError() {
    if (!this.hasPdfErrorTarget) return;
    this.pdfErrorTarget.classList.add("hidden");
    this.pdfErrorTarget.classList.remove("flex");
  }

  showPdfError() {
    if (this.hasPdfLoadingTarget) this.pdfLoadingTarget.classList.add("hidden");
    if (this.hasPdfErrorTarget) {
      this.pdfErrorTarget.classList.remove("hidden");
      this.pdfErrorTarget.classList.add("flex");
    }
  }

  async renderAllPages(token) {
    if (!this.pdfDoc || !this.hasPageStackTarget) return;

    const stack = this.pageStackTarget;
    stack.replaceChildren();
    this.pageEls = [];

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    for (let pageNum = 1; pageNum <= this.pdfDoc.numPages; pageNum += 1) {
      if (token !== this.renderToken) return;
      const page = await this.pdfDoc.getPage(pageNum);
      if (token !== this.renderToken) return;

      const viewport = page.getViewport({ scale: this.scale });
      const wrapper = document.createElement("div");
      wrapper.className = "drw-page";
      wrapper.dataset.page = String(pageNum);
      wrapper.style.width = `${viewport.width}px`;
      wrapper.style.height = `${viewport.height}px`;

      const canvas = document.createElement("canvas");
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;

      const context = canvas.getContext("2d");
      context.setTransform(dpr, 0, 0, dpr, 0, 0);

      wrapper.appendChild(canvas);
      stack.appendChild(wrapper);
      this.pageEls[pageNum] = wrapper;

      await page.render({ canvasContext: context, viewport }).promise;
    }

    if (this.hasPdfLoadingTarget) this.pdfLoadingTarget.classList.add("hidden");
    this.hidePdfError();
    this.updatePageFromScroll();
  }

  async rerender() {
    if (!this.pdfDoc) return;
    const token = ++this.renderToken;
    if (this.hasPdfLoadingTarget) {
      this.pdfLoadingTarget.classList.remove("hidden");
    }
    await this.renderAllPages(token);
  }

  zoomIn() {
    this.scale = Math.min(MAX_SCALE, this.scale + SCALE_STEP);
    this.rerender();
  }

  zoomOut() {
    this.scale = Math.max(MIN_SCALE, this.scale - SCALE_STEP);
    this.rerender();
  }

  async waitForPaneWidth(timeoutMs = 1500) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      if (this.hasPdfPaneTarget && this.pdfPaneTarget.clientWidth > 80) return;
      await new Promise((resolve) => window.requestAnimationFrame(resolve));
    }
  }

  async applyFitScale() {
    if (!this.pdfDoc || !this.hasPdfPaneTarget) return;
    const page = await this.pdfDoc.getPage(1);
    const unscaled = page.getViewport({ scale: 1 });
    const available = Math.max(240, this.pdfPaneTarget.clientWidth - 48);
    this.scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, available / unscaled.width));
  }

  async fitWidth() {
    if (!this.pdfDoc || !this.hasPdfPaneTarget) return;
    try {
      await this.applyFitScale();
      await this.rerender();
    } catch (_error) {
      /* ignore fit failures */
    }
  }

  prevPage() {
    this.goToPage(Math.max(1, this.currentPageNumber() - 1));
  }

  nextPage() {
    const total = this.pdfDoc?.numPages || 1;
    this.goToPage(Math.min(total, this.currentPageNumber() + 1));
  }

  currentPageNumber() {
    const label = this.hasPageLabelTarget ? Number(this.pageLabelTarget.textContent) : 1;
    return Number.isFinite(label) && label > 0 ? label : 1;
  }

  goToPage(n, { pulse = true } = {}) {
    const pageNum = Number(n);
    if (!Number.isFinite(pageNum) || pageNum < 1) return;
    const el = this.pageEls[pageNum];
    if (!el || !this.hasPdfPaneTarget) return;

    el.scrollIntoView({ behavior: "smooth", block: "start" });
    if (this.hasPageLabelTarget) this.pageLabelTarget.textContent = String(pageNum);
    if (pulse) this.pulsePage(el);
  }

  pulsePage(pageEl) {
    if (!pageEl) return;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    pageEl.classList.remove("is-pulsing", "is-pulsing-fade");
    // Force reflow so re-adding the class restarts the transition.
    void pageEl.offsetWidth;
    pageEl.classList.add("is-pulsing");
    if (reduceMotion) {
      window.setTimeout(() => pageEl.classList.remove("is-pulsing"), 120);
      return;
    }
    window.requestAnimationFrame(() => {
      pageEl.classList.add("is-pulsing-fade");
    });
    window.setTimeout(() => {
      pageEl.classList.remove("is-pulsing", "is-pulsing-fade");
    }, PULSE_MS);
  }

  updatePageFromScroll() {
    if (!this.hasPdfPaneTarget || !this.pageEls.length) return;
    const paneTop = this.pdfPaneTarget.getBoundingClientRect().top + 24;
    let best = 1;
    let bestDist = Infinity;
    this.pageEls.forEach((el, pageNum) => {
      if (!el || !pageNum) return;
      const dist = Math.abs(el.getBoundingClientRect().top - paneTop);
      if (dist < bestDist) {
        bestDist = dist;
        best = pageNum;
      }
    });
    if (this.hasPageLabelTarget) this.pageLabelTarget.textContent = String(best);
  }
}
