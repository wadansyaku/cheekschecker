import assert from "node:assert/strict";
import test from "node:test";
import worker, { tick } from "./worker.mjs";

const now = Date.parse("2026-10-15T06:00:00Z");
const env = { GITHUB_ACTIONS_TOKEN: "test-token" };
const iso = minutes => new Date(now - minutes * 60_000).toISOString();
const json = data => new Response(JSON.stringify(data), { status: 200 });

function server({ runs = [], state = {}, postStatus = 204 } = {}) {
  const calls = [];
  return { calls, fetcher: async (url, options) => {
    calls.push({ url, ...options });
    if (url.includes("/runs?")) return json({ workflow_runs: runs });
    if (url.includes("/contents/")) return json({
      encoding: "base64", content: Buffer.from(JSON.stringify(state)).toString("base64"),
    });
    return new Response(null, { status: postStatus });
  } };
}

test("overdue monitoring dispatches regular main without artifacts or diagnostics", async () => {
  const s = server({ state: { last_checked_at: iso(15) } });
  assert.equal((await tick(env, { ...s, now })).decision, "dispatch_accepted");
  const post = s.calls.at(-1);
  assert.deepEqual(JSON.parse(post.body), {
    ref: "main", inputs: {
      external_trigger: true, send_monitor_diagnostic: false, external_tick: new Date(now).toISOString(),
    },
  });
  assert.equal(post.redirect, "error");
  assert.ok(post.signal instanceof AbortSignal);
});

test("recent successful check skips even if the body was fetched earlier", async () => {
  const s = server({ state: { last_checked_at: iso(5), last_fetched_at: iso(90) } });
  assert.equal((await tick(env, { ...s, now })).reason, "recent_monitor_check");
  assert.equal(s.calls.length, 6);
});

test("legacy fetch time is accepted while missing time remains unknown", async () => {
  const old = server({ state: { last_fetched_at: iso(5) } });
  assert.equal((await tick(env, { ...old, now })).decision, "skip");
  const unknown = server();
  assert.equal((await tick(env, { ...unknown, now })).reason, "check_time_unknown");
});

test("recent failure checkpoint does not suppress a recovery attempt", async () => {
  const s = server({ state: {
    last_checked_at: iso(5), warning_throttle: { monitor_fetch_failure: { consecutive_runs: 1 } },
  } });
  assert.equal((await tick(env, { ...s, now })).decision, "dispatch_accepted");
});

for (const status of ["queued", "in_progress", "pending", "waiting", "requested"]) {
  test(`${status} monitor prevents overlapping dispatch`, async () => {
    const s = server({ runs: [{ status, created_at: iso(2) }] });
    assert.equal((await tick(env, { ...s, now })).reason, "monitor_active");
    assert.equal(s.calls.length, 5);
  });
}

test("stalled active run surfaces an error instead of piling up jobs", async () => {
  const s = server({ runs: [{ status: "queued", created_at: iso(25) }] });
  await assert.rejects(tick(env, { ...s, now }), /active_monitor_stalled/);
  assert.equal(s.calls.length, 5);
});

test("successful diagnostic does not substitute for a real monitor check", async () => {
  const s = server({ runs: [{ status: "completed", conclusion: "success", created_at: iso(1) }] });
  assert.equal((await tick(env, { ...s, now })).decision, "dispatch_accepted");
});

for (const status of [401, 403, 429, 500]) {
  test(`GitHub HTTP ${status} is not reported as accepted`, async () => {
    const s = server({ postStatus: status });
    await assert.rejects(tick(env, { ...s, now }), new RegExp(`github_http_${status}`));
    assert.equal(s.calls.filter(call => call.method === "POST").length, 1);
  });
}

test("invalid or future state time stops dispatch", async () => {
  for (const value of ["invalid", iso(-60), 123]) {
    const s = server({ state: { last_checked_at: value } });
    await assert.rejects(tick(env, { ...s, now }), /invalid_state_timestamp/);
    assert.equal(s.calls.length, 6);
  }
});

test("missing token makes no requests", async () => {
  const s = server();
  await assert.rejects(tick({}, { ...s, now }), /github_token_missing/);
  assert.equal(s.calls.length, 0);
});

test("malformed API data and transport errors never expose response bodies", async () => {
  for (const fetcher of [
    async () => json({ token: "must-not-log" }),
    async () => { throw new Error("secret-url must-not-log"); },
  ]) {
    await assert.rejects(tick(env, { fetcher, now }), error => !error.message.includes("must-not-log"));
  }
});

test("scheduled handler surfaces a coarse, safe configuration error", async () => {
  const logs = [];
  const original = console.error;
  console.error = text => logs.push(text);
  try {
    await assert.rejects(worker.scheduled({}, {}), /github_token_missing/);
  } finally { console.error = original; }
  assert.deepEqual(JSON.parse(logs[0]), { event: "monitor_scheduler_failed", reason: "github_token_missing" });
});
