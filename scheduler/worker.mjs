// The independent clock can trigger only this repository's regular monitor.
const API = "https://api.github.com/repos/wadansyaku/cheekschecker";
const FRESH_MS = 8 * 60_000;
const STALLED_MS = 20 * 60_000;
const ACTIVE = new Set(["queued", "in_progress", "pending", "waiting", "requested"]);

class SchedulerError extends Error {}

async function request(path, token, fetcher, options = {}) {
  let response;
  try {
    response = await fetcher(`${API}/${path}`, {
      ...options,
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "User-Agent": "cheekschecker-scheduler",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    throw new SchedulerError("github_connection_failed");
  }
  if (!response.ok) throw new SchedulerError(`github_http_${response.status}`);
  return response;
}

function ageOf(value, now) {
  if (value === null || value === undefined) return null;
  const timestamp = typeof value === "string" ? Date.parse(value) : NaN;
  if (!Number.isFinite(timestamp) || timestamp > now + 30_000) {
    throw new SchedulerError("invalid_state_timestamp");
  }
  return Math.max(0, now - timestamp);
}

export async function tick(env, { fetcher = fetch, now = Date.now(), scheduledTime = now } = {}) {
  const token = env.GITHUB_ACTIONS_TOKEN;
  if (typeof token !== "string" || !token.trim()) {
    throw new SchedulerError("github_token_missing");
  }
  // Query each active status so a long-lived run cannot fall off a history page.
  const pages = await Promise.all([...ACTIVE].map(async status => {
    const response = await request(
      `actions/workflows/monitor.yml/runs?branch=main&status=${status}&per_page=20`, token, fetcher,
    );
    const page = (await response.json()).workflow_runs;
    if (!Array.isArray(page)) throw new SchedulerError("invalid_runs_response");
    return page;
  }));
  const runs = pages.flat();
  const active = runs.filter(run => ACTIVE.has(run.status));
  if (active.length) {
    const ages = active.map(run => ageOf(run.created_at, now));
    if (ages.some(age => age === null || age > STALLED_MS)) {
      throw new SchedulerError("active_monitor_stalled");
    }
    return { decision: "skip", reason: "monitor_active" };
  }
  const stateResponse = await request("contents/monitor_state.json?ref=main", token, fetcher);
  let state;
  try {
    const file = await stateResponse.json();
    if (file.encoding !== "base64" || typeof file.content !== "string") {
      throw new Error("invalid content");
    }
    const bytes = Uint8Array.from(atob(file.content), char => char.charCodeAt(0));
    state = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new SchedulerError("invalid_state_response");
  }
  if (state === null || typeof state !== "object" || Array.isArray(state)) {
    throw new SchedulerError("invalid_state_response");
  }
  // A diagnostic does not advance this timestamp. Legacy states use fetch time.
  const age = ageOf(state.last_checked_at ?? state.last_fetched_at, now);
  const failures = state.warning_throttle?.monitor_fetch_failure?.consecutive_runs;
  if (age !== null && age < FRESH_MS && !failures) {
    return { decision: "skip", reason: "recent_monitor_check", age_seconds: Math.floor(age / 1000) };
  }
  // Never retry an ambiguous POST here. The next tick checks active runs first.
  await request("actions/workflows/monitor.yml/dispatches", token, fetcher, {
    method: "POST",
    body: JSON.stringify({
      ref: "main", inputs: {
        external_trigger: true, send_monitor_diagnostic: false,
        external_tick: new Date(scheduledTime).toISOString(),
      },
    }),
  });
  return { decision: "dispatch_accepted", reason: age === null ? "check_time_unknown" : "check_overdue" };
}

export default {
  async scheduled(controller, env) {
    try {
      console.log(JSON.stringify({ event: "monitor_scheduler", ...await tick(env, { scheduledTime: controller.scheduledTime }) }));
    } catch (error) {
      const reason = error instanceof SchedulerError ? error.message : "unexpected_scheduler_failure";
      console.error(JSON.stringify({ event: "monitor_scheduler_failed", reason }));
      throw new Error(reason);
    }
  },
};
