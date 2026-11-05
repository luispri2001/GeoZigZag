"""HTTP and CLI integration checks; no map services, ROS or Gazebo needed."""

import http.client
import json
import socket
import time
import subprocess
import sys
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from geozigzag.robot_coverage import coverage_request
from geozigzag.web_server import REPO_ROOT, SemanticRequestHandler


class CoverageAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class QuietHandler(SemanticRequestHandler):
            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(QuietHandler, directory=str(REPO_ROOT))
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, body=None, content_type="application/json"):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        conn.request(method, path, body=body, headers={"Content-Type": content_type})
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def test_web_and_cli_share_exact_plan(self):
        payload = json.loads(Path("configs/robot_coverage_demo.json").read_text())
        expected = coverage_request(payload)
        status, body = self.request("POST", "/api/coverage", json.dumps(payload))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["waypoints"], expected["waypoints"])
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder, "run")
            command = [
                sys.executable,
                "-m",
                "geozigzag.coverage_cli",
                "--out",
                str(out),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads((out / "plan.json").read_text())["waypoints"],
                expected["waypoints"],
            )
            original = (out / "plan.json").read_bytes()
            repeated = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(repeated.returncode, 2)
            self.assertEqual((out / "plan.json").read_bytes(), original)
            for name in [
                "request.json",
                "plan.json",
                "waypoints.csv",
                "waypoints.yaml",
                "route.geojson",
            ]:
                self.assertTrue((out / name).is_file())

    def test_invalid_requests_and_unknown_route(self):
        for body, content_type in [
            ("broken", "application/json"),
            ("[]", "application/json"),
            ("{}", "text/plain"),
            (json.dumps({"vertices": None}), "application/json"),
        ]:
            status, data = self.request("POST", "/api/coverage", body, content_type)
            self.assertEqual(status, 400)
            self.assertIn("error", json.loads(data))
        status, _ = self.request("POST", "/api/unknown", "{}")
        self.assertEqual(status, 404)

    def test_documented_module_starts_with_default_dem(self):
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            port = candidate.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-m", "geozigzag", "--port", str(port), "--no-auto-port"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                if process.poll() is not None:
                    self.fail(process.stderr.read())
                try:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", port, timeout=1
                    )
                    connection.request("GET", "/api/dem/status")
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    self.assertEqual(response.status, 200)
                    self.assertTrue(payload["available"])
                    self.assertEqual(
                        payload["source"]["type"], "mapzen_terrarium_aws_open_data"
                    )
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail(
                            "Documented server command did not start within 10 seconds."
                        )
                    time.sleep(0.05)
                finally:
                    connection.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stderr.close()

    def test_static_entrypoints_remain_available(self):
        for path in [
            "/",
            "/web/index.html",
            "/web/static/studio.js",
            "/web/static/robot-coverage.js",
            "/web/static/studio.css",
            "/configs/robot_coverage_demo.json",
        ]:
            status, data = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertTrue(data)


if __name__ == "__main__":
    unittest.main()
