import { Controller } from "@hotwired/stimulus";

const MIN_SCALE = 0.1;
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
    this.fitMode = true;
    this.pendingFit = true;
    this.loadToken = 0;
    this.renderJobs = new Map();
    this.pages = new Map();
    this.renderToken = 0;
    this.pageEls = [];
    this._onScroll = () => {
      if (this.scrollFrame) return;
      this.scrollFrame = window.requestAnimationFrame(() => {
        this.scrollFrame = null;
        this.updatePageFromScroll();
      });
    };

    if (this.hasPdfPaneTarget) {
      this.pdfPaneTarget.addEventListener("scroll", this._onScroll, { passive: true });
    }
    this.resizeObserver = new ResizeObserver(() => {
      const width = this.pdfPaneTarget.clientWidth;
      if (!width || width === this.paneWidth) return;
      this.paneWidth = width;
      clearTimeout(this.resizeTimer);
      this.resizeTimer = setTimeout(() => {
        if (this.fitMode && this.pdfDoc) this.fitWidth();
      }, 120);
    });
    if (this.hasPdfPaneTarget) this.resizeObserver.observe(this.pdfPaneTarget);
    this.loadPdf();
  }

  disconnectPdfPane() {
    if (this.hasPdfPaneTarget && this._onScroll) {
      this.pdfPaneTarget.removeEventListener("scroll", this._onScroll);
    }
    this.loadToken += 1;
    this.renderToken += 1;
    this.resizeObserver?.disconnect();
    clearTimeout(this.resizeTimer);
    window.cancelAnimationFrame(this.scrollFrame);
    this.cancelRendering();
    this.loadingTask?.destroy();
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

    const loadToken = ++this.loadToken;
    this.renderToken += 1;
    this.cancelRendering();
    this.loadingTask?.destroy();
    this.pdfDoc = null;
    this.pages.clear();
    this.fitMode = true;
    this.pendingFit = true;
    this.hidePdfError();
    try {
      await this.waitForPdfJs();
    } catch (error) {
      if (loadToken === this.loadToken) this.showPdfError();
      return;
    }
    if (loadToken !== this.loadToken) return;

    try {
      this.loadingTask = window.pdfjsLib.getDocument(this.pdfUrlValue);
      const pdf = await this.loadingTask.promise;
      if (loadToken !== this.loadToken) return;
      this.pdfDoc = pdf;
      if (this.hasPageTotalTarget) {
        this.pageTotalTarget.textContent = String(this.pdfDoc.numPages);
      }
      if (this.pendingFit) {
        await this.waitForPaneWidth();
        await this.applyFitScale();
        this.pendingFit = false;
      }
      if (loadToken !== this.loadToken) return;
      await this.renderAllPages(++this.renderToken);
    } catch (error) {
      console.error("PDF load failed", error);
      if (loadToken === this.loadToken) this.showPdfError();
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

  cancelRendering() {
    this.pageObserver?.disconnect();
    this.renderJobs?.forEach((job) => job.cancel());
    this.renderJobs?.clear();
  }

  async renderAllPages(token, anchor = null) {
    if (!this.pdfDoc || !this.hasPageStackTarget) return;
    this.cancelRendering();
    const pdf = this.pdfDoc;
    const scale = this.scale;
    const stack = this.pageStackTarget;
    stack.replaceChildren();
    this.pageEls = [];
    this.pageIntersections = new WeakMap();
    this.pageObserver = new IntersectionObserver((entries) => {
      if (token !== this.renderToken) return;
      for (const entry of entries) {
        this.pageIntersections.set(entry.target, entry.isIntersecting);
        if (entry.isIntersecting) this.paintPage(entry.target, token);
        else {
          const number = Number(entry.target.dataset.page);
          if (!this.renderJobs.has(number)) entry.target.querySelector('canvas')?.remove();
        }
      }
    }, { root: this.pdfPaneTarget, rootMargin: '400px 0px' });

    for (let pageNum = 1; pageNum <= pdf.numPages; pageNum += 1) {
      const page = this.pages.get(pageNum) || await pdf.getPage(pageNum);
      if (token !== this.renderToken) return;
      this.pages.set(pageNum, page);
      const viewport = page.getViewport({ scale });
      const wrapper = document.createElement('div');
      wrapper.className = 'drw-page';
      wrapper.dataset.page = String(pageNum);
      wrapper.style.width = `${viewport.width}px`;
      wrapper.style.height = `${viewport.height}px`;
      wrapper.setAttribute('aria-label', `Page ${pageNum}`);
      stack.appendChild(wrapper);
      this.pageEls[pageNum] = wrapper;
    }
    if (token !== this.renderToken) return;
    if (this.hasPdfLoadingTarget) this.pdfLoadingTarget.classList.add('hidden');
    this.hidePdfError();
    if (anchor && this.pageEls[anchor.page]) {
      const el = this.pageEls[anchor.page];
      const top = el.getBoundingClientRect().top - this.pdfPaneTarget.getBoundingClientRect().top;
      this.pdfPaneTarget.scrollTop += top + anchor.fraction * el.offsetHeight;
    }
    this.pageEls.forEach((el) => this.pageObserver.observe(el));
    this.updatePageFromScroll();
    this.afterPdfLayout?.();
  }

  async paintPage(wrapper, token) {
    const number = Number(wrapper.dataset.page);
    if (token !== this.renderToken || wrapper.querySelector('canvas') || this.renderJobs.has(number)) return;
    const page = this.pages.get(number);
    if (!page) return;
    const viewport = page.getViewport({ scale: this.scale });
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const canvas = document.createElement('canvas');
    canvas.width = Math.floor(viewport.width * dpr);
    canvas.height = Math.floor(viewport.height * dpr);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    const context = canvas.getContext('2d');
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    wrapper.appendChild(canvas);
    const job = page.render({ canvasContext: context, viewport });
    this.renderJobs.set(number, job);
    try {
      await job.promise;
    } catch (error) {
      canvas.remove();
      if (token === this.renderToken && error.name !== 'RenderingCancelledException') this.showPdfError();
    } finally {
      if (this.renderJobs.get(number) === job) this.renderJobs.delete(number);
      if (token !== this.renderToken || this.pageIntersections.get(wrapper) === false) canvas.remove();
    }
  }

  readingAnchor() {
    const page = this.currentPageNumber();
    const el = this.pageEls[page];
    if (!el || !this.hasPdfPaneTarget) return null;
    const offset = this.pdfPaneTarget.getBoundingClientRect().top - el.getBoundingClientRect().top;
    return { page, fraction: offset / Math.max(1, el.offsetHeight) };
  }

  async rerender(anchor = this.readingAnchor()) {
    if (!this.pdfDoc) return;
    const token = ++this.renderToken;
    await this.renderAllPages(token, anchor);
  }

  zoomIn() {
    this.fitMode = false;
    const anchor = this.readingAnchor();
    this.scale = Math.min(MAX_SCALE, this.scale + SCALE_STEP);
    this.rerender(anchor);
  }

  zoomOut() {
    this.fitMode = false;
    const anchor = this.readingAnchor();
    this.scale = Math.max(MIN_SCALE, this.scale - SCALE_STEP);
    this.rerender(anchor);
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
    const pdf = this.pdfDoc;
    const page = await pdf.getPage(1);
    if (pdf !== this.pdfDoc) return;
    const unscaled = page.getViewport({ scale: 1 });
    const available = Math.max(1, this.pdfPaneTarget.clientWidth - 48);
    this.scale = Math.min(MAX_SCALE, available / unscaled.width);
  }

  async fitWidth() {
    if (!this.pdfDoc || !this.hasPdfPaneTarget) return;
    try {
      this.fitMode = true;
      const anchor = this.readingAnchor();
      const pdf = this.pdfDoc;
      await this.applyFitScale();
      if (pdf === this.pdfDoc) await this.rerender(anchor);
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

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const top = el.getBoundingClientRect().top - this.pdfPaneTarget.getBoundingClientRect().top;
    this.pdfPaneTarget.scrollTo({ top: this.pdfPaneTarget.scrollTop + top, behavior: reduce ? 'instant' : 'smooth' });
    this.paintPage(el, this.renderToken);
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
