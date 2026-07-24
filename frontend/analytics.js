import posthog from "posthog-js";

const config = window.CRM_ANALYTICS || {};
const enabled = Boolean(config.token && config.enabled);

// Keep in sync with services/product_analytics.py BLOCKED_KEY_PARTS.
const BLOCKED_KEY_PARTS = [
  "address", "brokerage", "company", "content", "description", "email",
  "first_name", "last_name", "message", "name", "notes", "phone", "subject"
];

const ALLOWED_SURFACES = new Set([
  "register", "login", "dashboard", "activation", "contacts", "contact_detail",
  "tasks", "inbox", "user_todos", "profile", "settings", "upgrade", "app"
]);
const ALLOWED_ACTIONS = new Set([
  "click", "select", "submit", "back", "dismiss", "expand", "copy", "filter",
  "navigate", "focus", "completed", "validation_error"
]);

const seenImpressions = new Set();
let surfaceViewedAt = Date.now();
let activationCompleted = Boolean(config.activated);
let activationNavigating = false;

function safeProperties(properties = {}) {
  return Object.fromEntries(
    Object.entries(properties).filter(([key, value]) => {
      const normalized = String(key).toLowerCase();
      if (BLOCKED_KEY_PARTS.some((part) => normalized.includes(part))) return false;
      return value == null || ["string", "number", "boolean"].includes(typeof value);
    })
  );
}

function elapsedBucket(ms) {
  const seconds = Math.max(0, Math.floor((ms || 0) / 1000));
  if (seconds < 5) return "0_5s";
  if (seconds < 30) return "5_30s";
  if (seconds < 120) return "30_120s";
  if (seconds < 600) return "2_10m";
  return "10m_plus";
}

function allowlisted(properties = {}) {
  const next = { ...properties };
  if (next.surface && !ALLOWED_SURFACES.has(next.surface)) delete next.surface;
  if (next.action && !ALLOWED_ACTIONS.has(next.action)) delete next.action;
  if (next.component) next.component = String(next.component).slice(0, 60);
  if (next.target) next.target = String(next.target).slice(0, 60);
  if (next.path) next.path = String(next.path).slice(0, 40);
  if (next.step) next.step = String(next.step).slice(0, 40);
  return next;
}

if (enabled) {
  posthog.init(config.token, {
    api_host: config.host || "https://us.i.posthog.com",
    person_profiles: "identified_only",
    autocapture: false,
    capture_pageview: false,
    capture_pageleave: false,
    disable_session_recording: true,
    session_recording: {
      maskAllInputs: true,
      maskInputOptions: { password: true },
      maskTextSelector: "*"
    }
  });

  if (config.distinctId) {
    try {
      const anonymousId = posthog.get_distinct_id && posthog.get_distinct_id();
      if (
        anonymousId
        && anonymousId !== config.distinctId
        && typeof posthog.alias === "function"
        && config.aliasAnonymous
      ) {
        posthog.alias(config.distinctId, anonymousId);
      }
    } catch (_err) {
      // Alias is best-effort.
    }
    posthog.identify(config.distinctId);
  }
  if (config.replayAllowed) {
    posthog.startSessionRecording();
  }
}

function capture(event, properties = {}) {
  if (!enabled || !event) return;
  const props = safeProperties(allowlisted({
    activation_experience_version: config.experienceVersion,
    subscription_tier: config.subscriptionTier || config.subscriptionTier,
    activation_mode: config.activationMode,
    ...properties,
    elapsed_bucket: properties.elapsed_bucket || elapsedBucket(Date.now() - surfaceViewedAt)
  }));
  posthog.capture(event, props);
}

function captureInteraction(element, overrides = {}) {
  if (!element) return;
  const surface = element.dataset.analyticsSurface || overrides.surface;
  const component = element.dataset.analyticsComponent || overrides.component;
  const action = element.dataset.analyticsAction || overrides.action || "click";
  const target = element.dataset.analyticsTarget || overrides.target;
  if (!surface || !component || !target) return;
  capture("ui_interaction", {
    surface,
    component,
    action,
    target,
    path: element.dataset.analyticsPath || overrides.path,
    step: element.dataset.analyticsStep || overrides.step
  });
}

