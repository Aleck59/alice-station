"""Клиент VK API для аудио от имени аккаунта пользователя.

Токен пользователь получает сам: открывает ссылку входа (AUTH_URL) в браузере,
входит в ВК и копирует адрес страницы, на которую его перенесло. Из адреса мы
берём access_token и user_id. Пароль служба не видит и не хранит.

Аудио-методы VK API отвечают только клиенту, от имени которого выдан токен,
поэтому User-Agent и client_id должны совпадать (по умолчанию — Kate Mobile,
как в открытых библиотеках vkpymusic / vk_api). Оба значения можно поменять в
конфиге, если ВК их сменит.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

API = "https://api.vk.com/method/"
API_VERSION = "5.131"
DEFAULT_CLIENT_ID = 2685278  # Kate Mobile
DEFAULT_USER_AGENT = ("KateMobileAndroid/56 lite-460 (Android 4.4.2; SDK 19; x86; "
                      "unknown Android SDK built for x86; en)")
REDIRECT = "https://oauth.vk.com/blank.html"

# m3u8 → прямой mp3 (известная схема адресов CDN ВК; если не подошла —
# играем m3u8 как есть, GStreamer умеет HLS).
_M3U8_RE = re.compile(r"/[0-9a-f]+(/audios)?/([0-9a-f]+)/index\.m3u8")


class VkError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class VkAuthError(VkError):
    pass


def auth_url(client_id: int = DEFAULT_CLIENT_ID) -> str:
    q = urllib.parse.urlencode({
        "client_id": client_id, "scope": "audio,offline", "redirect_uri": REDIRECT,
        "display": "page", "response_type": "token", "revoke": 1, "v": API_VERSION,
    })
    return "https://oauth.vk.com/authorize?" + q


def parse_token(text: str) -> dict:
    """Достать access_token и user_id из адреса blank.html (или из «голого» токена)."""
    s = (text or "").strip()
    if not s:
        raise ValueError("пусто: вставьте адрес страницы после входа в ВК")
    if "access_token=" not in s:
        if re.fullmatch(r"[A-Za-z0-9_.\-]{40,400}", s):
            return {"access_token": s, "user_id": None}
        raise ValueError("в адресе нет access_token — скопируйте адрес целиком после входа")
    part = s.split("#", 1)[1] if "#" in s else s.split("?", 1)[-1]
    params = urllib.parse.parse_qs(part)
    token = (params.get("access_token") or [""])[0]
    if not token:
        raise ValueError("access_token пустой")
    uid = (params.get("user_id") or [None])[0]
    return {"access_token": token, "user_id": int(uid) if uid and uid.isdigit() else None}


def mp3_url(url: str) -> str:
    if ".m3u8" not in url:
        return url
    return _M3U8_RE.sub(lambda m: f"{m.group(1) or ''}/{m.group(2)}.mp3", url)


@dataclass
class Track:
    title: str
    artist: str
    url: str
    owner_id: int = 0
    id: int = 0
    duration: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.owner_id}_{self.id}"

    def public(self) -> dict:
        return {"title": self.title, "artist": self.artist, "duration": self.duration,
                "key": self.key}


def to_tracks(items: list[dict]) -> list[Track]:
    out = []
    for it in items or []:
        url = it.get("url") or ""
        if not url:  # недоступен в регионе / изъят правообладателем
            continue
        out.append(Track(title=it.get("title") or "", artist=it.get("artist") or "",
                         url=mp3_url(url), owner_id=int(it.get("owner_id") or 0),
                         id=int(it.get("id") or 0), duration=int(it.get("duration") or 0)))
    return out


class VkClient:
    def __init__(self, token: str, user_id: int | None = None, *,
                 user_agent: str = DEFAULT_USER_AGENT, timeout: float = 15,
                 opener=None):
        self.token = token
        self.user_id = user_id
        self.user_agent = user_agent
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    # ---- низкий уровень ----
    def call(self, method: str, **params) -> object:
        params = {k: v for k, v in params.items() if v is not None}
        params.update(access_token=self.token, v=API_VERSION)
        body = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(API + method, data=body, method="POST",
                                     headers={"User-Agent": self.user_agent})
        for attempt in range(3):
            try:
                with self._open(req, timeout=self.timeout) as r:
                    data = json.loads(r.read() or b"{}")
            except (urllib.error.URLError, OSError, ValueError) as e:
                if attempt == 2:
                    raise VkError(f"ВК недоступен: {e}") from None
                time.sleep(1 + attempt)
                continue
            err = data.get("error") if isinstance(data, dict) else None
            if not err:
                return data.get("response")
            code = int(err.get("error_code") or 0)
            msg = err.get("error_msg") or "ошибка VK API"
            if code == 6 and attempt < 2:  # слишком много запросов
                time.sleep(1)
                continue
            if code in (5, 27, 28):
                raise VkAuthError(f"вход в ВК недействителен: {msg}", code)
            if code in (15, 201, 203):
                raise VkError(f"нет доступа к аудио: {msg}", code)
            raise VkError(f"{method}: {msg}", code)
        raise VkError(f"{method}: не удалось после повторов")

    # ---- аккаунт ----
    def whoami(self) -> dict:
        r = self.call("users.get")
        u = (r or [{}])[0]
        self.user_id = self.user_id or u.get("id")
        return {"id": u.get("id"), "name": f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()}

    # ---- музыка ----
    def my_tracks(self, count: int = 200, offset: int = 0) -> list[Track]:
        r = self.call("audio.get", owner_id=self.user_id, count=count, offset=offset)
        return to_tracks((r or {}).get("items"))

    def search(self, query: str, count: int = 30) -> list[Track]:
        r = self.call("audio.search", q=query, count=count, auto_complete=1, sort=2)
        return to_tracks((r or {}).get("items"))

    def recommendations(self, count: int = 50) -> list[Track]:
        r = self.call("audio.getRecommendations", user_id=self.user_id, count=count, shuffle=1)
        return to_tracks((r or {}).get("items"))

    def mix(self, count: int = 30) -> list[Track]:
        """«Моя волна» ВК — поток VK Микс. Если метод недоступен этому клиенту,
        берём рекомендации."""
        try:
            r = self.call("audio.getStreamMixAudios", mix_id="common", count=count)
            items = r.get("items") if isinstance(r, dict) else r
            tracks = to_tracks(items)
            if tracks:
                return tracks
        except VkAuthError:
            raise
        except VkError as e:
            log.info("VK Микс недоступен (%s), беру рекомендации", e)
        return self.recommendations(count)

    def add(self, track: Track) -> None:
        self.call("audio.add", owner_id=track.owner_id, audio_id=track.id)
