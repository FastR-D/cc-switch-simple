"""Actual CLI subprocess + installed ModelTrace + local mock API integration."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

try:
    import modeltrace
except ImportError:
    modeltrace = None


class MockAPI(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.server.behavior == "auth":
            self.send_response(401)
            payload = {"error": {"message": "test rejection"}}
        else:
            self.send_response(200)
            count = 800
            text = "1 2 3" if self.server.behavior == "short" else ",".join(str((i * 17 + 13) % 1000) for i in range(count))
            if self.path.endswith("/responses"):
                payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}
            elif self.path.endswith("/messages"):
                payload = {"content": [{"type": "text", "text": text}]}
            else:
                payload = {"choices": [{"message": {"content": text}}]}
        self.server.requests.append((self.path, request))
        encoded = json.dumps(payload).encode("utf-8")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *arguments):
        pass


@unittest.skipIf(modeltrace is None, "installed ModelTrace library is required")
class LibraryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockAPI)
        self.server.behavior = "valid"
        self.server.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.source = self.root / "provider.yaml"
        self.config = {"name": "integration", "base_url": f"http://127.0.0.1:{self.server.server_port}/v1",
                       "type": "openai", "api_key": "local-integration-only", "model_name": "local-mock"}
        self.source.write_text(yaml.safe_dump(self.config), encoding="utf-8")

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def command(self, *arguments):
        # Run the installed console script away from the repository.
        binary = Path(sys.executable).with_name("cc-simple.exe" if sys.platform == "win32" else "cc-simple")
        environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run([str(binary), "--json", "--config-dir", str(self.root / "store"), "check", str(self.source), "--timeout", "5", "--attempts", "3", *arguments], cwd=self.root, env=environment, capture_output=True, encoding="utf-8", timeout=30)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(self.config["api_key"], result.stdout + result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_responses_default_full_fingerprint(self):
        code, result = self.command()
        self.assertEqual(code, 0, result)
        self.assertEqual(result["analysis"]["used_outputs"], 3)
        self.assertEqual(result["analysis"]["api_test"]["api_format"], "responses")
        self.assertEqual({item["model"] for item in result["analysis"]["results"]},
                         {item["id"] for item in modeltrace.load_bank()["models"]})
        self.assertEqual([path for path, _ in self.server.requests], ["/v1/responses"] * 3)
        self.assertFalse((self.root / "store").exists())

    def test_chat_protocol_override(self):
        code, result = self.command("--format", "openai")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["analysis"]["api_test"]["api_format"], "openai")
        self.assertEqual([path for path, _ in self.server.requests], ["/v1/chat/completions"] * 3)

    def test_anthropic_default_protocol(self):
        self.config["type"] = "anthropic"
        self.config["base_url"] = self.config["base_url"].removesuffix("/v1")
        self.source.write_text(yaml.safe_dump(self.config), encoding="utf-8")
        code, result = self.command()
        self.assertEqual(code, 0, result)
        self.assertEqual(result["analysis"]["api_test"]["api_format"], "anthropic")
        self.assertEqual([path for path, _ in self.server.requests], ["/v1/messages"] * 3)

    def test_auth_failure_and_short_outputs_return_nonzero(self):
        for behavior in ("auth", "short"):
            with self.subTest(behavior=behavior):
                self.server.behavior = behavior
                code, result = self.command()
                self.assertEqual(code, 1)
                self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
