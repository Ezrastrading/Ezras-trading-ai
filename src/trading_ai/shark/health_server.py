"""Minimal HTTP health endpoint for Railway / load balancers."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health" or self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            status: dict[str, Any] = {
                "status": "alive",
                "shark": "hunting",
                "version": "1.0",
            }
            self.wfile.write(json.dumps(status).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass


def start_health_server(port: Optional[int] = None) -> HTTPServer:
    p = int(port or os.environ.get("PORT") or 8080)
    server = HTTPServer(("0.0.0.0", p), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, name="shark-health-http", daemon=True)
    thread.start()
    return server
