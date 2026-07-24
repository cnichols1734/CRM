import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static targets = [
    "taskWindowButton", "pipelineValue", "onboarding", "groupChart",
    "activationWorkspace", "pathChooser", "contactPath", "inboxPath",
    "contactStep", "followUpStep", "activationName", "customDateWrap",
    "activationSubmit", "activationResult", "activationSuccess"
  ];
  static values = {
    groupStats: Array,
    quickAddUrl: String
  };

  connect() {
    this.animatePipelineValue();
    this._waitForApexCharts().then(() => this.renderGroupChart());
    this.initializeTodos();
    if (this.hasPathChooserTarget) {
      window.crmAnalytics?.capture?.("activation_step_viewed", {
        surface: "activation",
        step: "chooser",
        component: "path_chooser",
        target: "chooser"
      });
      window.crmAnalytics?.setActivationState?.({ lastStep: "chooser" });
      window.crmAnalytics?.captureImpression?.(this.pathChooserTarget);
    }
  }

  disconnect() {
    if (this.groupChart) {
      this.groupChart.destroy();
      this.groupChart = null;
    }
  }

  async setTaskWindow(event) {
    event.preventDefault();

    const days = Number(event.currentTarget.dataset.days);
    if (!Number.isInteger(days)) return;

    try {
      const response = await fetch("/api/update-task-window", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days })
      });

      if (!response.ok) throw new Error("Unable to save task window");
      window.location.reload();
    } catch (error) {
      console.error(error);
      window.alert("Unable to update the task window right now.");
    }
  }

  async updateTaskStatus(event) {
    const taskId = event.currentTarget.dataset.taskId;
    if (!taskId) return;

    const completed = event.currentTarget.checked;

    try {
      const response = await fetch(`/tasks/${taskId}/quick-update`, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded"
        },
        body: `status=${completed ? "completed" : "pending"}`
      });

      if (!response.ok) throw new Error("Unable to update task");
      const data = await response.json();
      if (completed && data.contact_id) {
        this._showNextActionPrompt(data.contact_id);
      } else {
        window.location.reload();
      }
    } catch (error) {
      console.error(error);
      event.currentTarget.checked = !completed;
      window.alert("Unable to update the task right now.");
    }
  }

  async dismissOnboarding(event) {
    event.preventDefault();

    if (this.hasOnboardingTarget) {
      this.onboardingTarget.classList.add("hidden");
    }

    try {
      await fetch("/dashboard/dismiss-onboarding", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      });
    } catch (error) {
      console.error("Failed to dismiss dashboard onboarding", error);
    }
  }

  selectContactPath(event) {
    event.preventDefault();
    this._selectActivationPath("manual");
    this.pathChooserTarget.classList.add("hidden");
    this.inboxPathTarget.classList.add("hidden");
    this.contactPathTarget.classList.remove("hidden");
    window.crmAnalytics?.capture?.("activation_step_viewed", {
      surface: "activation",
      step: "contact",
      path: "manual",
      component: "manual_form",
      target: "contact_step"
    });
    window.crmAnalytics?.setActivationState?.({
      lastStep: "contact",
      path: "manual",
      hadPath: true
    });
    window.requestAnimationFrame(() => this.activationNameTarget.focus());
  }

  selectInboxPath(event) {
    event.preventDefault();
    this._selectActivationPath("magic_inbox");
    this.pathChooserTarget.classList.add("hidden");
    this.contactPathTarget.classList.add("hidden");
    this.inboxPathTarget.classList.remove("hidden");
    window.crmAnalytics?.capture?.("activation_step_viewed", {
      surface: "activation",
      step: "inbox",
      path: "magic_inbox",
      component: "inbox_path",
      target: "inbox_instructions"
    });
    window.crmAnalytics?.setActivationState?.({
      lastStep: "inbox",
      path: "magic_inbox",
      hadPath: true
    });
  }

  recordImportPath() {
    this._selectActivationPath("csv_import");
    window.crmAnalytics?.setActivationState?.({
      lastStep: "csv_preview",
      path: "csv_import",
      hadPath: true
    });
  }

  resetActivationPath(event) {
    event.preventDefault();
    this.contactPathTarget.classList.add("hidden");
    this.inboxPathTarget.classList.add("hidden");
    this.pathChooserTarget.classList.remove("hidden");
    window.crmAnalytics?.captureInteraction?.(event.currentTarget, {
      surface: "activation",
      component: "manual_form",
      action: "back",
      target: "back_to_chooser"
    });
    this.showContactStep(event);
  }

  showFollowUpStep(event) {
    event.preventDefault();
    if (!this.activationNameTarget.value.trim()) {
      this.activationNameTarget.focus();
      this.activationNameTarget.reportValidity();
      window.crmAnalytics?.capture?.("ui_error_shown", {
        surface: "activation",
        component: "manual_form",
        target: "name_required",
        error_code: "validation"
      });
      return;
    }
    this.contactStepTarget.classList.add("hidden");
    this.followUpStepTarget.classList.remove("hidden");
    window.crmAnalytics?.capture?.("activation_step_viewed", {
      surface: "activation",
      step: "follow_up",
      path: "manual",
      component: "manual_form",
      target: "follow_up_step"
    });
    window.crmAnalytics?.setActivationState?.({ lastStep: "follow_up", path: "manual", hadPath: true });
  }

  showContactStep(event) {
    event.preventDefault();
    this.followUpStepTarget.classList.add("hidden");
    this.contactStepTarget.classList.remove("hidden");
    window.crmAnalytics?.setActivationState?.({ lastStep: "contact", path: "manual", hadPath: true });
  }

  toggleCustomDate(event) {
    this.customDateWrapTarget.classList.toggle(
      "hidden",
      event.currentTarget.value !== "custom"
    );
    const dateInput = this.customDateWrapTarget.querySelector("input");
    if (dateInput) dateInput.required = event.currentTarget.value === "custom";
  }

  async createActivation(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) {
      window.crmAnalytics?.capture?.("activation_submit_failed", {
        surface: "activation",
        path: "manual",
        error_code: "validation",
        component: "manual_form",
        target: "submit"
      });
      return;
    }

    this.activationSubmitTarget.disabled = true;
    this.activationSubmitTarget.textContent = "Saving…";
    this.activationResultTarget.className = "mt-3 text-sm text-slate-500";
    this.activationResultTarget.textContent = "";
    window.crmAnalytics?.setActivationState?.({ submitAttempted: true });
    window.crmAnalytics?.capture?.("ui_interaction", {
      surface: "activation",
      component: "manual_form",
      action: "submit",
      target: "submit",
      path: "manual",
      step: "follow_up"
    });

    const startedAt = Date.now();
    try {
      const response = await fetch(this.quickAddUrlValue, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        body: new FormData(form)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.status !== "success") {
        const errorCode = response.status === 409
          ? "conflict"
          : response.status >= 500
            ? "server"
            : "validation";
        window.crmAnalytics?.capture?.("activation_submit_failed", {
          surface: "activation",
          path: "manual",
          error_code: errorCode,
          component: "manual_form",
          target: "submit"
        });
        throw new Error(data.message || "Could not save your first follow-up.");
      }

      const contactName = data.contact?.name?.trim() || "Your contact";
      const dueDate = data.task?.due_date || "the selected day";
      this.contactPathTarget.classList.add("hidden");
      this.pathChooserTarget.classList.add("hidden");
      this.activationSuccessTarget.classList.remove("hidden");
      this.activationSuccessTarget.innerHTML = `
        <div class="max-w-2xl">
          <div class="flex h-10 w-10 items-center justify-center rounded-md bg-emerald-100 text-emerald-700">
            <i class="fas fa-check"></i>
          </div>
          <div class="crm-section-kicker mt-5">Your next follow-up</div>
          <h3 class="mt-2 text-2xl font-semibold tracking-tight text-slate-950" data-contact-name></h3>
          <p class="mt-2 text-sm text-slate-500">Due <strong class="text-slate-800" data-due-date></strong>. It is already on your task list.</p>
          <div class="mt-5 flex flex-wrap gap-2">
            <a class="crm-btn crm-btn-primary" href="/tasks"
               data-analytics-surface="activation"
               data-analytics-component="success_card"
               data-analytics-action="click"
               data-analytics-target="open_tasks">Open my tasks</a>
            <a class="crm-btn crm-btn-secondary" href="${data.view_url}"
               data-analytics-surface="activation"
               data-analytics-component="success_card"
               data-analytics-action="click"
               data-analytics-target="open_contact">Open contact</a>
          </div>
        </div>`;
      this.activationSuccessTarget.querySelector("[data-contact-name]").textContent = contactName;
      this.activationSuccessTarget.querySelector("[data-due-date]").textContent = dueDate;
      window.crmAnalytics?.markActivationComplete?.();
      window.crmAnalytics?.capture?.("ui_interaction", {
        surface: "activation",
        component: "manual_form",
        action: "completed",
        target: "success",
        path: "manual",
        latency_ms_bucket: Date.now() - startedAt < 1000 ? "0_1s" : "1s_plus"
      });
      window.crmAnalytics?.stopReplay();
    } catch (error) {
      const isNetwork = error instanceof TypeError;
      if (isNetwork) {
        window.crmAnalytics?.capture?.("activation_submit_failed", {
          surface: "activation",
          path: "manual",
          error_code: "network",
          component: "manual_form",
          target: "submit"
        });
      }
      this.activationResultTarget.className = "mt-3 text-sm text-red-600";
      this.activationResultTarget.textContent = error.message || "Please try again.";
      this.activationSubmitTarget.disabled = false;
      this.activationSubmitTarget.textContent = "Add contact and follow-up";
    }
  }

  async copyInboxAddress(event) {
    const address = event.currentTarget.dataset.address;
    if (!address) return;
    await navigator.clipboard.writeText(address);
    event.currentTarget.textContent = "Copied";
    window.crmAnalytics?.capture?.("inbox_address_copied", {
      surface: "activation",
      component: "inbox_path",
      action: "copy",
      target: "copy_address"
    });
    fetch("/dashboard/inbox-copied", { method: "POST" }).catch(() => {});
  }

  _selectActivationPath(path) {
    fetch("/dashboard/activation-path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path })
    }).catch(() => {});
  }

  _showNextActionPrompt(contactId) {
    this.element.querySelector("[data-next-action-prompt]")?.remove();
    const prompt = document.createElement("div");
    prompt.dataset.nextActionPrompt = "";
    prompt.className = "crm-surface mb-6 border-emerald-200 bg-emerald-50";
    prompt.innerHTML = `
      <div class="crm-surface-body flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div class="text-sm font-semibold text-emerald-900">Follow-up complete.</div>
          <p class="mt-1 text-sm text-emerald-800">Log the next action while the conversation is fresh.</p>
        </div>
        <div class="flex gap-2">
          <a class="crm-btn crm-btn-primary" href="/tasks/new?contact_id=${encodeURIComponent(contactId)}&return_to=contact">Schedule next action</a>
          <button type="button" class="crm-btn crm-btn-secondary" data-dismiss-next-action>Not now</button>
        </div>
      </div>`;
    prompt.querySelector("[data-dismiss-next-action]").addEventListener("click", () => {
      window.location.reload();
    });
    this.element.querySelector(".crm-page__inner")?.prepend(prompt);
    prompt.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  animatePipelineValue() {
    if (!this.hasPipelineValueTarget) return;

    const rawValue = Number(this.pipelineValueTarget.dataset.value || 0);
    const formattedTarget = new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0
    });

    const durationMs = 600;
    const start = performance.now();

    const tick = (timestamp) => {
      const progress = Math.min((timestamp - start) / durationMs, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      this.pipelineValueTarget.textContent = formattedTarget.format(rawValue * eased);

      if (progress < 1) {
        window.requestAnimationFrame(tick);
      }
    };

    window.requestAnimationFrame(tick);
  }

  initializeTodos() {
    if (!window.TodoManager || !document.getElementById("dashboardActiveTodoList")) return;

    this.dashboardTodos = new window.TodoManager({
      templateId: "todoItemTemplate",
      activeListId: "dashboardActiveTodoList",
      inputId: "dashboardNewTodoInput",
      addBtnId: "dashboardAddTodoBtn"
    });
  }

  _waitForApexCharts() {
    if (this._apexReady) return this._apexReady;
    this._apexReady = new Promise((resolve) => {
      if (window.ApexCharts) return resolve();
      const start = Date.now();
      const tick = () => {
        if (window.ApexCharts) return resolve();
        if (Date.now() - start > 5000) return resolve();
        setTimeout(tick, 50);
      };
      tick();
    });
    return this._apexReady;
  }

  renderGroupChart() {
    if (!this.hasGroupChartTarget || !window.ApexCharts || !this.groupStatsValue.length) return;

    this.groupChart = new window.ApexCharts(this.groupChartTarget, {
      series: this.groupStatsValue.map((item) => item.count),
      chart: {
        width: "100%",
        height: 220,
        type: "donut"
      },
      labels: this.groupStatsValue.map((item) => item.name),
      colors: this._chartColors(),
      dataLabels: {
        enabled: false
      },
      legend: {
        show: false
      },
      plotOptions: {
        pie: {
          donut: {
            size: "78%"
          }
        }
      },
      stroke: {
        width: 0
      },
      tooltip: {
        y: {
          formatter(value) {
            return `${value} contacts`;
          }
        }
      }
    });

    this.groupChart.render();

    // Re-render on theme toggle so colors stay readable in dark mode.
    this._themeListener = () => {
      if (!this.groupChart) return;
      this.groupChart.updateOptions({ colors: this._chartColors() }, false, false);
    };
    window.addEventListener("crm:theme", this._themeListener);
  }

  _chartColors() {
    const root = getComputedStyle(document.documentElement);
    const tok = (name, fallback) => (root.getPropertyValue(name).trim() || fallback);
    // Prefer ink/accent tokens so the donut adapts to theme automatically.
    return [
      tok("--accent",  "#c1623f"),
      tok("--ink",     "#1d2026"),
      tok("--ink-3",   "#7a7572"),
      tok("--ink-4",   "#a39e9b"),
      tok("--ink-5",   "#c9c5c1"),
      tok("--paper-3", "#ebe7e2"),
    ];
  }
}
