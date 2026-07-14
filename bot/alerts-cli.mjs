#!/usr/bin/env node
/**
 * alerts-cli.mjs — CLI mode for alerts.mjs.
 *
 * Reads state/pending-alerts.json (written by runner/run_routine.py: a
 * list of {routine, task_id, ts, result} entries queued whenever a
 * routine's frontmatter has `alert: telegram` and an LLM result was
 * produced), converts each into one or more Event objects, runs them
 * through alerts.mjs's dedup/cooldown logic, sends via telegram.mjs, and
 * clears state/pending-alerts.json only if every send succeeded.
 *
 * The runner's pending-alerts entries don't strictly match the Event
 * shape {domain, kind, status, detail, ts} — they wrap a routine's raw
 * llm_result. This CLI normalizes: if llm_result contains a `results`
 * array of {domain, status, detail} (matching the adapter/state shape),
 * one Event per result is emitted with kind = routine name. Otherwise a
 * single Event is emitted with domain = routine name, kind = "routine",
 * status = "alert", detail = a compact stringification of the result.
 */

import { readFileSync, writeFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { processEvent, loadAlertState, saveAlertState } from "./alerts.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const PENDING_PATH = path.join(REPO_ROOT, "state", "pending-alerts.json");

function eventsFromPendingEntry(entry) {
  const ts = entry.ts || new Date().toISOString();

  // Pre-normalized events (from run_routine.py's deterministic alert path).
  if (Array.isArray(entry.events) && entry.events.length > 0) {
    return entry.events;
  }

  const llmResult = entry.result || {};

  // Common shape: llm_result.results = [{domain, status, detail}, ...]
  const nested = Array.isArray(llmResult.results) ? llmResult.results : null;
  if (nested && nested.length > 0) {
    return nested.map((r) => ({
      domain: r.domain || entry.routine,
      kind: entry.routine,
      status: r.status || "alert",
      detail: r.detail || "",
      ts,
    }));
  }

  return [
    {
      domain: entry.routine,
      kind: "routine",
      status: "alert",
      detail:
        typeof llmResult === "string"
          ? llmResult.slice(0, 500)
          : JSON.stringify(llmResult).slice(0, 500),
      ts,
    },
  ];
}

async function main() {
  if (!existsSync(PENDING_PATH)) {
    console.log(`[alerts-cli] no pending alerts file at ${PENDING_PATH}`);
    return 0;
  }

  let pending;
  try {
    pending = JSON.parse(readFileSync(PENDING_PATH, "utf8"));
  } catch (e) {
    console.error(`[alerts-cli] failed to parse ${PENDING_PATH}: ${e}`);
    return 1;
  }

  if (!Array.isArray(pending) || pending.length === 0) {
    console.log("[alerts-cli] pending alerts file is empty");
    return 0;
  }

  let state = loadAlertState();
  let allOk = true;
  let sentCount = 0;

  for (const entry of pending) {
    const events = eventsFromPendingEntry(entry);
    for (const event of events) {
      try {
        const result = await processEvent(event, state);
        state = result.state;
        if (result.sent) sentCount++;
      } catch (e) {
        allOk = false;
        console.error(`[alerts-cli] failed to send alert for ${event.domain}/${event.kind}: ${e}`);
      }
    }
  }

  saveAlertState(state);

  if (allOk) {
    writeFileSync(PENDING_PATH, JSON.stringify([]));
    console.log(`[alerts-cli] processed ${pending.length} pending entries, sent ${sentCount} messages, cleared queue`);
    return 0;
  }

  console.error("[alerts-cli] one or more sends failed; leaving pending-alerts.json in place for retry");
  return 1;
}

main()
  .then((code) => process.exit(code))
  .catch((e) => {
    console.error(`[alerts-cli] FATAL: ${e && e.stack ? e.stack : e}`);
    process.exit(1);
  });
