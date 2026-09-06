#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/workspaces/AA-Efficiency-Dashboard"
cd "$REPO_DIR" 2>/dev/null || cd "$(dirname "$0")"

# Self-update first, then restart this script from the newly pulled copy.
if [[ "${1:-}" != "--after-update" ]]; then
  echo
  echo "=== Updating AA Efficiency Dashboard ==="
  git pull --ff-only origin main
  exec bash "$PWD/codespace-repair.sh" --after-update
fi

echo
echo "=== Installing Python dependencies ==="
python -m pip install -r requirements.txt

echo
echo "=== Installing system Chromium + all required Linux libraries ==="
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y chromium

echo
echo "=== Installing Playwright Chromium fallback ==="
python -m playwright install chromium

echo
echo "=== Verifying SYSTEM Chromium can actually launch ==="
python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        executable_path="/usr/bin/chromium",
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    page = browser.new_page()
    page.set_content("<title>ok</title>")
    assert page.title() == "ok"
    browser.close()
print("System Chromium launch test: OK")
PY

echo
echo "=== Restarting dashboard ==="
pkill -f '[p]ython.*app.py' 2>/dev/null || true
nohup python app.py >/tmp/aa-dashboard.log 2>&1 &

echo
echo "=== Waiting for dashboard ==="
python - <<'PY'
import json
import time
from urllib.request import urlopen

url = "http://127.0.0.1:8765/api/info"
last = None
for _ in range(30):
    try:
        with urlopen(url, timeout=1) as r:
            data = json.loads(r.read().decode("utf-8"))
        print("Dashboard health check: OK")
        print("Version:", data.get("version", "unknown"))
        break
    except Exception as e:
        last = e
        time.sleep(1)
else:
    raise SystemExit(f"Dashboard did not start: {last}")
PY

if [[ -n "${CODESPACE_NAME:-}" ]]; then
  DOMAIN="${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
  DASHBOARD_URL="https://${CODESPACE_NAME}-8765.${DOMAIN}/"
else
  DASHBOARD_URL="http://127.0.0.1:8765/"
fi

echo
echo "=============================================="
echo " AA Efficiency Dashboard repair complete"
echo "=============================================="
echo
echo "Open:"
echo "$DASHBOARD_URL"
echo
