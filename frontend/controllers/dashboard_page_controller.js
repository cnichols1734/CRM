import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static targets = ["taskWindowButton", "pipelineValue", "onboarding"];

  connect() {
    this.animatePipelineValue();
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
}
