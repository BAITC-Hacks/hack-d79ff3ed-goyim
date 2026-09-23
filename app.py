"""Run: python app.py. Requires Python 3.10+ and no third-party packages."""
from __future__ import annotations

import argparse
import json
import os
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from ai import EvidenceSelector
from matcher import Matcher
from comparison import add_comparison_fields
from event_parser import parse_event, ParserUnavailable, ParserValidationError

ROOT = Path(__file__).resolve().parent


def load_env():
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if key.strip() in {"OPENAI_API_KEY", "OPENAI_MODEL"}:
                    os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def make_handler(matcher=None, selector=None):
    matcher = matcher or Matcher()
    selector = selector or EvidenceSelector()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def send_body(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status, body):
            self.send_body(status, json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/catalog":
                self.send_json(200, dict(matcher.metadata(), ai_configured=bool(os.environ.get("OPENAI_API_KEY", "").strip())))
            elif path == "/api/health":
                self.send_json(200, {"status": "ok", "profiles": len(matcher.catalog)})
            else:
                assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "application/javascript"),
                          "/event-parser.js": ("event-parser.js", "application/javascript"),
                          "/style.css": ("style.css", "text/css"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}
                if path not in assets:
                    return self.send_json(404, {"error": "Страница не найдена."})
                filename, mime = assets[path]
                self.send_body(200, (ROOT / "web" / filename).read_bytes(), mime + "; charset=utf-8")

        def do_POST(self):
            path = urlsplit(self.path).path
            if path not in {"/api/recommend", "/api/parse-event"}:
                return self.send_json(404, {"error": "Метод не найден."})
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                return self.send_json(403, {"error": "Запрос разрешён только со страницы приложения."})
            if self.headers.get_content_type() != "application/json":
                return self.send_json(415, {"error": "Используйте Content-Type: application/json."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32_768:
                    return self.send_json(413, {"error": "Недопустимый размер запроса."})
                raw = json.loads(self.rfile.read(length).decode("utf-8"))
                if path == "/api/parse-event":
                    try:
                        return self.send_json(200, {"fields": parse_event(raw, matcher.metadata())})
                    except ParserUnavailable as error:
                        return self.send_json(503, {"error": str(error)})
                    except ParserValidationError as error:
                        return self.send_json(422, {"error": str(error)})
                start = time.perf_counter()
                result = matcher.recommend(raw)
                result = selector.enhance(result, matcher)
                result = add_comparison_fields(result, matcher)
                result["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 1)
                self.send_json(200, result)
            except (ValueError, UnicodeError) as error:
                self.send_json(400, {"error": str(error) if not isinstance(error, json.JSONDecodeError) else "Неверный JSON."})
            except Exception:
                self.send_json(500, {"error": "Не удалось выполнить подбор. Повторите запрос."})

        def log_message(self, format, *args):
            # Do not log request bodies, profiles or credentials.
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Firebird Match — подбор event-подрядчиков")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--open", action="store_true", help="Открыть браузер")
    args = parser.parse_args()
    load_env()
    server = ThreadingHTTPServer((args.host, args.port), make_handler())
    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}"
    print(f"Firebird Match: {url}\nОстановка: Ctrl+C", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
