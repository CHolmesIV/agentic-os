#!/usr/bin/env node
/**
 * send-digest.mjs — send a markdown file to Telegram as one or more
 * messages, chunked at 4000 chars.
 *
 * Called by runner/run_routine.py after a morning-digest routine run:
 *   node bot/send-digest.mjs state/digest-latest.md
 *
 * Usage: node bot/send-digest.mjs <path-to-markdown-file>
 */

import { readFileSync, existsSync } from "node:fs";
import { sendMessage, chunkText } from "./telegram.mjs";

async function main() {
  const filePath = process.argv[2];
  if (!filePath) {
    console.error("Usage: node bot/send-digest.mjs <path-to-markdown-file>");
    return 1;
  }
  if (!existsSync(filePath)) {
    console.error(`[send-digest] file not found: ${filePath}`);
    return 1;
  }

  const text = readFileSync(filePath, "utf8");
  if (!text.trim()) {
    console.log(`[send-digest] ${filePath} is empty, nothing to send`);
    return 0;
  }

  const chunks = chunkText(text);
  try {
    await sendMessage(text);
    console.log(`[send-digest] sent ${filePath} in ${chunks.length} message(s)`);
    return 0;
  } catch (e) {
    console.error(`[send-digest] failed to send: ${e}`);
    return 1;
  }
}

main()
  .then((code) => process.exit(code))
  .catch((e) => {
    console.error(`[send-digest] FATAL: ${e && e.stack ? e.stack : e}`);
    process.exit(1);
  });
