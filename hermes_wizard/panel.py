"""Loopback-only control panel. No third-party assets or telemetry."""

from __future__ import annotations

import json
import secrets
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .diagnostics import Settings, backup_database, collect, log_view, restart_webui


HTML = """<!doctype html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hermes Health Wizard</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, -apple-system, sans-serif; background:#0b1020; color:#e8edf7; }
    * { box-sizing: border-box; }
    body { margin:0; padding:24px; }
    main { max-width:1080px; margin:auto; }
    header { display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap; margin-bottom:22px; }
    h1 { margin:0; font-size:1.8rem; }
    h2 { margin:0 0 12px; font-size:1.12rem; }
    p { line-height:1.8; color:#b9c4d8; }
    .eyebrow { color:#7dd3fc; font-size:.82rem; letter-spacing:.08em; text-transform:uppercase; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(270px,1fr)); gap:16px; }
    .card { background:#151e31; border:1px solid #30405d; border-radius:16px; padding:20px; min-width:0; }
    .wide { grid-column:1/-1; }
    .badge { display:inline-block; padding:6px 11px; border-radius:999px; background:#334155; font-weight:700; }
    .badge.healthy { background:#14532d; color:#bbf7d0; }
    .badge.degraded { background:#78350f; color:#fde68a; }
    .badge.stopped { background:#7f1d1d; color:#fecaca; }
    .facts { margin:0; display:grid; grid-template-columns:auto 1fr; gap:9px 15px; }
    dt { color:#94a3b8; } dd { margin:0; overflow-wrap:anywhere; }
    button, select { font:inherit; }
    button { border:1px solid #466188; background:#243754; color:#eef6ff; border-radius:10px; padding:10px 14px; cursor:pointer; }
    button:hover { background:#315078; }
    button:focus-visible, select:focus-visible { outline:3px solid #7dd3fc; outline-offset:2px; }
    button.danger { background:#7f1d1d; border-color:#b91c1c; }
    button.danger:hover { background:#991b1b; }
    button:disabled { opacity:.5; cursor:wait; }
    .actions { display:flex; gap:10px; flex-wrap:wrap; }
    ul { margin:0; padding-right:22px; line-height:1.9; }
    pre { direction:ltr; text-align:left; white-space:pre-wrap; overflow-wrap:anywhere; max-height:420px; overflow:auto; background:#09111f; padding:14px; border-radius:10px; font-size:.83rem; }
    .muted { color:#94a3b8; }
    .notice { min-height:1.5em; color:#7dd3fc; }
    select { background:#09111f; color:#e8edf7; border:1px solid #466188; border-radius:10px; padding:9px; }
  </style>
</head>
<body>
<main>
  <header>
    <div><div class="eyebrow">LOCAL DIAGNOSTICS</div><h1>ویزارد سلامت هرمس</h1>
      <p>گزارش فقط روی همین رایانه ساخته می‌شود. هیچ داده‌ای ارسال یا حذف نمی‌شود.</p></div>
    <span id="status" class="badge">در حال بررسی…</span>
  </header>
  <div class="grid">
    <section class="card"><h2>وب‌یو‌آی</h2><dl class="facts" id="webuiFacts"></dl></section>
    <section class="card"><h2>دیتابیس</h2><dl class="facts" id="dbFacts"></dl></section>
    <section class="card"><h2>اقدام‌ها</h2>
      <div class="actions">
        <button id="refresh">بررسی دوباره</button>
        <button id="backup">پشتیبان‌گیری از دیتابیس</button>
        <button id="restart" class="danger">راه‌اندازی دوبارهٔ وب‌یو‌آی</button>
        <button id="export">دریافت گزارش JSON</button>
      </div>
      <p class="muted">قبل از راه‌اندازی دوباره، یک پشتیبان سازگار از دیتابیس گرفته می‌شود. پردازهٔ نامشخص به‌صورت خودکار متوقف نمی‌شود.</p>
    </section>
    <section class="card wide"><h2>یافته‌ها</h2><ul id="findings"></ul></section>
    <section class="card wide"><h2>برداشت از علت</h2><p id="diagnosis"></p></section>
    <section class="card wide"><h2>گام‌های پیشنهادی</h2><ul id="recommendations"></ul></section>
    <section class="card wide"><h2>لاگ‌ها</h2>
      <div class="actions"><select id="logKind" aria-label="نوع لاگ">
        <option value="errors">خطاها</option><option value="webui">وب‌یو‌آی</option>
        <option value="agent">ایجنت</option><option value="gateway">Gateway</option>
      </select><button id="loadLogs">نمایش ۱۰۰ خط آخر</button></div>
      <pre id="logs">برای دیدن لاگ، دکمهٔ بالا را بزنید.</pre>
    </section>
  </div>
  <p id="notice" class="notice" role="status"></p>
</main>
<script nonce="__NONCE__">
(() => {
  const params = new URLSearchParams(location.hash.slice(1));
  const token = params.get('token') || sessionStorage.getItem('hermesWizardToken');
  if (params.get('token')) {
    sessionStorage.setItem('hermesWizardToken', token);
    history.replaceState(null, '', location.pathname);
  }
  const $ = id => document.getElementById(id);
  const notice = message => { $('notice').textContent = message; };
  const api = async (path, method='GET') => {
    if (!token) throw new Error('توکن پنل یافت نشد؛ پنل را دوباره از ترمینال اجرا کنید.');
    const response = await fetch(path, {method, headers:{'X-Wizard-Token':token}, cache:'no-store'});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'درخواست ناموفق بود');
    return body;
  };
  const facts = (id, rows) => {
    const root = $(id); root.replaceChildren();
    for (const [label, value] of rows) {
      const dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = label; dd.textContent = String(value ?? 'نامشخص');
      root.append(dt, dd);
    }
  };
  const refresh = async () => {
    notice('در حال بررسی…');
    try {
      const report = await api('/api/report');
      const badge = $('status'); badge.className = 'badge ' + report.status;
      badge.textContent = ({healthy:'سالم', degraded:'نیازمند بررسی', stopped:'متوقف'})[report.status] || report.status;
      const w = report.webui, d = report.database;
      facts('webuiFacts', [
        ['پورت', report.settings.port], ['پردازه', (w.listener_pids || []).join(', ') || 'یافت نشد'],
        ['مدیریت با ctl.sh', w.managed_by_ctl ? 'بله' : 'خیر'],
        ['بررسی عمیق', w.deep_health.status + ' · ' + w.deep_health.seconds + 's'],
        ['فهرست نشست‌ها', w.sessions.status + ' · ' + w.sessions.seconds + 's'],
      ]);
      facts('dbFacts', [
        ['وضعیت', d.status], ['اندازه', d.bytes == null ? 'نامشخص' : (d.bytes/1048576).toFixed(1)+' MiB'],
        ['خطای قفل در ۲ ساعت', report.log_signals.recent_lock_errors],
        ['هشدار اتصال نویسنده', report.log_signals.recent_handle_warnings],
      ]);
      const list = $('findings'); list.replaceChildren();
      for (const finding of report.findings.length ? report.findings : ['مورد فعالی یافت نشد.']) {
        const li = document.createElement('li'); li.textContent = finding; list.append(li);
      }
      $('diagnosis').textContent = report.diagnosis;
      const steps = $('recommendations'); steps.replaceChildren();
      for (const step of report.recommendations.length ? report.recommendations : ['اقدام خاصی لازم نیست.']) {
        const li = document.createElement('li'); li.textContent = step; steps.append(li);
      }
      notice('آخرین بررسی: ' + report.generated_at);
      return report;
    } catch (error) { notice('خطا: ' + error.message); return null; }
  };
  $('refresh').onclick = refresh;
  $('loadLogs').onclick = async () => {
    try {
      const data = await api('/api/logs?kind=' + encodeURIComponent($('logKind').value));
      $('logs').textContent = data.lines.join('\\n') || 'لاگی یافت نشد.';
      notice('لاگ محلی نمایش داده شد.');
    } catch (error) { notice('خطا: ' + error.message); }
  };
  $('backup').onclick = async () => {
    $('backup').disabled = true; notice('در حال گرفتن پشتیبان…');
    try { const result = await api('/api/backup', 'POST'); notice('پشتیبان سالم ساخته شد: ' + result.path); }
    catch (error) { notice('خطا: ' + error.message); }
    finally { $('backup').disabled = false; }
  };
  $('restart').onclick = async () => {
    if (!confirm('وب‌یو‌آی دوباره راه‌اندازی شود؟ کارهای در حال اجرا ممکن است قطع شوند. پیش از آن از دیتابیس پشتیبان گرفته می‌شود.')) return;
    $('restart').disabled = true; notice('در حال پشتیبان‌گیری و راه‌اندازی دوباره…');
    try {
      const result = await api('/api/restart', 'POST');
      notice('وب‌یو‌آی آماده است. پشتیبان: ' + result.backup);
      await refresh();
    } catch (error) { notice('خطا: ' + error.message); }
    finally { $('restart').disabled = false; }
  };
  $('export').onclick = async () => {
    try {
      const report = await api('/api/report');
      const blob = new Blob([JSON.stringify(report, null, 2)], {type:'application/json'});
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob); link.download = 'hermes-health-report.json';
      link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      notice('گزارش ذخیره شد؛ پیش از ارسال به دیگران آن را بازبینی کنید.');
    } catch (error) { notice('خطا: ' + error.message); }
  };
  refresh();
})();
</script>
</body>
</html>"""


