---
name: site-health
schedule: "*/15 * * * *"
model: haiku
llm: on-change
adapters: [uptime, ssl_dns, forms, latency, page_weight, content_standards, hosting, playwright_check]
budget_usd: 0.75
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
     A `forms` result of `down` means the contact form cannot send mail —
     real leads are being lost; say so plainly.
   - **SLOW** — `latency` status `slow`: sustained (last-hour median over
     budget and 2x the 7-day baseline), never a single sample. Name the
     numbers from `detail`; first step: check the host, not the monitor.
   - **HEAVY** — `page_weight` status `heavy`: homepage bytes over budget.
     Name the largest assets from `detail`; first step: re-encode images.
   - **CONTENT BROKEN** — `content_standards` status `broken`: a page is
     noindex, an expected analytics tag is missing, or an internal link is
     dead. Quote the first items from `detail`. `warn` (em dashes, title or
     description length, missing canonical) is house-style drift, never an
     alert.
   - **OFF VPS** — `hosting` status `off_vps`: the domain is not pointed at
     the VPS, so visitors get old hosting. First step: the site's DNS cutover.
3. DOWN or unexpected degradation → emit an alert: site, what failed, since
   when, first debugging step to try. RECOVERED → emit an all-clear that
   references the original alert.
4. This routine is strictly read-only. Never propose taking a corrective
   action yourself — suggest the fix, the operator decides and triggers it
   through an approval-gated command.

Write the full merged result set to `state/site-health.json`.
