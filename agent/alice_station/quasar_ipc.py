"""Локальный IPC штатной Алисы (maind, 127.0.0.1:9875).

Формат (hackalice.me/devices/station-2/#voice, derangedabbot/yandex-station-2-airplay,
MidiPult cgi-bin/speak.py — последний проверен на Станции Миди):

    кадр      = <длина uint32 LE> + конверт (protobuf)
    конверт   : поле 253 = тип; 3 — состояние Алисы, 210 — вызов RPC
                поле 252 = вложенное сообщение
    состояние : поле 1 = статус (нет поля — 0 «свободна», 1 «слушает», 3 «фраза распознана»),
                поле 2 = текст, поле 7 = id запроса
    вызов     : поле 7 = "AliceCapability", поле <rpc> = аргументы

Отсюда агент узнаёт фразу «Алиса, включи ассистента», отменяет ответ Алисы и
озвучивает короткие сообщения её голосом (START_VOCALIZE).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

STATUS_IDLE, STATUS_LISTENING, STATUS_RECOGNIZED = 0, 1, 3


# ---------- protobuf-примитивы ----------

def varint(value: int) -> bytes:
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def pb_int(field: int, value: int) -> bytes:
    return varint(field * 8) + varint(value)


def pb_bytes(field: int, value: bytes) -> bytes:
    return varint(field * 8 + 2) + varint(len(value)) + value


def pb_parse(data: bytes) -> dict[int, object]:
    """Плоский разбор: {номер поля: значение} (последнее значение побеждает)."""
    pos = 0

    def num() -> int:
        nonlocal pos
        v = shift = 0
        while True:
            if pos >= len(data) or shift > 63:
                raise ValueError("битый varint")
            b = data[pos]
            pos += 1
            v |= (b & 127) << shift
            if b < 128:
                return v
            shift += 7

    out: dict[int, object] = {}
    while pos < len(data):
        key = num()
        field, wire = key >> 3, key & 7
        if field == 0:
            raise ValueError("поле 0")
        if wire == 0:
            out[field] = num()
        elif wire == 2:
            n = num()
            if pos + n > len(data):
                raise ValueError("обрезанное поле")
            out[field] = data[pos:pos + n]
            pos += n
        elif wire in (1, 5):
            pos += 8 if wire == 1 else 4
        else:
            raise ValueError(f"тип {wire}")
    return out


def frame(envelope: bytes) -> bytes:
    return len(envelope).to_bytes(4, "little") + envelope


def rpc_frame(rpc: int, payload: bytes) -> bytes:
    remote = pb_bytes(7, b"AliceCapability") + pb_bytes(rpc, payload)
    return frame(pb_bytes(252, remote) + pb_int(253, 210))


def vocalize_frame(text: str) -> bytes:
    directive = pb_bytes(1, text.encode("utf-8")) + pb_int(2, 1)
    return rpc_frame(8, pb_int(1, 18) + pb_int(3, 1) + pb_bytes(13, directive))


SUBSCRIBE_FRAME = rpc_frame(20, pb_bytes(1, b"AliceCapability"))
# «Отменить разговор Алисы»: rpc("AliceCapability", 8, [8, 3]) у derangedabbot.
CANCEL_FRAME = rpc_frame(8, pb_int(1, 8) + pb_int(3, 3))


@dataclass
class AliceEvent:
    status: int
    text: str = ""
    request_id: str = ""


def parse_event(envelope: bytes) -> AliceEvent | None:
    top = pb_parse(envelope)
    if top.get(253) != 3 or not isinstance(top.get(252), bytes):
        return None
    st = pb_parse(top[252])  # type: ignore[arg-type]
    text = st.get(2, b"")
    rid = st.get(7, b"")
    return AliceEvent(
        status=int(st.get(1, 0)),  # type: ignore[arg-type]
        text=text.decode("utf-8", "replace") if isinstance(text, bytes) else "",
        request_id=rid.decode("utf-8", "replace") if isinstance(rid, bytes) else str(rid or ""),
    )


EventHandler = Callable[[AliceEvent], Awaitable[None]]


class AliceIpc:
    def __init__(self, host: str = "127.0.0.1", port: int = 9875):
        self.host, self.port = host, port
        self.status = STATUS_IDLE
        self.on_event: EventHandler | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._status_changed = asyncio.Event()
        self._seen: dict[str, float] = {}

    @property
    def connected(self) -> bool:
        return self._writer is not None

    async def run(self) -> None:
        """Держит соединение, переподключается. Никогда не возвращается."""
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
            except OSError as e:
                log.debug("IPC Алисы недоступен: %s", e)
                await asyncio.sleep(5)
                continue
            self._writer = writer
            log.info("IPC Алисы подключён: %s:%d", self.host, self.port)
            try:
                writer.write(SUBSCRIBE_FRAME)
                await writer.drain()
                while True:
                    n = int.from_bytes(await reader.readexactly(4), "little")
                    if n > 1 << 20:
                        raise ValueError(f"кадр {n} байт")
                    body = await reader.readexactly(n)
                    try:
                        ev = parse_event(body)
                    except ValueError:
                        continue
                    if ev:
                        await self._dispatch(ev)
            except (asyncio.IncompleteReadError, ConnectionError, ValueError) as e:
                log.info("IPC Алисы: соединение потеряно (%s)", e)
            finally:
                self._writer = None
                writer.close()
            await asyncio.sleep(2)

    async def _dispatch(self, ev: AliceEvent) -> None:
        if ev.status != self.status:
            self.status = ev.status
            self._status_changed.set()
            self._status_changed = asyncio.Event()
        if ev.status == STATUS_RECOGNIZED and ev.text:
            # одна фраза приходит несколькими кадрами — пропускаем повторы
            key = ev.request_id or ev.text
            now = time.monotonic()
            self._seen = {k: t for k, t in self._seen.items() if now - t < 30}
            if key in self._seen:
                return
            self._seen[key] = now
        if self.on_event:
            await self.on_event(ev)

    async def send(self, data: bytes) -> bool:
        if not self._writer:
            return False
        try:
            self._writer.write(data)
            await self._writer.drain()
            return True
        except ConnectionError:
            return False

    async def cancel(self) -> bool:
        return await self.send(CANCEL_FRAME)

    async def vocalize(self, text: str) -> bool:
        return await self.send(vocalize_frame(text))

    async def wait_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while self.status != STATUS_IDLE:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            ev = self._status_changed
            try:
                await asyncio.wait_for(ev.wait(), left)
            except asyncio.TimeoutError:
                return False
        return True
