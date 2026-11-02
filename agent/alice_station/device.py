"""Железо колонки: громкость, свет, циферблат, кнопки.

Пути и имена — со страницы Станции Миди в вики hackalice.me:
- кольцо 24 RGB: /sys/class/leds/cucumber:{red,green,blue}:ring0..23 или /dev/fb0 (24×1, 24 bpp);
- логотип 6 RGB: cucumber:{red,green,blue}:logo0..5 или /dev/fb1 (6×1);
- циферблат: /sys/class/leds/cucumber:white:clock-*;
- кнопки: awinic_sar (KEY_VOICECOMMAND, громкость), gpio-keys mute.
Формат кадра framebuffer'ов в вики не исследован, поэтому по умолчанию — sysfs.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import struct
import subprocess
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

LEDS = Path("/sys/class/leds")
RING = 24
LOGO = 6


def _write(path: Path, value: str) -> bool:
    try:
        path.write_text(value)
        return True
    except OSError:
        return False


class Volume:
    def __init__(self, control: str = "Master"):
        self.control = control

    def get(self) -> int:
        try:
            out = subprocess.run(["amixer", "get", self.control], capture_output=True, text=True,
                                 timeout=3).stdout
        except (OSError, subprocess.TimeoutExpired):
            return -1
        m = re.search(r"\[(\d+)%\]", out)
        return int(m.group(1)) if m else -1

    def set(self, value: int) -> int:
        value = max(0, min(100, int(value)))
        subprocess.run(["amixer", "-q", "set", self.control, f"{value}%"], timeout=3, check=False)
        return value


class Lights:
    def __init__(self, backend: str = "sysfs", byte_order: str = "rgb", brightness: int = 255,
                 enabled: bool = True, root: Path = LEDS, fb_ring: str = "/dev/fb0",
                 fb_logo: str = "/dev/fb1"):
        self.backend = backend
        self.order = byte_order
        self.max = brightness
        self.enabled = enabled
        self.root = root
        self.fb_ring = fb_ring
        self.fb_logo = fb_logo
        self._anim: asyncio.Task | None = None

    def _scale(self, v: int) -> int:
        return max(0, min(255, int(v))) * self.max // 255

    def _fb_frame(self, colors: list[tuple[int, int, int]]) -> bytes:
        idx = {"r": 0, "g": 1, "b": 2}
        out = bytearray()
        for c in colors:
            out += bytes(self._scale(c[idx[ch]]) for ch in self.order)
        return bytes(out)

    def set_zone(self, zone: str, colors: list[tuple[int, int, int]]) -> None:
        if not self.enabled:
            return
        if self.backend == "fb":
            dev = self.fb_ring if zone == "ring" else self.fb_logo
            try:
                with open(dev, "wb") as f:
                    f.write(self._fb_frame(colors))
            except OSError as e:
                log.debug("fb %s: %s", dev, e)
            return
        for i, (r, g, b) in enumerate(colors):
            for name, v in (("red", r), ("green", g), ("blue", b)):
                _write(self.root / f"cucumber:{name}:{zone}{i}" / "brightness", str(self._scale(v)))

    def fill(self, r: int, g: int, b: int, zone: str = "ring") -> None:
        n = RING if zone == "ring" else LOGO
        self.set_zone(zone, [(r, g, b)] * n)

    def off(self) -> None:
        self.stop_animation()
        self.fill(0, 0, 0, "ring")
        self.fill(0, 0, 0, "logo")

    def stop_animation(self) -> None:
        if self._anim and not self._anim.done():
            self._anim.cancel()
        self._anim = None

    def animate(self, kind: str) -> None:
        """Анимации состояний: listening, thinking, speaking, activating, error."""
        self.stop_animation()
        if not self.enabled:
            return
        self._anim = asyncio.get_event_loop().create_task(self._run(kind))

    async def _run(self, kind: str) -> None:
        try:
            if kind == "listening":
                self.fill(0, 90, 255)
            elif kind == "speaking":
                i = 0
                while True:
                    lvl = 120 + (i % 10) * 12
                    self.fill(lvl // 3, 0, lvl)
                    i += 1
                    await asyncio.sleep(0.12)
            elif kind in ("thinking", "activating"):
                color = (255, 160, 0) if kind == "activating" else (0, 200, 200)
                pos = 0
                while True:
                    frame = [(0, 0, 0)] * RING
                    for k in range(4):
                        frame[(pos + k) % RING] = color
                    self.set_zone("ring", frame)
                    pos = (pos + 1) % RING
                    await asyncio.sleep(0.05)
            elif kind == "error":
                for _ in range(3):
                    self.fill(255, 0, 0)
                    await asyncio.sleep(0.25)
                    self.fill(0, 0, 0)
                    await asyncio.sleep(0.25)
            else:
                self.fill(0, 0, 0)
        except asyncio.CancelledError:
            pass


class Clock:
    """Белые сегменты циферблата. Раскладка сегментов по цифрам пока не снята —
    поэтому только сырые сегменты и «всё вкл/выкл»."""

    def __init__(self, root: Path = LEDS):
        self.root = root

    def segments(self) -> list[str]:
        return sorted(p.name.split("clock-", 1)[1] for p in self.root.glob("cucumber:white:clock-*"))

    def set(self, names: list[str] | None, on: bool) -> int:
        targets = names if names else self.segments()
        n = 0
        for s in targets:
            if _write(self.root / f"cucumber:white:clock-{s}" / "brightness", "255" if on else "0"):
                n += 1
        return n


# --- кнопки (evdev без сторонних модулей) ---------------------------------

EV_KEY = 1
_EVENT = struct.Struct("llHHi")  # struct input_event на 64-битном Linux


def find_input(name_hint: str, key_code: int) -> str | None:
    """Найти /dev/input/eventN по имени устройства из /proc/bus/input/devices."""
    try:
        text = Path("/proc/bus/input/devices").read_text()
    except OSError:
        return None
    for block in text.split("\n\n"):
        if name_hint and name_hint not in block:
            continue
        m = re.search(r"H: Handlers=.*?(event\d+)", block)
        if m:
            return f"/dev/input/{m.group(1)}"
    return None


async def watch_button(path: str, key_code: int, long_press_ms: int,
                       on_press: Callable[[bool], Awaitable[None]]) -> None:
    """Вызывает on_press(long) на отпускании кнопки. Читает в отдельном потоке."""
    loop = asyncio.get_running_loop()
    fd = os.open(path, os.O_RDONLY)
    down_at: float | None = None
    try:
        while True:
            data = await loop.run_in_executor(None, os.read, fd, _EVENT.size * 16)
            for off in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
                sec, usec, typ, code, value = _EVENT.unpack_from(data, off)
                if typ != EV_KEY or code != key_code:
                    continue
                t = sec + usec / 1e6
                if value == 1:
                    down_at = t
                elif value == 0 and down_at is not None:
                    held = (t - down_at) * 1000
                    down_at = None
                    await on_press(held >= long_press_ms)
    finally:
        os.close(fd)


async def watch_any_key(path: str, on_press: Callable[[], Awaitable[None]]) -> None:
    """Вызывает on_press() на нажатии любой клавиши устройства (кнопка mute: KEY_MUTE)."""
    loop = asyncio.get_running_loop()
    fd = os.open(path, os.O_RDONLY)
    try:
        while True:
            data = await loop.run_in_executor(None, os.read, fd, _EVENT.size * 16)
            for off in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
                _, _, typ, code, value = _EVENT.unpack_from(data, off)
                if typ == EV_KEY and value == 1:
                    await on_press()
    finally:
        os.close(fd)


def mac_address(preferred: str = "") -> str:
    if preferred:
        return preferred.lower()
    ifaces = sorted(os.listdir("/sys/class/net")) if os.path.isdir("/sys/class/net") else []
    for iface in ["wlan0", *ifaces]:
        if iface == "lo":
            continue
        try:
            mac = Path(f"/sys/class/net/{iface}/address").read_text().strip()
        except OSError:
            continue
        if mac and mac != "00:00:00:00:00:00":
            return mac.lower()
    return "02:00:00:00:00:01"
