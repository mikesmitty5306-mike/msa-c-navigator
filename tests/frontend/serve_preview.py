"""Local preview of the static app: serves web/ with a data folder mounted at /data/.

    ..\\.venv\\Scripts\\python.exe tests/frontend/serve_preview.py            # fixtures (default)
    ..\\.venv\\Scripts\\python.exe tests/frontend/serve_preview.py --data public-data
Then open http://127.0.0.1:8080/ in a browser. Service workers work on 127.0.0.1 without HTTPS.
Stop with Ctrl+C. Only this computer can reach it (bound to 127.0.0.1).
"""

from __future__ import annotations

import argparse
import functools
import http.server
import mimetypes
import threading
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
WEB = APP / "web"
FIXTURE_DATA = Path(__file__).resolve().parent / "fixtures" / "data"
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")


class PreviewHandler(http.server.SimpleHTTPRequestHandler):
    """Serves web/ at /, and the chosen data folder at /data/."""

    data_dir: Path = FIXTURE_DATA

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("/data/"):
            target = (self.data_dir / clean[len("/data/"):]).resolve()
            root = self.data_dir.resolve()
        else:
            target = (WEB / clean.lstrip("/")).resolve()
            root = WEB.resolve()
        if root not in target.parents and target != root:
            return str(root / "__forbidden__")
        return str(target)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):  # keep test output quiet
        pass


class PreviewServer(http.server.ThreadingHTTPServer):
    # The default backlog (5) is too small: a browser opens ~12 connections at once for the app's
    # modules, and on a busy Windows machine the extra ones were reset, so app.js never ran.
    request_queue_size = 128
    daemon_threads = True


def make_server(port: int = 0, data_dir: Path = FIXTURE_DATA) -> http.server.ThreadingHTTPServer:
    handler = type("Handler", (PreviewHandler,), {"data_dir": data_dir})
    return PreviewServer(("127.0.0.1", port), functools.partial(handler, directory=str(WEB)))


def serve_in_thread(data_dir: Path = FIXTURE_DATA):
    server = make_server(0, data_dir)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Preview the static app locally")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--data", type=Path, default=FIXTURE_DATA, help="folder with meta.json etc. (default: fixtures)")
    args = ap.parse_args()
    srv = make_server(args.port, args.data.resolve())
    print(f"Previewing web/ with data from {args.data} at http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
