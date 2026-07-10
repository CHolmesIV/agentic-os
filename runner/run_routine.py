#!/usr/bin/env python3
"""
Runner: executes one routine file.

Usage:
    run_routine.py routines/site-health.md [--no-llm] [--dry-llm]
                                             [--adapters-dir DIR]

For each routine:
  1. Parse YAML frontmatter (schedule, model, llm, adapters, budget_usd, alert).
  2. Run each listed adapter (adapters/<name>.py via python3, adapters/<name>.mjs
     via node) and collect its single JSON stdout document.
  3. Diff current vs previous state/<routine-name>.json on per-domain `status`
     only (ignoring `ts`/`latency`).
  4. Per `llm` mode, decide whether to invoke `claude -p` with the routine body
     + current/previous results as the prompt.
  5. Write merged state, append audit records, handle alerts + digest hand-off.

Exit code is 0 unless the runner itself fails (a failed/degraded routine run
is a normal outcome, not a runner failure).
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    print("ERROR: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent

MODEL_MAP = {
    "haiku": "claude-haiku-4-5-20251001",
    # "strong" -> omit --model, inherit the CLI's default model
}

REDACT_KEY_RE = re.compile(r"(token|key|password|secret)", re.IGNORECASE)


# --------------------------------------------------------------------------
# Frontmatter / routine parsing
# --------------------------------------------------------------------------

def parse_routine(path: Path) -> tuple[dict[str, Any], str]:
    """Return (frontmatter dict, body markdown) for a routine file."""
    text = path.read_text()
    if not text.startswith("---"):
        raise ValueError(f"{path}: missing YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"{path}: malformed frontmatter (need opening/closing ---)")
    frontmatter = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip("\n")
    return frontmatter, body


def routine_name(frontmatter: dict[str, Any], path: Path) -> str:
    return frontmatter.get("name") or path.stem


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------

def run_adapter(name: str, adapters_dir: Path) -> dict[str, Any]:
    """Run a single adapter by name, return its parsed JSON stdout doc.

    Detects adapters/<name>.py (run via python3) or adapters/<name>.mjs
    (run via node). Adapters must print exactly one JSON document to stdout:
    {"adapter": ..., "ts": ..., "results": [{"domain": ..., "status": ...}, ...]}
    """
    py_path = adapters_dir / f"{name}.py"
    js_path = adapters_dir / f"{name}.mjs"

    if py_path.exists():
        cmd = ["python3", str(py_path)]
    elif js_path.exists():
        cmd = ["node", str(js_path)]
    else:
        return {
            "adapter": name,
            "ts": now_iso(),
            "results": [],
            "error": f"adapter not found: {py_path} or {js_path}",
        }

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as exc:  # noqa: BLE001
        return {"adapter": name, "ts": now_iso(), "results": [], "error": str(exc)}

    if proc.returncode != 0:
        return {
            "adapter": name,
            "ts": now_iso(),
            "results": [],
            "error": f"exit {proc.returncode}: {proc.stderr.strip()[:500]}",
        }

    try:
        doc = json.loads(proc.stdout.strip())
    except json.JSONDecodeError as exc:
        return {
            "adapter": name,
            "ts": now_iso(),
            "results": [],
            "error": f"invalid JSON from adapter: {exc}",
        }
    return doc


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# Diffing (per-domain status only)
# --------------------------------------------------------------------------

def extract_domain_status(doc: dict[str, Any]) -> dict[str, Any]:
    """Map domain -> status for one adapter's results list."""
    out: dict[str, Any] = {}
    for r in doc.get("results", []) or []:
        domain = r.get("domain")
        if domain is not None:
            out[domain] = r.get("status")
    return out


def has_change(current_docs: list[dict[str, Any]], previous_state: dict[str, Any] | None) -> bool:
    if previous_state is None:
        return True
    prev_adapters = {a.get("adapter"): a for a in previous_state.get("adapters", [])}
    for doc in current_docs:
        name = doc.get("adapter")
        prev_doc = prev_adapters.get(name)
        if prev_doc is None:
            return True
        if extract_domain_status(doc) != extract_domain_status(prev_doc):
            return True
    return False


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------

