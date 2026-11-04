"""Конфигурация агента.

Файл — TOML (по умолчанию /data/alice-station/config.toml). Всё, что не задано,
берётся из DEFAULTS. Состояние, которое пишет сам агент (UUID, токен XiaoZhi,
выбранный голос), хранится отдельно в state.json рядом с конфигом.
"""
from __future__ import annotations

import copy
import json
import os
import tomllib
import uuid
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path(os.environ.get("ALICE_STATION_DIR", "/data/alice-station"))

DEFAULTS: dict[str, Any] = {
    "device": {
        # "midi" — Станция Миди, "pc" — эмулятор на компьютере
        "kind": "midi",
        "name": "alice-station-midi",
        # MAC для Device-Id. Пусто — взять из wlan0/первого интерфейса.
        "mac": "",
    },
    "xiaozhi": {
        "ota_url": "https://api.tenclass.net/xiaozhi/ota/",
        "language": "ru-RU",
        "protocol_version": 1,
        # auto — сервер сам решает, когда фраза закончилась (VAD на сервере)
        "listening_mode": "auto",
        # закрыть канал, если в режиме слушания ничего не распознано столько секунд
        "idle_timeout": 12,
    },
    "audio": {
        # Миди: только 96 кГц на hw:0,1, см. deploy/asoundrc. Ресемплинг делает ALSA.
        "capture_device": "alice_mic",
        "playback_device": "default",
        "capture_rate": 16000,
        "frame_ms": 60,
        # каталог с .asoundrc (агент запускает arecord/aplay с HOME=этот каталог)
        "alsa_home": "",
    },
    "trigger": {
        # Кнопка, которая запускает/прерывает диалог. 582 = KEY_VOICECOMMAND.
        "button_enabled": True,
        "key_code": 582,
        "long_press_ms": 600,
        "input_name_hint": "awinic_sar",
        # Кнопка отключения микрофона: одно нажатие в режиме ассистента — назад к Алисе
        "mute_exit": True,
        "mute_input_name_hint": "Name=\"mute\"",
    },
    "voice": {
        # cloud — голос, выбранный в консоли xiaozhi.me
        "profile": "cloud",
        "profiles": {
            "cloud": {"engine": "cloud", "title": "Голос из xiaozhi.me"},
            # ключ и voice_id задаются в панели (хранятся в state.json) или здесь
            "eleven": {"engine": "elevenlabs", "title": "ElevenLabs", "rate": 24000,
                       "model": "eleven_flash_v2_5"},
        },
    },
    "led": {
        "enabled": True,
        # sysfs — /sys/class/leds/cucumber:*; fb — /dev/fb0 и /dev/fb1
        "backend": "sysfs",
        "fb_byte_order": "rgb",
        "brightness": 255,
    },
    "music": {
        # шаблон команды плеера; {url} подставляется
        "player_cmd": [
            "/system/vendor/quasar/maind", "--gst-launch",
            "souphttpsrc location={url} ! decodebin ! audioconvert ! audioresample ! alsasink",
        ],
        "player_env": {"LD_LIBRARY_PATH": "/system/vendor/quasar:/system/vendor/quasar/libs"},
        "stations": {},
        "vk": {"enabled": False},
    },
    "assistant": {
        # Имя ассистента — для фраз и сообщений
        "name": "Катя",
        # С чего начинать после загрузки: alice — штатная Алиса (сток), assistant — XiaoZhi
        "default_mode": "alice",
        # Фразы, услышанные штатной Алисой («Алиса, включи ассистента»)
        "enter_phrases": ["включи ассистент", "запусти ассистент"],
        # Фразы, услышанные ассистентом («Катя, верни к Алисе»)
        "exit_phrases": ["верни к алисе", "верни алису", "выключи ассистента", "вернись к алисе"],
        # Заглушить ответ Алисы на фразу включения
        "mute_alice_reply": True,
        # В режиме ассистента Алиса не слушает: проснулась — сразу отменяем её диалог
        "alice_deaf": True,
        "enter_message": "{name} на связи. Чтобы спросить, скажите: {name}.",
        "exit_message": "Возвращаю Алису.",
        "ipc_host": "127.0.0.1",
        "ipc_port": 9875,
    },
    "wakeword": {
        # vosk — малая русская модель с грамматикой из слов вызова; none — без слова
        "engine": "vosk",
        "model": "/data/alice-station/models/vosk-model-small-ru-0.22",
        "words": ["катя", "лариса"],
        # Команды, которые Vosk слышит без слова вызова (выход из режима ассистента)
        "exit_commands": ["выключи ассистента", "выключи ассистент"],
        "threshold": 0.75,
    },
    "brain": {
        # xiaozhi — облако xiaozhi.me (голос, STT и модель там);
        # claude — конвейер как в статье: распознавание на колонке → Claude API → TTS;
        # openai — тот же конвейер с OpenAI-совместимой моделью
        "engine": "xiaozhi",
        "claude": {
            "model": "claude-haiku-5-5",
            "api_key": "",          # лучше задать в панели
            "max_tokens": 400,
            "history": 10,          # сколько реплик помнить
            "history_ttl": 300,     # через сколько секунд тишины забыть разговор
            "system": "",           # свой системный промпт; {name} — имя ассистента
        },
        # openai — любой OpenAI-совместимый /chat/completions с tools (DeepSeek, OpenRouter…)
        "openai": {
            "url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": "",
            "max_tokens": 400,
            "history": 10,
            "history_ttl": 300,
            "system": "",
        },
    },
    "stt": {
        # Распознавание для brain = claude: alice — штатный распознаватель Алисы через Luna
        # (/api/alice/recognize, без её ответа), vosk — малая модель Vosk на колонке
        "engine": "alice",
        "timeout": 8,               # сколько ждать вопрос после «Кати»
        "followup": 5,              # после ответа ещё столько секунд слушать продолжение
    },
    "midipult": {
        # auto — включить мост, если на колонке отвечает MidiPult (:8055) или Luna (:8099)
        "enabled": "auto",
        "url": "http://127.0.0.1:8055",
        "luna_url": "http://127.0.0.1:8099",
        "timeout": 15,
    },
    "panel": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 8060,
        "pin": "",
    },
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data: dict, directory: Path):
        self.data = data
        self.dir = directory
        self.state_path = directory / "state.json"
        self.state: dict[str, Any] = {}
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text("utf-8"))
            except (OSError, ValueError):
                self.state = {}
        if "client_id" not in self.state:
            self.state["client_id"] = str(uuid.uuid4())
            self.save_state()

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def save_state(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), "utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.state_path)


def load(path: Path | None = None) -> Config:
    path = path or DEFAULT_DIR / "config.toml"
    data: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
    merged = _merge(DEFAULTS, data)
    return Config(merged, path.parent)
