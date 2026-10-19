// Unit tests for the dashboard's latest-wins guard (src/agflow/dashboard/static/seq.js). Run with `node --test tests/js`.
import assert from "node:assert/strict";
import test from "node:test";

import seq from "../../src/agflow/dashboard/static/seq.js";

const { latest } = seq;

test("only the most recently started job is current", () => {
  const g = latest();
  const first = g.begin();
  assert.equal(g.isCurrent(first), true);
  const second = g.begin();
  assert.equal(g.isCurrent(first), false);
  assert.equal(g.isCurrent(second), true);
});

test("tokens strictly increase", () => {
  const g = latest();
  const tokens = [g.begin(), g.begin(), g.begin()];
  assert.deepEqual(tokens, [...tokens].sort((a, b) => a - b));
  assert.equal(new Set(tokens).size, 3);
});

test("guards are independent of each other", () => {
  const router = latest();
  const editor = latest();
  const r = router.begin();
  editor.begin();
  editor.begin();
  assert.equal(router.isCurrent(r), true);
});

test("a slow response to an earlier request does not overwrite a newer one", async () => {
  // The race from the audit: navigate to A, then to B before A's response arrives.
  const g = latest();
  const rendered = [];
  const load = (name, delayMs) => {
    const token = g.begin();
    return new Promise((resolve) => setTimeout(resolve, delayMs)).then(() => {
      if (g.isCurrent(token)) rendered.push(name);
    });
  };
  await Promise.all([load("A (slow)", 40), load("B (fast)", 5)]);
  assert.deepEqual(rendered, ["B (fast)"]);
});

test("without the guard the same interleaving renders stale content last (documents the bug)", async () => {
  const rendered = [];
  const loadUnguarded = (name, delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs)).then(() => rendered.push(name));
  await Promise.all([loadUnguarded("A (slow)", 40), loadUnguarded("B (fast)", 5)]);
  assert.equal(rendered.at(-1), "A (slow)");
});
