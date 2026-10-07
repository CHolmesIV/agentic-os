#!/usr/bin/env python3
"""Weekly traffic report: first-party, read-only, no page scripts.

Reads the nginx access logs on this box (the static vhosts log with the
`vhost` format, which puts $host first) plus the form relay's outcome lines in
journald, and writes a per-site summary for the last N days:

  - pageviews and approximate visitors (humans only; bots filtered by UA)
  - top pages, top external referrers, human 404s
  - search and AI crawler hits (is Google crawling? are AI bots reading us?)
  - form submissions by outcome (delivered / tagged / blocked)

Outputs state/history/traffic.json and state/history/traffic-latest.md
(kept out of state/*.json on purpose so the morning digest doesn't pay to
read it). `--send` posts the markdown to Telegram via bot/send-digest.mjs.

Usage: traffic_report.py [--days 7] [--send]
"""
import argparse
import gzip
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = REPO_ROOT / "config" / "sites.yml"
OUT_DIR = REPO_ROOT / "state" / "history"
LOG_DIR = Path("/var/log/nginx")

LINE = re.compile(
    r'^(?P<host>\S+) (?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>\S+)[^"]*" '
    r'(?P<status>\d{3}) \S+ "(?P<ref>[^"]*)" "(?P<ua>[^"]*)"'
)
SEARCH_BOTS = {"Googlebot": "googlebot", "Bingbot": "bingbot"}
AI_BOTS = {"GPTBot": "gptbot", "OAI-SearchBot": "oai-searchbot", "ChatGPT-User": "chatgpt-user",
           "ClaudeBot": "claudebot", "Claude-User": "claude-user", "PerplexityBot": "perplexitybot",
           "Google-Extended": "google-extended", "Applebot": "applebot", "Bytespider": "bytespider",
           "CCBot": "ccbot", "Amazonbot": "amazonbot"}
BOT_UA = re.compile(
    r"bot|crawl|spider|slurp|facebookexternalhit|preview|monitor|uptime|curl|wget|python|"
    r"go-http|okhttp|axios|node-fetch|undici|headless|lighthouse|pagespeed|scan|httpclient|"
    r"java/|libwww|zgrab|masscan|censys|nmap|agentic-os|dataforseo|ahrefs|semrush|feed|"
    r"whatsapp|telegram|discord|slack|skype|^-$|^$", re.I)
SCANNER_PATH = re.compile(r"wp-|xmlrpc|/\.(env|git|aws|ssh|vscode|DS_Store)|readme\.html|phpmyadmin|"
                          r"/cgi-bin|\.(php|asp|aspx|jsp|cgi|bak|sql|zip|tar|gz)$|/vendor/|/admin|/config", re.I)
SPAM_REF = re.compile(r"backlink|link|dofollow|domainrating|seo|traffic|\.(space|website|site|store|xyz|top|online|buzz|icu)$|^\d+\.\d+\.\d+\.\d+$", re.I)
ASSET = re.compile(r"\.(css|js|mjs|map|png|jpe?g|webp|avif|gif|svg|ico|woff2?|ttf|otf|mp4|webm|"
                   r"pdf|txt|xml|json|webmanifest|php)$", re.I)


def load_domains() -> list[str]:
    data = yaml.safe_load(SITES_PATH.read_text()) or {}
    return [s["domain"] for s in data.get("sites", []) if s.get("domain") and s.get("host", "vps") == "vps"]


def log_files() -> list[Path]:
    return sorted(LOG_DIR.glob("access.log*"), key=lambda p: p.stat().st_mtime)


def open_log(p: Path):
    return gzip.open(p, "rt", errors="replace") if p.suffix == ".gz" else p.open(errors="replace")


def site_of(host: str, domains: list[str]) -> str | None:
    host = host.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host if host in domains else None


