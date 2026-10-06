"""Read-only Hermes checks and explicit, guarded recovery actions."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen


@dataclass(frozen=True)
class Settings:
    hermes_home: Path
    webui_repo: Path
    port: int = 8787
    timeout: float = 3.0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def database(self) -> Path:
        return self.hermes_home / "state.db"

    @property
    def ctl(self) -> Path:
        return self.webui_repo / "ctl.sh"

    @property
    def pid_file(self) -> Path:
        return Path(os.environ.get("HERMES_WEBUI_PID_FILE", self.hermes_home / "webui.pid"))


def default_settings() -> Settings:
    home = Path.home()
    return Settings(
        hermes_home=Path(os.environ.get("HERMES_HOME", home / ".hermes")).expanduser().resolve(),
        webui_repo=Path(os.environ.get("HERMES_WEBUI_REPO", home / "hermes-webui")).expanduser().resolve(),
        port=int(os.environ.get("HERMES_WEBUI_PORT", "8787")),
    )


def redact(text: str) -> str:
    """Hide common credential forms before logs leave the machine as a report."""
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)\b(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
        r"password|secret)\b(\s*[:=]\s*[\"']?)([^\s,}\]&#\"']+)",
        r"\1\2[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)([?&](?:token|key|api_key)=)[^\s&]+", r"\1[REDACTED]", text)
    return text


def tail_lines(path: Path, count: int = 80, max_bytes: int = 512_000) -> list[str]:
    if not path.is_file():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
        lines = data.decode("utf-8", errors="replace").splitlines()
        if size > max_bytes and lines:
            lines = lines[1:]
        return [redact(line) for line in lines[-count:]]
    except OSError:
        return []


def listener_pids(port: int) -> list[int] | None:
    """None means lsof is unavailable; [] means the port has no listener."""
    if shutil.which("lsof") is None:
        return None
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode not in (0, 1):
        return None
    return sorted({int(item) for item in result.stdout.split() if item.isdecimal()})


def managed_pid(settings: Settings) -> int | None:
    try:
        raw = settings.pid_file.read_text().strip()
    except OSError:
        return None
    return int(raw) if raw.isdecimal() else None


def probe(url: str, timeout: float) -> dict:
    start = time.monotonic()
    try:
        with urlopen(url, timeout=timeout) as response:
            response.read(256)
            status = response.status
        return {"status": status, "seconds": round(time.monotonic() - start, 3)}
    except HTTPError as exc:
        return {"status": exc.code, "seconds": round(time.monotonic() - start, 3)}
    except (URLError, TimeoutError, OSError) as exc:
        return {"status": None, "seconds": round(time.monotonic() - start, 3),
                "error": type(exc).__name__}


def database_check(path: Path, max_seconds: float = 6.0) -> dict:
    if not path.is_file():
        return {"status": "missing"}
    deadline = time.monotonic() + max_seconds
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=min(max_seconds, 5))
        try:
            connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
            row = connection.execute("PRAGMA quick_check").fetchone()
            result = row[0] if row else "no result"
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return {"status": "error", "error": redact(str(exc))}
    return {"status": "ok" if result == "ok" else "error",
            "result": result if result == "ok" else redact(str(result))}


def _recent_log_signal(lines: list[str], phrase: str, now: datetime) -> list[str]:
    cutoff = now - timedelta(hours=2)
    hits = []
    for line in lines:
        if phrase not in line:
            continue
        try:
            timestamp = datetime.fromisoformat(line[:23])
        except ValueError:
            continue
        if timestamp >= cutoff:
            hits.append(line)
    return hits


def collect(settings: Settings) -> dict:
    pids = listener_pids(settings.port)
    pid_file = managed_pid(settings)
    base = settings.base_url
    health = probe(base + "/health", settings.timeout)
    deep = probe(base + "/health?deep=1", settings.timeout)
    sessions = probe(
        base + "/api/sessions?sidebar_source=webui&exclude_hidden=1", settings.timeout
    )
    db = database_check(settings.database)
    errors = tail_lines(settings.hermes_home / "logs" / "errors.log", 300)
    now = datetime.now()
    recent_locks = _recent_log_signal(errors, "database is locked", now)
    handle_warnings = _recent_log_signal(errors, "live SessionDB handles", now)
    findings = []
    if pids == []:
        findings.append("هیچ پردازه‌ای روی پورت تنظیم‌شدهٔ وب‌یو‌آی گوش نمی‌دهد.")
    if deep["status"] not in (200, 401):
        findings.append("بررسی عمیق سلامت پاسخ عادی نداد.")
    if sessions["status"] not in (200, 401):
        findings.append("فهرست نشست‌ها پاسخ عادی نداد.")
    if db["status"] != "ok":
        findings.append("بررسی دیتابیس وضعیت نیازمند بررسی را نشان می‌دهد.")
    if recent_locks:
        findings.append(f"در دو ساعت اخیر {len(recent_locks)} خطای قفل دیتابیس ثبت شده است.")
    if handle_warnings:
        findings.append("هرمس دربارهٔ چند اتصال هم‌زمان نویسنده به SessionDB هشدار داده است.")
    recommendations = []
    if recent_locks or handle_warnings:
        recommendations.append(
            "کارهای باز را ذخیره کنید، از state.db پشتیبان بگیرید و فقط پردازهٔ تأییدشدهٔ "
            "وب‌یو‌آی را بازراه‌اندازی کنید. پردازهٔ ترمینالی را از همان ترمینال متوقف کنید."
        )
        recommendations.append(
            "فقط یک نمونهٔ تحت مدیریت وب‌یو‌آی اجرا کنید و نوشتن هم‌زمان چند ابزار روی state.db را کاهش دهید."
        )
    if db["status"] != "ok":
        recommendations.append("تعمیر اجباری دیتابیس انجام ندهید؛ اول یک نسخه نگه دارید و خطای SQLite را بررسی کنید.")
    if pids == []:
        recommendations.append("پس از پشتیبان‌گیری موفق، وب‌یو‌آی را با ctl.sh اجرا کنید.")
    if pids and pid_file not in pids:
        recommendations.append(
            "این پردازه زیر کنترل ctl.sh نیست. پس از پشتیبان‌گیری، در ترمینال اصلی Ctrl+C بزنید. "
            "اگر گیر کرد، PID را بررسی و TERM بفرستید؛ فقط در صورت بی‌اثر بودن آن از KILL استفاده کنید. "
            "سپس یک نمونهٔ تحت مدیریت راه بیندازید."
        )
    if recent_locks and handle_warnings:
        diagnosis = (
            "احتمال زیاد: رقابت چند اتصال نویسنده بر سر SQLite باعث کندی یا قفل شدن WebUI شده است. "
            "لاگ‌ها هم‌زمانی را نشان می‌دهند، اما اتصال یا دستور دقیقِ آغازگر قفل را ثابت نمی‌کنند."
        )
    elif recent_locks:
        diagnosis = "خطای قفل SQLite ثبت شده است؛ منشأ دقیق قفل از این بررسی مشخص نمی‌شود."
    elif pids == []:
        diagnosis = "وب‌یو‌آی اکنون روی پورت تنظیم‌شده اجرا نمی‌شود؛ علت توقف را در لاگ‌ها بررسی کنید."
    elif deep["status"] not in (200, 401) or sessions["status"] not in (200, 401):
        diagnosis = "پردازه زنده است ولی پاسخ API عادی نیست؛ لاگ خطا و وضعیت دیتابیس را بررسی کنید."
    elif db["status"] != "ok":
        diagnosis = "بررسی سلامت دیتابیس موفق نبود؛ علت دقیق در جزئیات خطای SQLite است."
    else:
        diagnosis = "نشانهٔ فعال از هنگ کردن در این بررسی دیده نشد."
    status = "stopped" if pids == [] else "degraded" if findings else "healthy"
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": status,
        "settings": {
            "hermes_home": str(settings.hermes_home),
            "webui_repo": str(settings.webui_repo),
            "port": settings.port,
        },
        "webui": {
            "listener_pids": pids,
            "ctl_pid": pid_file,
            "managed_by_ctl": bool(pids is not None and pid_file in pids),
            "health": health,
            "deep_health": deep,
            "sessions": sessions,
        },
        "database": {"path": str(settings.database), "bytes": settings.database.stat().st_size
                     if settings.database.is_file() else None, **db},
        "log_signals": {
            "recent_lock_errors": len(recent_locks),
            "recent_handle_warnings": len(handle_warnings),
            "latest_lock_error": recent_locks[-1] if recent_locks else None,
            "latest_handle_warning": handle_warnings[-1] if handle_warnings else None,
        },
        "findings": findings,
        "diagnosis": diagnosis,
        "recommendations": recommendations,
    }


def log_view(settings: Settings, kind: str, lines: int = 100) -> dict:
    paths = {
        "errors": settings.hermes_home / "logs" / "errors.log",
        "agent": settings.hermes_home / "logs" / "agent.log",
        "gateway": settings.hermes_home / "logs" / "gateway.log",
        "webui": Path(os.environ.get("HERMES_WEBUI_LOG_FILE", settings.hermes_home / "webui.log")),
    }
    if kind not in paths:
        raise ValueError("Unknown log type")
    return {"kind": kind, "path": str(paths[kind]), "lines": tail_lines(paths[kind], min(lines, 300))}


def backup_database(settings: Settings) -> Path:
    if not settings.database.is_file():
        raise RuntimeError("state.db was not found; no restart was attempted")
    backup_dir = settings.hermes_home / "backups" / "hermes-health-wizard"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = datetime.now().strftime("state-%Y%m%d-%H%M%S")
    fd, temporary = tempfile.mkstemp(prefix=name + "-", suffix=".partial", dir=backup_dir)
    os.close(fd)
    temporary_path = Path(temporary)
    destination = temporary_path.with_suffix(".db")
    uri = "file:" + quote(str(settings.database), safe="/") + "?mode=ro"
    try:
        source = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            target = sqlite3.connect(temporary_path, timeout=5)
            try:
                deadline = time.monotonic() + 30
                def progress(_status, _remaining, _total):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Database backup timed out after 30 seconds")
                source.backup(target, pages=512, progress=progress, sleep=0.1)
                row = target.execute("PRAGMA quick_check").fetchone()
                if not row or row[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
            finally:
                target.close()
        finally:
            source.close()
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(destination)
        return destination
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def restart_webui(settings: Settings) -> dict:
    if not settings.ctl.is_file():
        raise RuntimeError(f"WebUI controller not found: {settings.ctl}")
    pids = listener_pids(settings.port)
    pid_file = managed_pid(settings)
    if pids is None:
        raise RuntimeError("Cannot verify WebUI ownership because lsof is unavailable")
    if pids and (len(pids) != 1 or pid_file not in pids):
        raise RuntimeError(
            "A WebUI process is listening but ctl.sh does not own it. Stop that foreground "
            "process in its own terminal, then run ctl.sh start. No process was terminated."
        )
    if pids:
        state_file = settings.hermes_home / "webui.ctl.env"
        if not state_file.is_file():
            raise RuntimeError(
                "ctl.sh state file is missing; refusing to restart a listener it cannot verify"
            )
        try:
            identity = subprocess.run(
                ["ps", "-ww", "-p", str(pid_file), "-o", "args="],
                capture_output=True, text=True, timeout=3, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                "Cannot inspect the WebUI process command; no restart was attempted"
            ) from exc
        if identity.returncode != 0 or str(settings.webui_repo) not in identity.stdout:
            raise RuntimeError(
                "Cannot verify that ctl.sh owns this WebUI process. Run recovery from a "
                "normal terminal with process-inspection permission. No restart was attempted."
            )
    backup = backup_database(settings)
    command = "restart" if pids else "start"
    env = os.environ.copy()
    env["HERMES_HOME"] = str(settings.hermes_home)
    env["HERMES_WEBUI_PORT"] = str(settings.port)
    try:
        result = subprocess.run(
            ["bash", str(settings.ctl), command], cwd=settings.webui_repo, env=env,
            capture_output=True, text=True, timeout=60, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"WebUI {command} timed out; backup: {backup}") from exc
    output = redact((result.stdout + "\n" + result.stderr).strip())[-3000:]
    if result.returncode != 0:
        raise RuntimeError(f"WebUI {command} failed (exit {result.returncode}); backup: {backup}\n{output}")
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if probe(settings.base_url + "/health?deep=1", 2)["status"] == 200:
            return {"ok": True, "action": command, "backup": str(backup), "output": output}
        time.sleep(0.5)
    raise RuntimeError(f"WebUI did not pass deep health after {command}; backup: {backup}")
