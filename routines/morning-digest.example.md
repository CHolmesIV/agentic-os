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

1. **Red flags first**, anything down, degraded, expiring (SSL < 21d), or a
   deadline within 72h. If nothing: "All green."
2. **Sites**, one line: N up, N degraded, N down; anything notable.
3. **Deadlines & pipeline**, from configured business sources (bids due,
   client deliverables, filings).
4. **Yesterday's agent activity**, what routines ran, anything gated that's
   still waiting on approval, spend total.
5. **One suggestion**, the single highest-leverage thing to fix or automate
   next, based on recurring noise in the logs.

Tone: direct, operator-to-operator, no filler. Hard cap ~300 words.
