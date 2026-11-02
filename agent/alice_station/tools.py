"""MCP-инструменты колонки. Имена совместимы с xiaozhi-esp32 там, где есть аналог."""
from __future__ import annotations

import asyncio
import itertools
import time
from typing import TYPE_CHECKING

from . import audio
from .mcp import prop
from .music import VkNotReady

if TYPE_CHECKING:
    from .app import App

_timer_ids = itertools.count(1)


def register(app: "App") -> None:
    m = app.mcp

    # ---- устройство ----
    @m.add("self.get_device_status",
           "Текущее состояние колонки: громкость, голос, что играет. Вызывай перед ответом на "
           "вопросы о колонке или перед относительным изменением громкости.")
    def _status():
        s = app.status()
        return {k: s[k] for k in ("volume", "voice", "music", "state")}

    @m.add("self.audio_speaker.set_volume",
           "Установить громкость колонки (0–100). Для «громче/тише» сначала узнай текущую "
           "через self.get_device_status.",
           {"volume": prop("integer", minimum=0, maximum=100)})
    def _volume(volume: int):
        return app.volume.set(volume)

    # ---- свет ----
    @m.add("self.light.set_rgb", "Залить кольцо подсветки цветом RGB (0–255).",
           {"r": prop("integer", minimum=0, maximum=255), "g": prop("integer", minimum=0, maximum=255),
            "b": prop("integer", minimum=0, maximum=255)})
    def _rgb(r: int, g: int, b: int):
        app.lights.stop_animation()
        app.lights.fill(r, g, b, "ring")
        app.lights.fill(r, g, b, "logo")
        return True

    @m.add("self.light.off", "Выключить подсветку кольца и логотипа.")
    def _light_off():
        app.lights.off()
        return True

    @m.add("self.clock.set_segments",
           "Включить или выключить белые сегменты циферблата. names — список имён сегментов "
           "(пусто — все).", {"names": prop("array", items={"type": "string"}),
                              "on": prop("boolean")}, required=["on"], user_only=True)
    def _clock(on: bool, names: list | None = None):
        return app.clock.set(names or None, on)

    # ---- режим ----
    @m.add("self.assistant.return_to_alice",
           "Выключить ассистента и вернуть штатную Алису. Вызывай, когда пользователь просит "
           "вернуть Алису, выключить ассистента или «верни к Алисе».")
    async def _to_alice():
        asyncio.get_running_loop().create_task(app.return_to_alice())
        return True

    # ---- таймеры ----
    @m.add("self.timer.set", "Поставить таймер на заданное число секунд. label — подпись.",
           {"seconds": prop("integer", minimum=1), "label": prop("string")}, required=["seconds"])
    def _timer_set(seconds: int, label: str = ""):
        tid = next(_timer_ids)
        due = time.time() + seconds

        async def fire():
            await asyncio.sleep(seconds)
            app.timers.pop(tid, None)
            beep = audio.tone(1046, 180) + audio.tone(0, 120) + audio.tone(1046, 180)
            for _ in range(3):
                app.lights.fill(255, 120, 0)
                await app.player.play_pcm(beep, 16000)
                app.lights.off()
                await asyncio.sleep(0.4)

        task = asyncio.get_running_loop().create_task(fire())
        app.timers[tid] = {"id": tid, "label": label, "due": due, "task": task}
        return {"id": tid, "seconds": seconds}

    @m.add("self.timer.list", "Список активных таймеров с оставшимся временем.")
    def _timer_list():
        now = time.time()
        return [{"id": t["id"], "label": t["label"], "left_seconds": int(t["due"] - now)}
                for t in app.timers.values()]

    @m.add("self.timer.cancel", "Отменить таймер по id.", {"id": prop("integer")})
    def _timer_cancel(id: int):
        t = app.timers.pop(id, None)
        if not t:
            return False
        t["task"].cancel()
        return True

    # ---- голос ----
    @m.add("self.voice.list_profiles", "Список доступных голосов колонки и текущий голос.")
    def _voices():
        return {"current": app.voice.current_id,
                "profiles": [p.public() for p in app.voice.profiles.values()]}

    @m.add("self.voice.set_profile",
           "Сменить голос колонки. profile — id или название из self.voice.list_profiles.",
           {"profile": prop("string")})
    def _voice_set(profile: str):
        return app.voice.set(profile).public()

    # ---- музыка ----
    @m.add("music.radio.list", "Список радиостанций, которые можно включить.")
    def _radio_list():
        return list(app.radio.stations)

    @m.add("music.radio.play", "Включить радиостанцию по названию.", {"station": prop("string")})
    async def _radio_play(station: str):
        tr = app.radio.find(station)
        if not tr:
            raise ValueError(f"нет станции «{station}»")
        await app.music.play([tr])
        app._music_was_playing = True  # после диалога продолжить
        app.music.pause()  # пока идёт диалог — на паузе
        return tr.public()

    @m.add("music.pause", "Пауза музыки.")
    def _pause():
        app._music_was_playing = False
        app.music.pause()
        return True

    @m.add("music.resume", "Продолжить музыку после паузы.")
    def _resume():
        app._music_was_playing = True
        return True

    @m.add("music.stop", "Остановить музыку и очистить очередь.")
    async def _stop():
        app._music_was_playing = False
        await app.music.stop()
        return True

    @m.add("music.next", "Следующий трек.")
    async def _next():
        await app.music.next()
        return True

    @m.add("music.status", "Что сейчас играет.")
    def _mstatus():
        return app.music.status()

    async def _vk_play(getter):
        try:
            tracks = await getter()
        except VkNotReady as e:
            return str(e)
        await app.music.play(tracks)
        app._music_was_playing = True
        app.music.pause()
        return {"queued": len(tracks), "first": tracks[0].public() if tracks else None}

    @m.add("music.vk.search_play", "Найти во ВКонтакте и включить музыку по запросу.",
           {"query": prop("string")})
    async def _vk_search(query: str):
        return await _vk_play(lambda: app.vk.search(query))

    @m.add("music.vk.wave", "Включить «мою волну» — рекомендательный поток ВКонтакте.")
    async def _vk_wave():
        return await _vk_play(app.vk.wave)

    @m.add("music.vk.my_tracks", "Включить мои треки ВКонтакте.")
    async def _vk_mine():
        return await _vk_play(app.vk.my_tracks)
