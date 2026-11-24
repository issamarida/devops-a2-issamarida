// Tests for the one calculation the pages do themselves: how far a price is
// from a rule's threshold. It must agree with the alerts service, where a
// price equal to the threshold does not fire a rule.
//
// Run with:  node --test "ui-tests/*.test.mjs"

import assert from "node:assert/strict";
import { test } from "node:test";

// ui/page.js listens for key and pointer events when it loads. There is no
// page here, so that one browser function is replaced by one that does nothing.
globalThis.addEventListener = () => {};

const { distanceText } = await import("../ui/quotes.js");

test("a price under an 'above' threshold shows the rise still needed", () => {
  assert.equal(distanceText(100, 110, "above"), "▲ 10.00% rise");
});

test("a price over a 'below' threshold shows the fall still needed", () => {
  assert.equal(distanceText(200, 150, "below"), "▼ 25.00% fall");
});

test("a price past the threshold says the rule fires on the next check", () => {
  assert.equal(distanceText(110.01, 110, "above"), "Met, fires on next check");
  assert.equal(distanceText(149.99, 150, "below"), "Met, fires on next check");
});

test("a price equal to the threshold has not met the rule", () => {
  assert.equal(distanceText(110, 110, "above"), "▲ 0.00% rise");
  assert.equal(distanceText(150, 150, "below"), "▼ 0.00% fall");
});
