"""
tests/test_interception.py — Integration tests verifying interception flows do not crash.
"""
import sys
import os
import time
import json
import threading
import http.server
import socketserver
import pytest
import urllib.parse
import urllib3
import requests
import httpx
import asyncio

class MockBackendHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"near_limit": False, "status": "ok"}).encode('utf-8'))

    def log_message(self, format, *args):
        pass

def get_free_port():
    with socketserver.TCPServer(("127.0.0.1", 0), None) as s:
        return s.server_address[1]

@pytest.fixture(scope="module")
def mock_backend():
    port = get_free_port()
    server = socketserver.TCPServer(("127.0.0.1", port), MockBackendHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/api/v1"
    server.shutdown()

def test_interception_flow(mock_backend):
    import computecapx
    
    # Instrument the SDK
    client = computecapx.instrument(
        api_key="test-key",
        project_id="test-project",
        backend_url=mock_backend
    )
    
    # 1. Test requests call (verifies urllib3 urlopen does not throw NameError for backend_url exclusion)
    try:
        res = requests.post(f"{mock_backend}/dummy", json={"model": "gpt-4"})
        assert res.status_code == 200
    except Exception as e:
        pytest.fail(f"Urllib3 / Requests interceptor raised an error: {e}")

    # 2. Test httpx sync call
    try:
        with httpx.Client() as c:
            res = c.post(f"{mock_backend}/dummy", json={"model": "gpt-4"})
            assert res.status_code == 200
    except Exception as e:
        pytest.fail(f"Httpx sync interceptor raised an error: {e}")

    # 3. Test httpx async call
    async def run_async():
        async with httpx.AsyncClient() as c:
            res = await c.post(f"{mock_backend}/dummy", json={"model": "gpt-4"})
            assert res.status_code == 200

    try:
        asyncio.run(run_async())
    except Exception as e:
        pytest.fail(f"Httpx async interceptor raised an error: {e}")
