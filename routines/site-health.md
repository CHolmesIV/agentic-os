---
name: site-health
schedule: "*/15 * * * *"
model: haiku
llm: on-change
adapters: [uptime, ssl_dns, forms, playwright_check]
budget_usd: 0.10
alert: telegram
---

# Site Health Sweep

For every site in `config/sites.yml`, the adapters have produced fresh results
(uptime, SSL/DNS, form endpoints, Playwright smoke check). You are invoked
only because something changed since the last run.

1. Compare current results to `state/site-health.json` (previous).
2. Classify each change:
   - **RECOVERED** — was failing, now passing.
   - **KNOWN-DEGRADED** — a form endpoint (or other check) whose configured
     `expect_status` in `config/sites.yml` is >= 400 and the observed status
     matches that expectation. This is expected behavior, not a failure —
     never alert this as down.
   - **DOWN** — failing in a way not covered by an explicit expected status.
3. DOWN or unexpected degradation → emit an alert: site, what failed, since
   when, first debugging step to try. RECOVERED → emit an all-clear that
   references the original alert.
4. This routine is strictly read-only. Never propose taking a corrective
   action yourself — suggest the fix, the operator decides and triggers it
   through an approval-gated command.

Write the full merged result set to `state/site-health.json`.
