import assert from "node:assert/strict";
import { test } from "node:test";
import { FormSubmission } from "../frontend/form_submission.js";

function setup() {
  const windowEvents = new EventTarget();
  globalThis.window = windowEvents;
  const buttons = [{ disabled: false }, { disabled: true }];
  const attributes = new Map();
  const content = {
    inert: false,
    querySelectorAll: () => buttons,
    setAttribute: (key, value) => attributes.set(key, value),
    removeAttribute: (key) => attributes.delete(key),
  };
  const indicator = { hidden: true };
  const submission = new FormSubmission(content, indicator);
  return { windowEvents, buttons, attributes, content, indicator, submission };
}

function submit() {
  return new Event("submit", { cancelable: true });
}

test("shows loading and blocks repeated submission until navigation", () => {
  const { submission, indicator, buttons, content, attributes } = setup();
  const first = submit();
  submission.start(first);
  assert.equal(first.defaultPrevented, false);
  assert.equal(indicator.hidden, false);
  assert.equal(content.inert, true);
  assert.equal(attributes.get("aria-busy"), "true");
  assert.equal(buttons[0].disabled, true);

  const second = submit();
  submission.start(second);
  assert.equal(second.defaultPrevented, true);
  assert.equal(indicator.hidden, false);
  submission.disconnect();
});

test("a rejected submission never enters the loading state", () => {
  const { submission, indicator, buttons, content } = setup();
  const rejected = submit();
  rejected.preventDefault();
  submission.start(rejected);
  assert.equal(indicator.hidden, true);
  assert.equal(buttons[0].disabled, false);
  assert.equal(content.inert, false);
  submission.disconnect();
});

test("returning through browser history restores controls and allows retry", () => {
  const { submission, windowEvents, indicator, buttons, content, attributes } = setup();
  submission.start(submit());
  windowEvents.dispatchEvent(new Event("pageshow"));
  assert.equal(indicator.hidden, true);
  assert.equal(content.inert, false);
  assert.equal(attributes.has("aria-busy"), false);
  assert.equal(buttons[0].disabled, false);
  assert.equal(buttons[1].disabled, true);

  const retry = submit();
  submission.start(retry);
  assert.equal(retry.defaultPrevented, false);
  assert.equal(indicator.hidden, false);
  submission.disconnect();
  assert.equal(indicator.hidden, true);
  assert.equal(content.inert, false);
});
