"""MCP-сервер колонки (JSON-RPC 2.0 внутри сообщений type=mcp).

Повторяет поведение main/mcp_server.cc: initialize, tools/list (с пагинацией
и withUserTools), tools/call. Инструменты регистрируются декоратором.
"""
from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import __version__

log = logging.getLogger(__name__)

PAGE_SIZE = 32


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[..., Any]
    properties: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    user_only: bool = False

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {"type": "object", "properties": self.properties,
                            "required": self.required},
        }


def prop(type_: str, description: str = "", **extra) -> dict:
    d = {"type": type_}
    if description:
        d["description"] = description
    d.update(extra)
    return d


class McpServer:
    def __init__(self, name: str):
        self.name = name
        self.tools: dict[str, Tool] = {}

    def add(self, name: str, description: str, properties: dict | None = None,
            required: list | None = None, user_only: bool = False):
        def deco(fn):
            self.tools[name] = Tool(name, description, fn, properties or {},
                                    required if required is not None else list((properties or {}).keys()),
                                    user_only)
            return fn
        return deco

    async def handle(self, payload: dict) -> dict | None:
        """Обработать входящий JSON-RPC, вернуть ответ (или None для уведомлений)."""
        method = payload.get("method")
        req_id = payload.get("id")
        params = payload.get("params") or {}
        if req_id is None:
            return None  # уведомление от сервера
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self.name, "version": __version__},
                }
            elif method == "tools/list":
                result = self._list(params)
            elif method == "tools/call":
                return await self._call(req_id, params)
            else:
                return _error(req_id, -32601, f"Method not implemented: {method}")
        except Exception as e:  # noqa: BLE001
            log.exception("MCP %s", method)
            return _error(req_id, -32603, str(e))
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _list(self, params: dict) -> dict:
        with_user = bool(params.get("withUserTools"))
        items = [t for t in self.tools.values() if with_user or not t.user_only]
        cursor = params.get("cursor") or ""
        start = 0
        if cursor:
            names = [t.name for t in items]
            start = names.index(cursor) if cursor in names else 0
        page = items[start:start + PAGE_SIZE]
        nxt = items[start + PAGE_SIZE].name if start + PAGE_SIZE < len(items) else ""
        result = {"tools": [t.describe() for t in page]}
        if nxt:
            result["nextCursor"] = nxt
        return result

    async def _call(self, req_id, params: dict) -> dict:
        name = params.get("name", "")
        tool = self.tools.get(name)
        if tool is None:
            return _error(req_id, -32601, f"Unknown tool: {name}")
        args = params.get("arguments") or {}
        missing = [r for r in tool.required if r not in args]
        if missing:
            return _error(req_id, -32602, f"Missing arguments: {', '.join(missing)}")
        try:
            value = tool.handler(**args)
            if inspect.isawaitable(value):
                value = await value
            is_error = False
        except Exception as e:  # noqa: BLE001
            log.warning("инструмент %s: %s", name, e)
            value, is_error = str(e), True
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)
        else:
            text = str(value)
        return {"jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": is_error}}


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
