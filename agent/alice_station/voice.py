"""Голосовые профили.

engine = "cloud"   — голос, который выбран у агента в консоли xiaozhi.me (Opus из облака).
engine = "command" — локальный синтез любой программой, которая читает текст из stdin и
                     пишет сырой PCM S16 моно в stdout (например, Piper с --output-raw).
engine = "http"    — OpenAI-совместимый POST {url}/audio/speech, response_format=pcm.
engine = "elevenlabs" — ElevenLabs text-to-speech, PCM 24 кГц; voice — voice_id.

Для локальных профилей облачный звук ответа игнорируется, а текст из
`tts sentence_start` озвучивается на колонке.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Profile:
    id: str
    engine: str = "cloud"
    title: str = ""
    rate: int = 22050
    cmd: list = field(default_factory=list)
    url: str = ""
    api_key: str = ""
    model: str = ""
    voice: str = ""
    speed: float = 1.0

    @property
    def is_local(self) -> bool:
        return self.engine != "cloud"

    def public(self) -> dict:
        return {"id": self.id, "engine": self.engine, "title": self.title or self.id}


def load_profiles(cfg: dict) -> dict[str, Profile]:
    out: dict[str, Profile] = {"cloud": Profile("cloud", "cloud", "Голос из xiaozhi.me")}
    for pid, p in (cfg.get("profiles") or {}).items():
        known = {k: v for k, v in p.items() if k in Profile.__dataclass_fields__ and k != "id"}
        out[pid] = Profile(pid, **known)
    return out


async def synthesize(p: Profile, text: str) -> tuple[bytes, int]:
    """Вернуть (pcm, rate)."""
    if p.engine == "command":
        proc = await asyncio.create_subprocess_exec(
            *p.cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate(text.encode("utf-8"))
        if proc.returncode != 0:
            raise RuntimeError(f"{p.cmd[0]} завершился с кодом {proc.returncode}")
        return out, p.rate
    if p.engine == "http":
        return await asyncio.to_thread(_http_tts, p, text)
    if p.engine == "elevenlabs":
        return await asyncio.to_thread(_elevenlabs_tts, p, text)
    raise ValueError(f"профиль {p.id}: движок {p.engine} не синтезирует локально")


def _http_tts(p: Profile, text: str) -> tuple[bytes, int]:
    body = {"model": p.model or "tts-1", "voice": p.voice or "alloy", "input": text,
            "response_format": "pcm", "speed": p.speed}
    headers = {"Content-Type": "application/json"}
    if p.api_key:
        headers["Authorization"] = f"Bearer {p.api_key}"
    url = p.url.rstrip("/") + "/audio/speech"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        # OpenAI отдаёт pcm как 24 кГц S16LE моно
        return r.read(), p.rate or 24000


def _elevenlabs_tts(p: Profile, text: str) -> tuple[bytes, int]:
    if not p.api_key or not p.voice:
        raise RuntimeError("ElevenLabs: нужны ключ и voice_id (панель → «Мозг и голос»)")
    rate = p.rate if p.rate in (16000, 22050, 24000, 44100) else 24000
    url = (p.url or "https://api.elevenlabs.io").rstrip("/") +         f"/v1/text-to-speech/{p.voice}?output_format=pcm_{rate}"
    body = {"text": text, "model_id": p.model or "eleven_flash_v2_5", "language_code": "ru"}
    if p.speed != 1.0:
        body["voice_settings"] = {"speed": p.speed}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "xi-api-key": p.api_key})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), rate


class VoiceManager:
    def __init__(self, cfg: dict, state: dict, save_state):
        self.profiles = load_profiles(cfg)
        self._state = state
        self._save = save_state
        wanted = state.get("voice_profile") or cfg.get("profile") or "cloud"
        self.current_id = wanted if wanted in self.profiles else "cloud"

    @property
    def current(self) -> Profile:
        return self.profiles[self.current_id]

    def set(self, profile_id: str) -> Profile:
        if profile_id not in self.profiles:
            # разрешаем искать по названию
            for p in self.profiles.values():
                if p.title.lower() == profile_id.lower():
                    profile_id = p.id
                    break
            else:
                raise KeyError(f"нет профиля {profile_id}; есть: {', '.join(self.profiles)}")
        self.current_id = profile_id
        self._state["voice_profile"] = profile_id
        self._save()
        return self.current
