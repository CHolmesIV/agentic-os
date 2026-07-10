#!/usr/bin/env bash
# deploy-to-vps.sh — one-time first-run bootstrap of the orchestrator on the VPS.
# Installs runtimes, clones the repo, then hands off to push-secrets.sh.
# SHARED BOX: this touches a VPS that also runs PrimeWright. It only installs
# language runtimes + this repo under /opt/agentic-os and NEVER touches nginx,
# other services, or /var/www. It pauses for your confirmation before apt.
set -euo pipefail

VPS_HOST="${VPS_HOST:-srv1797465.hstgr.cloud}"
VPS_USER="${VPS_USER:-root}"
VPS_KEY="${VPS_KEY:-$HOME/.ssh/cholmesiv_ed25519}"
REMOTE_DIR="${REMOTE_DIR:-/opt/agentic-os}"
REPO_URL="${REPO_URL:-https://github.com/CHolmesIV/agentic-os.git}"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"
ssh_opts=(-i "$VPS_KEY" -o StrictHostKeyChecking=accept-new)
target="$VPS_USER@$VPS_HOST"

echo "This will, on $target:"
echo "  1. apt install node 20 + python 3.12 (if missing)"
echo "  2. npm i -g @anthropic-ai/claude-code"
echo "  3. git clone/pull $REPO_URL into $REMOTE_DIR"
echo "  4. npm install + playwright chromium + pip deps"
echo "  5. copy your .env + configs up (via push-secrets.sh)"
echo "It will NOT touch nginx, other services, or /var/www."
read -r -p "Proceed? [y/N] " ok
[ "$ok" = "y" ] || { echo "aborted."; exit 0; }

ssh "${ssh_opts[@]}" "$target" bash -s <<REMOTE
set -euo pipefail
command -v node >/dev/null || { curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs; }
command -v python3 >/dev/null || apt-get install -y python3 python3-pip
command -v claude >/dev/null || npm install -g @anthropic-ai/claude-code
if [ -d "$REMOTE_DIR/.git" ]; then
  cd "$REMOTE_DIR" && git pull --ff-only
else
  git clone "$REPO_URL" "$REMOTE_DIR" && cd "$REMOTE_DIR"
fi
npm install
npx playwright install chromium --with-deps
command -v python3-venv >/dev/null 2>&1 || apt-get install -y python3-venv >/dev/null 2>&1 || true
python3 -m venv "$REMOTE_DIR/.venv" 2>/dev/null || { apt-get install -y python3-venv && python3 -m venv "$REMOTE_DIR/.venv"; }
"$REMOTE_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$REMOTE_DIR/.venv/bin/pip" install --quiet pyyaml requests dnspython
echo "remote setup complete"
REMOTE

echo "→ Handing off to push-secrets.sh for .env + configs"
bash "$REPO_DIR/scripts/push-secrets.sh"

echo "✓ Bootstrap done. Next: bash scripts/install-schedule-remote.sh (or run"
echo "  runner/install_schedule.sh on the VPS) to start the routines."
