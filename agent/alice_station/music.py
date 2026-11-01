"""Музыка: плеер и провайдеры.

Плеер запускает внешнюю команду (по умолчанию GStreamer из прошивки:
`maind --gst-launch souphttpsrc ...`), так что звук идёт в ALSA default и
регулируется общей громкостью.

Провайдеры:
- RadioProvider — интернет-радио из конфига (music.stations) — работает сразу;
- VkProvider    — ВКонтакте от имени аккаунта пользователя. Пока каркас: способ
                  получать поток и рекомендации ВК на устройстве ещё выбирается
                  (см. docs/roadmap.md, этап 4). Обход подписки и рекламы не делаем.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Track:
    title: str
    url: str
    artist: str = ""

    def public(self) -> dict:
        return {"title": self.title, "artist": self.artist}


class Player:
    def __init__(self, cmd: list[str], env: dict | None = None):
        self.cmd = cmd
        self.env = env or {}
        self.queue: list[Track] = []
        self.current: Track | None = None
        self.paused = False
        self._proc: asyncio.subprocess.Process | None = None
        self._runner: asyncio.Task | None = None

    @property
    def playing(self) -> bool:
        return self.current is not None and not self.paused

    def status(self) -> dict:
        return {"playing": self.playing, "paused": self.paused,
                "current": self.current.public() if self.current else None,
                "queue": len(self.queue)}

    async def play(self, tracks: list[Track]) -> None:
        await self.stop()
        self.queue = list(tracks)
        self._runner = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while self.queue:
            self.current = self.queue.pop(0)
            self.paused = False
            args = [a.replace("{url}", self.current.url) for a in self.cmd]
            log.info("играет: %s", self.current.title)
            self._proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, **self.env})
            await self._proc.wait()
        self.current = None
        self._proc = None

    async def next(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.kill()  # _run перейдёт к следующему

    def pause(self) -> None:
        if self._proc and self._proc.returncode is None and not self.paused:
            self._proc.send_signal(19)  # SIGSTOP
            self.paused = True

    def resume(self) -> None:
        if self._proc and self._proc.returncode is None and self.paused:
            self._proc.send_signal(18)  # SIGCONT
            self.paused = False

    async def stop(self) -> None:
        self.queue = []
        if self._proc and self._proc.returncode is None:
            if self.paused:
                self._proc.send_signal(18)
            self._proc.kill()
        if self._runner:
            try:
                await self._runner
            except asyncio.CancelledError:
                pass
        self._runner = None
        self.current = None
        self.paused = False


class RadioProvider:
    def __init__(self, stations: dict[str, str]):
        self.stations = stations

    def find(self, name: str) -> Track | None:
        n = name.lower().strip()
        for title, url in self.stations.items():
            if title.lower() == n:
                return Track(title, url)
        for title, url in self.stations.items():
            if n in title.lower():
                return Track(title, url)
        return None


class VkNotReady(RuntimeError):
    pass


class VkProvider:
    """Каркас провайдера ВК. Методы — то, что нужно MCP-инструментам."""

    MESSAGE = ("Музыка ВКонтакте пока не подключена: способ доступа к аудио ВК от имени "
               "аккаунта ещё выбирается (см. docs/roadmap.md, этап 4).")

    def __init__(self, cfg: dict):
        self.enabled = bool(cfg.get("enabled"))

    async def search(self, query: str, limit: int = 10) -> list[Track]:
        raise VkNotReady(self.MESSAGE)

    async def wave(self) -> list[Track]:
        """Рекомендательный поток («моя волна» / VK Микс)."""
        raise VkNotReady(self.MESSAGE)

    async def my_tracks(self, limit: int = 50) -> list[Track]:
        raise VkNotReady(self.MESSAGE)
