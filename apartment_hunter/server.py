"""Tiny zero-dependency web server (standard library only). Serves the UI and
streams a hunt over Server-Sent Events so the browser sees progress live.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import webhunt
from .config import load_config

WEB_DIR = Path(__file__).resolve().parent / "web"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console clean
        pass

    def _serve_file(self, name: str, ctype: str) -> None:
        try:
            body = (WEB_DIR / name).read_bytes()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html", "/home"):
            return self._serve_file("home.html", "text/html; charset=utf-8")
        if parsed.path in ("/apartment-hunter", "/apartment-hunter.html"):
            return self._serve_file("apartment-hunter.html", "text/html; charset=utf-8")
        if parsed.path == "/api/hunt":
            return self._hunt(parse_qs(parsed.query))
        self.send_error(404)

    def _hunt(self, q: dict) -> None:
        def one(key, default=None):
            vals = q.get(key)
            return vals[0] if vals else default

        params = {
            "country": one("country", ""),
            "city": one("city", ""),
            "area": one("area", ""),
            "currency": one("currency", "$"),
            "max_price": int(one("max_price") or 0) or None,
            "min_beds": float(one("min_beds") or 0) or None,
            "match_threshold": int(one("threshold") or 60),
            "keywords": [k.strip() for k in (one("keywords", "") or "").split(",") if k.strip()],
        }

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        cfg = load_config(os.environ.get("AH_CONFIG") or None)

        def emit(event: str, data: dict) -> None:
            chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()

        try:
            for event, data in webhunt.run_hunt(params, cfg):
                emit(event, data)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # never leave the UI hanging
            try:
                emit("log", {"level": "error", "msg": f"Server error: {exc}"})
                emit("done", {"processed": 0, "matched": 0, "sites": 0,
                              "currency": params["currency"]})
            except Exception:
                pass


def serve(port: int = 8000, config: "str | None" = None) -> None:
    if config:
        os.environ["AH_CONFIG"] = config
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  Apartment Hunter  ->  {url}\n  (Ctrl+C to stop)\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
