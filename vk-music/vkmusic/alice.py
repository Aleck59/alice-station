"""Связь со штатной Алисой через MidiPult (:8055) и Luna (:8099).

- stock_pause(): остановить музыку Алисы (на «включи …» она может запустить
  Яндекс Музыку раньше, чем сработает сценарий);
- cancel_speech(): оборвать её ответ («не могу включить ВК»);
- say(text): сказать фразу голосом Алисы;
- Ducker: пока Алиса слушает или говорит — наша музыка на паузе.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request

log = logging.getLogger(__name__)

BUSY_STATES = {"LISTENING", "SPEAKING", "BUSY", "SHAZAM"}


class Alice:
    def __init__(self, pult: str = "http://127.0.0.1:8055", luna: str = "http://127.0.0.1:8099"):
        self.pult = pult.rstrip("/")
        self.luna = luna.rstrip("/")

    def _req(self, url: str, body: dict | None = None, timeout: float = 8) -> dict:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        try:
            out = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return out if isinstance(out, dict) else {}

    def state(self) -> dict:
        return self._req(self.pult + "/cgi-bin/player.py", timeout=6)

    def stock_pause(self) -> None:
        try:
            self._req(self.pult + "/cgi-bin/media.py", {"action": "pause"}, timeout=10)
        except Exception as e:  # noqa: BLE001
            log.debug("media pause: %s", e)

    def cancel_speech(self) -> None:
        try:
            self._req(self.luna + "/api/alice/speech/cancel", {}, timeout=4)
        except Exception as e:  # noqa: BLE001
            log.debug("speech cancel: %s", e)

    def say(self, text: str) -> None:
        try:
            self._req(self.pult + "/cgi-bin/speak.py", {"text": text[:300]}, timeout=30)
        except Exception as e:  # noqa: BLE001
            log.info("speak: %s", e)

    def quiet_stock(self, rounds: int = 4, delay: float = 0.9) -> None:
        """В фоне несколько раз гасим музыку и речь Алисы после голосовой команды."""
        def worker():
            for _ in range(rounds):
                self.cancel_speech()
                self.stock_pause()
                time.sleep(delay)
        threading.Thread(target=worker, daemon=True).start()


class Ducker(threading.Thread):
    """Пауза нашей музыки, пока Алиса занята; продолжение, когда освободилась."""

    def __init__(self, alice: Alice, player, interval: float = 1.5):
        super().__init__(name="ducker", daemon=True)
        self.alice = alice
        self.player = player
        self.interval = interval
        self.ducked = False

    def step(self, state: dict) -> None:
        busy = state.get("alice") in BUSY_STATES
        stock_music = bool(state.get("playing"))
        if busy and self.player.playing:
            self.player.pause()
            self.ducked = True
        elif self.ducked and not busy:
            self.ducked = False
            if stock_music:
                # Алиса по просьбе включила свою музыку — уступаем ей
                self.player.stop()
            else:
                self.player.resume()

    def run(self) -> None:
        while True:
            time.sleep(self.interval)
            if not (self.player.playing or self.ducked):
                continue
            try:
                self.step(self.alice.state())
            except Exception as e:  # noqa: BLE001
                log.debug("состояние Алисы: %s", e)
