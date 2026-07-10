#!/usr/bin/env node
/**
 * playwright_check.mjs — Tier 0, read-only browser smoke test.
 *
 * Reads config/sites.yml; for every site with checks.playwright: true,
 * launches headless Chromium, loads the homepage, asserts a real render
 * (non-empty <title> or an <h1>), collects console errors, clicks the
 * first same-page navigation link and asserts the URL/DOM actually
 * changed, and saves a screenshot to state/screens/<domain>.png.
 *
 * Contract (adapters/README.md): print exactly one JSON document to
 * stdout: {adapter, ts, results:[{domain, status, console_errors,
 * screenshot, detail}]}. A site failure is a *result*, not an adapter
 * failure — exit 0. Exit non-zero only when the adapter itself can't run
 * (bad config, Playwright/Chromium missing, etc).
 *
 * Flags:
 *   --site <domain>   Only check this one domain (looked up in sites.yml
 *                      for check_host/scheme overrides; run even if
 *                      checks.playwright isn't true — an explicit CLI
 *                      request overrides the config filter).
 * Env:
 *   SITE_URL           If set, ignore sites.yml entirely and run a single
 *                       ad hoc check against this URL (domain in the
 *                       output is the URL's hostname). For adapter testing.
 */

import { chromium } from "playwright";
import { readFileSync } from "node:fs";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import yaml from "js-yaml";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const SITES_PATH = path.join(REPO_ROOT, "config", "sites.yml");
const SCREENS_DIR = path.join(REPO_ROOT, "state", "screens");
const NAV_TIMEOUT_MS = 15000;

function nowIso() {
  return new Date().toISOString();
}

function parseArgs(argv) {
  const out = { site: null };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--site" && argv[i + 1]) {
      out.site = argv[i + 1];
      i++;
    }
  }
  return out;
}

function loadSites() {
  const raw = readFileSync(SITES_PATH, "utf8");
  const data = yaml.load(raw) || {};
  return data.sites || [];
}

/** Build the {url, headers, targetDesc} for a site the same way uptime.py does. */
function targetFor(site) {
  const domain = site.domain;
  const checkHost = site.check_host;
  const scheme = site.check_host_scheme || "https";
  if (checkHost) {
    return {
      url: `${scheme}://${checkHost}/`,
      headers: { Host: domain },
      targetDesc: `${scheme}://${checkHost}/ (Host: ${domain})`,
    };
  }
  return {
    url: `https://${domain}/`,
    headers: {},
    targetDesc: `https://${domain}/`,
  };
}

