#!/usr/bin/env python3
"""Tiny counter app for the verification-skill fixture."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"count": 0}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"ok": True})
        elif self.path == "/count":
            self.send_json(200, {"count": STATE["count"]})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path == "/increment":
            STATE["count"] += 1
            self.send_json(200, {"count": STATE["count"]})
        elif self.path == "/reset":
            STATE["count"] = 0
            self.send_json(200, {"count": STATE["count"]})
        else:
            self.send_json(404, {"error": "not found"})

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
