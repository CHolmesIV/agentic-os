#!/usr/bin/env node
/**
 * telegram.mjs — fetch-based Telegram Bot API wrapper.
 *
 * Outbound-only by design (SECURITY.md #10): getUpdates long-polling means
 * no inbound port, no public webhook. Two capabilities:
 *
 *   - sendMessage(text, chatId?)  — send a message to a chat. Works even
 *     if the allowlist isn't configured (needed so alerts/digest can still
 *     reach the operator's own TELEGRAM_CHAT_ID).
 *   - pollUpdates(onMessage)      — long-poll getUpdates, filtering to the
 *     hard allowlist in config/systems.yml `telegram.allowed_user_ids`.
 *     Unknown senders are ignored (no response to them) and an alert is
 *     sent to the operator chat (TELEGRAM_CHAT_ID) per SECURITY.md #10 /
 *     README "bot takeover" mitigation.
 *
 * Env:
 *   TELEGRAM_BOT_TOKEN   required for any API call.
 *   TELEGRAM_CHAT_ID     default chat for sendMessage() when no chatId arg
 *                        is given, and where "unknown sender" alerts go.
 *
 * Config:
 *   config/systems.yml: systems.telegram.allowed_user_ids: [ids...]
 *   If missing, empty, or [0] (the example placeholder), the allowlist is
 *   considered "not configured": pollUpdates() logs a message and refuses
 *   to start (no long-poll loop), but sendMessage() still works.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import yaml from "js-yaml";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const SYSTEMS_PATH = path.join(REPO_ROOT, "config", "systems.yml");

const API_BASE = "https://api.telegram.org";
const CHUNK_LIMIT = 4000;

function apiUrl(token, method) {
  return `${API_BASE}/bot${token}/${method}`;
}

function loadAllowedUserIds() {
  let data;
  try {
    data = yaml.load(readFileSync(SYSTEMS_PATH, "utf8")) || {};
  } catch {
    return [];
  }
  const ids = data?.systems?.telegram?.allowed_user_ids;
  if (!Array.isArray(ids)) return [];
  return ids;
}

/** "Not configured" = missing, empty, or the example placeholder [0]. */
function isAllowlistConfigured(ids) {
  if (!ids || ids.length === 0) return false;
  if (ids.length === 1 && ids[0] === 0) return false;
  return true;
}

function requireToken() {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  if (!token) {
    throw new Error("TELEGRAM_BOT_TOKEN is not set");
  }
  return token;
}

/** Split text into <=CHUNK_LIMIT-char pieces, breaking on newlines where possible. */
export function chunkText(text, limit = CHUNK_LIMIT) {
  if (text.length <= limit) return [text];
  const chunks = [];
  let rest = text;
  while (rest.length > limit) {
    let cut = rest.lastIndexOf("\n", limit);
    if (cut <= 0) cut = limit;
    chunks.push(rest.slice(0, cut));
    rest = rest.slice(cut).replace(/^\n/, "");
  }
  if (rest.length > 0) chunks.push(rest);
  return chunks;
}

/**
 * Send a message (or several, chunked at 4000 chars) to a chat.
 * Returns an array of Telegram API responses (one per chunk).
 */
export async function sendMessage(text, chatId = process.env.TELEGRAM_CHAT_ID, options = {}) {
  const token = requireToken();
  if (!chatId) {
    throw new Error("No chat id: pass chatId or set TELEGRAM_CHAT_ID");
  }
  const chunks = chunkText(String(text));
  const responses = [];
  for (const chunk of chunks) {
    const res = await fetch(apiUrl(token, "sendMessage"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId,
        text: chunk,
        disable_web_page_preview: true,
        ...options,
      }),
    });
    const json = await res.json();
    if (!res.ok || json.ok === false) {
      throw new Error(`sendMessage failed: ${res.status} ${JSON.stringify(json)}`);
    }
    responses.push(json);
  }
  return responses;
}

/**
 * Long-poll getUpdates and invoke onMessage(update) for each allowlisted
 * sender. Unknown senders are ignored (no reply to them) and the operator
 * chat gets an alert. Refuses to start if the allowlist isn't configured.
 */
export async function pollUpdates(onMessage, { pollTimeoutSeconds = 30 } = {}) {
  const allowedIds = loadAllowedUserIds();
  if (!isAllowlistConfigured(allowedIds)) {
    console.log(
      "[telegram] allowlist not configured (systems.telegram.allowed_user_ids missing/empty/[0]) " +
        "— refusing to start getUpdates polling. sendMessage() still works."
    );
    return;
  }

  const token = requireToken();
  let offset = 0;
  console.log(`[telegram] polling started; allowlist=${JSON.stringify(allowedIds)}`);

  // eslint-disable-next-line no-constant-condition
  while (true) {
    let json;
    try {
      const res = await fetch(
        apiUrl(token, "getUpdates") +
          `?timeout=${pollTimeoutSeconds}&offset=${offset}`,
        { method: "GET" }
      );
      json = await res.json();
    } catch (e) {
      console.error(`[telegram] getUpdates error: ${e}`);
      await new Promise((r) => setTimeout(r, 3000));
      continue;
    }

    if (!json.ok) {
      console.error(`[telegram] getUpdates returned not-ok: ${JSON.stringify(json)}`);
      await new Promise((r) => setTimeout(r, 3000));
      continue;
    }

    for (const update of json.result || []) {
      offset = Math.max(offset, update.update_id + 1);
      const msg = update.message || update.edited_message;
      const fromId = msg?.from?.id;

      if (fromId === undefined) continue;

      if (!allowedIds.includes(fromId)) {
        console.warn(`[telegram] ignoring message from unknown user id ${fromId}`);
        try {
          await sendMessage(
            `Unauthorized Telegram sender ignored: user_id=${fromId}` +
              (msg?.from?.username ? ` (@${msg.from.username})` : "") +
              (msg?.text ? ` — text: ${msg.text.slice(0, 200)}` : "")
          );
        } catch (e) {
          console.error(`[telegram] failed to alert operator about unknown sender: ${e}`);
        }
        continue;
      }

      try {
        await onMessage(update);
      } catch (e) {
        console.error(`[telegram] onMessage handler threw: ${e}`);
      }
    }
  }
}

export { isAllowlistConfigured, loadAllowedUserIds };

// CLI smoke-test entry point: `node bot/telegram.mjs poll` or `node bot/telegram.mjs send "text"`.
if (import.meta.url === `file://${process.argv[1]}`) {
  const [cmd, ...rest] = process.argv.slice(2);
  if (cmd === "send") {
    sendMessage(rest.join(" "))
      .then((r) => console.log(JSON.stringify(r)))
      .catch((e) => {
        console.error(String(e));
        process.exit(1);
      });
  } else if (cmd === "poll") {
    pollUpdates(async (update) => {
      console.log(JSON.stringify(update));
    }).catch((e) => {
      console.error(String(e));
      process.exit(1);
    });
  } else {
    console.log("Usage: node bot/telegram.mjs send <text> | poll");
  }
}