async function checkOneTarget(browser, domain, { url, headers, targetDesc }) {
  const consoleErrors = [];
  const detailBits = [];
  let status = "down";
  let screenshotPath = null;

  const context = await browser.newContext({
    userAgent: "agentic-os-playwright-check/1.0",
    extraHTTPHeaders: headers,
  });
  const page = await context.newPage();

  page.on("console", (msg) => {
    if (msg.type() === "error") {
      consoleErrors.push(msg.text().slice(0, 500));
    }
  });
  page.on("pageerror", (err) => {
    consoleErrors.push(`pageerror: ${String(err).slice(0, 500)}`);
  });

  try {
    const resp = await page.goto(url, {
      timeout: NAV_TIMEOUT_MS,
      waitUntil: "load",
    });

    const httpStatus = resp ? resp.status() : null;
    if (httpStatus !== null && httpStatus >= 400) {
      detailBits.push(`homepage returned HTTP ${httpStatus}`);
    }

    const title = (await page.title()) || "";
    const h1Count = await page.locator("h1").count();
    const rendered = title.trim().length > 0 || h1Count > 0;
    if (!rendered) {
      detailBits.push("no non-empty <title> or <h1> found — render assertion failed");
    }

    // Screenshot regardless of nav outcome — useful for debugging either way.
    mkdirSync(SCREENS_DIR, { recursive: true });
    screenshotPath = path.join("state", "screens", `${domain}.png`);
    await page.screenshot({ path: path.join(REPO_ROOT, screenshotPath), fullPage: false });

    // Click first same-document nav link and assert navigation actually happened.
    let navOk = false;
    let navSkippedReason = null;
    const beforeUrl = page.url();
    const link = page.locator("a[href]").first();
    const linkCount = await link.count();
    if (linkCount === 0) {
      navSkippedReason = "no <a href> links found on homepage";
    } else {
      try {
        const [navResult] = await Promise.allSettled([
          page.waitForURL((u) => u.toString() !== beforeUrl, { timeout: NAV_TIMEOUT_MS }),
          link.click({ timeout: NAV_TIMEOUT_MS }),
        ]);
        // waitForURL resolves as first item; if it rejected, treat as failed nav.
        if (navResult.status === "fulfilled") {
          navOk = true;
        } else {
          navSkippedReason = `nav did not complete: ${navResult.reason}`;
        }
      } catch (e) {
        navSkippedReason = `nav click failed: ${String(e).slice(0, 300)}`;
      }
    }

    if (navSkippedReason) {
      detailBits.push(navSkippedReason);
    }

    if (!rendered) {
      status = "down";
    } else if (
      (httpStatus !== null && httpStatus >= 400) ||
      consoleErrors.length > 0 ||
      (!navOk && linkCount > 0)
    ) {
      status = "degraded";
    } else {
      status = "ok";
    }

    detailBits.push(`checked ${targetDesc}; nav=${navOk ? "ok" : "skipped/failed"}`);
  } catch (err) {
    status = "down";
    detailBits.push(`navigation failed: ${String(err).slice(0, 500)}`);
    // Best-effort screenshot even on failure, if the page got far enough to paint anything.
    try {
      mkdirSync(SCREENS_DIR, { recursive: true });
      screenshotPath = path.join("state", "screens", `${domain}.png`);
      await page.screenshot({ path: path.join(REPO_ROOT, screenshotPath), fullPage: false });
    } catch {
      screenshotPath = null;
    }
  } finally {
    await context.close();
  }

  return {
    domain,
    status,
    console_errors: consoleErrors,
    screenshot: screenshotPath,
    detail: detailBits.join("; "),
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const siteUrlOverride = process.env.SITE_URL;

  let jobs = [];

  if (siteUrlOverride) {
    let domain;
    try {
      domain = new URL(siteUrlOverride).hostname;
    } catch {
      domain = siteUrlOverride;
    }
    jobs = [{ domain, target: { url: siteUrlOverride, headers: {}, targetDesc: siteUrlOverride } }];
  } else {
    let sites;
    try {
      sites = loadSites();
    } catch (e) {
      process.stderr.write(`ERROR loading ${SITES_PATH}: ${e}\n`);
      return 1;
    }

    let candidates = sites;
    if (args.site) {
      candidates = sites.filter((s) => s.domain === args.site);
      if (candidates.length === 0) {
        process.stderr.write(`ERROR: --site ${args.site} not found in config/sites.yml\n`);
        return 1;
      }
    } else {
      candidates = sites.filter((s) => (s.checks || {}).playwright === true);
    }

    jobs = candidates.map((s) => ({ domain: s.domain, target: targetFor(s) }));
  }

  if (jobs.length === 0) {
    const doc = { adapter: "playwright_check", ts: nowIso(), results: [] };
    console.log(JSON.stringify(doc, null, 2));
    return 0;
  }

  let browser;
  try {
    browser = await chromium.launch({ headless: true });
  } catch (e) {
    process.stderr.write(`ERROR launching chromium: ${e}\n`);
    return 1;
  }

  const results = [];
  try {
    for (const job of jobs) {
      const result = await checkOneTarget(browser, job.domain, job.target);
      results.push(result);
    }
  } finally {
    await browser.close();
  }

  const doc = {
    adapter: "playwright_check",
    ts: nowIso(),
    results,
  };
  console.log(JSON.stringify(doc, null, 2));
  return 0;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    process.stderr.write(`FATAL: ${err && err.stack ? err.stack : err}\n`);
    process.exit(1);
  });