def collect(days: int, domains: list[str]) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    s = {d: {"first": None, "pageviews": 0, "visitors": set(), "pages": Counter(), "refs": Counter(),
             "not_found": Counter(), "search": Counter(), "ai": Counter()} for d in domains}
    rows, scanners = [], set()
    for p in log_files():
        if datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) < since:
            continue
        with open_log(p) as f:
            for line in f:
                m = LINE.match(line)
                if not m:
                    continue  # combined-format lines (PrimeWright) carry no host
                d = site_of(m["host"], domains)
                if not d:
                    continue
                try:
                    ts = datetime.strptime(m["ts"], "%d/%b/%Y:%H:%M:%S %z")
                except ValueError:
                    continue
                rows.append((d, ts, m))
                if SCANNER_PATH.search(m["path"].split("?")[0]):
                    scanners.add(m["ip"])  # probes for .env / wp-admin: not a visitor, whatever its UA says
    for d, ts, m in rows:
        if s[d]["first"] is None or ts < s[d]["first"]:
            s[d]["first"] = ts
        if ts < since:
            continue
        if True:
            if True:
                ua, path, status = m["ua"], m["path"].split("?")[0], m["status"]
                low = ua.lower()
                for name, key in SEARCH_BOTS.items():
                    if key in low:
                        s[d]["search"][name] += 1
                for name, key in AI_BOTS.items():
                    if key in low:
                        s[d]["ai"][name] += 1
                if BOT_UA.search(ua) or m["method"] != "GET" or m["ip"] in scanners:
                    continue
                if status == "404" and not ASSET.search(path):
                    s[d]["not_found"][path] += 1
                if status != "200" or ASSET.search(path):
                    continue
                s[d]["pageviews"] += 1
                s[d]["pages"][path] += 1
                s[d]["visitors"].add(hashlib.sha256(f'{m["ip"]}|{ua}|{ts:%Y-%m-%d}'.encode()).hexdigest()[:16])
                ref = m["ref"]
                if ref and ref != "-":
                    rh = (urlsplit(ref).hostname or "").lower()
                    if rh and site_of(rh, [d]) != d and not SPAM_REF.search(rh):
                        s[d]["refs"][rh[4:] if rh.startswith("www.") else rh] += 1
    return s


def forms(days: int) -> dict:
    out: dict = defaultdict(Counter)
    try:
        raw = subprocess.run(["journalctl", "-u", "form-relay", "-o", "cat", "--since", f"-{days} days"],
                             capture_output=True, text=True, timeout=60).stdout
    except Exception:  # noqa: BLE001
        return {}
    for line in raw.splitlines():
        if '"evt":"form"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        o = e.get("outcome", "?")
        bucket = {"delivered": "delivered", "delivered_tagged": "tagged_spam"}.get(o, "blocked")
        out[(e.get("site") or "?").removeprefix("www.")][bucket] += 1
    return out


def render(days: int, stats: dict, fx: dict) -> tuple[dict, str]:
    end = datetime.now(timezone.utc)
    doc = {"generated_at": end.isoformat(), "days": days, "sites": {}}
    started = end - timedelta(days=days)
    lines = [f"Traffic report, last {days} days (to {end:%b %d})", "Humans only. Visitors are approximate (one per device per day).", ""]
    order = sorted(stats, key=lambda d: stats[d]["pageviews"], reverse=True)
    for d in order:
        x = stats[d]
        f = dict(fx.get(d, {}))
        site = {
            "pageviews": x["pageviews"], "visitors": len(x["visitors"]),
            "top_pages": x["pages"].most_common(5), "top_referrers": x["refs"].most_common(5),
            "not_found": x["not_found"].most_common(3), "search_bots": dict(x["search"]),
            "ai_bots": dict(x["ai"]), "forms": f,
        }
        doc["sites"][d] = site
        first = x["first"]
        site["data_since"] = first.isoformat() if first else None
        if first is None:
            lines += [f"{d}: no log data yet (site-tagged logging started 2026-10-07)", ""]
            doc["sites"][d] = site
            continue
        partial = first > started + timedelta(hours=12)
        lines.append(f"{d}: {site['visitors']} visitors, {site['pageviews']} pageviews"
                     + (f" (data since {first:%b %d})" if partial else ""))
        if site["top_pages"]:
            lines.append("  Top: " + ", ".join(f"{p} ({n})" for p, n in site["top_pages"][:3]))
        if site["top_referrers"]:
            lines.append("  From: " + ", ".join(f"{h} ({n})" for h, n in site["top_referrers"][:3]))
        if f:
            lines.append(f"  Forms: {f.get('delivered', 0)} delivered, {f.get('tagged_spam', 0)} tagged spam, {f.get('blocked', 0)} blocked")
        g = site["search_bots"].get("Googlebot", 0)
        ai = sum(site["ai_bots"].values())
        lines.append(f"  Crawlers: Googlebot {g}, AI bots {ai}" + ("  <- Google isn't crawling" if g == 0 and not partial else ""))
        if site["not_found"]:
            lines.append("  404s: " + ", ".join(f"{p} ({n})" for p, n in site["not_found"]))
        lines.append("")
    return doc, "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--send", action="store_true", help="post the report to Telegram")
    a = ap.parse_args()
    domains = load_domains()
    doc, text = render(a.days, collect(a.days, domains), forms(a.days))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "traffic.json").write_text(json.dumps(doc, indent=2))
    md = OUT_DIR / "traffic-latest.md"
    md.write_text(text)
    print(text)
    if a.send:
        r = subprocess.run(["node", str(REPO_ROOT / "bot" / "send-digest.mjs"), str(md)], check=False)
        return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
