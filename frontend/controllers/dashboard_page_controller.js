import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static targets = ["taskWindowButton", "pipelineValue", "onboarding", "groupChart"];
  static values = {
    groupStats: Array
  };

  connect() {
    this.animatePipelineValue();
    this.renderGroupChart();
    this.initializeTodos();
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
      window.location.reload();
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
