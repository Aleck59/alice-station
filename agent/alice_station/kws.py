"""Слово активации ассистента («Катя», «Лариса» …).

Движок по умолчанию — Vosk с малой русской моделью (vosk-model-small-ru) в режиме
грамматики: распознаватель знает только слова вызова и [unk], поэтому работает
как кейворд-спотер и не превращает в текст остальную речь. Срабатывание —
по финальному результату с уверенностью не ниже порога.

Своё слово — просто строка в конфиге, обучать ничего не нужно.
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

log = logging.getLogger(__name__)


def normalize(text: str) -> str:
    t = text.lower().replace("ё", "е")
    return "".join(ch if ch.isalnum() or ch == " " else " " for ch in t).strip()


class Spotter(Protocol):
    def feed(self, pcm: bytes) -> str | None: ...
    def reset(self) -> None: ...


class VoskSpotter:
    def __init__(self, model_path: str, words: list[str], threshold: float = 0.75, rate: int = 16000,
                 commands: list[str] | None = None):
        vosk = import_vosk()

        vosk.SetLogLevel(-1)
        self.words = [normalize(w) for w in words]
        self.commands = [" ".join(normalize(c).split()) for c in commands or []]
        self.threshold = threshold
        self._model = vosk.Model(model_path)
        grammar = json.dumps(self.words + self.commands + ["[unk]"], ensure_ascii=False)
        self._rec = vosk.KaldiRecognizer(self._model, rate, grammar)
        self._rec.SetWords(True)
        log.info("слово активации: %s (Vosk, порог %.2f)", ", ".join(self.words), threshold)

    def feed(self, pcm: bytes) -> str | None:
        if not self._rec.AcceptWaveform(pcm):
            return None
        result = json.loads(self._rec.Result())
        return pick_command(result, self.commands, self.threshold) or pick(result, self.words, self.threshold)

    def reset(self) -> None:
        self._rec.Reset()

    def transcriber(self, rate: int = 16000) -> "VoskTranscriber":
        """Полное распознавание речи на той же модели (без грамматики)."""
        return VoskTranscriber(import_vosk().KaldiRecognizer(self._model, rate))


class VoskTranscriber:
    def __init__(self, rec):
        self._rec = rec

    def feed(self, pcm: bytes) -> str | None:
        """Финальный текст фразы (после паузы) или None, пока говорят."""
        if not self._rec.AcceptWaveform(pcm):
            return None
        return json.loads(self._rec.Result()).get("text", "")

    def partial(self) -> str:
        return json.loads(self._rec.PartialResult()).get("partial", "")

    def finish(self) -> str:
        return json.loads(self._rec.FinalResult()).get("text", "")

    def reset(self) -> None:
        self._rec.Reset()


def import_vosk():
    """Модуль vosk тянет `srt` только для субтитров; если его нет — подставляем заглушку."""
    import sys
    import types
    try:
        import srt  # noqa: F401
    except ImportError:
        sys.modules["srt"] = types.ModuleType("srt")
    # libvosk собран с C++11 ABI, а libstdc++ из Entware (GCC 8) — без него.
    # Установщик кладёт libstdc++ посвежее; грузим его до libvosk.so.
    import ctypes
    import os
    lib = os.environ.get("ALICE_STATION_LIBSTDCXX", "/data/opt/alice-station/lib/libstdc++.so.6")
    if os.path.exists(lib):
        ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
    import vosk  # type: ignore
    return vosk


COMMAND_PREFIX = "cmd:"


def pick_command(result: dict, commands: list[str], threshold: float) -> str | None:
    """Команда из грамматики («выключи ассистента»): вся фраза, средняя уверенность ≥ порога."""
    text = " ".join(normalize(result.get("text", "")).split())
    items = result.get("result") or []
    for c in commands:
        if c and c in text:
            confs = [float(i.get("conf", 0)) for i in items if normalize(i.get("word", "")) in c.split()]
            if confs and sum(confs) / len(confs) >= threshold:
                return COMMAND_PREFIX + c
    return None


def pick(result: dict, words: list[str], threshold: float) -> str | None:
    """Выбрать слово вызова из финального результата Vosk."""
    for item in result.get("result") or []:
        w = normalize(item.get("word", ""))
        if w in words and float(item.get("conf", 0)) >= threshold:
            return w
    return None


class NullSpotter:
    """Без слова активации (нет Vosk/модели): ассистент запускается кнопкой и из панели."""

    def feed(self, pcm: bytes) -> str | None:
        return None

    def reset(self) -> None:
        pass


def create(cfg: dict) -> Spotter:
    if cfg.get("engine", "vosk") != "vosk":
        return NullSpotter()
    try:
        return VoskSpotter(cfg["model"], cfg["words"], float(cfg.get("threshold", 0.75)),
                           commands=cfg.get("exit_commands") or [])
    except Exception as e:  # noqa: BLE001
        log.warning("слово активации выключено: %s (нужны pip vosk и модель %s)", e, cfg.get("model"))
        return NullSpotter()
