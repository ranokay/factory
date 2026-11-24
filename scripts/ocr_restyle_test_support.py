"""Fixtures and a local GitHub API double for the restyle tests."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class FakeGitHub:
    def __init__(self):
        self.issue_comments = []
        self.review_comments = []
        self.patches = []
        self.fail_path = ""
        self.fail_once = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/repos/o/r/issues/7/comments":
                    outer.reply(self, outer.issue_comments)
                elif path == "/repos/o/r/pulls/7/comments":
                    outer.reply(self, outer.review_comments)
                else:
                    outer.reply(self, {"message": "not found"}, 404)

            def do_PATCH(self):
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if outer.fail_once and path == outer.fail_path:
                    outer.fail_once = False
                    outer.reply(self, {"message": "forced failure"}, 500)
                    return
                outer.patches.append((path, payload["body"]))
                if path.startswith("/repos/o/r/issues/comments/"):
                    ident = int(path.rsplit("/", 1)[1])
                    for comment in outer.issue_comments:
                        if comment["id"] == ident:
                            comment["body"] = payload["body"]
                elif path.startswith("/repos/o/r/pulls/comments/"):
                    ident = int(path.rsplit("/", 1)[1])
                    for comment in outer.review_comments:
                        if comment["id"] == ident:
                            comment["body"] = payload["body"]
                else:
                    outer.reply(self, {"message": "not found"}, 404)
                    return
                outer.reply(self, {"id": ident})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @staticmethod
    def reply(handler, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    @property
    def url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def seed(self, summary_body, inline_body):
        self.issue_comments.append(
            {"id": 1, "body": summary_body, "html_url": "https://example.test/issues/1"}
        )
        self.review_comments.append(
            {
                "id": 2,
                "body": inline_body,
                "html_url": "https://example.test/pull/7#discussion_r2",
                "path": "scripts/a.py",
                "line": 12,
                "start_line": 10,
            }
        )


def finding(path="scripts/a.py", severity="high", category="bug", start=10, end=12, content=None):
    return {
        "path": path,
        "content": content
        or "**A finding title**\n\nThe explanation names the failure and the line.",
        "suggestion_code": "subprocess.run(['git', 'fetch'], check=True)",
        "existing_code": "pass",
        "start_line": start,
        "end_line": end,
        "category": category,
        "severity": severity,
    }


def carrier():
    return {
        "path": "REVIEW.md",
        "content": "**Diagram**\n\n```mermaid\nflowchart LR\n  A[action] --> B[script]\n```",
        "start_line": 1,
        "end_line": 1,
        "category": "documentation",
        "severity": "low",
    }


def ocr_result(comments, message=None, warnings=None):
    return {
        "status": "complete",
        "message": message or f"Review complete: {len(comments)} finding(s) across 5 selected item(s).",
        "comments": comments,
        "warnings": warnings or [],
        "manifest": {
            "input": {
                "resolved_base": "def56789abcdef0123456789abcdef0123456789",
                "resolved_head": "abc1234def567890123456789abcdef0123456789",
            },
            "execution": {"ocr_version": "v1.14.0", "model": "deepseek-v4.1-flash"},
        },
    }
