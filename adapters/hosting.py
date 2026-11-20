#!/usr/bin/env python3
"""Hosting adapter, Tier 0, read-only.

Catches the "it's on the VPS but the domain never got pointed there" gap
(a site staged on the VPS whose DNS still points at the old host).

For every site with `host: vps` (and no `check_host`), resolve the apex and
www through public resolvers and confirm at least one A record is the VPS.

  ok       - domain points at the VPS
  off_vps  - domain resolves somewhere else (pages once, recovers once)
  ok w/note- DNS lookup failed this run (no alert; uptime covers outages)

VPS address comes from `systems.vps_ip` in config/systems.yml. There is no
default: when it is unset the adapter exits with an error.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = REPO_ROOT / "config" / "sites.yml"
SYSTEMS_PATH = REPO_ROOT / "config" / "systems.yml"
RESOLVERS = ["1.1.1.1", "8.8.8.8"]


def vps_ip():
    try:
        data = yaml.safe_load(SYSTEMS_PATH.read_text()) or {}
        return str((data.get("systems") or {}).get("vps_ip") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def resolve(name, resolver):
    try:
        import dns.resolver  # dnspython, already used by ssl_dns
        r = dns.resolver.Resolver(configure=False)
        r.nameservers = [resolver]
        r.lifetime = 5
        return sorted({a.to_text() for a in r.resolve(name, "A")})
    except Exception:  # noqa: BLE001
        p = subprocess.run(["dig", "+short", "A", name, f"@{resolver}"], capture_output=True, text=True, timeout=10)
        return sorted({x for x in p.stdout.split() if x[0].isdigit()})


def main():
    try:
        sites = (yaml.safe_load(SITES_PATH.read_text()) or {}).get("sites", [])
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"adapter": "hosting", "error": str(e)}), file=sys.stderr)
        return 1
    ip = vps_ip()
    if not ip:
        print(json.dumps({"adapter": "hosting", "error": "systems.vps_ip is not set in config/systems.yml"}), file=sys.stderr)
        return 1
    results = []
    for site in sites:
        d = site.get("domain")
        if not d or site.get("host") != "vps" or site.get("check_host"):
            continue
        seen = {}
        for name in (d, f"www.{d}"):
            ips = []
            for res in RESOLVERS:
                ips = resolve(name, res)
                if ips:
                    break
            seen[name] = ips
        apex = seen[d]
        if not apex:
            results.append({"domain": d, "status": "ok", "detail": "DNS lookup failed this run; not judged"})
        elif ip in apex:
            www = seen[f"www.{d}"]
            note = "" if (not www or ip in www) else f"; www resolves to {www} (not the VPS)"
            results.append({"domain": d, "status": "ok", "detail": f"apex -> {ip} (VPS){note}"})
        else:
            results.append({"domain": d, "status": "off_vps",
                            "detail": f"sites.yml says host: vps but {d} resolves to {apex}, not {ip}. "
                                      "Visitors are getting the old host; finish the DNS cutover."})
    print(json.dumps({"adapter": "hosting", "ts": datetime.now(timezone.utc).isoformat(), "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
