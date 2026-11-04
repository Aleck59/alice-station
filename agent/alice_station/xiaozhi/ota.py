"""Проверка версии и активация устройства в облаке XiaoZhi.

Повторяет main/ota.cc из xiaozhi-esp32 (активация версии 1, без серийного номера):

1. POST {ota_url} c JSON-описанием «платы». В ответе могут быть:
   activation {code, message, challenge, timeout_ms}, websocket {url, token, ...},
   mqtt {...}, server_time {...}, firmware {...} (прошивку ESP32 мы игнорируем).
2. Если есть activation — показываем/произносим код, пользователь вводит его в
   консоли xiaozhi.me, а мы опрашиваем POST {ota_url}activate:
   200 — активировано, 202 — ещё ждём.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .. import __version__

log = logging.getLogger(__name__)


@dataclass
class OtaResult:
    activation_code: str = ""
    activation_message: str = ""
    activation_challenge: str = ""
    activation_timeout_ms: int = 0
    websocket: dict = field(default_factory=dict)
    mqtt: dict = field(default_factory=dict)
    server_time: dict = field(default_factory=dict)

    @property
    def needs_activation(self) -> bool:
        return bool(self.activation_code or self.activation_challenge)


def parse_response(body: str) -> OtaResult:
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("ответ OTA — не объект")
    r = OtaResult()
    act = data.get("activation")
    if isinstance(act, dict):
        r.activation_code = str(act.get("code") or "")
        r.activation_message = str(act.get("message") or "")
        r.activation_challenge = str(act.get("challenge") or "")
        if isinstance(act.get("timeout_ms"), int):
            r.activation_timeout_ms = act["timeout_ms"]
    for key in ("websocket", "mqtt", "server_time"):
        if isinstance(data.get(key), dict):
            setattr(r, key, data[key])
    return r


class OtaClient:
    def __init__(self, url: str, mac: str, client_id: str, board_name: str, language: str = "ru-RU",
                 timeout: float = 15.0):
        self.url = url
        self.mac = mac
        self.client_id = client_id
        self.board_name = board_name
        self.language = language
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Activation-Version": "1",
            "Device-Id": self.mac,
            "Client-Id": self.client_id,
            "User-Agent": f"{self.board_name}/{__version__}",
            "Accept-Language": self.language,
            "Content-Type": "application/json",
        }

    def system_info(self) -> dict:
        # Облако ждёт описание ESP32-платы; отдаём правдоподобный минимум.
        return {
            "version": 2,
            "language": self.language,
            "flash_size": 0,
            "minimum_free_heap_size": "0",
            "mac_address": self.mac,
            "uuid": self.client_id,
            "chip_model_name": "linux-aarch64",
            "application": {
                "name": "alice-station",
                "version": __version__,
                "compile_time": "",
                "idf_version": "",
                "elf_sha256": "",
            },
            "board": {"type": self.board_name, "name": self.board_name},
        }

    def _post(self, url: str, payload: dict) -> tuple[int, str]:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def check_version(self) -> OtaResult:
        status, body = self._post(self.url, self.system_info())
        if status != 200:
            raise RuntimeError(f"OTA вернул {status}: {body[:200]}")
        return parse_response(body)

    def activate(self) -> bool | None:
        """True — активировано, None — ждём ввода кода, False — ошибка."""
        url = self.url + ("activate" if self.url.endswith("/") else "/activate")
        status, body = self._post(url, {})
        if status == 200:
            return True
        if status == 202:
            return None
        log.warning("activate: %s %s", status, body[:200])
        return False
