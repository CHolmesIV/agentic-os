#!/usr/bin/env node
/**
 * alerts.mjs — dedup + cooldown logic for Event alerts, and delivery via
 * telegram.mjs.
 *
 * An Event, per docs/ARCHITECTURE.md's common record schema, is normalized
 * here to: { domain, kind, status, detail, ts }.
 *   - domain: the site/system the event is about.
 *   - kind:   what's being watched (e.g. "uptime", "ssl", "playwright").
 *   - status: "ok" | "degraded" | "down" (or adapter-specific values).
 *   - detail: human-readable context.
 *   - ts:     ISO timestamp.
 *
 * Rules:
 *   - Dedup key = `${domain}:${kind}`. The same (domain, kind) pair with an
 *     unchanged status does not re-alert.
 *   - 30-minute cooldown per key even when status *has* changed, to avoid
 *     flapping spam — EXCEPT a RECOVERED transition always sends,
 *     referencing the prior alert. A RECOVERED transition is either a
 *     return to "ok" from any non-ok state, or a departure from "down"
 *     into a less-severe state (reported as PARTIAL RECOVERY).
 *   - Last-alert state persisted at state/bot-alerts.json:
 *     { "<domain>:<kind>": { status, ts, message_ts } }
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { sendMessage } from "./telegram.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const STATE_PATH = path.join(REPO_ROOT, "state", "bot-alerts.json");
const COOLDOWN_MS = 30 * 60 * 1000;
// "slow" (latency adapter: sustained, not one sample) and "heavy" (page_weight
// adapter: homepage over budget) page once on entry and once on recovery.
// "broken" (content_standards: noindex, missing analytics, dead internal link)
// and "off_vps" (hosting: domain not pointed at the VPS) added 2026-10-07.
const PAGING_STATUSES = new Set(["down", "slow", "heavy", "broken", "off_vps"]);

function keyFor(event) {
  return `${event.domain}:${event.kind}`;
}

export function loadAlertState() {
  if (!existsSync(STATE_PATH)) return {};
  try {
    return JSON.parse(readFileSync(STATE_PATH, "utf8"));
  } catch {
    return {};
  }
}

export function saveAlertState(state) {
  mkdirSync(path.dirname(STATE_PATH), { recursive: true });
  writeFileSync(STATE_PATH, JSON.stringify(state, null, 2));
}

/**
 * A recovery is EITHER a full return to "ok" from any non-ok state, OR a
 * departure from "down" into a less-severe state (e.g. down -> degraded).
 *
 * The second case matters: before this, a site that came back up but still
 * failed a secondary check (content match, etc) landed in "degraded", which
 * is neither "ok" nor a paging status. It therefore never sent a recovery
 * AND never updated stored state, so the key stayed pinned at "down"
 * indefinitely and every later transition was suppressed as "unchanged".
 */
function recoveryFor(event, prior) {
  if (!prior) return { recovered: false };
  const leftDown = prior.status === "down" && event.status !== "down";
  const backToOk = prior.status !== "ok" && event.status === "ok";
  return { recovered: leftDown || backToOk };
}

function formatMessage(event, prior) {
  const { recovered } = recoveryFor(event, prior);
  if (recovered) {
    const headline =
      event.status === "ok"
        ? `RECOVERED: ${event.domain} [${event.kind}] is back to ok.`
        : `PARTIAL RECOVERY: ${event.domain} [${event.kind}] is no longer down (now ${event.status}).`;
    return (
      `${headline}\n` +
      `Prior alert: ${prior.status} at ${prior.ts}${prior.detail ? ` — ${prior.detail}` : ""}\n` +
      (event.detail ? `Detail: ${event.detail}` : "")
    ).trim();
  }
  return (
    `ALERT: ${event.domain} [${event.kind}] is ${event.status}.\n` +
    (event.detail ? `Detail: ${event.detail}\n` : "") +
    `ts: ${event.ts}`
  ).trim();
}

/**
 * Decide whether `event` should alert right now, given `state` (mutated
 * in place on send). Returns { shouldSend, message, recovered }.
 */
export function evaluateEvent(event, state) {
  const key = keyFor(event);
  const prior = state[key];
  const now = event.ts ? Date.parse(event.ts) : Date.now();

  const { recovered } = recoveryFor(event, prior);
  const statusUnchanged = prior && prior.status === event.status;
  const withinCooldown = prior && now - Date.parse(prior.ts) < COOLDOWN_MS;

  if (!recovered && !PAGING_STATUSES.has(event.status)) {
    return { shouldSend: false, reason: "non-paging status" };
  }

  if (!recovered) {
    if (statusUnchanged) {
      return { shouldSend: false, reason: "status unchanged" };
    }
    if (withinCooldown) {
      return { shouldSend: false, reason: "within 30-min cooldown" };
    }
  }

  const message = formatMessage(event, prior);
  return { shouldSend: true, message, recovered: !!recovered, key, prior };
}

/**
 * Process one Event: evaluate, send via Telegram if warranted, persist
 * updated state. Returns the evaluation result plus `sent: boolean`.
 */
export async function processEvent(event, state = loadAlertState()) {
  const evalResult = evaluateEvent(event, state);
  if (!evalResult.shouldSend) {
    return { ...evalResult, sent: false, state };
  }

  await sendMessage(evalResult.message);

  state[evalResult.key] = {
    status: event.status,
    ts: event.ts || new Date().toISOString(),
    detail: event.detail || "",
  };
  saveAlertState(state);

  return { ...evalResult, sent: true, state };
}

export { keyFor, COOLDOWN_MS };
