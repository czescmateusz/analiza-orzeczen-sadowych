"""Serve the app locally without browser caching (so changes show up on a plain refresh).

    .venv/Scripts/python scripts/serve.py [port]      # default 8765, http://127.0.0.1:8765
"""
import functools
import http.server
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    handler = functools.partial(NoCacheHandler, directory=str(APP))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"http://127.0.0.1:{port}")
        httpd.serve_forever()
