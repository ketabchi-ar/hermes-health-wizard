import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hermes_wizard.diagnostics import Settings
from hermes_wizard.panel import make_handler


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        settings = Settings(root / "home", root / "webui")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(settings, "test-token"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, method="GET", headers=None):
        request = Request(self.base + path, method=method, headers=headers or {})
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_html_is_local_and_api_requires_token(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn(b"hermesWizardToken", body)
        status, _, _ = self.request("/api/report")
        self.assertEqual(status, 403)

    def test_post_rejects_bad_origin_without_backing_up(self):
        with patch("hermes_wizard.panel.backup_database") as backup:
            status, _, body = self.request(
                "/api/backup", method="POST",
                headers={"X-Wizard-Token": "test-token", "Origin": "https://example.com"},
            )
            self.assertEqual(status, 403)
            self.assertEqual(json.loads(body)["error"], "Invalid origin")
            backup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
