---
name: morning-digest
schedule: "0 7 * * *"
model: strong
llm: always
adapters: []
budget_usd: 1.00
alert: telegram
---

# Morning Digest

Read all `state/*.json` and produce one Telegram-sized briefing:

1. **Red flags first** — anything down, degraded, expiring (SSL < 21d), or a
   deadline within 72h. If nothing: "All green."
2. **Sites** — one line: N up, N degraded, N down; anything notable.
3. **Deadlines & pipeline** — from configured business sources (bids due,
   client deliverables, filings).
4. **Yesterday's agent activity** — read the tail of `logs/audit.jsonl` for
   the last 24 hours: which routines ran, adapters invoked, any Task/Run
   records with `outcome: failed` or `awaiting_approval`, and total spend
   (sum of run costs) for the period. Call out anything still waiting on
   approval.
5. **One suggestion** — the single highest-leverage thing to fix or automate
   next, based on recurring noise in the logs.

Tone: direct, operator-to-operator, no filler. Hard cap ~300 words.

Write the finished digest to `state/digest-latest.md` — this is what gets
sent to Telegram.
