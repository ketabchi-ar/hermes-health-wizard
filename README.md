# Hermes Health Wizard

A small, local diagnostic panel for Hermes Agent and Hermes WebUI. It runs with Python 3.10+ and its standard library; no dependencies are downloaded to use `run.sh`.

## Quick start

On macOS or Linux, clone or download this repository, then run:

```bash
cd hermes-health-wizard
./run.sh
```

The terminal prints a private loopback URL and opens the panel in your browser. Keep that terminal open while using the panel. The panel binds only to `127.0.0.1`, chooses a free port, requires a random token for API calls, and stores no report outside your computer unless you choose **Download JSON report**. Review an exported report before sharing it: paths and error text may contain private information even though common secret patterns are redacted.

If your Hermes installation uses other paths or a different WebUI port:

```bash
./run.sh --hermes-home /path/to/.hermes --webui-repo /path/to/hermes-webui --port 8787 panel
```

The environment variables `HERMES_HOME`, `HERMES_WEBUI_REPO`, `HERMES_WEBUI_PORT`, `HERMES_WEBUI_PID_FILE`, and `HERMES_WEBUI_LOG_FILE` are also supported. The last two follow overrides used by WebUI's `ctl.sh`.

## Terminal commands

```bash
./run.sh doctor             # Read-only summary; exit 0 only when healthy
./run.sh doctor --json      # Structured report for support or scripts
./run.sh logs errors        # Last 100 redacted lines
./run.sh logs webui --lines 200
./run.sh backup             # Consistent SQLite online backup
./run.sh restart-webui      # Interactive confirmation
./run.sh restart-webui --yes
./run.sh panel --no-browser # Print the local URL
```

`doctor` checks the listener, `/health`, `/health?deep=1`, `/api/sessions`, SQLite `PRAGMA quick_check`, and recent lock/SessionDB handle warnings. Network checks use short timeouts. The report describes evidence, not a guaranteed root cause. A historical warning within two hours can keep the status at `degraded` after service has recovered.

## What recovery does

The **Back up** action uses SQLite's online backup API, checks the resulting file, and writes it under `HERMES_HOME/backups/hermes-health-wizard/` with user-only permissions. It does not delete, replace, vacuum, or reset `state.db`.

The **Restart WebUI** action first verifies that the current listener matches the PID file controlled by `ctl.sh`; it then takes a backup and invokes `ctl.sh restart`. If no listener exists, it takes a backup and invokes `ctl.sh start`. It verifies `/health?deep=1` afterward. If another process owns the port, the action stops and reports the PID. It does not signal an unknown process. A service managed by launchd/systemd should be restarted with its own supervisor.

For a stuck foreground process, take a backup, close its original terminal with `Ctrl+C`, and confirm the listener is gone before starting one `ctl.sh` instance. If `Ctrl+C` fails, inspect the reported PID and stop it with `TERM`; use `KILL` only after `TERM` fails. The wizard deliberately leaves this judgment to the operator because the port alone does not prove process ownership.

## Preventing recurring hangs

- Run one supervised WebUI instance. Use the WebUI project's `ctl.sh`, launchd, or systemd consistently; avoid a second foreground copy on the same Hermes home.
- Watch `/health?deep=1` and the session list, not just whether port 8787 is open. A listener can stay alive while SQLite writes are blocked.
- Check `errors.log` for `database is locked` and `live SessionDB handles`. Multiple long-lived writer handles are a useful signal for a WebUI or Agent issue that warrants an upstream bug report with redacted logs.
- Preserve backups before any restart or deeper repair. Do not delete `state.db`, its WAL/SHM files, or session files to clear a hang.

The behavior above is based on the current [Hermes session storage documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/session-storage.md) and [WebUI supervisor guide](https://github.com/nesquena/hermes-webui/blob/master/docs/supervisor.md). Installation layouts and service managers vary, so inspect the report before a recovery action.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q hermes_wizard
```

Tests use temporary Hermes data and never restart a live installation.

## License

MIT. See [LICENSE](LICENSE).
