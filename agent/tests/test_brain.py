"""Конвейер без XiaoZhi: Claude с инструментами (фейковый API), сессия «Катя → вопрос → ответ»."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alice_station import brain, config  # noqa: E402
from alice_station.app import App, MODE_ASSISTANT  # noqa: E402
from alice_station.mcp import McpServer, prop  # noqa: E402


class FakeClaude(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeClaude.requests.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        last = body["messages"][-1]
        if isinstance(last["content"], str):
            out = {"stop_reason": "tool_use", "content": [
                {"type": "thinking", "thinking": "…", "signature": "x"},
                {"type": "tool_use", "id": "t1", "name": "self__light__set_rgb",
                 "input": {"r": 0, "g": 255, "b": 0}}]}
        else:
            out = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Готово. Зелёный свет."}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class ClaudeTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_loop(self):
        srv = HTTPServer(("127.0.0.1", 0), FakeClaude)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        mcp = McpServer("t")
        seen = {}

        @mcp.add("self.light.set_rgb", "свет", {"r": prop("integer"), "g": prop("integer"), "b": prop("integer")})
        def set_rgb(r, g, b):
            seen.update(r=r, g=g, b=b)
            return True

        c = brain.Claude({"url": f"http://127.0.0.1:{srv.server_port}/v1/messages"}, mcp, lambda: "k", "Катя")
        reply = await c.ask("включи зелёный")
        srv.shutdown()
        self.assertEqual(reply, "Готово. Зелёный свет.")
        self.assertEqual(seen, {"r": 0, "g": 255, "b": 0})
        first = FakeClaude.requests[0]
        self.assertEqual(first["headers"]["x-api-key"], "k")
        self.assertIn("self__light__set_rgb", [t["name"] for t in first["body"]["tools"]])
        self.assertIn("Катя", first["body"]["system"])
        # tool_result ушёл вторым запросом
        res = FakeClaude.requests[1]["body"]["messages"][-1]["content"][0]
        self.assertEqual((res["type"], res["tool_use_id"]), ("tool_result", "t1"))

    async def test_openai_compat_tool_loop(self):
        class FakeOpenAI(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if body["messages"][-1]["role"] == "user":
                    msg = {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "self__light__off", "arguments": "{}"}}]}
                else:
                    msg = {"role": "assistant", "content": "Выключила."}
                data = json.dumps({"choices": [{"message": msg}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        mcp = McpServer("t")
        called = []
        mcp.add("self.light.off", "погасить", {})(lambda: called.append(1) or True)
        c = brain.OpenAICompat({"url": f"http://127.0.0.1:{srv.server_port}/v1"}, mcp, lambda: "k", "Катя")
        reply = await c.ask("выключи свет")
        srv.shutdown()
        self.assertEqual(reply, "Выключила.")
        self.assertEqual(called, [1])

    async def test_no_key(self):
        c = brain.Claude({}, McpServer("t"), lambda: "", "Катя")
        with self.assertRaises(RuntimeError):
            await c.ask("привет")

    def test_sentences(self):
        self.assertEqual(brain.sentences("Привет! Как дела? Хорошо."), ["Привет!", "Как дела?", "Хорошо."])


class FakePlayer:
    def __init__(self):
        self.written = []

    async def play_pcm(self, *a):
        pass

    async def write(self, pcm, rate):
        self.written.append(pcm)

    async def stop(self):
        pass

    async def finish(self):
        pass


class PipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_session(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load(Path(d) / "config.toml")
            cfg.data["wakeword"]["engine"] = "none"
            cfg.data["led"]["enabled"] = False
            cfg.data["assistant"]["ipc_port"] = 1
            app = App(cfg)
            app.player = FakePlayer()
            app.update_settings({"brain": "claude", "elevenlabs": "ek", "elevenlabs_voice": "v1"})
            self.assertEqual(app.status()["keys"], {"anthropic": False, "llm": False, "elevenlabs": True,
                                                    "elevenlabs_voice": "v1"})
            p = app._tts_profile()
            self.assertEqual((p.engine, p.api_key, p.voice), ("elevenlabs", "ek", "v1"))
            await app.set_mode(MODE_ASSISTANT, announce=False)

            heard = ["какая погода", ""]

            async def listen(timeout):
                return heard.pop(0)

            async def ask(text):
                return "Солнечно. Плюс десять."

            spoken = []

            async def fake_synth(profile, text):
                spoken.append(text)
                return b"\0\0", 24000

            app._listen, app.brain.ask = listen, ask
            import alice_station.app as appmod
            orig, appmod.synthesize = appmod.synthesize, fake_synth
            try:
                await app.start_session("катя")
                await asyncio.wait_for(app._pipeline, 5)
            finally:
                appmod.synthesize = orig
            self.assertEqual(spoken, ["Солнечно.", "Плюс десять."])
            self.assertEqual(app.last_stt, "какая погода")
            self.assertEqual(app.state, "idle")


if __name__ == "__main__":
    unittest.main()
