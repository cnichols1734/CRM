import PdfPaneController from "./pdf_pane_controller";

export default class extends PdfPaneController {
  static targets = [
    "pdfPane",
    "pageStack",
    "pdfLoading",
    "pdfError",
    "pageLabel",
    "pageTotal",
    "form",
    "docSelect",
  ];

  static values = {
    pdfUrl: String,
    saveUrl: String,
  };

  connect() {
    this._onOpen = (event) => {
      if (event.target.closest("[data-listing-info-compare-open]")) {
        this.open();
      }
    };
    this._onDialogClose = () => {
      document.body.classList.remove("overflow-hidden");
    };
    document.addEventListener("click", this._onOpen);
    this.element.addEventListener("close", this._onDialogClose);
  }

  disconnect() {
    document.removeEventListener("click", this._onOpen);
    this.element.removeEventListener("close", this._onDialogClose);
    this.disconnectPdfPane();
  }

  open() {
    if (typeof this.element.showModal === "function") {
      this.element.showModal();
    } else {
      this.element.setAttribute("open", "");
    }
    document.body.classList.add("overflow-hidden");
    if (!this.pdfDoc) {
      this.showPdfLoading();
      this.pendingFit = true;
      this.connectPdfPane();
    } else {
      window.requestAnimationFrame(() => this.fitWidth());
    }
  }

  close() {
    if (typeof this.element.close === "function") {
      this.element.close();
    } else {
      this.element.removeAttribute("open");
    }
    document.body.classList.remove("overflow-hidden");
  }

  switchDoc() {
    if (!this.hasDocSelectTarget) return;
    this.pdfUrlValue = this.docSelectTarget.value;
    this.showPdfLoading();
    this.pendingFit = true;
    this.disconnectPdfPane();
    this.connectPdfPane();
  }

  showPdfLoading() {
    if (this.hasPdfLoadingTarget) this.pdfLoadingTarget.classList.remove("hidden");
    if (this.hasPdfErrorTarget) {
      this.pdfErrorTarget.classList.add("hidden");
      this.pdfErrorTarget.classList.remove("flex");
    }
  }

  confirm(event) {
    event.preventDefault();
    if (!this.hasFormTarget || !this.saveUrlValue) return;
    const payload = {};
    new FormData(this.formTarget).forEach((value, key) => {
      payload[key] = value;
    });
    fetch(this.saveUrlValue, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify(payload),
    })
      .then((res) => res.json())
      .then((data) => {
        if (!data.success) throw new Error(data.error || "Could not save listing info");
        this.close();
        window.location.reload();
      })
      .catch((error) => {
        window.alert(error.message || "Could not save listing info");
      });
  }
}
