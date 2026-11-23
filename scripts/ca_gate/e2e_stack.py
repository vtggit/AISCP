"""The merge gate's e2e pass: the proxy the specs talk to, and the junit merge.

The engine's build gate proves an ``[e2e-proof]`` slice on the real stack: a freshly
migrated database, the backend served from the PR's own code, and a proxy that serves the
frontend root and forwards ``/api/`` the way production's nginx (``location /api/``) does.
``e2e_proofs.sh`` brings up the same stack on the CI runner, so the merge gate checks
those proofs itself. This module is its Python half:

``proxy``
    Serve the frontend root as ``python3 -m http.server`` does and forward every
    ``/api/`` request to the backend with its method, headers and body. It is a copy of
    the engine gate's proxy, so a spec meets the same server in the build and here.
``merge-junit``
    Append the e2e junit's suites to the gate's junit (written fresh when the mocked pass
    did not run). Each e2e testcase gets the ``file`` attribute
    ``<prefix>/<classname>`` that Playwright omits, so the contract checker binds it to
    the proof nodeid.

Pure stdlib, like the rest of ``ca_gate``.
"""

import argparse
import http.client
import http.server
import os
import socketserver
import sys
import urllib.parse
import xml.etree.ElementTree as ET

# Hop-by-hop headers belong to one connection and are never forwarded; Content-Length is
# recomputed from the body actually sent.
HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "transfer-encoding",
        "upgrade",
        "te",
        "trailer",
        "content-length",
    }
)
BACKEND_TIMEOUT = 300


def make_handler(root, backend_url):
    """The request handler: static files from ``root``, ``/api/`` to ``backend_url``."""
    backend = urllib.parse.urlsplit(backend_url)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=root, **kwargs)

        def log_message(self, fmt, *args):
            sys.stderr.write(f"{self.command} {fmt % args}\n")

        def _proxy(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            conn = http.client.HTTPConnection(
                backend.hostname, backend.port or 80, timeout=BACKEND_TIMEOUT
            )
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
            except OSError as exc:
                self.send_error(502, f"e2e proxy: backend unreachable: {exc}")
                return
            finally:
                conn.close()
            self.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                if key.lower() not in HOP:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _route(self, static):
            if self.path.startswith("/api/"):
                return self._proxy()
            if static is None:
                return self.send_error(405)
            return static()

        def do_GET(self):
            self._route(super().do_GET)

        def do_HEAD(self):
            self._route(super().do_HEAD)

        def do_POST(self):
            self._route(None)

        def do_PUT(self):
            self._route(None)

        def do_PATCH(self):
            self._route(None)

        def do_DELETE(self):
            self._route(None)

    return Handler


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(root, port, backend_url, host="127.0.0.1"):
    """The proxy, bound and ready to serve (port 0 picks a free port)."""
    return Server((host, port), make_handler(root, backend_url))


def merge_junit(into, extra, file_prefix):
    """Append the suites of the junit at ``extra`` to the junit at ``into``.

    ``into`` is written fresh when it does not exist. Each appended testcase without a
    ``file`` attribute gets ``<file_prefix>/<classname>``, and the root's totals are
    re-summed over every suite."""
    added = ET.parse(extra).getroot()
    prefix = file_prefix.rstrip("/")
    for case in added.iter("testcase"):
        classname = case.get("classname") or ""
        if classname and not case.get("file"):
            case.set("file", f"{prefix}/{classname}")
    suites = [added] if added.tag == "testsuite" else list(added)
    if os.path.exists(into):
        tree = ET.parse(into)
        root = tree.getroot()
        if root.tag != "testsuites":
            wrapper = ET.Element("testsuites")
            wrapper.append(root)
            tree = ET.ElementTree(wrapper)
            root = wrapper
    else:
        root = ET.Element("testsuites")
        tree = ET.ElementTree(root)
    for suite in suites:
        root.append(suite)
    for attr in ("tests", "failures", "errors", "skipped"):
        total = 0
        for suite in root.iter("testsuite"):
            try:
                total += int(suite.get(attr) or 0)
            except ValueError:
                continue
        root.set(attr, str(total))
    tree.write(into, encoding="utf-8", xml_declaration=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="The merge gate's e2e pass helpers.")
    sub = ap.add_subparsers(dest="command", required=True)
    proxy = sub.add_parser(
        "proxy", help="serve the frontend root and forward /api/ to the backend"
    )
    proxy.add_argument("--root", required=True, help="the frontend root to serve")
    proxy.add_argument("--port", type=int, required=True, help="the port to listen on")
    proxy.add_argument("--backend", required=True, help="the backend's base URL")
    merge = sub.add_parser(
        "merge-junit", help="append the e2e junit to the gate's junit"
    )
    merge.add_argument(
        "--into", required=True, help="the gate's junit (written when absent)"
    )
    merge.add_argument("--extra", required=True, help="the e2e pass's junit")
    merge.add_argument(
        "--file-prefix", required=True, help="the e2e specs' repo-relative directory"
    )
    args = ap.parse_args(argv)
    if args.command == "proxy":
        make_server(args.root, args.port, args.backend).serve_forever()
    else:
        merge_junit(args.into, args.extra, args.file_prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
