import posthog from "posthog-js";

const config = window.CRM_ANALYTICS || {};
const enabled = Boolean(config.token && config.enabled);

function safeProperties(properties = {}) {
  const blocked = [
    "address", "company", "content", "description", "email", "message",
    "name", "notes", "phone", "subject"
  ];
  return Object.fromEntries(
    Object.entries(properties).filter(([key, value]) => {
      const normalized = String(key).toLowerCase();
      if (blocked.some((part) => normalized.includes(part))) return false;
      return value == null || ["string", "number", "boolean"].includes(typeof value);
    })
  );
}

if (enabled) {
  posthog.init(config.token, {
    api_host: config.host || "https://us.i.posthog.com",
    person_profiles: "identified_only",
    autocapture: false,
    capture_pageview: false,
    capture_pageleave: false,
    disable_session_recording: true,
    mask_all_text: true,
    session_recording: {
      maskAllInputs: true,
      maskInputOptions: { password: true }
    }
  });

  if (config.distinctId) {
    posthog.identify(config.distinctId);
  }
  if (config.replayAllowed) {
    posthog.startSessionRecording();
  }
}

function capture(event, properties = {}) {
  if (!enabled || !event) return;
  posthog.capture(event, safeProperties(properties));
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-analytics-event]");
  if (!target) return;
  capture(target.dataset.analyticsEvent, {
    path: target.dataset.analyticsPath || undefined,
    surface: target.dataset.analyticsSurface || undefined
  });
});

const registrationForm = document.querySelector('[data-analytics-form="registration"]');
if (registrationForm) {
  capture("registration_viewed");
  if (registrationForm.dataset.validationCategories) {
    capture("registration_validation_failed", {
      categories: registrationForm.dataset.validationCategories
    });
  }
  registrationForm.addEventListener("submit", () => capture("registration_started"));
}

window.crmAnalytics = {
  capture,
  startReplay() {
    if (enabled && config.sessionReplay) posthog.startSessionRecording();
  },
  stopReplay() {
    if (enabled) posthog.stopSessionRecording();
  }
};

