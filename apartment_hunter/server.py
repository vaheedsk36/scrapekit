"""Tiny zero-dependency web server (standard library only). Serves the UI and
streams a hunt over Server-Sent Events so the browser sees progress live.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import webhunt
from .config import load_config

_PKG_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = _PKG_DIR / "templates"
STATIC_DIR = _PKG_DIR / "static"

# Jinja2 environment for the tool/home pages. Templates and static assets ship
# inside the package so they end up in the wheel.
_JINJA = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(default=True, default_for_string=True),
)

# URL path -> template name. Every page route (and its ".html" alias) is served
# by rendering the matching Jinja template.
PAGE_TEMPLATES = {
    "/": "home.html.j2",
    "/index.html": "home.html.j2",
    "/home": "home.html.j2",
    "/apartment-hunter": "apartment-hunter.html.j2",
    "/apartment-hunter.html": "apartment-hunter.html.j2",
    "/price-tracker": "price-tracker.html.j2",
    "/price-tracker.html": "price-tracker.html.j2",
    "/job-radar": "job-radar.html.j2",
    "/job-radar.html": "job-radar.html.j2",
    "/grants": "grants.html.j2",
    "/grants.html": "grants.html.j2",
    "/flight-deals": "flight-deals.html.j2",
    "/flight-deals.html": "flight-deals.html.j2",
    "/car-finder": "car-finder.html.j2",
    "/car-finder.html": "car-finder.html.j2",
}

_STATIC_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console clean
        pass

    def _render_page(self, template: str) -> None:
        try:
            body = _JINJA.get_template(template).render().encode("utf-8")
        except Exception:
            self.send_error(500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, rel: str) -> None:
        # Resolve within STATIC_DIR and refuse any path traversal.
        target = (STATIC_DIR / rel).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_error(404)
            return
        try:
            body = target.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            self.send_error(404)
            return
        ctype = _STATIC_TYPES.get(target.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in PAGE_TEMPLATES:
            return self._render_page(PAGE_TEMPLATES[parsed.path])
        if parsed.path.startswith("/static/"):
            return self._serve_static(parsed.path[len("/static/"):])
        if parsed.path == "/api/settings":
            from . import settings
            return self._send_json(settings.public_status())
        if parsed.path == "/api/hunt":
            return self._hunt(parse_qs(parsed.query))
        if parsed.path == "/api/track":
            return self._track(parse_qs(parsed.query))
        if parsed.path == "/api/jobs":
            return self._jobs(parse_qs(parsed.query))
        if parsed.path == "/api/grants":
            return self._grants(parse_qs(parsed.query))
        if parsed.path == "/api/flights":
            return self._flights(parse_qs(parsed.query))
        if parsed.path == "/api/cars":
            return self._cars(parse_qs(parsed.query))
        self.send_error(404)

    def do_POST(self):
        from . import providers, settings
        parsed = urlparse(self.path)
        if parsed.path == "/api/settings":
            d = self._read_json()
            settings.set_settings(d.get("provider"), d.get("model"), d.get("api_key"))
            return self._send_json(settings.public_status())
        if parsed.path == "/api/provider/models":
            d = self._read_json()
            provider = d.get("provider") or "openai"
            key = d.get("api_key") or settings.get_effective()[2]
            if not key:
                return self._send_json({"error": "API key required"}, 400)
            try:
                return self._send_json({"models": providers.list_models(provider, key)})
            except Exception as exc:
                return self._send_json({"error": str(exc)}, 400)
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

    def _track(self, q: dict) -> None:
        def one(key, default=None):
            vals = q.get(key)
            return vals[0] if vals else default

        params = {
            "country": one("country", ""),
            "product": one("product", ""),
            "condition": one("condition", ""),
            "currency": one("currency", "$"),
            "target_price": int(one("target_price") or 0) or None,
            "threshold": int(one("threshold") or 60),
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

        from . import price_hunt
        try:
            for event, data in price_hunt.run_track(params, cfg):
                emit(event, data)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            try:
                emit("log", {"level": "error", "msg": f"Server error: {exc}"})
                emit("done", {"processed": 0, "deals": 0, "sources": 0,
                              "currency": params["currency"]})
            except Exception:
                pass

    def _sse(self, run_fn, params: dict) -> None:
        """Shared SSE streamer: run a tool's generator and emit its events."""
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
            for event, data in run_fn(params, cfg):
                emit(event, data)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            try:
                emit("log", {"level": "error", "msg": f"Server error: {exc}"})
                emit("done", {"processed": 0, "matched": 0, "sources": 0,
                              "currency": params.get("currency", "$")})
            except Exception:
                pass

    def _jobs(self, q: dict) -> None:
        def one(key, default=None):
            vals = q.get(key)
            return vals[0] if vals else default
        params = {
            "country": one("country", ""),
            "location": one("location", ""),
            "role": one("role", ""),
            "seniority": one("seniority", ""),
            "remote": one("remote", ""),
            "currency": one("currency", "$"),
            "threshold": int(one("threshold") or 60),
            "keywords": [k.strip() for k in (one("keywords", "") or "").split(",") if k.strip()],
        }
        from . import job_hunt
        self._sse(job_hunt.run_jobs, params)

    def _grants(self, q: dict) -> None:
        def one(key, default=None):
            vals = q.get(key)
            return vals[0] if vals else default
        params = {
            "country": one("country", ""),
            "sector": one("sector", ""),
            "type": one("type", "both"),
            "currency": one("currency", "$"),
            "threshold": int(one("threshold") or 60),
            "keywords": [k.strip() for k in (one("keywords", "") or "").split(",") if k.strip()],
        }
        from . import grants_hunt
        self._sse(grants_hunt.run_grants, params)

    def _flights(self, q: dict) -> None:
        def one(key, default=None):
            vals = q.get(key)
            return vals[0] if vals else default
        params = {
            "country": one("country", ""),
            "origin": one("origin", ""),
            "destination": one("destination", ""),
            "when": one("when", ""),
            "cabin": one("cabin", "Economy"),
            "currency": one("currency", "$"),
            "threshold": int(one("threshold") or 60),
        }
        from . import flight_hunt
        self._sse(flight_hunt.run_flights, params)

    def _cars(self, q: dict) -> None:
        def one(key, default=None):
            vals = q.get(key)
            return vals[0] if vals else default
        params = {
            "country": one("country", ""),
            "location": one("location", ""),
            "query": one("query", ""),
            "max_price": int(one("max_price") or 0) or None,
            "min_year": int(one("min_year") or 0) or None,
            "fuel": one("fuel", "Any"),
            "currency": one("currency", "$"),
            "threshold": int(one("threshold") or 60),
        }
        from . import car_hunt
        self._sse(car_hunt.run_cars, params)


def serve(port: int = 8000, config: "str | None" = None) -> None:
    if config:
        os.environ["AH_CONFIG"] = config
    from .config import _load_env  # populate env from .env so keys resolve at startup
    _load_env()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  ScrapeKit  ->  {url}\n  (Ctrl+C to stop)\n")
    if os.environ.get("SCRAPEKIT_NO_OPEN") != "1":
        try:
            import threading
            import webbrowser
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
