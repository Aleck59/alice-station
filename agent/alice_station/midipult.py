"""Мост к MidiPult и Luna на Станции Миди: MCP-инструменты «как у Алисы».

MidiPult (github.com/RoBiN19666/midipult, MIT) — веб-панель на самой колонке:
CGI на 127.0.0.1:8055 (с localhost пускает без PIN). Luna — шлюз на 127.0.0.1:8099,
через который MidiPult рисует часы, кольцо и бегущую строку. Эндпоинты и параметры
взяты из MidiPult docs/ARCHITECTURE.md, index.html и cgi-bin/*.py.

Luna:
    POST /api/clock          {"time": "HH:MM", "brightness": 10..255} | {"segments": {"A1": 255, …}}
    POST /api/clock/text     {"text": "…", "duration_ms": 3000..15000}   0-9, A-Y без K M V W X Z, _ -
    POST /api/led/ring       {"colors": ["#RRGGBB" × 24]}
    POST /api/led/off/ring   {}
    POST /api/alice/speech/cancel {}
MidiPult CGI (POST JSON):
    display.py {off|on|save|ring_brightness}, media.py {action}, radio.py {index|stop},
    command.py {text}, speak.py {text}, scenarios.py {run}, vpn.py {start|stop|info},
    bluetooth.py {action, on}, zigbee.py, system.py, volume.py {level}
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .mcp import prop

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)

RING = 24
CLOCK_TEXT_RE = re.compile(r"^[0-9A-JLNOPQRSTUY_\-]{1,32}$")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Цвета по-русски — чтобы модель могла передавать и «красный», и #FF0000
COLOR_NAMES = {
    "красный": "#FF0000", "оранжевый": "#FF6A00", "жёлтый": "#FFD000", "желтый": "#FFD000",
    "зелёный": "#00E676", "зеленый": "#00E676", "голубой": "#00B0FF", "синий": "#0033FF",
    "фиолетовый": "#8A2BE2", "розовый": "#FF4FA0", "белый": "#FFFFFF", "тёплый белый": "#FFB46B",
    "теплый белый": "#FFB46B", "бирюзовый": "#00D5C0", "чёрный": "#000000", "черный": "#000000",
}


class BridgeError(RuntimeError):
    pass


def to_hex(color: str) -> str:
    c = str(color).strip().lower()
    if c in COLOR_NAMES:
        return COLOR_NAMES[c]
    if re.match(r"^[0-9a-f]{6}$", c):
        c = "#" + c
    if not HEX_RE.match(c):
        raise ValueError(f"цвет «{color}»: нужен #RRGGBB или название ({', '.join(sorted(COLOR_NAMES))})")
    return c.upper()


def expand_colors(colors: list[str]) -> list[str]:
    """1 цвет — на всё кольцо; несколько — повторить узор по кругу до 24 диодов."""
    hexes = [to_hex(c) for c in colors]
    if not hexes:
        raise ValueError("пустой список цветов")
    return [hexes[i % len(hexes)] for i in range(RING)]


def clock_text(text: str) -> str:
    t = text.strip().upper().replace(" ", "_")
    if not CLOCK_TEXT_RE.match(t):
        raise ValueError("на циферблате только 0-9, латинские A-Y кроме K M V W X Z, _ и -, "
                         "до 32 символов")
    return t


def _port_open(url: str, timeout: float = 0.5) -> bool:
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname, u.port or 80), timeout):
            return True
    except OSError:
        return False


class Bridge:
    def __init__(self, cfg: dict):
        self.pult = cfg.get("url", "http://127.0.0.1:8055").rstrip("/")
        self.luna = cfg.get("luna_url", "http://127.0.0.1:8099").rstrip("/")
        self.timeout = float(cfg.get("timeout", 15))

    def available(self) -> dict:
        return {"midipult": _port_open(self.pult), "luna": _port_open(self.luna)}

    def _post(self, url: str, body: dict, timeout: float | None = None) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error") or e.reason
            except ValueError:
                msg = e.reason
            raise BridgeError(f"{urlparse(url).path}: {msg}") from None
        except (urllib.error.URLError, OSError) as e:
            raise BridgeError(f"{urlparse(url).netloc} недоступен: {e}") from None
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            return {"raw": raw[:200].decode("utf-8", "replace")}
        if isinstance(data, dict) and data.get("ok") is False:
            raise BridgeError(str(data.get("error") or "ошибка"))
        return data if isinstance(data, dict) else {"result": data}

    async def cgi(self, script: str, body: dict | None = None, timeout: float | None = None) -> dict:
        return await asyncio.to_thread(self._post, f"{self.pult}/cgi-bin/{script}", body or {}, timeout)

    async def luna_call(self, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        return await asyncio.to_thread(self._post, f"{self.luna}{path}", body or {}, timeout)

    # ---- высокоуровневые действия ----
    async def ring(self, colors: list[str]) -> None:
        # Luna не обнуляет каналы прошлого цвета — сначала чёрный кадр (как в MidiPult)
        await self.luna_call("/api/led/ring", {"colors": ["#000000"] * RING})
        await self.luna_call("/api/led/ring", {"colors": colors})

    async def show_time(self, brightness: int = 170) -> str:
        now = time.strftime("%H:%M")
        await self.luna_call("/api/clock", {"time": now, "brightness": max(10, min(255, brightness))})
        try:
            await self.cgi("display.py", {"save": 1}, timeout=6)
        except BridgeError:
            pass
        return now


def register(app: "App", bridge: Bridge) -> None:
    m = app.mcp
    app.bridge = bridge

    # ---------- циферблат ----------
    @m.add("self.display.show_text",
           "Бегущая строка на LED-циферблате колонки. Циферблат семисегментный: только цифры, "
           "латинские буквы A-Y кроме K M V W X Z, _ и - (пробел станет _). Русский текст пиши "
           "латиницей и подбирай слова без запрещённых букв, например HELLO, OGO, 2026. До 32 символов.",
           {"text": prop("string"), "seconds": prop("integer", "сколько показывать, 3–15", minimum=3, maximum=15)},
           required=["text"])
    async def _clock_text(text: str, seconds: int = 0):
        t = clock_text(text)
        ms = seconds * 1000 if seconds else min(15000, max(3000, 1000 + len(t) * 200))
        await bridge.luna_call("/api/clock/text", {"text": t, "duration_ms": ms}, timeout=5 + ms / 1000)
        return {"shown": t, "ms": ms}

    @m.add("self.display.show_time", "Показать текущее время на циферблате (вернуть часы).",
           {"brightness": prop("integer", "яркость 10–255", minimum=10, maximum=255)}, required=[])
    async def _show_time(brightness: int = 170):
        return {"time": await bridge.show_time(brightness)}

    @m.add("self.display.set_brightness", "Яркость циферблата 10–255.",
           {"brightness": prop("integer", minimum=10, maximum=255)})
    async def _clock_bright(brightness: int):
        return {"time": await bridge.show_time(brightness), "brightness": brightness}

    @m.add("self.display.off", "Погасить циферблат (экран часов).")
    async def _display_off():
        await bridge.cgi("display.py", {"off": 1})
        return True

    @m.add("self.display.on", "Включить циферблат обратно.")
    async def _display_on():
        await bridge.cgi("display.py", {"on": 1, "time": time.strftime("%H:%M")})
        return True

    @m.add("self.display.draw_digits",
           "Нарисовать на 4 знакоместах циферблата произвольные сегменты. digits — список из "
           "4 строк, в каждой буквы горящих сегментов a-g (a верх, b верх-право, c низ-право, "
           "d низ, e низ-лево, f верх-лево, g середина). Пример: «8» = abcdefg.",
           {"digits": prop("array", items={"type": "string"})}, user_only=True)
    async def _draw(digits: list):
        segs = {}
        for i in range(4):
            lit = str(digits[i]).lower() if i < len(digits) else ""
            for s in "abcdefg":
                segs[f"{s.upper()}{i + 1}"] = 255 if s in lit else 0
        await bridge.luna_call("/api/clock", {"segments": segs})
        return True

    # ---------- кольцо (перекрывает sysfs-версию из tools.py) ----------
    @m.add("self.light.set_rgb", "Залить кольцо подсветки цветом RGB (0–255).",
           {"r": prop("integer", minimum=0, maximum=255), "g": prop("integer", minimum=0, maximum=255),
            "b": prop("integer", minimum=0, maximum=255)})
    async def _rgb(r: int, g: int, b: int):
        app.lights.stop_animation()
        await bridge.ring(["#%02X%02X%02X" % (r, g, b)] * RING)
        return True

    @m.add("self.light.set_colors",
           "Раскрасить кольцо (24 диода). colors — один цвет или узор из нескольких, повторяется "
           "по кругу. Цвет — #RRGGBB или название: " + ", ".join(sorted(set(COLOR_NAMES))) + ".",
           {"colors": prop("array", items={"type": "string"})})
    async def _colors(colors: list):
        app.lights.stop_animation()
        await bridge.ring(expand_colors(colors))
        return True

    @m.add("self.light.off", "Выключить подсветку кольца.")
    async def _off():
        app.lights.stop_animation()
        await bridge.luna_call("/api/led/off/ring", {})
        return True

    @m.add("self.light.set_brightness", "Яркость кольца (ток светодиодов) 0–255.",
           {"brightness": prop("integer", minimum=0, maximum=255)})
    async def _ring_bright(brightness: int):
        await bridge.cgi("display.py", {"ring_brightness": brightness})
        return True

    # ---------- плеер Алисы ----------
    @m.add("alice.player.control",
           "Управлять штатным плеером Алисы (Яндекс Музыка): play, pause, next, prev, stop.",
           {"action": prop("string", enum=["play", "pause", "next", "prev", "stop"])})
    async def _media(action: str):
        return await bridge.cgi("media.py", {"action": action})

    @m.add("alice.player.status", "Что сейчас играет у штатного плеера Алисы.")
    async def _player():
        return await bridge.cgi("player.py")

    @m.add("alice.send_command",
           "Передать текстовую команду штатной Алисе — как если бы её сказали вслух. Используй "
           "для умного дома Яндекса («включи свет в спальне»), Яндекс Музыки («включи мою волну»), "
           "погоды, будильников и всего, что умеет Алиса. text — команда без слова «Алиса».",
           {"text": prop("string")})
    async def _command(text: str):
        return await bridge.cgi("command.py", {"text": text}, timeout=30)

    @m.add("alice.say", "Произнести текст голосом Алисы.", {"text": prop("string")}, user_only=True)
    async def _say(text: str):
        return await bridge.cgi("speak.py", {"text": text}, timeout=30)

    # ---------- радио MidiPult ----------
    @m.add("midipult.radio.list", "Список радиостанций MidiPult.")
    async def _radio_list():
        data = await bridge.cgi("radio.py")
        return [s.get("name") for s in data.get("stations") or []]

    @m.add("midipult.radio.play", "Включить радиостанцию MidiPult по названию.", {"station": prop("string")})
    async def _radio_play(station: str):
        data = await bridge.cgi("radio.py")
        st = data.get("stations") or []
        name = station.lower().strip()
        found = next((s for s in st if s.get("name", "").lower() == name), None) or \
            next((s for s in st if name in s.get("name", "").lower()), None)
        if not found:
            raise ValueError(f"нет станции «{station}»")
        idx = found.get("index", st.index(found))
        await bridge.cgi("radio.py", {"index": idx}, timeout=40)
        return {"playing": found.get("name")}

    @m.add("midipult.radio.stop", "Выключить радио MidiPult.")
    async def _radio_stop():
        await bridge.cgi("radio.py", {"stop": 1})
        return True

    # ---------- сценарии ----------
    @m.add("midipult.scenario.list", "Список сценариев MidiPult (имена и фразы).")
    async def _sc_list():
        data = await bridge.cgi("scenarios.py")
        return [{"name": s.get("name"), "phrases": s.get("phrases")} for s in data.get("scenarios") or []]

    @m.add("midipult.scenario.run", "Запустить сценарий MidiPult по имени.", {"name": prop("string")})
    async def _sc_run(name: str):
        return await bridge.cgi("scenarios.py", {"run": name}, timeout=120)

    # ---------- сеть ----------
    @m.add("midipult.vpn.set", "Включить или выключить VPN (sing-box) на колонке.", {"on": prop("boolean")})
    async def _vpn(on: bool):
        return await bridge.cgi("vpn.py", {"start": 1} if on else {"stop": 1}, timeout=120 if on else 30)

    @m.add("midipult.vpn.status", "Состояние VPN на колонке.")
    async def _vpn_status():
        d = await bridge.cgi("vpn.py", {"info": 1})
        return {k: d.get(k) for k in ("running", "selected", "autostart") if k in d}

    @m.add("midipult.bluetooth.set",
           "Bluetooth колонки: power — включить/выключить; discoverable — режим сопряжения "
           "(чтобы телефон нашёл колонку).",
           {"mode": prop("string", enum=["power", "discoverable"]), "on": prop("boolean")})
    async def _bt(mode: str, on: bool):
        return await bridge.cgi("bluetooth.py", {"action": mode, "on": 1 if on else 0})

    # ---------- датчики ----------
    @m.add("self.system.status", "Температура процессора, загрузка, память колонки.")
    async def _sys():
        return await bridge.cgi("system.py")

    @m.add("midipult.zigbee.devices", "Устройства Zigbee, подключённые к колонке (только список и статус).")
    async def _zb():
        return await bridge.cgi("zigbee.py")


def setup(app: "App") -> Bridge | None:
    cfg = app.cfg["midipult"]
    mode = cfg.get("enabled", "auto")
    if mode is False or mode == "off":
        return None
    bridge = Bridge(cfg)
    if mode == "auto":
        avail = bridge.available()
        if not any(avail.values()):
            log.info("MidiPult/Luna не найдены — MCP-мост выключен")
            return None
        log.info("MidiPult: %s, Luna: %s", avail["midipult"], avail["luna"])
    register(app, bridge)
    return bridge
