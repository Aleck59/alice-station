"""WebSocket-протокол XiaoZhi (версия 1: сырые Opus-кадры в бинарных сообщениях).

Источник: docs/websocket.md и main/protocols/ в xiaozhi-esp32.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

import websockets

log = logging.getLogger(__name__)

JsonHandler = Callable[[dict], Awaitable[None]]
AudioHandler = Callable[[bytes], Awaitable[None]]


@dataclass
class AudioParams:
    format: str = "opus"
    sample_rate: int = 16000
    channels: int = 1
    frame_duration: int = 60

    def to_json(self) -> dict:
        return {"format": self.format, "sample_rate": self.sample_rate,
                "channels": self.channels, "frame_duration": self.frame_duration}


def build_hello(params: AudioParams, version: int = 1) -> dict:
    return {
        "type": "hello",
        "version": version,
        "features": {"mcp": True},
        "transport": "websocket",
        "audio_params": params.to_json(),
    }


def auth_header(token: str) -> str:
    # как в websocket_protocol.cc: без пробела — добавляем "Bearer "
    return token if " " in token else f"Bearer {token}"


class Connection:
    def __init__(self, url: str, token: str, mac: str, client_id: str, version: int = 1,
                 hello_timeout: float = 10.0):
        self.url = url
        self.token = token
        self.mac = mac
        self.client_id = client_id
        self.version = version
        self.hello_timeout = hello_timeout
        self.session_id = ""
        self.server_audio = AudioParams(sample_rate=24000)
        self._ws = None
        self._reader: asyncio.Task | None = None
        self.on_json: JsonHandler | None = None
        self.on_audio: AudioHandler | None = None
        self.on_closed: Callable[[], Awaitable[None]] | None = None

    @property
    def is_open(self) -> bool:
        return self._ws is not None

    async def open(self, up: AudioParams) -> None:
        headers = {
            "Protocol-Version": str(self.version),
            "Device-Id": self.mac,
            "Client-Id": self.client_id,
        }
        if self.token:
            headers["Authorization"] = auth_header(self.token)
        self._ws = await websockets.connect(self.url, additional_headers=headers, max_size=2**20,
                                            ping_interval=20)
        await self._ws.send(json.dumps(build_hello(up, self.version)))
        try:
            while True:
                raw = await asyncio.wait_for(self._ws.recv(), self.hello_timeout)
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "hello" and msg.get("transport") == "websocket":
                    break
        except Exception:
            await self.close()
            raise
        self.session_id = msg.get("session_id", "") or ""
        ap = msg.get("audio_params") or {}
        self.server_audio = AudioParams(
            format=ap.get("format", "opus"),
            sample_rate=int(ap.get("sample_rate", 24000)),
            channels=int(ap.get("channels", 1)),
            frame_duration=int(ap.get("frame_duration", 60)),
        )
        log.info("канал открыт, session=%s, аудио сервера %s Гц", self.session_id,
                 self.server_audio.sample_rate)
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        ws = self._ws
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    if self.on_audio:
                        await self.on_audio(raw)
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    log.warning("не JSON: %r", raw[:200])
                    continue
                if self.on_json:
                    await self.on_json(msg)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            log.exception("ошибка чтения канала")
        finally:
            if self._ws is ws:
                self._ws = None
                if self.on_closed:
                    await self.on_closed()

    async def send_json(self, msg: dict) -> None:
        if self._ws is None:
            return
        if self.session_id and "session_id" not in msg:
            msg = {"session_id": self.session_id, **msg}
        await self._ws.send(json.dumps(msg, ensure_ascii=False))

    async def send_audio(self, packet: bytes) -> None:
        if self._ws is not None:
            await self._ws.send(packet)

    async def listen_start(self, mode: str = "auto") -> None:
        await self.send_json({"type": "listen", "state": "start", "mode": mode})

    async def listen_stop(self) -> None:
        await self.send_json({"type": "listen", "state": "stop"})

    async def wake_detected(self, text: str) -> None:
        await self.send_json({"type": "listen", "state": "detect", "text": text})

    async def abort(self, reason: str = "") -> None:
        msg = {"type": "abort"}
        if reason:
            msg["reason"] = reason
        await self.send_json(msg)

    async def send_mcp(self, payload: dict) -> None:
        await self.send_json({"type": "mcp", "payload": payload})

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        if self._reader and self._reader is not asyncio.current_task():
            self._reader.cancel()
        if ws is not None and self.on_closed:
            await self.on_closed()
