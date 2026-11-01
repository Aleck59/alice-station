"""Главный цикл агента: активация, диалог с XiaoZhi, звук, свет, музыка."""
from __future__ import annotations

import asyncio
import logging
import time

from . import audio, device, opus
from .config import Config
from .mcp import McpServer
from .music import Player as MusicPlayer, RadioProvider, VkProvider
from .voice import VoiceManager, synthesize
from .xiaozhi.ota import OtaClient
from .xiaozhi.protocol import AudioParams, Connection

log = logging.getLogger(__name__)

IDLE, ACTIVATING, CONNECTING, LISTENING, SPEAKING, ERROR = (
    "idle", "activating", "connecting", "listening", "speaking", "error")


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = IDLE
        self.activation_code = ""
        self.activation_message = ""
        self.last_stt = ""
        self.last_reply = ""
        self.emotion = ""
        self.mac = device.mac_address(cfg["device"]["mac"])
        a = cfg["audio"]
        self.capture = audio.Capture(a["capture_device"], a["capture_rate"], a["frame_ms"], a["alsa_home"])
        self.player = audio.Player(a["playback_device"], a["alsa_home"])
        self.volume = device.Volume()
        l = cfg["led"]
        self.lights = device.Lights(l["backend"], l["fb_byte_order"], l["brightness"], l["enabled"])
        self.clock = device.Clock()
        self.voice = VoiceManager(cfg["voice"], cfg.state, cfg.save_state)
        m = cfg["music"]
        self.music = MusicPlayer(m["player_cmd"], m.get("player_env"))
        self.radio = RadioProvider(m.get("stations") or {})
        self.vk = VkProvider(m.get("vk") or {})
        self.mcp = McpServer(cfg["device"]["name"])
        self.conn: Connection | None = None
        self._encoder: opus.Encoder | None = None
        self._decoder: opus.Decoder | None = None
        self._decoder_rate = 0
        self._tts_queue: asyncio.Queue[str] = asyncio.Queue()
        self._tts_worker: asyncio.Task | None = None
        self._music_was_playing = False
        self._listen_since = 0.0
        self._lock = asyncio.Lock()
        self.timers: dict[int, dict] = {}
        from . import tools
        tools.register(self)

    # ---------- состояние ----------
    def set_state(self, state: str) -> None:
        if state == self.state:
            return
        log.info("состояние: %s → %s", self.state, state)
        self.state = state
        anim = {LISTENING: "listening", SPEAKING: "speaking", CONNECTING: "thinking",
                ACTIVATING: "activating", ERROR: "error"}.get(state)
        if anim:
            self.lights.animate(anim)
        else:
            self.lights.off()
        if state == LISTENING:
            self._listen_since = time.monotonic()

    def status(self) -> dict:
        return {
            "state": self.state,
            "activation_code": self.activation_code,
            "activation_message": self.activation_message,
            "last_stt": self.last_stt,
            "last_reply": self.last_reply,
            "emotion": self.emotion,
            "volume": self.volume.get(),
            "voice": self.voice.current.public(),
            "voices": [p.public() for p in self.voice.profiles.values()],
            "music": self.music.status(),
            "stations": list(self.radio.stations),
            "connected": bool(self.cfg.state.get("websocket", {}).get("url")),
        }

    # ---------- активация ----------
    def _ota(self) -> OtaClient:
        x = self.cfg["xiaozhi"]
        return OtaClient(x["ota_url"], self.mac, self.cfg.state["client_id"],
                         self.cfg["device"]["name"], x["language"])

    async def ensure_activated(self) -> None:
        ota = self._ota()
        delay = 10
        while True:
            try:
                res = await asyncio.to_thread(ota.check_version)
            except Exception as e:  # noqa: BLE001
                log.warning("OTA недоступен: %s; повтор через %d с", e, delay)
                self.set_state(ERROR)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300)
                continue
            delay = 10
            if res.websocket:
                self.cfg.state["websocket"] = res.websocket
                self.cfg.save_state()
            if not res.needs_activation:
                self.activation_code = ""
                self.set_state(IDLE)
                log.info("устройство активировано")
                return
            self.activation_code = res.activation_code
            self.activation_message = res.activation_message
            self.set_state(ACTIVATING)
            log.warning("КОД АКТИВАЦИИ: %s — введите его в консоли xiaozhi.me (%s)",
                        res.activation_code, res.activation_message)
            await self._announce_code(res.activation_code)
            for _ in range(10):
                ok = await asyncio.to_thread(ota.activate)
                if ok:
                    break
                await asyncio.sleep(3 if ok is None else 10)

    async def _announce_code(self, code: str) -> None:
        p = self.voice.current
        if p.is_local:
            text = "Код активации: " + ", ".join(code)
            try:
                pcm, rate = await synthesize(p, text)
                await self.player.play_pcm(pcm, rate)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("не удалось озвучить код: %s", e)
        # без локального голоса — сигнал; код виден в веб-панели и в логе
        await self.player.play_pcm(audio.tone(660, 120) + audio.tone(990, 120), 16000)

    # ---------- диалог ----------
    async def trigger(self, long_press: bool = False) -> None:
        """Кнопка/панель: idle → начать, speaking → перебить, listening → закончить."""
        if self.state == IDLE:
            await self.start_session()
        elif self.state == SPEAKING:
            await self.interrupt()
        elif self.state == LISTENING:
            await self.end_session()
        elif self.state == ACTIVATING:
            log.info("код активации: %s", self.activation_code)

    async def start_session(self) -> None:
        async with self._lock:
            if self.conn and self.conn.is_open:
                return
            ws = self.cfg.state.get("websocket") or {}
            if not ws.get("url"):
                log.warning("нет адреса WebSocket — устройство не активировано")
                await self.ensure_activated()
                ws = self.cfg.state.get("websocket") or {}
                if not ws.get("url"):
                    return
            self.set_state(CONNECTING)
            self._music_was_playing = self.music.playing
            self.music.pause()
            x = self.cfg["xiaozhi"]
            conn = Connection(ws["url"], ws.get("token", ""), self.mac, self.cfg.state["client_id"],
                              int(ws.get("version", x["protocol_version"])))
            conn.on_json = self._on_json
            conn.on_audio = self._on_audio
            conn.on_closed = self._on_closed
            up = AudioParams(sample_rate=self.cfg["audio"]["capture_rate"],
                             frame_duration=self.cfg["audio"]["frame_ms"])
            try:
                await conn.open(up)
            except Exception as e:  # noqa: BLE001
                log.error("не удалось открыть канал: %s", e)
                self.set_state(ERROR)
                await asyncio.sleep(1.5)
                self.set_state(IDLE)
                self._resume_music()
                return
            self.conn = conn
            self._encoder = opus.Encoder(up.sample_rate, 1)
            await self.player.play_pcm(audio.tone(880, 120), 16000)
            await conn.listen_start(x["listening_mode"])
            self.set_state(LISTENING)

    async def interrupt(self) -> None:
        if self.conn:
            await self.conn.abort()
        await self._drop_tts()
        await self.player.stop()
        if self.conn and self.conn.is_open:
            await self.conn.listen_start(self.cfg["xiaozhi"]["listening_mode"])
            self.set_state(LISTENING)

    async def end_session(self) -> None:
        if self.conn:
            await self.conn.close()

    async def _on_closed(self) -> None:
        self.conn = None
        await self._drop_tts()
        await self.player.finish()
        self.set_state(IDLE)
        self._resume_music()

    def _resume_music(self) -> None:
        if self._music_was_playing:
            self.music.resume()
        self._music_was_playing = False

    async def _on_json(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "tts":
            st = msg.get("state")
            if st == "start":
                self.set_state(SPEAKING)
            elif st == "sentence_start":
                text = msg.get("text", "")
                self.last_reply = text
                log.info("<< %s", text)
                if self.voice.current.is_local and text:
                    self._ensure_tts_worker()
                    await self._tts_queue.put(text)
            elif st == "stop":
                asyncio.create_task(self._after_tts())
        elif t == "stt":
            self.last_stt = msg.get("text", "")
            self._listen_since = time.monotonic()
            log.info(">> %s", self.last_stt)
        elif t == "llm":
            self.emotion = msg.get("emotion", "")
        elif t == "mcp":
            resp = await self.mcp.handle(msg.get("payload") or {})
            if resp is not None and self.conn:
                await self.conn.send_mcp(resp)
        elif t == "system":
            log.info("system: %s (игнорируем)", msg.get("command"))
        elif t == "alert":
            log.warning("alert: %s %s", msg.get("status"), msg.get("message"))

    async def _after_tts(self) -> None:
        if self.voice.current.is_local:
            await self._tts_queue.join()
        await self.player.finish()
        if self.state == SPEAKING and self.conn and self.conn.is_open:
            await self.conn.listen_start(self.cfg["xiaozhi"]["listening_mode"])
            self.set_state(LISTENING)

    async def _on_audio(self, packet: bytes) -> None:
        if self.state != SPEAKING or self.voice.current.is_local or not self.conn:
            return  # как в прошивке: звук во время слушания отбрасывается
        rate = self.conn.server_audio.sample_rate
        if self._decoder is None or self._decoder_rate != rate:
            self._decoder = opus.Decoder(rate, 1)
            self._decoder_rate = rate
        try:
            pcm = self._decoder.decode(packet)
        except RuntimeError as e:
            log.debug("кадр не декодирован: %s", e)
            return
        await self.player.write(pcm, rate)

    # ---------- локальный голос ----------
    def _ensure_tts_worker(self) -> None:
        if self._tts_worker is None or self._tts_worker.done():
            self._tts_worker = asyncio.create_task(self._tts_loop())

    async def _tts_loop(self) -> None:
        while True:
            text = await self._tts_queue.get()
            try:
                pcm, rate = await synthesize(self.voice.current, text)
                await self.player.write(pcm, rate)
            except Exception as e:  # noqa: BLE001
                log.warning("локальный TTS: %s", e)
            finally:
                self._tts_queue.task_done()

    async def _drop_tts(self) -> None:
        while not self._tts_queue.empty():
            self._tts_queue.get_nowait()
            self._tts_queue.task_done()

    # ---------- микрофон ----------
    async def mic_loop(self) -> None:
        idle_timeout = self.cfg["xiaozhi"]["idle_timeout"]
        async for frame in self.capture.frames():
            if self.state != LISTENING or not self.conn or not self._encoder:
                continue
            if idle_timeout and time.monotonic() - self._listen_since > idle_timeout:
                log.info("тишина %d с — закрываю канал", idle_timeout)
                asyncio.create_task(self.end_session())
                continue
            try:
                await self.conn.send_audio(self._encoder.encode(frame))
            except Exception as e:  # noqa: BLE001
                log.debug("отправка звука: %s", e)

    async def button_loop(self) -> None:
        t = self.cfg["trigger"]
        if not t["button_enabled"]:
            return
        path = device.find_input(t["input_name_hint"], t["key_code"])
        if not path:
            log.info("кнопка не найдена (%s) — запуск только из панели", t["input_name_hint"])
            return
        log.info("кнопка: %s, код %s", path, t["key_code"])
        while True:
            try:
                await device.watch_button(path, t["key_code"], t["long_press_ms"], self.trigger)
            except OSError as e:
                log.warning("кнопка: %s", e)
                await asyncio.sleep(5)

    async def run(self) -> None:
        tasks = [asyncio.create_task(self.mic_loop()), asyncio.create_task(self.button_loop())]
        if self.cfg["panel"]["enabled"]:
            from .panel import start_panel
            start_panel(self, asyncio.get_running_loop())
        await self.ensure_activated()
        await asyncio.gather(*tasks)