def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if REDACT_KEY_RE.search(str(k)) else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def append_audit(log_path: Path, record: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(json.dumps(redact(record)) + "\n")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------
# LLM invocation
# --------------------------------------------------------------------------

def build_claude_command(
    frontmatter: dict[str, Any],
    body: str,
    current_docs: list[dict[str, Any]],
    previous_state: dict[str, Any] | None,
) -> tuple[list[str], str]:
    """Build the `claude -p` command + full prompt text."""
    prompt_payload = {
        "current_results": current_docs,
        "previous_results": (previous_state or {}).get("adapters", []),
    }
    prompt = body + "\n\n---\n\n```json\n" + json.dumps(prompt_payload, indent=2) + "\n```"

    cmd = ["claude", "-p", prompt]

    model = frontmatter.get("model", "strong")
    mapped = MODEL_MAP.get(model)
    if mapped:
        cmd += ["--model", mapped]
    # "strong" (or anything unmapped) -> omit --model, inherit default.

    budget = frontmatter.get("budget_usd")
    if budget is not None:
        cmd += ["--max-budget-usd", str(budget)]

    cmd += ["--output-format", "json"]
    cmd += ["--allowedTools", "Read"]

    return cmd, prompt


def run_claude(cmd: list[str]) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "raw": ""}
    if proc.returncode != 0:
        return {"error": f"exit {proc.returncode}: {proc.stderr.strip()[:1000]}", "raw": proc.stdout}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw": proc.stdout}


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Run one agentic-os routine.")
    parser.add_argument("routine", type=Path, help="Path to routine markdown file")
    parser.add_argument("--no-llm", action="store_true", help="Skip the LLM call entirely")
    parser.add_argument(
        "--dry-llm",
        action="store_true",
        help="Print the claude command instead of running it",
    )
    parser.add_argument(
        "--adapters-dir",
        type=Path,
        default=REPO_ROOT / "adapters",
        help="Override adapters directory (for testing)",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=REPO_ROOT / "state",
        help="Override state directory (for testing)",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=REPO_ROOT / "logs",
        help="Override logs directory (for testing)",
    )
    args = parser.parse_args()

    try:
        frontmatter, body = parse_routine(args.routine)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR parsing routine {args.routine}: {exc}", file=sys.stderr)
        return 1

    name = routine_name(frontmatter, args.routine)
    state_path = args.state_dir / f"{name}.json"
    audit_path = args.logs_dir / "audit.jsonl"

    task_id = new_id("task")
    run_id = new_id("run")
    started = now_iso()

    append_audit(audit_path, {
        "type": "Task",
        "id": task_id,
        "routine": name,
        "initiator": "cron",
        "ts": started,
    })

    # --- previous state -----------------------------------------------
    previous_state: dict[str, Any] | None = None
    if state_path.exists():
        try:
            previous_state = json.loads(state_path.read_text())
        except Exception:  # noqa: BLE001
            previous_state = None

    # --- adapters -------------------------------------------------------
    adapter_names = frontmatter.get("adapters") or []
    current_docs: list[dict[str, Any]] = []
    for adapter_name in adapter_names:
        doc = run_adapter(adapter_name, args.adapters_dir)
        current_docs.append(doc)
        append_audit(audit_path, {
            "type": "Run",
            "id": new_id("run"),
            "task_id": task_id,
            "routine": name,
            "tool": f"adapter:{adapter_name}",
            "params": {},
            "outcome": "failed" if doc.get("error") else "ok",
            "ts": now_iso(),
        })

    changed = has_change(current_docs, previous_state)

    # --- decide on LLM invocation ----------------------------------------
    llm_mode = frontmatter.get("llm", "never")
    should_invoke_llm = (
        not args.no_llm
        and llm_mode != "never"
        and (llm_mode == "always" or (llm_mode == "on-change" and changed))
    )

    llm_result: dict[str, Any] | None = None
    if llm_mode != "never" and (should_invoke_llm or args.dry_llm):
        cmd, _prompt = build_claude_command(frontmatter, body, current_docs, previous_state)
        if args.dry_llm:
            print(" ".join(_shell_quote(c) for c in cmd))
        elif should_invoke_llm:
            llm_result = run_claude(cmd)
            append_audit(audit_path, {
                "type": "Run",
                "id": new_id("run"),
                "task_id": task_id,
                "routine": name,
                "tool": "claude -p",
                "params": {"model": frontmatter.get("model", "strong"), "budget_usd": frontmatter.get("budget_usd")},
                "outcome": "failed" if llm_result and llm_result.get("error") else "ok",
                "ts": now_iso(),
            })

    # --- merge + write state ---------------------------------------------
    merged_state = {
        "routine": name,
        "ts": now_iso(),
        "adapters": current_docs,
        "changed": changed,
    }
    if llm_result is not None:
        merged_state["llm_result"] = llm_result

    if not args.dry_llm:
        args.state_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(merged_state, indent=2))

    # --- alerts -------------------------------------------------------
    if frontmatter.get("alert") == "telegram" and llm_result is not None:
        alerts_path = args.state_dir / "pending-alerts.json"
        pending: list[Any] = []
        if alerts_path.exists():
            try:
                pending = json.loads(alerts_path.read_text())
            except Exception:  # noqa: BLE001
                pending = []
        pending.append({
            "routine": name,
            "task_id": task_id,
            "ts": now_iso(),
            "result": llm_result,
        })
        alerts_path.write_text(json.dumps(pending, indent=2))
        append_audit(audit_path, {
            "type": "Event",
            "id": new_id("evt"),
            "routine": name,
            "ts": now_iso(),
            "note": "alert queued to state/pending-alerts.json",
        })

    # --- morning-digest special case --------------------------------------
    if name == "morning-digest" and not args.dry_llm:
        digest_path = args.state_dir / "digest-latest.md"
        send_script = REPO_ROOT / "bot" / "send-digest.mjs"
        if digest_path.exists() and send_script.exists():
            subprocess.run(["node", str(send_script), str(digest_path)], check=False)
        elif not digest_path.exists():
            print(f"WARNING: {digest_path} not found after morning-digest run; digest not sent", file=sys.stderr)
        else:
            print(f"WARNING: {send_script} not found; digest not sent", file=sys.stderr)

    append_audit(audit_path, {
        "type": "Run",
        "id": run_id,
        "task_id": task_id,
        "routine": name,
        "tool": "run_routine.py",
        "outcome": "ok",
        "changed": changed,
        "llm_invoked": llm_result is not None,
        "ts": now_iso(),
    })

    return 0


def _shell_quote(s: str) -> str:
    if re.match(r"^[\w@%+=:,./-]+$", s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    sys.exit(main())
