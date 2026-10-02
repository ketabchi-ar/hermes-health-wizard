"""Terminal entry point for the Hermes Health Wizard."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .diagnostics import Settings, backup_database, collect, default_settings, log_view, restart_webui
from .panel import serve_panel


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="hermes-health-wizard",
        description="Local Hermes Agent/WebUI diagnosis and guarded recovery.",
    )
    result.add_argument("--hermes-home", type=Path, help="Hermes data directory (default: ~/.hermes)")
    result.add_argument("--webui-repo", type=Path, help="WebUI checkout (default: ~/hermes-webui)")
    result.add_argument("--port", type=int, help="WebUI port (default: 8787)")
    result.add_argument("--timeout", type=float, help="HTTP timeout in seconds (default: 3)")
    commands = result.add_subparsers(dest="command")
    panel = commands.add_parser("panel", help="Open the local browser panel (default)")
    panel.add_argument("--no-browser", action="store_true", help="Print URL without opening a browser")
    doctor = commands.add_parser("doctor", help="Print a read-only health report")
    doctor.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    logs = commands.add_parser("logs", help="Show recent redacted logs")
    logs.add_argument("kind", choices=("errors", "webui", "agent", "gateway"), nargs="?", default="errors")
    logs.add_argument("--lines", type=int, default=100)
    commands.add_parser("backup", help="Make a consistent state.db backup")
    restart = commands.add_parser("restart-webui", help="Back up and restart a verified ctl.sh WebUI")
    restart.add_argument("--yes", action="store_true", help="Confirm this action noninteractively")
    return result


def _settings(args: argparse.Namespace, cli: argparse.ArgumentParser) -> Settings:
    settings = default_settings()
    changes = {}
    if args.hermes_home is not None:
        changes["hermes_home"] = args.hermes_home.expanduser().resolve()
    if args.webui_repo is not None:
        changes["webui_repo"] = args.webui_repo.expanduser().resolve()
    if args.port is not None:
        if not 1 <= args.port <= 65535:
            cli.error("--port must be between 1 and 65535")
        changes["port"] = args.port
    if args.timeout is not None:
        if not 0.1 <= args.timeout <= 60:
            cli.error("--timeout must be between 0.1 and 60 seconds")
        changes["timeout"] = args.timeout
    return replace(settings, **changes)


def _print_report(report: dict) -> None:
    webui = report["webui"]
    print(f"Status: {report['status']}")
    print(f"WebUI port: {report['settings']['port']}")
    print(f"Listener PIDs: {webui['listener_pids']}")
    print(f"Managed by ctl.sh: {webui['managed_by_ctl']}")
    print(f"Deep health HTTP: {webui['deep_health']['status']}")
    print(f"Sessions HTTP: {webui['sessions']['status']}")
    print(f"Database: {report['database']['status']}")
    print(f"Recent lock errors: {report['log_signals']['recent_lock_errors']}")
    print(f"Recent writer-handle warnings: {report['log_signals']['recent_handle_warnings']}")
    print(f"Diagnosis: {report['diagnosis']}")
    for heading, key in (("Findings", "findings"), ("Suggested steps", "recommendations")):
        print(f"{heading}:")
        for item in report[key]:
            print(f"  - {item}")
        if not report[key]:
            print("  - None")


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    settings = _settings(args, cli)
    command = args.command or "panel"
    try:
        if command == "panel":
            serve_panel(settings, open_browser=not getattr(args, "no_browser", False))
            return 0
        if command == "doctor":
            report = collect(settings)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                _print_report(report)
            return 0 if report["status"] == "healthy" else 1
        if command == "logs":
            if not 1 <= args.lines <= 300:
                cli.error("--lines must be between 1 and 300")
            data = log_view(settings, args.kind, args.lines)
            print(f"# {data['path']}")
            print("\n".join(data["lines"]) if data["lines"] else "(No log lines found)")
            return 0
        if command == "backup":
            print(backup_database(settings))
            return 0
        if command == "restart-webui":
            if not args.yes:
                if not sys.stdin.isatty():
                    cli.error("restart-webui requires --yes when no terminal is attached")
                answer = input("Back up state.db and restart the verified WebUI? [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    print("Cancelled.")
                    return 0
            print(json.dumps(restart_webui(settings), ensure_ascii=False, indent=2))
            return 0
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    cli.error(f"Unknown command: {command}")
    return 2