function captureImpression(element) {
  if (!element) return;
  const surface = element.dataset.analyticsSurface;
  const component = element.dataset.analyticsComponent || element.dataset.analyticsImpression;
  const target = element.dataset.analyticsImpression || element.dataset.analyticsTarget || component;
  if (!surface || !component || !target) return;
  const key = `${surface}:${component}:${target}`;
  if (seenImpressions.has(key)) return;
  seenImpressions.add(key);
  capture("ui_element_viewed", { surface, component, target });
}

document.addEventListener("click", (event) => {
  const target = event.target.closest(
    "[data-analytics-action], [data-analytics-event], [data-analytics-target]"
  );
  if (!target) return;

  // Legacy one-off event names still supported.
  if (target.dataset.analyticsEvent) {
    capture(target.dataset.analyticsEvent, {
      path: target.dataset.analyticsPath || undefined,
      surface: target.dataset.analyticsSurface || undefined,
      target: target.dataset.analyticsTarget || undefined,
      component: target.dataset.analyticsComponent || undefined
    });
  }

  if (target.dataset.analyticsTarget || target.dataset.analyticsAction) {
    captureInteraction(target);
  }

  if (
    config.activationMode === "start"
    && target.dataset.analyticsNav
  ) {
    activationNavigating = true;
    capture("nav_away_during_activation", {
      surface: "activation",
      dest: target.dataset.analyticsNav,
      component: "shell_nav",
      action: "navigate",
      target: target.dataset.analyticsNav
    });
  }
});

document.querySelectorAll("[data-analytics-impression]").forEach(captureImpression);
if (typeof IntersectionObserver !== "undefined") {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) captureImpression(entry.target);
    });
  }, { threshold: 0.35 });
  document.querySelectorAll("[data-analytics-impression]").forEach((el) => observer.observe(el));
}

const registrationForm = document.querySelector('[data-analytics-form="registration"]');
if (registrationForm) {
  capture("registration_viewed", { surface: "register", component: "registration_form", target: "form" });
  if (registrationForm.dataset.validationCategories) {
    capture("registration_validation_failed", {
      surface: "register",
      categories: registrationForm.dataset.validationCategories
    });
  }
  registrationForm.addEventListener("submit", () => {
    capture("registration_started", { surface: "register", component: "registration_form", action: "submit", target: "submit" });
  });
  window.addEventListener("pagehide", () => {
    if (document.visibilityState === "hidden") {
      capture("registration_abandoned", {
        surface: "register",
        last_step: "form",
        component: "registration_form",
        target: "abandon"
      });
    }
  });
}

const loginForm = document.querySelector('[data-analytics-form="login"]');
if (loginForm) {
  capture("login_viewed", { surface: "login", component: "login_form", target: "form" });
}

function markActivationComplete() {
  activationCompleted = true;
}

function captureActivationAbandoned(details = {}) {
  if (activationCompleted || config.activationMode !== "start" || activationNavigating) {
    return;
  }
  const key = "activation_abandoned";
  if (seenImpressions.has(key)) return;
  seenImpressions.add(key);
  capture("activation_abandoned", {
    surface: "activation",
    component: "activation_workspace",
    target: "abandon",
    had_path: Boolean(details.hadPath || details.path),
    last_step: details.lastStep || "chooser",
    path: details.path || undefined,
    submit_attempted: Boolean(details.submitAttempted)
  });
}

window.addEventListener("pagehide", () => {
  if (window.crmAnalytics && window.crmAnalytics._activationState) {
    captureActivationAbandoned(window.crmAnalytics._activationState);
  }
});

window.crmAnalytics = {
  capture,
  captureInteraction,
  captureImpression,
  captureActivationAbandoned,
  markActivationComplete,
  _activationState: {
    lastStep: "chooser",
    path: null,
    hadPath: false,
    submitAttempted: false
  },
  setActivationState(next) {
    this._activationState = { ...this._activationState, ...next };
  },
  startReplay() {
    if (enabled && config.sessionReplay) posthog.startSessionRecording();
  },
  stopReplay() {
    if (enabled) posthog.stopSessionRecording();
  }
};
