"""«Мозг» без XiaoZhi: Claude Messages API с инструментами колонки (как в статье на Хабре).

Конвейер: слово вызова → распознавание (Алиса через Luna или Vosk) → Claude → TTS.
Инструменты те же, что видит XiaoZhi: берутся из McpServer, точки в именах
заменяются на «__» (API разрешает только [a-zA-Z0-9_-]).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.request

from .mcp import McpServer

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_SYSTEM = (
    "Ты голосовой ассистент {name} в умной колонке. Отвечай по-русски, кратко: "
    "1–3 предложения, без markdown, списков и эмодзи — ответ озвучивается. "
    "Для управления колонкой (громкость, подсветка, циферблат, таймеры, радио, команды Алисе) "
    "вызывай инструменты."
)
MAX_TOOL_ROUNDS = 5


def tool_name(mcp_name: str) -> str:
    return mcp_name.replace(".", "__")


def text_of(content: list) -> str:
    """Собрать только блоки text (thinking и tool_use пропускаем)."""
    return " ".join(b.get("text", "") for b in content if b.get("type") == "text").strip()


def sentences(text: str) -> list[str]:
    """Порезать ответ на фразы для поочерёдного синтеза."""
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p for p in (s.strip() for s in parts) if p]


class Claude:
    def __init__(self, cfg: dict, mcp: McpServer, key_getter, name: str):
        self.cfg = cfg
        self.mcp = mcp
        self._key = key_getter
        self.system = (cfg.get("system") or DEFAULT_SYSTEM).format(name=name)
        self.history: list[dict] = []
        self._last = 0.0
        self._rpc_id = 0

    def _tools(self) -> list[dict]:
        out = []
        for t in self.mcp.tools.values():
            if t.user_only:
                continue
            d = t.describe()
            out.append({"name": tool_name(d["name"]), "description": d["description"],
                        "input_schema": d["inputSchema"]})
        return out

    def _post(self, body: dict) -> dict:
        key = self._key()
        if not key:
            raise RuntimeError("нет ключа Anthropic API (панель → «Мозг и голос»)")
        req = urllib.request.Request(
            self.cfg.get("url") or API_URL, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=float(self.cfg.get("timeout", 30))) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Claude API {e.code}: {e.read()[:300].decode('utf-8', 'replace')}") from e

    async def ask(self, text: str) -> str:
        # история живёт, пока разговор продолжается; после паузы — с чистого листа
        if time.monotonic() - self._last > float(self.cfg.get("history_ttl", 300)):
            self.history = []
        self.history.append({"role": "user", "content": text})
        by_api_name = {tool_name(n): n for n in self.mcp.tools}
        reply = ""
        for _ in range(MAX_TOOL_ROUNDS):
            body = {"model": self.cfg.get("model", "claude-haiku-5-5"),
                    "max_tokens": int(self.cfg.get("max_tokens", 400)),
                    "system": self.system, "messages": self.history, "tools": self._tools()}
            res = await asyncio.to_thread(self._post, body)
            content = res.get("content") or []
            self.history.append({"role": "assistant", "content": content})
            reply = text_of(content) or reply
            calls = [b for b in content if b.get("type") == "tool_use"]
            if res.get("stop_reason") != "tool_use" or not calls:
                break
            results = []
            for c in calls:
                out, err = await self._call(by_api_name.get(c["name"], c["name"]), c.get("input") or {})
                results.append({"type": "tool_result", "tool_use_id": c["id"], "content": out,
                                "is_error": err})
            self.history.append({"role": "user", "content": results})
        self._trim()
        self._last = time.monotonic()
        return reply

    async def _call(self, name: str, args: dict) -> tuple[str, bool]:
        self._rpc_id += 1
        log.info("инструмент: %s %s", name, json.dumps(args, ensure_ascii=False))
        resp = await self.mcp.handle({"jsonrpc": "2.0", "id": self._rpc_id, "method": "tools/call",
                                      "params": {"name": name, "arguments": args}}) or {}
        if "error" in resp:
            return resp["error"].get("message", "ошибка"), True
        r = resp.get("result") or {}
        return text_of(r.get("content") or []) or "ok", bool(r.get("isError"))

    def _trim(self) -> None:
        trim_history(self.history, int(self.cfg.get("history", 10)))


def trim_history(history: list[dict], limit: int) -> None:
    """Оставить последние N пользовательских реплик, не разрывая пары вызов/результат."""
    starts = [i for i, m in enumerate(history) if m["role"] == "user" and isinstance(m["content"], str)]
    if len(starts) > limit:
        del history[:starts[-limit]]


class OpenAICompat(Claude):
    """Любой OpenAI-совместимый /chat/completions с tools: DeepSeek, OpenRouter, свой сервер."""

    def _tools(self) -> list[dict]:
        return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["input_schema"]}}
                for t in super()._tools()]

    def _post(self, body: dict) -> dict:
        key = self._key()
        if not key:
            raise RuntimeError("нет ключа LLM (панель → «Мозг и голос»)")
        url = (self.cfg.get("url") or "https://api.deepseek.com/v1").rstrip("/") + "/chat/completions"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=float(self.cfg.get("timeout", 30))) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"LLM {e.code}: {e.read()[:300].decode('utf-8', 'replace')}") from e

    async def ask(self, text: str) -> str:
        if time.monotonic() - self._last > float(self.cfg.get("history_ttl", 300)):
            self.history = []
        self.history.append({"role": "user", "content": text})
        by_api_name = {tool_name(n): n for n in self.mcp.tools}
        reply = ""
        for _ in range(MAX_TOOL_ROUNDS):
            body = {"model": self.cfg.get("model", "deepseek-chat"),
                    "max_tokens": int(self.cfg.get("max_tokens", 400)),
                    "messages": [{"role": "system", "content": self.system}] + self.history,
                    "tools": self._tools()}
            res = await asyncio.to_thread(self._post, body)
            msg = (res.get("choices") or [{}])[0].get("message") or {}
            self.history.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
            reply = (msg.get("content") or "").strip() or reply
            calls = msg.get("tool_calls") or []
            if not calls:
                break
            for c in calls:
                fn = c.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                out, _ = await self._call(by_api_name.get(fn.get("name", ""), fn.get("name", "")), args)
                self.history.append({"role": "tool", "tool_call_id": c.get("id"), "content": out})
        self._trim()
        self._last = time.monotonic()
        return reply
