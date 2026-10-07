#!/usr/bin/env python3
"""Content-standards adapter — Tier 0, read-only.

The house rules for every public page, checked once a day per site
(cached in state/history/content-standards.json so the status is stable
between measurements and nothing re-alerts):

  broken (pages)  - any page tells search engines not to index it
                    (meta robots / X-Robots-Tag noindex), an expected
                    analytics tag is missing, or an internal link 404s
  warn            - em dashes in copy, title outside 30-60 chars,
                    meta description outside 70-160 chars, missing
                    canonical. Reported in detail; never pages.
  ok              - none of the above

Pages come from the site's sitemap (capped) plus the homepage. Sites can
declare `analytics: [G-XXXX, ...]` in config/sites.yml; each id must appear
on every crawled page. Opt out with `checks.content: false`. Skips
`host: external` sites. Always audits the public domain, even for sites
checked elsewhere via `check_host`. A Hostinger CDN bot-challenge
page is reported as "not measured", never as a failure.
"""
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = REPO_ROOT / "config" / "sites.yml"
CACHE_PATH = REPO_ROOT / "state" / "history" / "content-standards.json"
UA = "agentic-os-content/1.0"
REFRESH_SECONDS = 24 * 3600
MAX_PAGES = 60
TIMEOUT = 15
DEADLINE_SECONDS = 95

DASH_RE = re.compile(r"—|―|&mdash;|&#8212;| – | -- ")
CHALLENGE = "Checking your browser"


def load_sites():
    with SITES_PATH.open() as f:
        return (yaml.safe_load(f) or {}).get("sites", [])


def load_cache():
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:  # noqa: BLE001
        return {}


def save_cache(c):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, indent=2))
    tmp.replace(CACHE_PATH)


def visible_text(doc):
    doc = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", doc)
    return html.unescape(re.sub(r"<[^>]+>", " ", doc))


def get(session, url):
    r = session.get(url, timeout=TIMEOUT)
    if "html" in r.headers.get("Content-Type", "") or "xml" in r.headers.get("Content-Type", ""):
        r.encoding = "utf-8"
    return r


def audit_site(site, deadline):
    d = site["domain"]
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Encoding": "gzip"})
    home = f"https://{d}/"
    urls = [home]
    try:
        sm = get(s, f"https://{d}/sitemap.xml")
        if sm.status_code == 200 and CHALLENGE not in sm.text:
            urls += [html.unescape(u) for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sm.text)]
    except requests.RequestException:
        pass
    seen, pages = set(), []
    for u in urls:
        u = urldefrag(u)[0]
        if u in seen or len(pages) >= MAX_PAGES or time.monotonic() > deadline:
            continue
        seen.add(u)
        try:
            r = get(s, u)
        except requests.RequestException as e:
            pages.append({"url": u, "error": e.__class__.__name__})
            continue
        if CHALLENGE in r.text:
            return {"status": "ok", "detail": "not measured: hosting bot-challenge page served to the monitor"}
        pages.append({"url": u, "status": r.status_code, "html": r.text if r.status_code == 200 else "",
                      "xrobots": r.headers.get("X-Robots-Tag", "")})

    broken, warn = [], []
    expected = site.get("analytics") or []
    internal = set()
    dash_pages = 0
    for p in pages:
        path = urlparse(p["url"]).path or "/"
        if p.get("error") or p.get("status") != 200:
            broken.append(f"{path} -> {p.get('error') or p.get('status')}")
            continue
        doc = p["html"]
        robots = " ".join(re.findall(r'<meta[^>]+name=["\']robots["\'][^>]+content=["\']([^"\']+)', doc, re.I)) + " " + p["xrobots"]
        if "noindex" in robots.lower() and "404" not in path:
            broken.append(f"{path} is noindex")
        for gid in expected:
            if gid not in doc:
                broken.append(f"{path} missing analytics {gid}")
        title = html.unescape((re.search(r"(?is)<title>(.*?)</title>", doc) or [None, ""])[1]).strip()
        desc_m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', doc, re.I)
        desc = html.unescape(desc_m.group(1)).strip() if desc_m else ""
        if not 30 <= len(title) <= 60:
            warn.append(f"{path} title {len(title)} chars")
        if not 70 <= len(desc) <= 160:
            warn.append(f"{path} description {len(desc)} chars")
        if not re.search(r'<link[^>]+rel=["\']canonical["\']', doc, re.I):
            warn.append(f"{path} no canonical")
        if DASH_RE.search(title + " " + desc + " " + visible_text(doc)):
            dash_pages += 1
        for href in re.findall(r'<a\b[^>]*href=["\']([^"\'#]+)', doc, re.I):
            full = urldefrag(urljoin(p["url"], href.strip()))[0]
            if (urlparse(full).hostname or "").replace("www.", "") == d and not re.search(r"\.(pdf|jpe?g|png|webp|zip)$", full, re.I):
                internal.add(full)
    if dash_pages:
        warn.insert(0, f"em dashes on {dash_pages} page(s)")
    for u in sorted(internal - seen)[:80]:
        if time.monotonic() > deadline:
            break
        try:
            st = s.get(u, timeout=TIMEOUT, allow_redirects=True).status_code
        except requests.RequestException as e:
            st = e.__class__.__name__
        if st != 200:
            broken.append(f"link {urlparse(u).path} -> {st}")

    status = "broken" if broken else ("warn" if warn else "ok")
    bits = [f"{len(pages)} pages checked"]
    if broken:
        bits.append("BROKEN: " + "; ".join(broken[:6]) + (f" (+{len(broken) - 6} more)" if len(broken) > 6 else ""))
    if warn:
        bits.append("standards: " + "; ".join(warn[:6]) + (f" (+{len(warn) - 6} more)" if len(warn) > 6 else ""))
    return {"status": status, "detail": " | ".join(bits), "broken_count": len(broken), "warn_count": len(warn)}


def main():
    try:
        sites = load_sites()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"adapter": "content_standards", "error": str(e)}), file=sys.stderr)
        return 1
    cache = load_cache()
    now = time.time()
    deadline = time.monotonic() + DEADLINE_SECONDS
    results = []
    for site in sites:
        d = site.get("domain")
        if (not d or site.get("host") == "external"
                or (site.get("checks") or {}).get("content") is False):
            continue
        c = cache.get(d)
        if c and now - c.get("measured_at", 0) < REFRESH_SECONDS:
            entry = c
        elif time.monotonic() > deadline:
            entry = c or {"status": "ok", "detail": "not measured yet (time limit)", "measured_at": 0}
        else:
            entry = audit_site(site, deadline)
            entry["measured_at"] = round(now)
            cache[d] = entry
        when = (datetime.fromtimestamp(entry["measured_at"], timezone.utc).strftime("%Y-%m-%d %H:%MZ")
                if entry.get("measured_at") else "never")
        results.append({"domain": d, "status": entry["status"], "detail": f"{entry['detail']} (measured {when})"})
    configured = {s.get("domain") for s in sites}
    save_cache({k: v for k, v in cache.items() if k in configured})
    print(json.dumps({"adapter": "content_standards", "ts": datetime.now(timezone.utc).isoformat(), "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
