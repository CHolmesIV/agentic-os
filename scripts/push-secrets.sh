#!/usr/bin/env bash
# push-secrets.sh — the easy button.
# Fill .env and config/*.yml locally in your normal editor, run this, done.
# It copies them to the VPS and re-validates. No installs, no restarts, safe to
# run as often as you like (e.g. every time you change a credential).
set -euo pipefail

# --- VPS target (override via env if these ever change) ----------------------
VPS_HOST="${VPS_HOST:-srv1797465.hstgr.cloud}"
VPS_USER="${VPS_USER:-root}"
VPS_KEY="${VPS_KEY:-$HOME/.ssh/cholmesiv_ed25519}"
REMOTE_DIR="${REMOTE_DIR:-/opt/agentic-os}"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

ssh_opts=(-i "$VPS_KEY" -o StrictHostKeyChecking=accept-new)
scp_opts=(-i "$VPS_KEY" -o StrictHostKeyChecking=accept-new)
target="$VPS_USER@$VPS_HOST"

# --- preflight: do the local files exist and look filled in? -----------------
[ -f .env ] || { echo "✗ .env not found. Copy .env.example to .env and fill it."; exit 1; }
[ -f config/sites.yml ] || { echo "✗ config/sites.yml not found."; exit 1; }
[ -f config/systems.yml ] || { echo "✗ config/systems.yml not found."; exit 1; }

missing=""
for k in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID ANTHROPIC_API_KEY; do
  v="$(grep -E "^$k=" .env | cut -d= -f2- || true)"
  [ -z "$v" ] && missing="$missing $k"
done
if [ -n "$missing" ]; then
  echo "⚠ These core keys are still blank in .env:$missing"
  echo "  (pushing anyway — fill them and re-run when ready)"
fi

echo "→ Pushing .env + configs to $target:$REMOTE_DIR"
ssh "${ssh_opts[@]}" "$target" "mkdir -p $REMOTE_DIR/config"
scp "${scp_opts[@]}" .env               "$target:$REMOTE_DIR/.env"
scp "${scp_opts[@]}" config/sites.yml   "$target:$REMOTE_DIR/config/sites.yml"
scp "${scp_opts[@]}" config/systems.yml "$target:$REMOTE_DIR/config/systems.yml"
ssh "${ssh_opts[@]}" "$target" "chmod 600 $REMOTE_DIR/.env"

echo "→ Validating config on the VPS"
ssh "${ssh_opts[@]}" "$target" "cd $REMOTE_DIR && python3 runner/validate_config.py"

echo "✓ Done. Secrets and configs are live on the VPS."
