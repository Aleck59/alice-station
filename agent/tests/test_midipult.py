"""MCP-мост к MidiPult/Luna с фейковым сервером."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alice_station import config, midipult  # noqa: E402
from alice_station.app import App  # noqa: E402

CALLS: list[tuple[str, dict]] = []
STATIONS = [{"index": 0, "name": "Европа Плюс"}, {"index": 1, "name": "Радио Рекорд"}]


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        CALLS.append((self.path, body))
        out: dict = {"ok": True}
        if self.path == "/cgi-bin/radio.py" and not body:
            out["stations"] = STATIONS
        elif self.path == "/cgi-bin/scenarios.py" and body.get("run") == "нет":
            self.send_response(404)
            data = json.dumps({"ok": False, "error": "Сценарий не найден: нет"}).encode()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        data = json.dumps(out, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class HelpersTest(unittest.TestCase):
    def test_colors(self):
        self.assertEqual(midipult.to_hex("красный"), "#FF0000")
        self.assertEqual(midipult.to_hex("00ff00"), "#00FF00")
        with self.assertRaises(ValueError):
            midipult.to_hex("сиреневенький")
        ring = midipult.expand_colors(["красный", "#0000ff"])
        self.assertEqual(len(ring), 24)
        self.assertEqual(ring[:3], ["#FF0000", "#0000FF", "#FF0000"])

    def test_clock_text(self):
        self.assertEqual(midipult.clock_text("hello 2026"), "HELLO_2026")
        for bad in ("privet", "привет", "x" * 3, "1" * 33):
            with self.assertRaises(ValueError):
                midipult.clock_text(bad)


class BridgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        CALLS.clear()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{self.srv.server_port}"
        self.tmp = tempfile.TemporaryDirectory()
        cfg = config.load(Path(self.tmp.name) / "config.toml")
        cfg.data["midipult"].update(url=url, luna_url=url)
        cfg.data["wakeword"]["engine"] = "none"
        cfg.data["led"]["enabled"] = False
        self.app = App(cfg)
        self.assertIsNotNone(self.app.bridge)

    async def asyncTearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    async def call(self, tool, **args):
        r = await self.app.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                       "params": {"name": tool, "arguments": args}})
        return r["result"]

    async def test_tools_listed(self):
        names, cursor = set(), ""
        while True:  # облако XiaoZhi листает список по nextCursor
            r = await self.app.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                           "params": {"cursor": cursor}})
            names |= {t["name"] for t in r["result"]["tools"]}
            cursor = r["result"].get("nextCursor", "")
            if not cursor:
                break
        for n in ("self.display.show_text", "self.light.set_colors", "alice.send_command",
                  "alice.player.control", "midipult.scenario.run", "midipult.vpn.set"):
            self.assertIn(n, names)
        self.assertNotIn("self.display.draw_digits", names)  # только для пользователя

    async def test_ring_and_display(self):
        r = await self.call("self.light.set_colors", colors=["зелёный"])
        self.assertFalse(r["isError"])
        self.assertEqual(CALLS[0], ("/api/led/ring", {"colors": ["#000000"] * 24}))
        self.assertEqual(CALLS[1], ("/api/led/ring", {"colors": ["#00E676"] * 24}))
        await self.call("self.light.set_rgb", r=255, g=0, b=0)
        self.assertEqual(CALLS[-1][1]["colors"][0], "#FF0000")
        await self.call("self.light.off")
        self.assertEqual(CALLS[-1][0], "/api/led/off/ring")
        r = await self.call("self.display.show_text", text="hello")
        self.assertEqual(CALLS[-1], ("/api/clock/text", {"text": "HELLO", "duration_ms": 3000}))
        r = await self.call("self.display.show_text", text="привет")
        self.assertTrue(r["isError"])
        await self.call("self.display.show_time", brightness=200)
        self.assertEqual(CALLS[-2][0], "/api/clock")
        self.assertEqual(CALLS[-2][1]["brightness"], 200)
        self.assertEqual(CALLS[-1], ("/cgi-bin/display.py", {"save": 1}))
        await self.call("self.display.off")
        self.assertEqual(CALLS[-1], ("/cgi-bin/display.py", {"off": 1}))

    async def test_alice_and_pult(self):
        await self.call("alice.send_command", text="включи свет в спальне")
        self.assertEqual(CALLS[-1], ("/cgi-bin/command.py", {"text": "включи свет в спальне"}))
        await self.call("alice.player.control", action="next")
        self.assertEqual(CALLS[-1], ("/cgi-bin/media.py", {"action": "next"}))
        r = await self.call("midipult.radio.play", station="рекорд")
        self.assertIn("Радио Рекорд", r["content"][0]["text"])
        self.assertEqual(CALLS[-1], ("/cgi-bin/radio.py", {"index": 1}))
        await self.call("midipult.vpn.set", on=True)
        self.assertEqual(CALLS[-1], ("/cgi-bin/vpn.py", {"start": 1}))
        await self.call("midipult.bluetooth.set", mode="discoverable", on=True)
        self.assertEqual(CALLS[-1], ("/cgi-bin/bluetooth.py", {"action": "discoverable", "on": 1}))
        r = await self.call("midipult.scenario.run", name="нет")
        self.assertTrue(r["isError"])
        self.assertIn("Сценарий не найден", r["content"][0]["text"])

    async def test_disabled_when_absent(self):
        cfg = config.load(Path(self.tmp.name) / "x.toml")
        cfg.data["midipult"].update(url="http://127.0.0.1:1", luna_url="http://127.0.0.1:2")
        cfg.data["wakeword"]["engine"] = "none"
        app = App(cfg)
        self.assertIsNone(app.bridge)
        self.assertNotIn("self.display.show_text", app.mcp.tools)


if __name__ == "__main__":
    unittest.main()
