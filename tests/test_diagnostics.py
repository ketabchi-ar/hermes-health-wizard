import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_wizard.cli import main
from hermes_wizard.diagnostics import (
    Settings, backup_database, collect, database_check, log_view, redact, restart_webui,
)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.home = root / "hermes"
        self.repo = root / "webui"
        self.home.mkdir()
        self.repo.mkdir()
        self.settings = Settings(self.home, self.repo, port=18787, timeout=0.2)

    def tearDown(self):
        self.temp.cleanup()

    def make_database(self):
        with sqlite3.connect(self.settings.database) as db:
            db.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, title TEXT)")
            db.execute("INSERT INTO sessions(title) VALUES ('keep this data')")

    def test_backup_is_consistent_and_preserves_source(self):
        self.make_database()
        backup = backup_database(self.settings)
        self.assertTrue(backup.is_file())
        self.assertEqual(database_check(backup)["status"], "ok")
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute("SELECT title FROM sessions").fetchone()[0], "keep this data")
        with sqlite3.connect(self.settings.database) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)

    def test_unmanaged_listener_is_never_restarted_or_backed_up(self):
        self.make_database()
        (self.repo / "ctl.sh").write_text("#!/bin/sh\nexit 0\n")
        with patch("hermes_wizard.diagnostics.listener_pids", return_value=[4321]), \
             patch("hermes_wizard.diagnostics.managed_pid", return_value=None), \
             patch("hermes_wizard.diagnostics.subprocess.run") as run:
            with self.assertRaisesRegex(RuntimeError, "does not own"):
                restart_webui(self.settings)
            run.assert_not_called()
        self.assertFalse((self.home / "backups").exists())

    def test_managed_restart_backs_up_before_controller_runs(self):
        self.make_database()
        (self.repo / "ctl.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.home / "webui.ctl.env").write_text("PID=123\n")
        backup_seen = []
        def fake_run(command, **_kwargs):
            if command[0] == "ps":
                return subprocess.CompletedProcess(command, 0, f"python {self.repo}/bootstrap.py", "")
            backup_seen.extend((self.home / "backups" / "hermes-health-wizard").glob("*.db"))
            return subprocess.CompletedProcess(command, 0, "restarted", "")
        with patch("hermes_wizard.diagnostics.listener_pids", return_value=[123]), \
             patch("hermes_wizard.diagnostics.managed_pid", return_value=123), \
             patch("hermes_wizard.diagnostics.subprocess.run", side_effect=fake_run), \
             patch("hermes_wizard.diagnostics.probe", return_value={"status": 200}):
            result = restart_webui(self.settings)
        self.assertEqual(result["action"], "restart")
        self.assertEqual(len(backup_seen), 1)
        self.assertEqual(database_check(backup_seen[0])["status"], "ok")

    def test_restart_refuses_when_process_inspection_is_unavailable(self):
        self.make_database()
        (self.repo / "ctl.sh").write_text("#!/bin/sh\nexit 0\n")
        state = self.home / "webui.ctl.env"
        state.write_text("PID=123\n")
        with patch("hermes_wizard.diagnostics.listener_pids", return_value=[123]), \
             patch("hermes_wizard.diagnostics.managed_pid", return_value=123), \
             patch("hermes_wizard.diagnostics.subprocess.run", return_value=subprocess.CompletedProcess(
                 ["ps"], 1, "", "operation not permitted")) as run:
            with self.assertRaisesRegex(RuntimeError, "Cannot verify"):
                restart_webui(self.settings)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(state.read_text(), "PID=123\n")
        self.assertFalse((self.home / "backups").exists())

    def test_recent_lock_and_writer_warnings_appear_in_report(self):
        self.make_database()
        from datetime import datetime
        stamp = datetime.now().isoformat(timespec="milliseconds")
        logs = self.home / "logs"
        logs.mkdir()
        (logs / "errors.log").write_text(
            f"{stamp} database is locked\n{stamp} 5 live SessionDB handles\n"
        )
        with patch("hermes_wizard.diagnostics.listener_pids", return_value=[123]), \
             patch("hermes_wizard.diagnostics.managed_pid", return_value=123), \
             patch("hermes_wizard.diagnostics.probe", return_value={"status": 200, "seconds": 0.01}):
            report = collect(self.settings)
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["log_signals"]["recent_lock_errors"], 1)
        self.assertEqual(report["log_signals"]["recent_handle_warnings"], 1)
        self.assertIn("SQLite", report["diagnosis"])
        self.assertTrue(report["recommendations"])

    def test_log_view_redacts_common_credentials(self):
        logs = self.home / "logs"
        logs.mkdir()
        (logs / "errors.log").write_text("Authorization: Bearer abc123\napi_key=secret123\n")
        lines = log_view(self.settings, "errors")["lines"]
        self.assertNotIn("abc123", "\n".join(lines))
        self.assertNotIn("secret123", "\n".join(lines))
        self.assertIn("[REDACTED]", "\n".join(lines))

    def test_doctor_json_is_parseable(self):
        self.make_database()
        from io import StringIO
        from contextlib import redirect_stdout
        output = StringIO()
        with patch("hermes_wizard.diagnostics.listener_pids", return_value=[]), \
             patch("hermes_wizard.diagnostics.probe", return_value={"status": None, "seconds": 0.01}), \
             redirect_stdout(output):
            code = main([
                "--hermes-home", str(self.home), "--webui-repo", str(self.repo),
                "--port", "18787", "doctor", "--json",
            ])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
