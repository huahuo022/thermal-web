#!/usr/bin/env python3
"""thermal-web - a small self-hosted web UI for ESC/POS thermal printers.

Runs with the Python standard library only.  Text is printed through the
printer's native code page, images are dithered in the browser and sent as a
1 bit bitmap, so the on-screen preview is exactly what the printer burns.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import posixpath
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import escpos
import transport
import auth
import update
from store import Store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
VERSION = "1.2.0"
MAX_BODY = 12 * 1024 * 1024
PRINT_LOCK = threading.Lock()
STARTED = time.time()


def log(message: str):
    sys.stderr.write("[thermal-web] %s\n" % message)
    sys.stderr.flush()


def build_payload(request: dict, settings: dict) -> tuple:
    """Turn an API request into ESC/POS bytes.  Returns ``(payload, summary)``."""
    kind = str(request.get("kind", "text")).lower()
    width = int(settings.get("width_dots", 576))
    encoding = settings.get("encoding", "gbk")
    columns = max(1, width // 12)

    if kind == "receipt":
        blocks = request.get("blocks") or []
        payload = escpos.render_receipt(
            blocks, width_dots=width, encoding=encoding, columns=columns,
            cut=settings.get("cut", "partial"), feed=int(settings.get("feed_lines", 4)),
            chinese=bool(settings.get("chinese_mode", True)),
        )
        return payload, "%d 个区块" % len(blocks)

    printer = escpos.Escpos(
        width_dots=width, encoding=encoding, columns=columns,
        cut=settings.get("cut", "partial"), feed=int(settings.get("feed_lines", 4)),
        chinese=bool(settings.get("chinese_mode", True)),
    )
    printer.init()
    if printer.chinese and str(encoding).lower().startswith("gb"):
        printer.chinese_mode(True)

    if kind == "text":
        text = str(request.get("text", ""))
        printer.line(text, align=request.get("align", "left"),
                     size=request.get("size", "normal"), bold=bool(request.get("bold")),
                     underline=int(request.get("underline", 0)),
                     invert=bool(request.get("invert")),
                     font=request.get("font", settings.get("font", "a")))
        summary = "文本 %d 字符" % len(text)

    elif kind == "qr":
        printer.qr(request.get("data", ""), int(request.get("module", 6)),
                   request.get("ecc", "M"), request.get("align", "center"))
        summary = "二维码"

    elif kind == "barcode":
        printer.barcode(request.get("data", ""), request.get("symbology", "code128"),
                        int(request.get("height", 80)), int(request.get("width", 2)),
                        request.get("hri", "below"), request.get("font", "a"),
                        request.get("align", "center"))
        summary = "条码 %s" % request.get("symbology", "code128")

    elif kind in ("image", "bitmap"):
        packed = base64.b64decode(request.get("bitmap", ""))
        image_width = int(request.get("width", width))
        image_height = int(request.get("height", 0))
        if image_width <= 0 or image_width % 8 or image_height <= 0:
            raise ValueError("bitmap needs width (multiple of 8) and height")
        printer.raster(packed, image_width, image_height)
        printer.feed(1)
        summary = "位图 %dx%d" % (image_width, image_height)

    elif kind == "image-file":
        try:
            from PIL import Image
        except ImportError:
            raise ValueError("Pillow is not installed on the server; "
                             "use browser side dithering (kind=bitmap) instead")
        blob = base64.b64decode(request.get("file", ""))
        import io
        image = Image.open(io.BytesIO(blob))
        threshold = int(request.get("threshold", settings.get("threshold", 160)))
        printer.raster_from_image(image, request.get("dither", "threshold"), threshold,
                                  bool(request.get("trim", True)))
        printer.feed(1)
        summary = "图片 %s" % image.format

    else:
        raise ValueError("unknown job kind %r" % kind)

    if not request.get("no_cut"):
        printer.feed(printer.feed_lines)
        printer.cut()
    return printer.bytes(), summary


def test_receipt(settings: dict) -> tuple:
    """A self test page: styles, divider, QR, barcode, raster and cut."""
    width = int(settings.get("width_dots", 576))
    columns = max(1, width // 12)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    blocks = [
        {"type": "text", "text": "THERMAL-WEB", "align": "center", "size": "double",
         "bold": True},
        {"type": "text", "text": "self test / 自检页", "align": "center"},
        {"type": "divider"},
        {"type": "kv", "key": "打印目标", "value": settings.get("target", "")},
        {"type": "kv", "key": "纸宽", "value": "%d dots / %d 列" % (width, columns)},
        {"type": "kv", "key": "编码", "value": str(settings.get("encoding", "gbk"))},
        {"type": "kv", "key": "切纸", "value": str(settings.get("cut", "partial"))},
        {"type": "kv", "key": "时间", "value": now},
        {"type": "divider"},
        {"type": "text", "text": "字体 A 正常 / 加倍 / 加粗 / 下划线",
         "align": "center", "size": "normal"},
        {"type": "text", "text": "DOUBLE SIZE 加倍字", "align": "center", "size": "double",
         "bold": True},
        {"type": "text", "text": "反白测试 INVERT", "align": "center", "invert": True},
        {"type": "text", "text": "下划线文本", "align": "center", "underline": 1},
        {"type": "divider"},
        {"type": "row", "cells": ["品名", "数量", "金额"], "widths": [columns - 16, 6, 10],
         "aligns": ["left", "center", "right"], "bold": True},
        {"type": "row", "cells": ["测试商品", "1", "12.00"],
         "widths": [columns - 16, 6, 10], "aligns": ["left", "center", "right"]},
        {"type": "row", "cells": ["另一个项目", "2", "34.50"],
         "widths": [columns - 16, 6, 10], "aligns": ["left", "center", "right"]},
        {"type": "divider"},
        {"type": "kv", "key": "合计", "value": "¥ 46.50", "bold": True},
        {"type": "qr", "data": "https://github.com/", "module": 5},
        {"type": "barcode", "data": "123456789012", "symbology": "ean13", "height": 70},
        {"type": "text", "text": "如纸边被截断，请在设置里改为 384 点(58mm)",
         "align": "center", "size": "normal"},
    ]
    payload = escpos.render_receipt(
        blocks, width_dots=width, encoding=settings.get("encoding", "gbk"),
        columns=columns, cut=settings.get("cut", "partial"),
        feed=int(settings.get("feed_lines", 4)),
        chinese=bool(settings.get("chinese_mode", True)),
    )
    return payload, "自检页"


class Handler(BaseHTTPRequestHandler):
    server_version = "thermal-web/" + VERSION
    store: Store = None
    data_dir: str = ""
    username: str = "admin"
    password: str = ""
    password_sha256: str = ""
    secret: bytes = b""
    session_ttl: int = auth.DEFAULT_SESSION_HOURS * 3600
    cookie_secure: bool = False
    attempts = auth.Attempts()
    PUBLIC_PATHS = ("/login", "/login.js", "/style.css", "/favicon.ico")

    # ------------------------------------------------------------- plumbing
    def log_message(self, fmt, *args):
        log("%s - %s" % (self.address_string(), fmt % args))

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message, status=400):
        self._json({"ok": False, "error": str(message)}, status)

    # ------------------------------------------------------------------ auth
    @property
    def auth_enabled(self) -> bool:
        return bool(self.password or self.password_sha256)

    def _client(self) -> str:
        return self.client_address[0] if self.client_address else "?"

    def _forwarded_proto(self) -> str:
        """Scheme the client used, as reported by a reverse proxy."""
        value = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        if value in ("http", "https"):
            return value
        visitor = (self.headers.get("CF-Visitor") or "").replace(" ", "")
        if '"scheme":"https"' in visitor:
            return "https"
        if "proto=https" in (self.headers.get("Forwarded") or "").lower():
            return "https"
        return ""

    def _secure_cookie(self) -> bool:
        """Mark the session cookie Secure on HTTPS, but keep plain HTTP usable."""
        return bool(self.cookie_secure) or self._forwarded_proto() == "https"

    def _basic_user(self):
        pair = auth.basic_credentials(self.headers.get("Authorization", ""))
        if not pair or not auth.check_credentials(pair[0], pair[1], self.username,
                                                   self.password, self.password_sha256):
            return None
        return pair[0]

    def _cookie_user(self):
        token = auth.read_cookie(self.headers.get("Cookie", ""))
        return auth.parse_token(token, self.secret) if token else None

    def _authorized(self):
        """``""`` when auth is off, the user name when authenticated, else None."""
        if not self.auth_enabled:
            return ""
        return self._basic_user() or self._cookie_user()

    def _unauthorized(self, api: bool):
        """No ``WWW-Authenticate`` header on purpose: the browser must keep
        using our own login page instead of its native basic auth dialog."""
        if api:
            return self._json({"ok": False, "error": "未登录或会话已过期",
                               "login": "/login"}, 401)
        target = "/login?next=" + urllib.parse.quote(self.path or "/")
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _redirect(self, target: str):
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _session_cookie(self, user: str) -> str:
        token = auth.make_token(user, self.secret, self.session_ttl)
        cookie = "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" % (
            auth.COOKIE_NAME, token, int(self.session_ttl))
        if self._secure_cookie():
            cookie += "; Secure"
        return cookie

    def _expired_cookie(self) -> str:
        cookie = "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % auth.COOKIE_NAME
        if self._secure_cookie():
            cookie += "; Secure"
        return cookie

    def _send_json(self, payload, status=200, cookie: str = None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _login(self, request: dict):
        target = str(request.get("next") or "/")
        if not target.startswith("/") or target.startswith("//"):
            target = "/"
        if not self.auth_enabled:
            return self._send_json({"ok": True, "redirect": target})

        client = self._client()
        if self.attempts.blocked(client):
            log("login throttled for %s" % client)
            return self._json({"ok": False, "error": "尝试次数过多，请稍后再试"}, 429)

        user = str(request.get("username", ""))
        password = str(request.get("password", ""))
        if not auth.check_credentials(user, password, self.username, self.password,
                                      self.password_sha256):
            self.attempts.fail(client)
            log("failed login for user %r from %s" % (user, client))
            return self._json({"ok": False, "error": "用户名或密码错误"}, 401)

        self.attempts.reset(client)
        log("login ok: %s from %s (client scheme=%r, secure cookie=%s)"
            % (user, client, self._forwarded_proto() or "http", self._secure_cookie()))
        return self._send_json({"ok": True, "redirect": target},
                               cookie=self._session_cookie(user))

    def _logout(self):
        log("logout from %s" % self._client())
        return self._send_json({"ok": True, "redirect": "/login"},
                               cookie=self._expired_cookie())

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _static(self, path: str):
        relative = posixpath.normpath(path.lstrip("/")) or "index.html"
        if relative.startswith(".."):
            return self._error("not found", 404)
        full = os.path.join(WEB_DIR, relative)
        if not os.path.isfile(full):
            return self._error("not found", 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full, "rb") as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _status(self):
        settings = self.store.settings()
        info = transport.describe(settings["target"])
        self._json({
            "ok": True,
            "version": VERSION,
            "started_at": int(STARTED),
            "settings": settings,
            "printer": info,
            "auth_required": self.auth_enabled,
            "user": self._authorized() or None,
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    # ---------------------------------------------------------------- routes
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/login", "/login/"):
                if not self.auth_enabled:
                    return self._redirect("/")
                return self._static("login.html")
            if self._authorized() is None and path not in self.PUBLIC_PATHS:
                return self._unauthorized(api=path.startswith("/api/"))
            if path == "/api/status":
                return self._status()
            if path == "/api/settings":
                return self._json({"ok": True, "settings": self.store.settings()})
            if path == "/api/history":
                query = urllib.parse.parse_qs(parsed.query)
                limit = int(query.get("limit", ["50"])[0])
                return self._json({"ok": True, "entries": self.store.history(limit)})
            if path == "/api/update":
                query = urllib.parse.parse_qs(parsed.query)
                fetch = query.get("fetch", ["1"])[0].lower() not in ("0", "false", "no")
                settings = self.store.settings()
                report = update.check(settings.get("repo_path", ""), self.data_dir, fetch)
                payload = {"ok": not report["error"]}
                payload.update(report)
                return self._json(payload, 200 if payload["ok"] else 502)
            if path.startswith("/api/history/") and path.endswith("/payload"):
                entry_id = int(path.split("/")[3])
                payload = self.store.payload(entry_id)
                return self._json({"ok": True, "bytes": len(payload),
                                   "base64": base64.b64encode(payload).decode()})
            if path in ("/", "/index.html"):
                return self._static("index.html")
            return self._static(path)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc, 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            request = self._body()
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

        try:
            if path == "/api/login":
                return self._login(request)
            if path == "/api/logout":
                return self._logout()
            if self._authorized() is None:
                return self._unauthorized(api=True)
            if path == "/api/settings":
                settings = self.store.save_settings(request)
                return self._json({"ok": True, "settings": settings})

            if path == "/api/update/apply":
                settings = self.store.settings()
                repo = settings.get("repo_path", "")
                try:
                    result = update.apply(repo, BASE_DIR, self.data_dir)
                except update.UpdateError as exc:
                    return self._error(exc, 400)
                log("update started: %s -> %s (repo %s)" % (
                    (result["from"] or {}).get("short", "?"),
                    (result["to"] or {}).get("short", "?"), repo))
                return self._json({
                    "ok": True,
                    "from": result["from"],
                    "to": result["to"],
                    "runner": result.get("runner"),
                    "message": "更新已开始，拉取完成后服务会自动重启",
                })

            if path == "/api/history/clear":
                removed = self.store.clear_history()
                return self._json({"ok": True, "removed": removed})

            if path.startswith("/api/reprint/"):
                entry_id = int(path.rsplit("/", 1)[1])
                payload = self.store.payload(entry_id)
                if not payload:
                    return self._error("history entry %d has no stored payload" % entry_id,
                                       404)
                settings = self.store.settings()
                with PRINT_LOCK:
                    ok, message = transport.send(payload, settings["target"])
                self.store.add_history("reprint", "重打 #%d" % entry_id, len(payload),
                                       settings["target"], "sent" if ok else "failed", message)
                if not ok:
                    return self._error(message, 502)
                return self._json({"ok": True, "message": message, "bytes": len(payload)})

            if path in ("/api/print", "/api/preview", "/api/test"):
                settings = self.store.settings()
                dry_run = bool(request.get("dry_run")) or path == "/api/preview"
                if path == "/api/test":
                    payload, summary = test_receipt(settings)
                    kind = "test"
                else:
                    payload, summary = build_payload(request, settings)
                    kind = str(request.get("kind", "text"))

                if dry_run:
                    head = payload[:512]
                    return self._json({
                        "ok": True, "dry_run": True, "bytes": len(payload),
                        "hex": head.hex(" "), "truncated": len(payload) > len(head),
                        "summary": summary,
                    })

                with PRINT_LOCK:
                    ok, message = transport.send(payload, settings["target"])
                entry_id = self.store.add_history(
                    kind, summary, len(payload), settings["target"],
                    "sent" if ok else "failed", message, payload)
                if not ok:
                    return self._error(message, 502)
                return self._json({"ok": True, "message": message, "bytes": len(payload),
                                   "summary": summary, "history_id": entry_id})

            return self._error("not found", 404)
        except Exception as exc:  # noqa: BLE001
            log("error on %s: %s" % (path, exc))
            return self._error(exc, 500)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32
    allow_reuse_address = True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Web UI for ESC/POS thermal printers")
    parser.add_argument("--host", default=os.environ.get("THERMAL_WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("THERMAL_WEB_PORT", "8080")))
    parser.add_argument("--data", default=os.environ.get(
        "THERMAL_WEB_DATA", os.path.join(BASE_DIR, "data")))
    options = parser.parse_args(argv)

    Handler.store = Store(os.path.join(options.data, "thermal-web.db"))
    Handler.data_dir = options.data
    Handler.username = os.environ.get("THERMAL_WEB_USER", "admin")
    Handler.password = os.environ.get("THERMAL_WEB_PASSWORD", "")
    Handler.password_sha256 = os.environ.get("THERMAL_WEB_PASSWORD_SHA256", "")
    Handler.secret = auth.load_secret(os.path.join(options.data, "session.key"))
    Handler.session_ttl = int(float(os.environ.get(
        "THERMAL_WEB_SESSION_HOURS", auth.DEFAULT_SESSION_HOURS)) * 3600)
    Handler.cookie_secure = os.environ.get(
        "THERMAL_WEB_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes", "on")

    server = Server((options.host, options.port), Handler)
    log("v%s listening on http://%s:%d  (data: %s)" %
        (VERSION, options.host, options.port, options.data))
    if Handler.auth_enabled:
        source = "sha256" if Handler.password_sha256 else "plaintext"
        log("login required (user=%s, password source=%s, session=%dh, secure cookie=%s)"
            % (Handler.username, source, int(Handler.session_ttl / 3600),
               Handler.cookie_secure))
    else:
        log("authentication disabled, the UI is open to everyone")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
