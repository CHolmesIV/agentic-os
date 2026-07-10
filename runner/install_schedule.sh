#!/usr/bin/env bash
# Installs cron entries for every routine in routines/*.md (excludes
# *.example.md), invoking runner/run_routine.py with absolute paths.
# Idempotent: existing agentic-os entries (marker-delimited) are replaced.
#
# Usage:
#   runner/install_schedule.sh              # install/update cron entries
#   runner/install_schedule.sh --uninstall  # remove all agentic-os cron entries
#
# macOS note: cron is the primary, supported mechanism for this project
# (target deployment is a VPS running Ubuntu 24.04). On macOS, a launchd
# plist per routine would be the idiomatic alternative, but that support is
# optional / nice-to-have for local dev only. This script always uses cron;
# if `crontab` isn't usable on this machine, it warns and exits without
# installing rather than attempting a launchd fallback.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROUTINES_DIR="$REPO_ROOT/routines"
RUNNER="$REPO_ROOT/runner/run_routine.py"
MARKER_BEGIN="# >>> agentic-os managed block >>>"
MARKER_END="# <<< agentic-os managed block <<<"
PYTHON_BIN="$(command -v python3 || true)"

usage() {
  echo "Usage: $0 [--uninstall]" >&2
  exit 1
}

UNINSTALL=0
case "${1:-}" in
  "") ;;
  --uninstall) UNINSTALL=1 ;;
  -h|--help) usage ;;
  *) usage ;;
esac

if ! command -v crontab >/dev/null 2>&1; then
  echo "WARNING: 'crontab' not found on this system." >&2
  echo "This project targets a cron-based VPS (Ubuntu 24.04)." >&2
  echo "macOS launchd support is optional/nice-to-have and not implemented" >&2
  echo "by this script. Skipping schedule installation." >&2
  exit 0
fi

# Strip any existing agentic-os managed block from the current crontab.
strip_managed_block() {
  crontab -l 2>/dev/null | awk -v b="$MARKER_BEGIN" -v e="$MARKER_END" '
    $0==b {skip=1; next}
    $0==e {skip=0; next}
    !skip {print}
  '
}

if [ "$UNINSTALL" -eq 1 ]; then
  new_crontab="$(strip_managed_block)"
  printf '%s\n' "$new_crontab" | crontab -
  echo "Removed agentic-os cron entries."
  exit 0
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "ERROR: python3 not found; cannot install schedule." >&2
  exit 1
fi

if [ ! -d "$ROUTINES_DIR" ]; then
  echo "ERROR: routines directory not found: $ROUTINES_DIR" >&2
  exit 1
fi

new_entries=""
for routine in "$ROUTINES_DIR"/*.md; do
  [ -e "$routine" ] || continue
  base="$(basename "$routine")"
  case "$base" in
    *.example.md) continue ;;
  esac

  schedule="$(awk '
    /^---$/ { d++; next }
    d==1 && /^schedule:/ {
      sub(/^schedule:[[:space:]]*/, "");
      gsub(/^"|"$/, "");
      gsub(/^'"'"'|'"'"'$/, "");
      print;
      exit
    }
    d==2 { exit }
  ' "$routine")"

  if [ -z "$schedule" ]; then
    echo "WARNING: no 'schedule:' found in frontmatter of $routine, skipping" >&2
    continue
  fi

  log_file="$REPO_ROOT/logs/cron-$(basename "$routine" .md).log"
  entry="$schedule cd $REPO_ROOT && $PYTHON_BIN $RUNNER $routine >> $log_file 2>&1"
  new_entries="${new_entries}${entry}"$'\n'
done

if [ -z "$new_entries" ]; then
  echo "WARNING: no routines found to schedule (nothing in $ROUTINES_DIR besides examples?)" >&2
fi

existing="$(strip_managed_block)"
{
  printf '%s\n' "$existing"
  echo "$MARKER_BEGIN"
  printf '%s' "$new_entries"
  echo "$MARKER_END"
} | crontab -

echo "Installed $(printf '%s' "$new_entries" | grep -c . || true) cron entries."
crontab -l | sed -n "/$MARKER_BEGIN/,/$MARKER_END/p"
