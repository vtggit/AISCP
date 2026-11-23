"""Self-tests for the merge gate's e2e pass helpers (scripts/ca_gate/e2e_stack.py)."""

import http.client
import http.server
import json
import os
import sys
import threading
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "ca_gate")
)

from check_pr_contract import parse_junit  # noqa: E402
from e2e_stack import make_server, merge_junit  # noqa: E402

MOCKED_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites tests="1" failures="0" errors="0" skipped="0">
  <testsuite name="a.spec.js" tests="1" failures="0" errors="0" skipped="0">
    <testcase name="a works" classname="a.spec.js" file="app/tests/a.spec.js"/>
  </testsuite>
</testsuites>
"""

# The shape Playwright's junit reporter writes: no ``file`` attribute on a testcase.
E2E_JUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites id="" name="" tests="2" failures="1" skipped="0" errors="0" time="3.2">
  <testsuite name="issue7_e2e.spec.js" hostname="chromium" tests="2" failures="1"
             skipped="0" errors="0" time="3.2">
    <testcase name="issue7 e2e" classname="issue7_e2e.spec.js" time="2.1"></testcase>
    <testcase name="other" classname="issue7_e2e.spec.js" time="1.1">
      <failure message="boom" type="FAILURE">boom</failure>
    </testcase>
  </testsuite>
</testsuites>
"""


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_merge_appends_the_e2e_cases_with_their_spec_file(tmp_path):
    into = _write(tmp_path / "report.xml", MOCKED_JUNIT)
    extra = _write(tmp_path / "report-e2e.xml", E2E_JUNIT)
    merge_junit(into, extra, "app/e2e")
    root = ET.parse(into).getroot()
    files = sorted(case.get("file") for case in root.iter("testcase"))
    assert files == [
        "app/e2e/issue7_e2e.spec.js",
        "app/e2e/issue7_e2e.spec.js",
        "app/tests/a.spec.js",
    ]
    assert (root.get("tests"), root.get("failures")) == ("3", "1")


def test_merge_writes_the_junit_when_the_mocked_pass_did_not_run(tmp_path):
    into = str(tmp_path / "report.xml")
    extra = _write(tmp_path / "report-e2e.xml", E2E_JUNIT)
    merge_junit(into, extra, "app/e2e/")
    root = ET.parse(into).getroot()
    assert root.tag == "testsuites"
    assert [case.get("file") for case in root.iter("testcase")] == [
        "app/e2e/issue7_e2e.spec.js",
        "app/e2e/issue7_e2e.spec.js",
    ]
    assert (root.get("tests"), root.get("failures")) == ("2", "1")


def test_a_merged_case_binds_to_its_proof_nodeid(tmp_path):
    # The checker reads ``file::name`` and strips --repo-root (app/), which leaves the
    # contract's nodeid: e2e/issue7_e2e.spec.js::issue7 e2e.
    into = _write(tmp_path / "report.xml", MOCKED_JUNIT)
    extra = _write(tmp_path / "report-e2e.xml", E2E_JUNIT)
    merge_junit(into, extra, "app/e2e")
    junit = parse_junit(into)
    assert "app/e2e/issue7_e2e.spec.js::issue7 e2e" in junit["passed"]
    assert "app/e2e/issue7_e2e.spec.js::other" in junit["failed"]
    assert "app/tests/a.spec.js::a works" in junit["passed"]


class _Backend(http.server.BaseHTTPRequestHandler):
    """Echoes what reached it; any path under /api/down answers 503."""

    def _echo(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        payload = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "body": body,
                "auth": self.headers.get("Authorization"),
            }
        ).encode()
        self.send_response(503 if self.path.startswith("/api/down") else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _echo

    def log_message(self, *args):
        pass


def _start(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _stop(*servers):
    for server in servers:
        server.shutdown()
        server.server_close()


def _request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


@pytest.fixture
def proxy_port(tmp_path):
    (tmp_path / "index.html").write_text("<p>the app</p>", encoding="utf-8")
    backend = _start(http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Backend))
    backend_url = f"http://127.0.0.1:{backend.server_address[1]}"
    proxy = _start(make_server(str(tmp_path), 0, backend_url))
    yield proxy.server_address[1]
    _stop(proxy, backend)


def test_the_proxy_serves_the_frontend_root(proxy_port):
    status, body = _request(proxy_port, "GET", "/")
    assert status == 200
    assert b"the app" in body


def test_the_proxy_forwards_api_requests_whole(proxy_port):
    status, body = _request(
        proxy_port,
        "POST",
        "/api/things?x=1",
        body=b'{"a": 1}',
        headers={"Authorization": "Bearer t", "Content-Type": "application/json"},
    )
    assert status == 200
    assert json.loads(body) == {
        "method": "POST",
        "path": "/api/things?x=1",
        "body": '{"a": 1}',
        "auth": "Bearer t",
    }


def test_the_proxy_passes_the_backend_status_through(proxy_port):
    status, _ = _request(proxy_port, "GET", "/api/down")
    assert status == 503


def test_the_proxy_refuses_a_write_outside_api(proxy_port):
    status, _ = _request(proxy_port, "POST", "/index.html", body=b"x")
    assert status == 405


def test_the_proxy_answers_502_when_the_backend_is_down(tmp_path):
    gone = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Backend)
    backend_url = f"http://127.0.0.1:{gone.server_address[1]}"
    gone.server_close()
    proxy = _start(make_server(str(tmp_path), 0, backend_url))
    try:
        status, _ = _request(proxy.server_address[1], "GET", "/api/health")
    finally:
        _stop(proxy)
    assert status == 502
