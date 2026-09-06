from __future__ import annotations

import json
import os
import sys
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

APP_NAME = "AAEfficiencyDashboard"
VERSION = "1.1.0"

def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent

ROOT = resource_dir()
WEB_DIR = ROOT / "web"

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
DATA_DIR = LOCALAPPDATA / APP_NAME
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = DATA_DIR / "data.json"

state_lock = threading.Lock()
state = {
    "refreshing": False,
    "last_error": None,
    "logs": [],
    "version": VERSION,
}

def read_cache():
    if not CACHE_FILE.exists():
        return {"merged": [], "models": [], "coding": [], "meta": {}, "logs": []}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"merged": [], "models": [], "coding": [], "meta": {}, "logs": []}

def write_cache(data):
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CACHE_FILE)

def run_refresh():
    with state_lock:
        if state["refreshing"]:
            return
        state["refreshing"] = True
        state["last_error"] = None
        state["logs"] = ["Starting live Artificial Analysis refresh..."]
    try:
        from scraper import scrape_all
        result = scrape_all(DATA_DIR, headless=True)
        payload = {
            "models": result.models,
            "coding": result.coding,
            "merged": result.merged,
            "meta": result.meta,
            "logs": result.logs,
        }

        priced_models = sum(1 for x in result.models if x.get("cost") is not None)
        if len(result.models) < 40 or priced_models < 35:
            raise RuntimeError(
                f"Scrape looks incomplete: {len(result.models)} Intelligence rows, "
                f"{priced_models} with Cost per Task. Refusing to replace the last "
                "good cache. Diagnostics were saved."
            )
        write_cache(payload)
        with state_lock:
            state["logs"] = result.logs
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        with state_lock:
            state["last_error"] = err
            state["logs"] = state.get("logs", []) + [
                err,
                traceback.format_exc(),
                f"Diagnostics folder: {DATA_DIR / 'debug'}",
            ]
    finally:
        with state_lock:
            state["refreshing"] = False

class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{VERSION}"

    def log_message(self, fmt, *args):
        pass

    def _json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _file(self, path: Path, ctype: str):
        if not path.exists():
            self.send_error(404)
            return
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        p = urlparse(self.path)
        if p.path in ("/", "/index.html"):
            return self._file(WEB_DIR / "index.html", "text/html; charset=utf-8")
        if p.path == "/api/data":
            return self._json(read_cache())
        if p.path == "/api/status":
            with state_lock:
                return self._json(dict(state))
        if p.path == "/api/info":
            return self._json({
                "version": VERSION,
                "data_dir": str(DATA_DIR),
                "cache_file": str(CACHE_FILE),
            })
        self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path)
        if p.path == "/api/refresh":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b"{}"
            try:
                req = json.loads(body or b"{}")
            except Exception:
                req = {}
            with state_lock:
                if state["refreshing"]:
                    return self._json({"ok": False, "message": "Refresh already running"}, 409)
            threading.Thread(target=run_refresh, daemon=True).start()
            return self._json({"ok": True, "message": "Refresh started"})
        self.send_error(404)

def _dashboard_urls(host: str, port: int) -> tuple[str, str]:
    local_url = f"http://{host}:{port}/"
    codespace = os.environ.get("CODESPACE_NAME")
    if codespace:
        domain = os.environ.get(
            "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN",
            "app.github.dev",
        )
        public_url = f"https://{codespace}-{port}.{domain}/"
    else:
        public_url = local_url
    return local_url, public_url


def _existing_dashboard_is_running(local_url: str) -> bool:
    try:
        with urlopen(local_url + "api/info", timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return bool(payload.get("version"))
    except Exception:
        return False


def main():
    host = "127.0.0.1"
    port = 8765
    local_url, public_url = _dashboard_urls(host, port)

    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        # Codespaces starts the dashboard automatically. If the user runs
        # python app.py again, do not crash just because our own server is
        # already listening on 8765.
        if getattr(e, "errno", None) in (48, 98, 10048) and _existing_dashboard_is_running(local_url):
            print("")
            print("AA Efficiency Dashboard is already running.")
            print(f"Open it here: {public_url}")
            print("")
            try:
                webbrowser.open(public_url)
            except Exception:
                pass
            return
        raise

    threading.Timer(0.8, lambda: webbrowser.open(public_url)).start()
    print(f"AA Efficiency Dashboard: {public_url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
