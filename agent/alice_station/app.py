"""Главный цикл агента: активация, диалог с XiaoZhi, звук, свет, музыка."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
import urllib.request

from . import audio, device, kws, opus
from .kws import normalize
from .quasar_ipc import AliceEvent, AliceIpc, STATUS_LISTENING, STATUS_RECOGNIZED
from .brain import Claude, OpenAICompat, sentences
from .config import Config
from .mcp import McpServer
from .music import Player as MusicPlayer, RadioProvider, VkProvider
from .voice import Profile, VoiceManager, alice_markup, speech_seconds, synthesize
from .xiaozhi.ota import OtaClient
from .xiaozhi.protocol import AudioParams, Connection

log = logging.getLogger(__name__)

IDLE, ACTIVATING, CONNECTING, LISTENING, SPEAKING, ERROR = (
    "idle", "activating", "connecting", "listening", "speaking", "error")
MODE_ALICE, MODE_ASSISTANT = "alice", "assistant"


def phrase_match(text: str, phrases: list[str]) -> bool:
    t = " ".join(normalize(text).split())
    return any(" ".join(normalize(p).split()) in t for p in phrases)


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
        asst = cfg["assistant"]
        self.assistant_name = asst["name"]
        self.mode = asst["default_mode"] if asst["default_mode"] in (MODE_ALICE, MODE_ASSISTANT) else MODE_ALICE
        self.ipc = AliceIpc(asst["ipc_host"], int(asst["ipc_port"]))
        self.ipc.on_event = self._on_alice_event
        self.spotter: kws.Spotter | None = None
        self._mic_wanted = asyncio.Event()
        self._switching = False
        self._alice_status = 0
        self._own_speech_until = 0.0  # Алиса озвучивает наши фразы — её не глушим
        self._silencing = False
        self._alice_stt = False       # Алиса распознаёт для нас — не глушить
        self._pipeline: asyncio.Task | None = None
        self._stt_q: asyncio.Queue[bytes] | None = None
        self.brains = {
            "claude": Claude(cfg["brain"]["claude"], self.mcp,
                             lambda: self._key("anthropic") or cfg["brain"]["claude"]["api_key"],
                             self.assistant_name),
            "openai": OpenAICompat(self._openai_cfg(), self.mcp,
                                   lambda: self._key("llm") or cfg["brain"]["openai"]["api_key"],
                                   self.assistant_name),
        }
        from . import midipult, tools
        tools.register(self)
        self.bridge = midipult.setup(self)  # перекрывает свет/дисплей версиями через Luna

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
            "mode": self.mode,
            "assistant_name": self.assistant_name,
            "alice_ipc": self.ipc.connected,
            "wakeword": self.cfg["wakeword"]["words"] if not isinstance(self.spotter, kws.NullSpotter) else [],
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
            "brain": self.brain_engine,
            "stt": self.stt_engine,
            "llm": {"url": self._openai_cfg()["url"], "model": self._openai_cfg()["model"]},
            "keys": {"anthropic": bool(self._key("anthropic") or self.cfg["brain"]["claude"]["api_key"]),
                     "llm": bool(self._key("llm") or self.cfg["brain"]["openai"]["api_key"]),
                     "elevenlabs": bool(self._key("elevenlabs")),
                     "elevenlabs_voice": self._key("elevenlabs_voice")},
        }

    # ---------- настройки конвейера ----------
    @property
    def brain_engine(self) -> str:
        return self.cfg.state.get("brain_engine") or self.cfg["brain"]["engine"]

    @property
    def stt_engine(self) -> str:
        return self.cfg.state.get("stt_engine") or self.cfg["stt"]["engine"]

    def _key(self, name: str) -> str:
        return str((self.cfg.state.get("keys") or {}).get(name) or "")

    def update_settings(self, body: dict) -> None:
        """Панель: мозг, распознавание, ключи. Пустые ключи не затирают сохранённые."""
        st = self.cfg.state
        if body.get("brain") in ("xiaozhi", "claude", "openai"):
            st["brain_engine"] = body["brain"]
        if body.get("stt") in ("alice", "vosk"):
            st["stt_engine"] = body["stt"]
        keys = st.setdefault("keys", {})
        for k in ("anthropic", "llm", "elevenlabs", "elevenlabs_voice"):
            v = str(body.get(k) or "").strip()
            if v:
                keys[k] = v
        for k in ("llm_url", "llm_model"):
            v = str(body.get(k) or "").strip()
            if v:
                st[k] = v
        self.brains["openai"].cfg = self._openai_cfg()
        self.cfg.save_state()

    def _openai_cfg(self) -> dict:
        c = dict(self.cfg["brain"]["openai"])
        c["url"] = self.cfg.state.get("llm_url") or c["url"]
        c["model"] = self.cfg.state.get("llm_model") or c["model"]
        return c

    @property
    def brain(self) -> Claude:
        return self.brains.get(self.brain_engine, self.brains["claude"])

    def _tts_profile(self) -> Profile:
        """Профиль для локального синтеза; ключи ElevenLabs подставляются из панели."""
        p = self.voice.current
        if not p.is_local and self.brain_engine != "xiaozhi":
            p = self.voice.profiles.get("eleven", p)
        if p.engine == "elevenlabs":
            p = dataclasses.replace(p, api_key=p.api_key or self._key("elevenlabs"),
                                    voice=p.voice or self._key("elevenlabs_voice"))
        return p

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
        if await self.ipc.vocalize("Код активации ассистента: " + ", ".join(code)):
            return
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
        if self.mode == MODE_ALICE:
            await self.set_mode(MODE_ASSISTANT, announce=False)
        if self.state == IDLE:
            await self.start_session()
        elif self.state == SPEAKING:
            await self.interrupt()
        elif self.state == LISTENING:
            await self.end_session()
        elif self.state == ACTIVATING:
            log.info("код активации: %s", self.activation_code)

    async def start_session(self, wake_word: str = "") -> None:
        if self.brain_engine != "xiaozhi":
            if self._pipeline is None or self._pipeline.done():
                self._pipeline = asyncio.create_task(self._pipeline_session())
            return
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
            self._update_mic()
            self._encoder = opus.Encoder(up.sample_rate, 1)
            await self.player.play_pcm(audio.tone(880, 120), 16000)
            if wake_word:
                await conn.wake_detected(wake_word)
            await conn.listen_start(x["listening_mode"])
            self.set_state(LISTENING)

    async def interrupt(self) -> None:
        if self._pipeline and not self._pipeline.done():
            await self._drop_tts()
            await self.player.stop()  # сессия сама вернётся к слушанию
            return
        if self.conn:
            await self.conn.abort()
        await self._drop_tts()
        await self.player.stop()
        if self.conn and self.conn.is_open:
            await self.conn.listen_start(self.cfg["xiaozhi"]["listening_mode"])
            self.set_state(LISTENING)

    async def end_session(self) -> None:
        if self._pipeline and not self._pipeline.done():
            self._pipeline.cancel()
        if self.conn:
            await self.conn.close()

    async def _on_closed(self) -> None:
        self.conn = None
        await self._drop_tts()
        await self.player.finish()
        self.set_state(IDLE)
        if self.spotter:
            self.spotter.reset()
        self._resume_music()
        self._update_mic()

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
            if phrase_match(self.last_stt, self.cfg["assistant"]["exit_phrases"]):
                asyncio.create_task(self.return_to_alice())
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

    # ---------- конвейер без XiaoZhi (brain = claude | openai) ----------
    async def _pipeline_session(self) -> None:
        """«Катя» → вопрос → Claude → озвучка; потом ещё followup секунд ждём продолжения."""
        st = self.cfg["stt"]
        self._music_was_playing = self.music.playing
        self.music.pause()
        try:
            await self.player.play_pcm(audio.tone(880, 120), 16000)
            timeout = float(st["timeout"])
            while self.mode == MODE_ASSISTANT:
                self.set_state(LISTENING)
                text = (await self._listen(timeout)).strip()
                if not text:
                    break
                self.last_stt = text
                log.info(">> %s", text)
                if phrase_match(text, self.cfg["assistant"]["exit_phrases"]):
                    await self.return_to_alice()
                    return
                self.set_state(CONNECTING)
                try:
                    reply = await self.brain.ask(text)
                except Exception as e:  # noqa: BLE001
                    log.warning("Claude: %s", e)
                    reply = "Не получилось связаться с моделью."
                self.last_reply = reply
                log.info("<< %s", reply)
                self.set_state(SPEAKING)
                self._ensure_tts_worker()
                for s in sentences(reply):
                    await self._tts_queue.put(s)
                await self._tts_queue.join()
                await self.player.finish()
                timeout = float(st["followup"])
        except asyncio.CancelledError:
            await self._drop_tts()
            await self.player.stop()
            raise
        finally:
            self._stt_q = None
            self.set_state(IDLE)
            if self.spotter:
                self.spotter.reset()
            self._resume_music()

    async def _listen(self, timeout: float) -> str:
        if self.stt_engine == "vosk" and hasattr(self.spotter, "transcriber"):
            return await self._listen_vosk(timeout)
        return await self._listen_alice(timeout)

    async def _listen_alice(self, timeout: float) -> str:
        """Штатный распознаватель Алисы в режиме recognition-only (Luna), без её ответа."""
        url = self.cfg["midipult"]["luna_url"].rstrip("/") + \
            f"/api/alice/recognize?timeout_ms={int(timeout * 1000)}"

        def post() -> dict:
            req = urllib.request.Request(url, data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout + 5) as r:
                return json.loads(r.read() or b"{}")

        self._alice_stt = True
        try:
            data = await asyncio.to_thread(post)
        except Exception as e:  # noqa: BLE001  (таймаут — это тишина)
            log.debug("recognize: %s", e)
            return ""
        finally:
            await asyncio.sleep(0.3)
            self._alice_stt = False
        return str(data.get("transcript") or data.get("text") or
                   (data.get("result") or {}).get("transcript") or "")

    async def _listen_vosk(self, timeout: float) -> str:
        rec = self.spotter.transcriber()  # type: ignore[union-attr]
        self._stt_q = asyncio.Queue()
        deadline = time.monotonic() + timeout
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    return await asyncio.to_thread(rec.finish)
                try:
                    frame = await asyncio.wait_for(self._stt_q.get(), left)
                except asyncio.TimeoutError:
                    continue
                text = await asyncio.to_thread(rec.feed, frame)
                if text:
                    return text
                if await asyncio.to_thread(rec.partial):
                    deadline = max(deadline, time.monotonic() + 1.5)  # говорят — ждём конца фразы
        finally:
            self._stt_q = None

    # ---------- локальный голос ----------
    def _ensure_tts_worker(self) -> None:
        if self._tts_worker is None or self._tts_worker.done():
            self._tts_worker = asyncio.create_task(self._tts_loop())

    async def _tts_loop(self) -> None:
        while True:
            text = await self._tts_queue.get()
            try:
                p = self._tts_profile()
                if p.engine == "alice":
                    await self._alice_speak(p, text)
                    continue
                pcm, rate = await synthesize(p, text)
                await self.player.write(pcm, rate)
            except Exception as e:  # noqa: BLE001
                log.warning("локальный TTS: %s", e)
            finally:
                self._tts_queue.task_done()

    async def _alice_speak(self, p, text: str) -> None:
        """Фраза ассистента синтезом Алисы с голосом профиля (<speaker voice=…>)."""
        markup = alice_markup(p, text)
        secs = speech_seconds(text)
        # своя озвучка Алисы не должна считаться её пробуждением (alice_deaf)
        self._own_speech_until = time.monotonic() + secs + 2
        if not await self.ipc.vocalize(markup):
            raise RuntimeError("нет связи с Алисой (127.0.0.1:9875)")
        # статуса «говорит» в канале нет — ждём по оценке длительности, чтобы фразы не наложились
        await asyncio.sleep(secs)
        await self.ipc.wait_idle(2.0)

    async def _drop_tts(self) -> None:
        while not self._tts_queue.empty():
            self._tts_queue.get_nowait()
            self._tts_queue.task_done()

    # ---------- режимы ----------
    def _update_mic(self) -> None:
        if self.mode == MODE_ASSISTANT or (self.conn and self.conn.is_open):
            self._mic_wanted.set()
        else:
            self._mic_wanted.clear()

    async def set_mode(self, mode: str, announce: bool = True) -> None:
        if mode == self.mode:
            return
        log.info("режим: %s → %s", self.mode, mode)
        if mode == MODE_ASSISTANT and self.spotter is None:
            self.spotter = await asyncio.to_thread(kws.create, self.cfg["wakeword"])
        self.mode = mode  # после загрузки модели: режим и микрофон меняются вместе
        if mode == MODE_ASSISTANT:
            self.spotter.reset()
            self._update_mic()
            if announce:
                await self._say(self.cfg["assistant"]["enter_message"])
        else:
            if self.conn and self.conn.is_open:
                await self.conn.close()
            self._update_mic()
            if announce:
                await self._say(self.cfg["assistant"]["exit_message"])

    async def _say(self, template: str) -> None:
        """Короткое системное сообщение голосом Алисы; без IPC — сигнал."""
        text = template.format(name=self.assistant_name)
        self._own_speech_until = time.monotonic() + 3 + len(text) / 12
        if not await self.ipc.vocalize(text):
            await self.player.play_pcm(audio.tone(660, 100) + audio.tone(880, 140), 16000)

    async def return_to_alice(self) -> None:
        if self._pipeline and not self._pipeline.done() and self._pipeline is not asyncio.current_task():
            self._pipeline.cancel()
        if self.conn and self.conn.is_open:
            await self.conn.abort()
        await self._drop_tts()
        await self.player.stop()
        await self.set_mode(MODE_ALICE)

    async def _on_alice_event(self, ev: AliceEvent) -> None:
        woke = ev.status == STATUS_LISTENING and self._alice_status != STATUS_LISTENING
        self._alice_status = ev.status
        if woke and self.mode == MODE_ASSISTANT and self.cfg["assistant"]["alice_deaf"]                 and not self._switching and not self._alice_stt \
                and time.monotonic() > self._own_speech_until:
            asyncio.create_task(self._silence_alice())
            return
        if ev.status != STATUS_RECOGNIZED or not ev.text:
            return
        log.info("Алиса услышала: %s", ev.text)
        if self.mode == MODE_ALICE and not self._switching and \
                phrase_match(ev.text, self.cfg["assistant"]["enter_phrases"]):
            asyncio.create_task(self._enter_from_alice())

    async def _silence_alice(self) -> None:
        """Режим ассистента: Алиса проснулась — сразу отменить её диалог и не дать ответить."""
        if self._silencing:
            return
        self._silencing = True
        vol = self.volume.get()
        try:
            log.info("режим ассистента: Алиса проснулась — отменяю")
            if vol > 0:
                self.volume.set(0)
            await self.ipc.cancel()
            await self.ipc.wait_idle(4.0)
            await asyncio.sleep(0.3)
        finally:
            if vol > 0:
                self.volume.set(vol)
            self._silencing = False

    async def _enter_from_alice(self) -> None:
        """«Алиса, включи ассистента»: погасить ответ Алисы и включить ассистента."""
        self._switching = True
        try:
            mute = self.cfg["assistant"]["mute_alice_reply"]
            vol = self.volume.get() if mute else -1
            if vol > 0:
                self.volume.set(0)
            await self.ipc.cancel()
            await self.ipc.wait_idle(6.0)
            await asyncio.sleep(0.5)
            if vol > 0:
                self.volume.set(vol)
            await self.set_mode(MODE_ASSISTANT)
        finally:
            self._switching = False

    # ---------- микрофон ----------
    async def mic_loop(self) -> None:
        idle_timeout = self.cfg["xiaozhi"]["idle_timeout"]
        while True:
            if not self._mic_wanted.is_set():
                if self.capture.running:
                    await self.capture.stop()
                await self._mic_wanted.wait()
            frame = await self.capture.read()
            if self._stt_q is not None:
                self._stt_q.put_nowait(frame)
            elif self.state == LISTENING and self.conn and self._encoder:
                if idle_timeout and time.monotonic() - self._listen_since > idle_timeout:
                    log.info("тишина %d с — закрываю канал", idle_timeout)
                    asyncio.create_task(self.end_session())
                    continue
                try:
                    await self.conn.send_audio(self._encoder.encode(frame))
                except Exception as e:  # noqa: BLE001
                    log.debug("отправка звука: %s", e)
            elif self.mode == MODE_ASSISTANT and self.spotter and self.state in (IDLE, SPEAKING):
                word = await asyncio.to_thread(self.spotter.feed, frame)
                if word and word.startswith(kws.COMMAND_PREFIX):
                    log.info("команда: %s", word[len(kws.COMMAND_PREFIX):])
                    self.spotter.reset()
                    asyncio.create_task(self.return_to_alice())
                elif word:
                    log.info("слово активации: %s", word)
                    self.spotter.reset()
                    if self.state == IDLE:
                        asyncio.create_task(self.start_session(wake_word=word))
                    else:
                        asyncio.create_task(self.interrupt())

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
                await device.watch_button(path, t["key_code"], t["long_press_ms"], self._on_button)
            except OSError as e:
                log.warning("кнопка: %s", e)
                await asyncio.sleep(5)

    async def mute_button_loop(self) -> None:
        """Кнопка отключения микрофона: одно нажатие в режиме ассистента — назад к Алисе."""
        t = self.cfg["trigger"]
        if not t["mute_exit"]:
            return
        path = device.find_input(t["mute_input_name_hint"], 0)
        if not path:
            log.info("кнопка mute не найдена (%s)", t["mute_input_name_hint"])
            return
        log.info("кнопка mute: %s", path)
        while True:
            try:
                await device.watch_any_key(path, self._on_mute_button)
            except OSError as e:
                log.warning("кнопка mute: %s", e)
                await asyncio.sleep(5)

    async def _on_mute_button(self) -> None:
        if self.mode == MODE_ASSISTANT:
            log.info("кнопка mute — возвращаю Алису")
            await self.return_to_alice()

    async def _on_button(self, long_press: bool) -> None:
        # В режиме Алисы кнопка принадлежит Алисе
        if self.mode == MODE_ASSISTANT:
            await self.trigger(long_press)

    async def run(self) -> None:
        tasks = [asyncio.create_task(self.mic_loop()), asyncio.create_task(self.button_loop()),
                 asyncio.create_task(self.mute_button_loop()),
                 asyncio.create_task(self.ipc.run())]
        if self.mode == MODE_ASSISTANT:
            self.mode = MODE_ALICE
            await self.set_mode(MODE_ASSISTANT, announce=False)
        if self.cfg["panel"]["enabled"]:
            from .panel import start_panel
            start_panel(self, asyncio.get_running_loop())
        await self.ensure_activated()
        await asyncio.gather(*tasks)
