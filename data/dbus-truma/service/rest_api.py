import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Optional


class TrumaRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Truma REST API."""

    # These are set by TrumaRestApi before starting the server
    state_getter = None      # callable() -> dict (from TrumaState.get_status)
    command_sender = None    # callable(topic, param, value) -> (bool, str)
    health_getter = None     # callable() -> dict

    def do_GET(self):
        if self.path == "/api/status":
            self._json_response(self.state_getter())
        elif self.path.startswith("/api/status/"):
            section = self.path.split("/")[-1]
            status = self.state_getter()
            if section in status:
                self._json_response(status[section])
            else:
                self._json_response({"error": f"unknown section: {section}"}, 404)
        elif self.path == "/api/health":
            self._json_response(self.health_getter())
        else:
            self._json_response({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/command":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                data = json.loads(body)

                topic = data.get("topic")
                param = data.get("param")
                value = data.get("value")

                if not all([topic, param, value is not None]):
                    self._json_response({"error": "missing topic, param, or value"}, 400)
                    return

                ok, msg = self.command_sender(topic, param, int(value))
                if ok:
                    self._json_response({"status": "ok", "message": msg})
                else:
                    self._json_response({"error": msg}, 400)
            except json.JSONDecodeError:
                self._json_response({"error": "invalid JSON"}, 400)
            except Exception as e:
                self._json_response({"error": str(e)}, 500)
        else:
            self._json_response({"error": "not found"}, 404)

    def _json_response(self, data: Any, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self._json_response({})

    def log_message(self, format, *args):
        """Suppress default logging — use structured logging instead."""
        pass


class TrumaRestApi:
    """REST API server running in a background thread."""

    def __init__(self, state_getter, command_sender, health_getter, port=8090):
        self.port = port
        self._server = None
        self._thread = None
        self._start_time = time.time()

        # Wire up handlers
        TrumaRequestHandler.state_getter = state_getter
        TrumaRequestHandler.command_sender = command_sender
        TrumaRequestHandler.health_getter = health_getter

    def start(self):
        """Start the REST API server in a background thread."""
        self._server = HTTPServer(("0.0.0.0", self.port), TrumaRequestHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        print(f"[REST] API listening on port {self.port}")

    def stop(self):
        """Stop the REST API server."""
        if self._server:
            self._server.shutdown()
            print("[REST] API stopped")
