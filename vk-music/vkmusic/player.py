"""Плеер: очередь треков, по одному процессу GStreamer на трек.

Команда задаётся шаблоном (config: player.cmd), {url} заменяется адресом трека.
По умолчанию — GStreamer прошивки через `maind --gst-launch`, как звуки
MidiPult, так что звук идёт в ALSA default. Пауза — SIGSTOP/SIGCONT процесса.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from typing import Callable

from .vk import Track

log = logging.getLogger(__name__)

QUASAR = "/system/vendor/quasar"
DEFAULT_CMD = [QUASAR + "/maind", "--gst-launch",
               "playbin uri={url} audio-sink=alsasink"]
# CA-сертификаты прошивки: без них souphttpsrc/hlsdemux не открывают HTTPS
DEFAULT_ENV = {"LD_LIBRARY_PATH": QUASAR + ":libs",
               "SSL_CERT_FILE": QUASAR + "/ca-certificates.crt"}


class Player:
    def __init__(self, cmd: list[str] | None = None, env: dict | None = None,
                 cwd: str | None = None, popen=subprocess.Popen):
        self.cmd = cmd or DEFAULT_CMD
        self.env = DEFAULT_ENV if env is None else env
        self.cwd = cwd if cwd is not None else (QUASAR if os.path.isdir(QUASAR) else None)
        self._popen = popen
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self.queue: list[Track] = []
        self.history: list[Track] = []
        self.current: Track | None = None
        self.paused = False
        self.mode = ""                     # "wave" | "mine" | "search" | ""
        self.refill: Callable[[], list[Track]] | None = None
        self.on_error: Callable[[str], None] | None = None
        self.last_error = ""
        self.started_at = 0.0
        self._proc: subprocess.Popen | None = None
        self._skip = False
        self._gen = 0
        threading.Thread(target=self._loop, name="player", daemon=True).start()

    # ---- состояние ----
    @property
    def playing(self) -> bool:
        return self.current is not None and not self.paused

    def status(self) -> dict:
        with self._lock:
            return {
                "playing": self.playing, "paused": self.paused, "mode": self.mode,
                "current": self.current.public() if self.current else None,
                "position": int(time.time() - self.started_at) if self.current else 0,
                "queue": [t.public() for t in self.queue[:20]], "queue_len": len(self.queue),
                "error": self.last_error,
            }

    # ---- управление ----
    def play(self, tracks: list[Track], mode: str = "",
             refill: Callable[[], list[Track]] | None = None) -> None:
        with self._lock:
            self._gen += 1
            self.queue = list(tracks)
            self.mode = mode
            self.refill = refill
            self.last_error = ""
            self._kill()
            self.current = None
            self.paused = False
        self._wake.set()

    def enqueue(self, tracks: list[Track]) -> None:
        with self._lock:
            self.queue.extend(tracks)
        self._wake.set()

    def next(self) -> None:
        with self._lock:
            self._skip = True
            self._kill()
        self._wake.set()

    def prev(self) -> None:
        with self._lock:
            if not self.history:
                return
            back = self.history.pop()
            if self.current:
                self.queue.insert(0, self.current)
            self.queue.insert(0, back)
            self._skip = True
            self._kill()
        self._wake.set()

    def pause(self) -> None:
        with self._lock:
            if self._alive() and not self.paused:
                self._proc.send_signal(signal.SIGSTOP)
                self.paused = True

    def resume(self) -> None:
        with self._lock:
            if self._alive() and self.paused:
                self._proc.send_signal(signal.SIGCONT)
                self.paused = False

    def toggle(self) -> None:
        if self.paused:
            self.resume()
        else:
            self.pause()

    def stop(self) -> None:
        with self._lock:
            self._gen += 1
            self.queue = []
            self.refill = None
            self.mode = ""
            self._kill()
            self.current = None
            self.paused = False
        self._wake.set()

    # ---- внутреннее ----
    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _kill(self) -> None:
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                p.send_signal(signal.SIGCONT)
                p.kill()
            except OSError:
                pass

    def _next_track(self) -> Track | None:
        with self._lock:
            if not self.queue and self.refill is not None:
                refill, gen = self.refill, self._gen
            else:
                return self.queue.pop(0) if self.queue else None
        try:
            more = refill()  # сеть — без блокировки
        except Exception as e:  # noqa: BLE001
            self._report(f"не удалось подгрузить треки: {e}")
            return None
        with self._lock:
            if gen != self._gen:
                return None
            seen = {t.key for t in self.history[-100:]}
            self.queue.extend(t for t in more if t.key not in seen)
            return self.queue.pop(0) if self.queue else None

    def _report(self, msg: str) -> None:
        log.warning(msg)
        self.last_error = msg
        if self.on_error:
            try:
                self.on_error(msg)
            except Exception:  # noqa: BLE001
                pass

    def _loop(self) -> None:
        fails = 0
        while True:
            track = self._next_track()
            if track is None:
                with self._lock:
                    self.current = None
                    self.paused = False
                self._wake.wait()
                self._wake.clear()
                continue
            with self._lock:
                gen = self._gen
                self.current = track
                self.paused = False
                self._skip = False
                args = [a.replace("{url}", track.url) for a in self.cmd]
                log.info("играет: %s — %s", track.artist, track.title)
                try:
                    self._proc = self._popen(args, cwd=self.cwd, env={**os.environ, **self.env},
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except OSError as e:
                    self._proc = None
                    self._report(f"плеер не запустился: {e}")
                    self.stop()
                    continue
                self.started_at = time.time()
                proc = self._proc
            rc = proc.wait()
            with self._lock:
                skipped = self._skip or gen != self._gen
                if gen == self._gen:
                    self.history.append(track)
                    del self.history[:-200]
            played = time.time() - self.started_at
            if not skipped and rc != 0 and played < 3:
                fails += 1
                self._report(f"трек не воспроизвёлся: {track.artist} — {track.title}")
                if fails >= 5:
                    self._report("подряд 5 треков не играют — останавливаю")
                    self.stop()
                    fails = 0
            else:
                fails = 0
