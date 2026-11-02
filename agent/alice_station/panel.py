"""Веб-панель в локальной сети (http.server, без зависимостей).

GET  /             — страница
GET  /api/status   — состояние JSON
POST /api/talk     — начать/прервать/закончить диалог (как кнопка)
POST /api/volume   {"volume": N}
POST /api/voice    {"profile": id}
POST /api/radio    {"station": name} | {"stop": true}
POST /api/mode    {"mode": "alice" | "assistant"}
POST /api/reactivate — сбросить токен и пройти активацию заново

Если задан panel.pin — заголовок X-Pin или ?pin= обязателен.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)
PAGE = (Path(__file__).parent / "panel.html").read_text("utf-8")


def make_handler(app: "App", loop: asyncio.AbstractEventLoop, pin: str):
    def run(coro, timeout=30):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    async def call(fn, *a):
        r = fn(*a)
        if asyncio.iscoroutine(r):
            r = await r
        return r

    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.debug("panel: " + fmt, *args)

        def _send(self, code: int, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else (
                body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if not pin:
                return True
            given = self.headers.get("X-Pin") or (parse_qs(urlparse(self.path).query).get("pin") or [""])[0]
            return hmac.compare_digest(given, pin)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if not self._authorized():
                return self._send(401, {"error": "pin"})
            if path == "/api/status":
                return self._send(200, run(call(app.status)))
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._authorized():
                return self._send(401, {"error": "pin"})
            path = urlparse(self.path).path
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 65536)
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("ожидался объект")
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            try:
                if path == "/api/talk":
                    run(app.trigger())
                elif path == "/api/volume":
                    run(call(app.volume.set, int(body["volume"])))
                elif path == "/api/voice":
                    run(call(app.voice.set, str(body["profile"])))
                elif path == "/api/radio":
                    if body.get("stop"):
                        run(app.music.stop())
                    else:
                        tr = app.radio.find(str(body.get("station", "")))
                        if not tr:
                            return self._send(404, {"error": "нет такой станции"})
                        run(app.music.play([tr]))
                elif path == "/api/mode":
                    run(app.set_mode(str(body["mode"])) if body["mode"] in ("alice", "assistant")
                        else asyncio.sleep(0))
                elif path == "/api/settings":
                    run(call(app.update_settings, body))
                elif path == "/api/reactivate":
                    app.cfg.state.pop("websocket", None)
                    app.cfg.save_state()
                    asyncio.run_coroutine_threadsafe(app.ensure_activated(), loop)
                else:
                    return self._send(404, {"error": "not found"})
            except (KeyError, ValueError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                log.exception("panel")
                return self._send(500, {"error": str(e)})
            self._send(200, run(call(app.status)))

    return H


def start_panel(app: "App", loop: asyncio.AbstractEventLoop) -> ThreadingHTTPServer:
    p = app.cfg["panel"]
    srv = ThreadingHTTPServer((p["host"], int(p["port"])), make_handler(app, loop, p.get("pin", "")))
    threading.Thread(target=srv.serve_forever, daemon=True, name="panel").start()
    log.info("панель: http://%s:%s/", p["host"], p["port"])
    return srv
