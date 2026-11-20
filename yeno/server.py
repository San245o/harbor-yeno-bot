"""Small stdlib HTTP adapter. One serialized strategy state per process."""
from __future__ import annotations

import argparse
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .strategy import Bot

MAX_BODY_BYTES = 1_048_576


def make_server(host: str = "127.0.0.1", port: int = 8080, bot: Bot | None = None) -> ThreadingHTTPServer:
    engine = bot or Bot()

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(2)

        def send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self.send_json(200, {"status": "ok", "bot": "Harbor"}) if self.path == "/health" else self.send_error(404)

        def do_POST(self) -> None:
            if self.path != "/decide":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY_BYTES:
                    self.send_error(413 if length > MAX_BODY_BYTES else 400)
                    return
                def reject_constant(value: str) -> None:
                    raise ValueError(f"Invalid JSON number: {value}")
                observation = json.loads(self.rfile.read(length), parse_constant=reject_constant)
            except (ValueError, UnicodeError, TimeoutError):
                self.send_error(400)
                return
            try:
                response = engine.decide(observation)
            except Exception:
                logging.exception("Unexpected strategy error")
                response = {"action": "HOLD"}
            self.send_json(200, response)

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Harbor paper bot; no external connectivity required")
    parser.add_argument("--host", default=os.environ.get("YENO_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("YENO_PORT", "8080")))
    args = parser.parse_args()
    server = make_server(args.host, args.port)
    print(f"Harbor listening on http://{args.host}:{server.server_port}/decide", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