def make_handler(settings: Settings, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HermesHealthWizard/0.1"

        def log_message(self, format_string, *args):
            # Keep the URL fragment token out of logs (it is never sent over HTTP).
            return

        def _headers(self, status: int, content_type: str, length: int):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

        def _json(self, status: int, data: dict):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _authorized(self):
            supplied = self.headers.get("X-Wizard-Token", "")
            return secrets.compare_digest(supplied, token)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                nonce = secrets.token_urlsafe(18)
                body = HTML.replace("__NONCE__", nonce).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Security-Policy",
                                 f"default-src 'none'; script-src 'nonce-{nonce}'; "
                                 "style-src 'unsafe-inline'; connect-src 'self'")
                self.end_headers()
                self.wfile.write(body)
                return
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "Unauthorized"})
                return
            if parsed.path == "/api/report":
                self._json(HTTPStatus.OK, collect(settings))
            elif parsed.path == "/api/logs":
                kind = parse_qs(parsed.query).get("kind", ["errors"])[0]
                try:
                    self._json(HTTPStatus.OK, log_view(settings, kind))
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self):
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "Unauthorized"})
                return
            origin = self.headers.get("Origin")
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if origin and origin != expected:
                self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid origin"})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid content length"})
                return
            if content_length < 0 or content_length > 1024:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Request too large"})
                return
            try:
                if self.path == "/api/backup":
                    self._json(HTTPStatus.OK, {"ok": True, "path": str(backup_database(settings))})
                elif self.path == "/api/restart":
                    self._json(HTTPStatus.OK, restart_webui(settings))
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            except Exception as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)})

    return Handler


def serve_panel(settings: Settings, open_browser: bool = True) -> None:
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(settings, token))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/#token={token}"
    print("Hermes Health Wizard is running on this computer.")
    print("Open this private local URL:", url)
    print("Press Ctrl+C to stop the panel.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
