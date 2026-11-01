"""Захват и воспроизведение через arecord/aplay (alsa-utils есть в прошивке и в Entware).

На Миди микрофоны (hw:0,1) можно открывать только на 96 кГц — иначе PDM
перенастраивается и Алиса глохнет. Поэтому захват идёт через PCM `alice_mic`
из deploy/asoundrc: ALSA сама держит hw:0,1 на 96 кГц/4 канала и отдаёт нам
16 кГц моно. Файл подключается как ~/.asoundrc: процессы arecord/aplay
запускаются с HOME=audio.alsa_home.
"""
from __future__ import annotations

import asyncio
import logging
import os
import struct
from typing import AsyncIterator

log = logging.getLogger(__name__)


def _env(alsa_home: str) -> dict:
    env = dict(os.environ)
    if alsa_home:
        env["HOME"] = alsa_home
    return env


class Capture:
    def __init__(self, device: str, rate: int = 16000, frame_ms: int = 60, alsa_home: str = ""):
        self.device = device
        self.rate = rate
        self.frame_bytes = rate * frame_ms // 1000 * 2
        self.alsa_home = alsa_home
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        if self._proc and self._proc.returncode is None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            "arecord", "-q", "-D", self.device, "-f", "S16_LE", "-c", "1", "-r", str(self.rate),
            "-t", "raw", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=_env(self.alsa_home))
        log.info("захват запущен: %s @ %d Гц", self.device, self.rate)

    async def frames(self) -> AsyncIterator[bytes]:
        await self.start()
        assert self._proc and self._proc.stdout
        while True:
            try:
                chunk = await self._proc.stdout.readexactly(self.frame_bytes)
            except asyncio.IncompleteReadError:
                log.warning("arecord завершился, перезапуск через 1 с")
                await asyncio.sleep(1)
                self._proc = None
                await self.start()
                continue
            yield chunk

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            await self._proc.wait()
        self._proc = None


class Player:
    """Поток PCM S16 моно в aplay. Процесс живёт, пока идёт ответ."""

    def __init__(self, device: str = "default", alsa_home: str = ""):
        self.device = device
        self.alsa_home = alsa_home
        self.rate = 0
        self._proc: asyncio.subprocess.Process | None = None

    async def _ensure(self, rate: int) -> None:
        if self._proc and self._proc.returncode is None and self.rate == rate:
            return
        await self.stop()
        self.rate = rate
        self._proc = await asyncio.create_subprocess_exec(
            "aplay", "-q", "-D", self.device, "-f", "S16_LE", "-c", "1", "-r", str(rate), "-t", "raw",
            stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=_env(self.alsa_home))

    async def write(self, pcm: bytes, rate: int) -> None:
        await self._ensure(rate)
        assert self._proc and self._proc.stdin
        try:
            self._proc.stdin.write(pcm)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            self._proc = None

    async def finish(self) -> None:
        """Дождаться, пока всё записанное доиграет."""
        p, self._proc = self._proc, None
        if p and p.returncode is None:
            try:
                p.stdin.close()
            except Exception:
                pass
            await p.wait()

    async def stop(self) -> None:
        """Оборвать воспроизведение сразу."""
        p, self._proc = self._proc, None
        if p and p.returncode is None:
            p.kill()
            await p.wait()

    async def play_pcm(self, pcm: bytes, rate: int) -> None:
        await self.write(pcm, rate)
        await self.finish()


def tone(freq: float = 880.0, ms: int = 150, rate: int = 16000, volume: float = 0.3) -> bytes:
    """Короткий сигнал (earcon) без внешних файлов."""
    import math
    n = rate * ms // 1000
    fade = max(1, rate // 200)
    out = bytearray()
    for i in range(n):
        env = min(1.0, i / fade, (n - i) / fade)
        out += struct.pack("<h", int(32767 * volume * env * math.sin(2 * math.pi * freq * i / rate)))
    return bytes(out)
