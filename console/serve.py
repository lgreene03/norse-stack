#!/usr/bin/env python3
"""Tiny static server for the Norse Console.

Serves the console/ directory (this script's own directory) with no-cache
headers so edits show up on reload. Stdlib only.

Usage:
    python3 serve.py            # serves on http://localhost:8090
    PORT=9000 python3 serve.py  # override the port
"""
import http.server
import os
import socketserver

PORT = int(os.environ.get("PORT", "8090"))
DIRECTORY = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):
        # Keep the console quiet but show request lines.
        print("  " + (fmt % args))


class Server(socketserver.TCPServer):
    allow_reuse_address = True


def main():
    with Server(("", PORT), Handler) as httpd:
        url = "http://localhost:%d/" % PORT
        print("Norse Console serving %s" % DIRECTORY)
        print("  -> %s" % url)
        print("  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
