"""HTTP-служба VK Музыки: веб-страница, API и адреса для сценариев MidiPult.

Адреса для голосовых сценариев (GET, отвечают сразу, музыка стартует в фоне;
?voice=1 — погасить ответ/музыку Алисы и озвучить ошибку):
    /play/wave  /play/mine  /play/search?q=…  /pause  /resume  /toggle
    /next  /prev  /stop  /like
API для страницы:
    GET  /api/status   GET /api/search?q=…   POST /api/play {"key"|"keys"}
    GET  /api/auth/url POST /api/auth {"url"}  POST /api/logout
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import signal
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from . import __version__
from .alice import Alice, Ducker
from .player import Player
from .vk import Track, VkAuthError, VkClient, VkError, auth_url, parse_token

log = logging.getLogger(__name__)

DEFAULTS = {
    "listen": "0.0.0.0",
    "port": 8061,
    "data_dir": "/data/vk-music",
    "pin": "",                  # если задан — нужен для входа/выхода не с самой колонки
    "voice_confirm": True,      # «Включаю мою волну ВК»
    "client_id": 2685278,
    "user_agent": "",
    "pult_url": "http://127.0.0.1:8055",
    "luna_url": "http://127.0.0.1:8099",
    "player": {},               # {"cmd": [...], "env": {...}}
}


def load_config(path: str | None) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


class Service:
    def __init__(self, cfg: dict, player: Player | None = None, alice: Alice | None = None,
                 client_factory=VkClient):
        self.cfg = cfg
        self.data_dir = cfg["data_dir"]
        self.token_path = os.path.join(self.data_dir, "token.json")
        self.alice = alice or Alice(cfg["pult_url"], cfg["luna_url"])
        pc = cfg.get("player") or {}
        self.player = player or Player(pc.get("cmd"), pc.get("env"))
        self._client_factory = client_factory
        self.vk: VkClient | None = None
        self.account: dict = {}
        self.results: dict[str, Track] = {}
        self._lock = threading.Lock()
        self._load_token()

    # ---- аккаунт ----
    def _client(self, token: str, uid):
        kw = {"user_agent": self.cfg["user_agent"]} if self.cfg.get("user_agent") else {}
        return self._client_factory(token, uid, **kw)

    def _load_token(self) -> None:
        try:
            with open(self.token_path, encoding="utf-8") as f:
                t = json.load(f)
            self.vk = self._client(t["access_token"], t.get("user_id"))
            self.account = t.get("account") or {}
        except (OSError, ValueError, KeyError):
            self.vk = None

    def login(self, text: str) -> dict:
        t = parse_token(text)
        client = self._client(t["access_token"], t["user_id"])
        who = client.whoami()
        client.my_tracks(count=1)  # сразу проверить, что аудио отвечает этому токену
        os.makedirs(self.data_dir, exist_ok=True)
        tmp = self.token_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"access_token": t["access_token"], "user_id": who["id"], "account": who}, f)
        os.replace(tmp, self.token_path)
        self.vk, self.account = client, who
        return who

    def logout(self) -> None:
        self.player.stop()
        self.vk, self.account = None, {}
        try:
            os.remove(self.token_path)
        except OSError:
            pass

    def need_vk(self) -> VkClient:
        if self.vk is None:
            raise VkAuthError("ВК не подключён: войдите на странице службы")
        return self.vk

    # ---- музыка ----
    def play_wave(self) -> None:
        vk = self.need_vk()
        self.player.play(vk.mix(), "wave", refill=vk.mix)

    def play_mine(self, shuffle: bool = False) -> None:
        import random
        tracks = self.need_vk().my_tracks()
        if not tracks:
            raise VkError("в «Моей музыке» нет доступных треков")
        if shuffle:
            random.shuffle(tracks)
        self.player.play(tracks, "mine")

    def search(self, q: str) -> list[Track]:
        q = (q or "").strip()
        if not q:
            raise ValueError("пустой запрос")
        tracks = self.need_vk().search(q)
        with self._lock:
            self.results = {t.key: t for t in tracks}
        return tracks

    def play_search(self, q: str) -> None:
        tracks = self.search(q)
        if not tracks:
            raise VkError(f"ничего не нашлось по запросу «{q}»")
        self.player.play(tracks, "search")

    def play_keys(self, keys: list[str]) -> None:
        with self._lock:
            tracks = [self.results[k] for k in keys if k in self.results]
        if not tracks:
            raise ValueError("треки не найдены — повторите поиск")
        self.player.play(tracks, "search")

    def like(self) -> None:
        cur = self.player.current
        if cur is None:
            raise ValueError("сейчас ничего не играет")
        self.need_vk().add(cur)

    def status(self) -> dict:
        return {"version": __version__, "connected": self.vk is not None,
                "account": self.account, "player": self.player.status()}


PAGES = {"wave": "Включаю мою волну ВК", "mine": "Включаю ваши треки ВК",
         "search": "Ищу во ВКонтакте"}


class Handler(BaseHTTPRequestHandler):
    server_version = "vk-music/" + __version__
    svc: Service  # задаётся в make_server

    def log_message(self, fmt, *args):  # noqa: N802
        log.debug("%s %s", self.address_string(), fmt % args)

    # ---- утилиты ----
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 65536:
            return {}
        try:
            data = json.loads(self.rfile.read(n))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _local(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def _pin_ok(self, q: dict) -> bool:
        pin = str(self.svc.cfg.get("pin") or "")
        if not pin or self._local():
            return True
        given = self.headers.get("X-Pin") or (q.get("pin") or [""])[0]
        return given == pin

    # ---- маршруты ----
    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")

    def _route(self, method: str) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path.rstrip("/") or "/"
        body = self._body() if method == "POST" else {}
        voice = (q.get("voice") or ["0"])[0] == "1"
        svc = self.svc
        try:
            if path == "/" and method == "GET":
                html = resources.files("vkmusic").joinpath("panel.html").read_bytes()
                return self._send(200, html, "text/html; charset=utf-8")
            if path == "/api/status":
                return self._json(200, {"ok": True, **svc.status()})
            if path == "/api/auth/url":
                return self._json(200, {"ok": True, "url": auth_url(int(svc.cfg["client_id"]))})
            if path in ("/api/auth", "/api/logout"):
                if method != "POST":
                    return self._json(405, {"ok": False, "error": "нужен POST"})
                if not self._pin_ok(q):
                    return self._json(403, {"ok": False, "error": "нужен PIN"})
                if path == "/api/logout":
                    svc.logout()
                    return self._json(200, {"ok": True})
                who = svc.login(str(body.get("url") or ""))
                return self._json(200, {"ok": True, "account": who})
            if path == "/api/search":
                tracks = svc.search((q.get("q") or [body.get("q", "")])[0])
                return self._json(200, {"ok": True, "tracks": [t.public() for t in tracks]})
            if path == "/api/play":
                keys = body.get("keys") or ([body["key"]] if body.get("key") else [])
                svc.play_keys([str(k) for k in keys])
                return self._json(200, {"ok": True})
            if path.startswith("/play/"):
                what = path[len("/play/"):]
                if what == "search":
                    arg = (q.get("q") or [body.get("q", "")])[0]
                    action = lambda: svc.play_search(arg)  # noqa: E731
                elif what == "wave":
                    action = svc.play_wave
                elif what == "mine":
                    shuffle = (q.get("shuffle") or ["0"])[0] == "1"
                    action = lambda: svc.play_mine(shuffle)  # noqa: E731
                else:
                    return self._json(404, {"ok": False, "error": "нет такого режима"})
                return self._start(action, voice, PAGES.get(what))
            simple = {"/pause": svc.player.pause, "/resume": svc.player.resume,
                      "/toggle": svc.player.toggle, "/next": svc.player.next,
                      "/prev": svc.player.prev, "/stop": svc.player.stop, "/like": svc.like}
            if path in simple:
                if voice:
                    svc.alice.quiet_stock(rounds=2)
                simple[path]()
                return self._json(200, {"ok": True, **svc.player.status()})
            return self._json(404, {"ok": False, "error": "не найдено"})
        except VkAuthError as e:
            if voice:
                svc.alice.say("ВКонтакте не подключён. Войдите на странице музыки.")
            return self._json(401, {"ok": False, "error": str(e)})
        except (VkError, ValueError) as e:
            if voice:
                svc.alice.say(str(e))
            return self._json(400, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("ошибка запроса %s", path)
            return self._json(500, {"ok": False, "error": f"внутренняя ошибка: {e}"})

    def _start(self, action, voice: bool, phrase: str | None) -> None:
        """Сценарий MidiPult ждёт ответа не дольше 10 с — запуск в фоне."""
        svc = self.svc
        if svc.vk is None:
            raise VkAuthError("ВК не подключён: войдите на странице службы")
        if voice:
            svc.alice.quiet_stock()

        def run():
            try:
                if voice and phrase and svc.cfg.get("voice_confirm"):
                    svc.alice.say(phrase)
                action()
            except VkAuthError:
                if voice:
                    svc.alice.say("Вход во ВКонтакте устарел. Войдите заново на странице музыки.")
            except (VkError, ValueError) as e:
                svc.player.last_error = str(e)
                if voice:
                    svc.alice.say(str(e))
            except Exception:  # noqa: BLE001
                log.exception("запуск музыки")
        threading.Thread(target=run, daemon=True).start()
        self._json(202, {"ok": True, "started": True})


def make_server(svc: Service) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"svc": svc})
    httpd = ThreadingHTTPServer((svc.cfg["listen"], int(svc.cfg["port"])), handler)
    httpd.daemon_threads = True
    return httpd


def run(cfg: dict) -> None:
    svc = Service(cfg)
    Ducker(svc.alice, svc.player).start()
    httpd = make_server(svc)

    def _term(*_):
        svc.player.stop()  # не оставлять играющий GStreamer
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _term)
    log.info("vk-music %s слушает %s:%s, ВК %s", __version__, cfg["listen"], cfg["port"],
             "подключён" if svc.vk else "не подключён")
    httpd.serve_forever()
