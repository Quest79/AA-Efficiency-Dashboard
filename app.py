from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

APP_NAME = "AAEfficiencyDashboard"
VERSION = "1.1.4"

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
google_fonts_lock = threading.Lock()
google_fonts_cache = {"families": [], "loaded_at": 0.0}
state = {
    "refreshing": False,
    "refresh_target": None,
    "last_error": None,
    "logs": [],
    "version": VERSION,
}

def get_google_fonts():
    import time
    now = time.time()
    with google_fonts_lock:
        cached = google_fonts_cache.get("families") or []
        if cached and now - float(google_fonts_cache.get("loaded_at", 0)) < 21600:
            return cached

    req = Request(
        "https://fonts.google.com/metadata/fonts",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urlopen(req, timeout=20) as response:
        raw = response.read().decode("utf-8", errors="replace").strip()

    # Some Google metadata endpoints may prefix JSON with an anti-XSSI guard.
    if raw.startswith(")]}'"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[4:]

    payload = json.loads(raw)
    families = []
    for item in payload.get("familyMetadataList", []):
        family = str(item.get("family") or "").strip()
        if family:
            families.append(family)
    families = sorted(set(families), key=str.casefold)

    with google_fonts_lock:
        google_fonts_cache["families"] = families
        google_fonts_cache["loaded_at"] = now
    return families


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

def run_refresh(target: str):
    target = "coding" if target == "coding" else "int"
    with state_lock:
        if state["refreshing"]:
            return
        state["refreshing"] = True
        state["refresh_target"] = target
        state["last_error"] = None
        state["logs"] = [f"Starting {target.upper()} refresh from its AA page only..."]

    try:
        from scraper import scrape_all

        result = scrape_all(DATA_DIR, headless=True, target=target)
        existing = read_cache()
        payload = {
            "models": list(existing.get("models") or []),
            "coding": list(existing.get("coding") or []),
            "merged": list(existing.get("merged") or []),
            "meta": dict(existing.get("meta") or {}),
            "logs": result.logs,
        }

        # Preserve the detailed scraper logs even if validation below fails.
        with state_lock:
            state["logs"] = list(result.logs)

        if target == "int":
            priced_models = sum(1 for x in result.models if x.get("cost") is not None)
            if len(result.models) < 40 or priced_models < 35:
                raise RuntimeError(
                    f"INT scrape looks incomplete: {len(result.models)} rows, "
                    f"{priced_models} with Cost per Task. Existing INT cache kept."
                )
            payload["models"] = result.models
            payload["meta"].update(result.meta)
            payload["meta"]["coding_rows"] = len(payload["coding"])
        else:
            # Never throw away valid Coding rows just because AA currently
            # exposes fewer than the advertised total. Zero is the only
            # unusable result.
            if not result.coding:
                raise RuntimeError(
                    "Coding scrape returned 0 rows from the Coding page. "
                    "Existing Coding cache kept; open Diagnostics for exact source counts."
                )
            payload["coding"] = result.coding
            payload["meta"].update(result.meta)
            payload["meta"]["model_rows"] = len(payload["models"])
            payload["meta"]["model_rows_with_cost"] = sum(
                1 for x in payload["models"] if x.get("cost") is not None
            )

        write_cache(payload)

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
            state["refresh_target"] = None

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
        if p.path == "/api/google-fonts":
            try:
                return self._json({"families": get_google_fonts()})
            except Exception as e:
                return self._json({"families": [], "error": str(e)}, 502)
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
            target = "coding" if req.get("target") == "coding" else "int"
            with state_lock:
                if state["refreshing"]:
                    return self._json({"ok": False, "message": "Refresh already running"}, 409)
            threading.Thread(target=run_refresh, args=(target,), daemon=True).start()
            return self._json({"ok": True, "message": f"{target} refresh started"})
        self.send_error(404)

def _dashboard_urls(port: int) -> tuple[str, str]:
    local_url = f"http://127.0.0.1:{port}/"
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


def _dashboard_responds(local_url: str) -> bool:
    try:
        with urlopen(local_url + "api/info", timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("version"):
            return False
        with urlopen(local_url, timeout=1.5) as response:
            html = response.read(4096).decode("utf-8", errors="ignore")
        return "AA Efficiency Dashboard" in html
    except Exception:
        return False


def _linux_listener_pids(port: int) -> list[int]:
    """Find PIDs that own LISTEN sockets on port using /proc only."""
    if not sys.platform.startswith("linux"):
        return []

    wanted_inodes: set[str] = set()
    port_hex = f"{port:04X}"

    for procnet in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(procnet).read_text(encoding="utf-8", errors="ignore").splitlines()[1:]
        except Exception:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue
            local = parts[1]
            state_code = parts[3]
            if ":" not in local or state_code != "0A":
                continue
            if local.rsplit(":", 1)[1].upper() != port_hex:
                continue
            wanted_inodes.add(parts[9])

    if not wanted_inodes:
        return []

    pids: list[int] = []
    proc = Path("/proc")
    for pdir in proc.iterdir():
        if not pdir.name.isdigit():
            continue
        try:
            fd_dir = pdir / "fd"
            for fd in fd_dir.iterdir():
                try:
                    target = os.readlink(fd)
                except Exception:
                    continue
                if target.startswith("socket:[") and target[8:-1] in wanted_inodes:
                    pids.append(int(pdir.name))
                    break
        except Exception:
            continue
    return sorted(set(pids))


def _is_our_dashboard_process(pid: int) -> bool:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        cmd = raw.replace(b"\0", b" ").decode("utf-8", errors="ignore")
    except Exception:
        return False
    return "app.py" in cmd and (
        "AA-Efficiency-Dashboard" in cmd
        or str(ROOT) in cmd
    )


def _stop_stale_codespace_dashboard(port: int) -> bool:
    """Stop only an older instance of this dashboard that owns the port."""
    stopped = False
    for pid in _linux_listener_pids(port):
        if pid == os.getpid() or not _is_our_dashboard_process(pid):
            continue
        try:
            print(f"Replacing stale dashboard process PID {pid} on port {port}...")
            os.kill(pid, signal.SIGTERM)
            stopped = True
        except ProcessLookupError:
            pass

    if not stopped:
        return False

    deadline = time.time() + 3.0
    while time.time() < deadline:
        alive = [pid for pid in _linux_listener_pids(port) if _is_our_dashboard_process(pid)]
        if not alive:
            return True
        time.sleep(0.1)

    for pid in _linux_listener_pids(port):
        if pid != os.getpid() and _is_our_dashboard_process(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    time.sleep(0.2)
    return True


def main():
    port = 8765
    in_codespaces = bool(os.environ.get("CODESPACE_NAME"))
    # Codespaces forwarding must be able to reach the service from outside
    # the process namespace. Local Windows keeps loopback-only behavior.
    bind_host = "0.0.0.0" if in_codespaces else "127.0.0.1"
    local_url, public_url = _dashboard_urls(port)

    try:
        server = ThreadingHTTPServer((bind_host, port), Handler)
    except OSError as e:
        if getattr(e, "errno", None) not in (48, 98, 10048):
            raise

        if in_codespaces:
            # Never print "already running" and quit just because some old
            # localhost process answers. Replace our stale listener and bind
            # the current version to all interfaces so Codespaces can forward it.
            if not _stop_stale_codespace_dashboard(port):
                raise RuntimeError(
                    f"Port {port} is in use by a process that is not this dashboard."
                ) from e
            server = ThreadingHTTPServer((bind_host, port), Handler)
        elif _dashboard_responds(local_url):
            print("")
            print("AA Efficiency Dashboard is already running.")
            print(f"Open it here: {public_url}")
            print("")
            try:
                webbrowser.open(public_url)
            except Exception:
                pass
            return
        else:
            raise

    print(f"AA Efficiency Dashboard v{VERSION}")
    print(f"Listening on {bind_host}:{port}")
    print(f"Open it here: {public_url}")

    if not in_codespaces:
        threading.Timer(0.8, lambda: webbrowser.open(public_url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
