"""Local web UI server (standard library only).

    twb serve [--port 8765] [--open]

Serves the single-page app from ``web/static`` and a small JSON API that calls
``traction_workbench.service``.  It binds to 127.0.0.1 by default; it is a
local engineering tool, not a hardened multi-user service.
"""

from __future__ import annotations

import json
import math
import mimetypes
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__
from .. import service as S
from ..decision import _jsonable
from ..errors import InputValidationError, OutsideModelDomain
from ..io import parse_json
from . import api

STATIC = Path(__file__).resolve().parent / "static"


class Handler(BaseHTTPRequestHandler):
    server_version = f"TractionWorkbench/{__version__}"

    def log_message(self, fmt, *args):  # quieter console
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(_jsonable(obj), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            path = "/static/index.html"
        if path.startswith("/api/"):
            return self._api(path, None)
        if path.startswith("/static/"):
            target = (STATIC / path[len("/static/"):]).resolve()
            if STATIC not in target.parents or not target.is_file():
                return self._json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            return self._send(200, target.read_bytes(), ctype)
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        n = int(self.headers.get("Content-Length") or 0)
        if n > 5_000_000:
            return self._json(413, {"error": "request too large"})
        raw = self.rfile.read(n).decode("utf-8") if n else "{}"
        try:
            body = parse_json(raw)
        except InputValidationError as exc:
            return self._json(400, {"error": str(exc), "status": "INVALID_INPUT"})
        return self._api(path, body)

    def _api(self, path: str, body):
        name = path[len("/api/"):]
        fn = api.ROUTES.get(name)
        if fn is None:
            return self._json(404, {"error": f"unknown endpoint {name!r}"})
        try:
            return self._json(200, fn(body or {}))
        except InputValidationError as exc:
            return self._json(400, {"error": str(exc), "field": exc.field, "status": "INVALID_INPUT"})
        except OutsideModelDomain as exc:
            return self._json(422, {"error": str(exc), "status": "OUTSIDE_MODEL_DOMAIN"})
        except (KeyError, TypeError, ValueError) as exc:
            return self._json(400, {"error": f"bad request: {exc}", "status": "INVALID_INPUT"})
        except Exception as exc:  # pragma: no cover - surfaced to the UI
            traceback.print_exc()
            return self._json(500, {"error": f"internal error: {exc}"})


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False, verbose: bool = False):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.verbose = verbose
    url = f"http://{host}:{port}/"
    print(f"Traction Workbench {__version__} UI at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
