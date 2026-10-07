#!/usr/bin/env python3
"""Page-weight adapter — Tier 0, read-only.

Fast HTML with a 12 MB homepage is still a slow site. This adapter fetches
each site's homepage, follows every image/CSS/JS/font referenced directly in
the HTML (src, srcset first candidate, stylesheet/script hrefs, inline
url(...)), and sums the bytes actually transferred.

Status:
  - heavy : total > 3 MB, or any single asset > 1 MB
  - ok    : otherwise (assets over 300 KB are still listed in `detail`
            as advice; they don't page)

Measured at most once per 24h; between measurements the cached result in
state/history/page-weight.json is re-emitted unchanged, so the status stays
stable and nothing re-alerts. Skips `host: external` sites (not ours to
fix) and `check_host` sites (public DNS doesn't point at the copy we serve).
Opt out per site with `checks.page_weight: false`.
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = REPO_ROOT / "config" / "sites.yml"
CACHE_PATH = REPO_ROOT / "state" / "history" / "page-weight.json"
USER_AGENT = "agentic-os-pageweight/1.0"
REFRESH_SECONDS = 24 * 3600
TIMEOUT_SECONDS = 15
DEADLINE_SECONDS = 90          # whole adapter; the runner kills adapters at 120s
MAX_ASSETS = 60
MAX_ASSET_BYTES = 25_000_000   # stop counting past this; it's already "heavy"
HEAVY_TOTAL = 3_000_000
HEAVY_SINGLE = 1_000_000
ADVISE_SINGLE = 300_000

ASSET_EXT = r"(?:png|jpe?g|webp|avif|gif|svg|css|js|woff2?|ttf|otf)"
REF_PATTERNS = [
    re.compile(r"""\ssrc=["']([^"']+)["']""", re.I),
    re.compile(r"""\ssrcset=["']\s*([^"'\s,]+)""", re.I),
    re.compile(r"""<link[^>]+rel=["'](?:stylesheet|preload|icon)["'][^>]*href=["']([^"']+)["']""", re.I),
    re.compile(r"""<link[^>]+href=["']([^"']+)["'][^>]*rel=["'](?:stylesheet|preload|icon)["']""", re.I),
    re.compile(r"""url\(\s*["']?([^)"']+\.""" + ASSET_EXT + r""")(?:\?[^)"']*)?["']?\s*\)""", re.I),
]


def load_sites() -> list[dict]:
    with SITES_PATH.open() as f:
        data = yaml.safe_load(f) or {}
    return data.get("sites", [])


def load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:  # noqa: BLE001
        return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2))
    tmp.replace(CACHE_PATH)


def wire_bytes(session: requests.Session, url: str) -> int | None:
    """Bytes on the wire (compressed), streamed so big files don't sit in memory."""
    try:
        with session.get(url, stream=True, timeout=TIMEOUT_SECONDS,
                         headers={"Accept-Encoding": "gzip, br"}) as resp:
            if resp.status_code >= 400:
                return None
            total = 0
            for chunk in resp.raw.stream(65536, decode_content=False):
                total += len(chunk)
                if total > MAX_ASSET_BYTES:
                    break
            return total
    except requests.exceptions.RequestException:
        return None


def human(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{n // 1000} KB"


def measure(domain: str, deadline: float) -> dict:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    page = f"https://{domain}/"
    try:
        resp = session.get(page, timeout=TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as e:
        return {"status": "ok", "detail": f"not measured: homepage fetch failed ({e.__class__.__name__})"}
    if resp.status_code >= 400:
        return {"status": "ok", "detail": f"not measured: homepage returned {resp.status_code}"}
    html = resp.text
    html_bytes = wire_bytes(session, page) or len(resp.content)

    refs: list[str] = []
    for pat in REF_PATTERNS:
        for m in pat.finditer(html):
            ref = m.group(1).strip()
            if ref.startswith(("data:", "javascript:", "#", "mailto:", "tel:")):
                continue
            url = urljoin(page, ref)
            if urlparse(url).scheme not in ("http", "https"):
                continue
            if not re.search(r"\." + ASSET_EXT + r"(?:$|\?)", urlparse(url).path + ("?" if urlparse(url).query else ""), re.I):
                continue
            if url not in refs:
                refs.append(url)

    sizes: list[tuple[str, int]] = []
    partial = False
    for url in refs[:MAX_ASSETS]:
        if time.monotonic() > deadline:
            partial = True
            break
        n = wire_bytes(session, url)
        if n is not None:
            sizes.append((url, n))
    if len(refs) > MAX_ASSETS:
        partial = True

    total = html_bytes + sum(n for _, n in sizes)
    largest = sorted(sizes, key=lambda x: -x[1])
    big = [(u, n) for u, n in largest if n > ADVISE_SINGLE]
    heavy = total > HEAVY_TOTAL or (largest and largest[0][1] > HEAVY_SINGLE)

    detail = f"homepage {human(total)} across {len(sizes)} assets"
    if big:
        detail += "; largest: " + ", ".join(
            f"{urlparse(u).path.rsplit('/', 1)[-1][:40]} {human(n)}" for u, n in big[:5])
    if partial:
        detail += " (partial: asset cap or time limit reached)"
    return {"status": "heavy" if heavy else "ok", "detail": detail,
            "total_bytes": total, "asset_count": len(sizes)}


def main() -> int:
    try:
        sites = load_sites()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"adapter": "page_weight", "error": str(e)}), file=sys.stderr)
        return 1

    cache = load_cache()
    now = time.time()
    deadline = time.monotonic() + DEADLINE_SECONDS
    results = []
    for site in sites:
        domain = site.get("domain")
        checks = site.get("checks", {}) or {}
        if (not domain or site.get("host") == "external" or site.get("check_host")
                or checks.get("page_weight") is False):
            continue
        cached = cache.get(domain)
        if cached and now - cached.get("measured_at", 0) < REFRESH_SECONDS:
            entry = cached
        elif time.monotonic() > deadline:
            entry = cached or {"status": "ok", "detail": "not measured yet (time limit)", "measured_at": 0}
        else:
            entry = measure(domain, deadline)
            entry["measured_at"] = round(now)
            cache[domain] = entry
        measured = (datetime.fromtimestamp(entry["measured_at"], timezone.utc).strftime("%Y-%m-%d %H:%MZ")
                    if entry.get("measured_at") else "never")
        results.append({
            "domain": domain,
            "status": entry["status"],
            "total_bytes": entry.get("total_bytes"),
            "detail": f"{entry['detail']} (measured {measured})",
        })

    configured = {s.get("domain") for s in sites}
    save_cache({d: v for d, v in cache.items() if d in configured})

    print(json.dumps({
        "adapter": "page_weight",
        "ts": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
