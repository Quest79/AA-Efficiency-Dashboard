#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/workspaces/AA-Efficiency-Dashboard"
cd "$REPO_DIR" 2>/dev/null || cd "$(dirname "$0")"

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
echo "=== Fixing broken apt sources if needed ==="
for f in /etc/apt/sources.list.d/*; do
  if [ -f "$f" ] && grep -qs "dl.yarnpkg.com" "$f"; then
    echo "Disabling broken Yarn apt source: $f"
    sudo mv "$f" "$f.aa-disabled"
  fi
done

echo
echo "=== Installing system Chromium + required Linux libraries ==="
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
pkill -f '[p]ython.*AA-Efficiency-Dashboard.*/app.py' 2>/dev/null || true
pkill -f '[p]ython.*app.py' 2>/dev/null || true
sleep 0.3
nohup python app.py >/tmp/aa-dashboard.log 2>&1 &

echo
echo "=== Waiting for dashboard ==="
python - <<'PY'
import json
import time
from urllib.request import urlopen

info_url = "http://127.0.0.1:8765/api/info"
root_url = "http://127.0.0.1:8765/"
last = None
for _ in range(30):
    try:
        with urlopen(info_url, timeout=1) as r:
            data = json.loads(r.read().decode("utf-8"))
        with urlopen(root_url, timeout=1) as r:
            html = r.read(8192).decode("utf-8", errors="ignore")
        if "AA Efficiency Dashboard" not in html:
            raise RuntimeError("root page did not contain dashboard HTML")
        print("Dashboard API health check: OK")
        print("Dashboard root page: OK")
        print("Version:", data.get("version", "unknown"))
        break
    except Exception as e:
        last = e
        time.sleep(1)
else:
    raise SystemExit(f"Dashboard did not start correctly: {last}")
PY

echo
echo "=== Verifying Codespaces listener bind ==="
if command -v ss >/dev/null 2>&1; then
  ss -ltn | grep -E '(^|[[:space:]])0\.0\.0\.0:8765[[:space:]]|\[::\]:8765' || {
    echo "ERROR: dashboard is not listening on all interfaces at port 8765"
    echo "--- /tmp/aa-dashboard.log ---"
    tail -100 /tmp/aa-dashboard.log || true
    exit 1
  }
fi

DASHBOARD_URL="http://127.0.0.1:8765/"

if [[ -n "${CODESPACE_NAME:-}" ]]; then
  echo
  echo "=== Triggering Codespaces port forwarding ==="
  # GitHub Codespaces watches terminal output for localhost URLs and
  # automatically forwards those ports. Do not hide this URL in a log file.
  echo "http://localhost:8765/"

  # Give the Codespaces port-forwarder time to register the tunnel, then ask
  # GitHub for the actual browse URL instead of constructing one blindly.
  if command -v gh >/dev/null 2>&1; then
    FORWARDED_URL=""
    for _ in {1..20}; do
      FORWARDED_URL="$(gh codespace ports -c "$CODESPACE_NAME"         --json sourcePort,browseUrl         --jq '.[] | select(.sourcePort == 8765) | .browseUrl' 2>/dev/null | head -n1 || true)"
      if [[ -n "$FORWARDED_URL" ]]; then
        break
      fi
      sleep 1
    done

    if [[ -n "$FORWARDED_URL" ]]; then
      DASHBOARD_URL="$FORWARDED_URL"
      echo "Codespaces port 8765 forwarded: OK"
    else
      echo
      echo "ERROR: GitHub did not create the port-8765 tunnel."
      echo "The dashboard itself IS running locally, but the Codespaces proxy is not forwarding it."
      echo "Open the VS Code PORTS tab and confirm 8765 is listed, or rebuild the Codespace once."
      exit 1
    fi
  else
    DOMAIN="${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
    DASHBOARD_URL="https://${CODESPACE_NAME}-8765.${DOMAIN}/"
  fi
fi

echo
echo "=============================================="
echo " AA Efficiency Dashboard repair complete"
echo "=============================================="
echo
echo "Open:"
echo "$DASHBOARD_URL"
echo
