"""Тесты без колонки: протокол, MCP, активация, полный диалог с фейковым сервером XiaoZhi.

Запуск: python3 -m unittest discover -s agent/tests -t agent
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import websockets  # noqa: E402

from alice_station import config, device, opus  # noqa: E402
from alice_station.mcp import McpServer, prop  # noqa: E402
from alice_station.xiaozhi import ota  # noqa: E402
from alice_station.xiaozhi.protocol import AudioParams, Connection, auth_header, build_hello  # noqa: E402


class OtaParseTest(unittest.TestCase):
    def test_activation_and_websocket(self):
        r = ota.parse_response(json.dumps({
            "activation": {"code": "123456", "message": "xiaozhi.me\n123456", "timeout_ms": 30000},
            "websocket": {"url": "wss://x/ws", "token": "t"},
            "firmware": {"version": "9.9.9", "url": "http://ignored"},
        }))
        self.assertTrue(r.needs_activation)
        self.assertEqual(r.activation_code, "123456")
        self.assertEqual(r.websocket["url"], "wss://x/ws")

    def test_no_activation(self):
        r = ota.parse_response('{"websocket": {"url": "wss://x"}}')
        self.assertFalse(r.needs_activation)


class FakeOta(BaseHTTPRequestHandler):
    activated = False
    calls: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        FakeOta.calls.append((self.path, dict(self.headers), json.loads(body)))
        if self.path.endswith("/activate"):
            code = 200 if FakeOta.activated else 202
            self.send_response(code)
            self.end_headers()
            return
        data = {"websocket": {"url": "ws://127.0.0.1:1/", "token": "tok"}}
        if not FakeOta.activated:
            data["activation"] = {"code": "424242", "message": "xiaozhi.me"}
        out = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class OtaClientTest(unittest.TestCase):
    def test_flow(self):
        srv = HTTPServer(("127.0.0.1", 0), FakeOta)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = ota.OtaClient(f"http://127.0.0.1:{srv.server_port}/xiaozhi/ota/", "aa:bb:cc:dd:ee:ff",
                              "uuid-1", "alice-station-midi")
            r = c.check_version()
            self.assertEqual(r.activation_code, "424242")
            self.assertIsNone(c.activate())
            FakeOta.activated = True
            self.assertTrue(c.activate())
            self.assertFalse(c.check_version().needs_activation)
            path, headers, body = FakeOta.calls[0]
            self.assertEqual(headers["Device-Id"], "aa:bb:cc:dd:ee:ff")
            self.assertEqual(headers["Client-Id"], "uuid-1")
            self.assertEqual(body["mac_address"], "aa:bb:cc:dd:ee:ff")
            self.assertEqual(FakeOta.calls[1][0], "/xiaozhi/ota/activate")
        finally:
            srv.shutdown()
            srv.server_close()


class McpTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.m = McpServer("test")

        @self.m.add("self.audio_speaker.set_volume", "vol", {"volume": prop("integer")})
        def _v(volume):
            return volume

        @self.m.add("self.secret", "hidden", user_only=True)
        async def _s():
            return True

        @self.m.add("self.fail", "boom")
        def _f():
            raise ValueError("нет")

    async def test_initialize(self):
        r = await self.m.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["result"]["protocolVersion"], "2024-11-05")

    async def test_list_hides_user_only(self):
        r = await self.m.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in r["result"]["tools"]]
        self.assertIn("self.audio_speaker.set_volume", names)
        self.assertNotIn("self.secret", names)
        r = await self.m.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                                 "params": {"withUserTools": True}})
        self.assertIn("self.secret", [t["name"] for t in r["result"]["tools"]])

    async def test_call(self):
        r = await self.m.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                 "params": {"name": "self.audio_speaker.set_volume", "arguments": {"volume": 40}}})
        self.assertEqual(r["result"]["content"][0]["text"], "40")
        r = await self.m.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                 "params": {"name": "self.secret", "arguments": {}}})
        self.assertEqual(r["result"]["content"][0]["text"], "true")
        r = await self.m.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                 "params": {"name": "self.fail", "arguments": {}}})
        self.assertTrue(r["result"]["isError"])
        r = await self.m.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                 "params": {"name": "nope"}})
        self.assertEqual(r["error"]["code"], -32601)
        r = await self.m.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                 "params": {"name": "self.audio_speaker.set_volume", "arguments": {}}})
        self.assertEqual(r["error"]["code"], -32602)

    async def test_notification_no_reply(self):
        self.assertIsNone(await self.m.handle({"jsonrpc": "2.0", "method": "notifications/x"}))


class ProtocolTest(unittest.TestCase):
    def test_hello_and_auth(self):
        h = build_hello(AudioParams(), 1)
        self.assertEqual(h["audio_params"], {"format": "opus", "sample_rate": 16000, "channels": 1,
                                             "frame_duration": 60})
        self.assertTrue(h["features"]["mcp"])
        self.assertEqual(auth_header("abc"), "Bearer abc")
        self.assertEqual(auth_header("Bearer abc"), "Bearer abc")


class OpusTest(unittest.TestCase):
    def test_roundtrip(self):
        try:
            enc, dec = opus.Encoder(16000), opus.Decoder(16000)
        except OSError as e:
            self.skipTest(str(e))
        from alice_station.audio import tone
        pcm = tone(440, 60, 16000)
        pkt = enc.encode(pcm)
        self.assertGreater(len(pkt), 0)
        self.assertEqual(len(dec.decode(pkt)), len(pcm))


class LightsTest(unittest.TestCase):
    def test_sysfs_and_fb(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for c in ("red", "green", "blue"):
                for i in range(device.RING):
                    (root / f"cucumber:{c}:ring{i}").mkdir()
            (root / "cucumber:white:clock-a1").mkdir()
            (root / "cucumber:white:clock-deg").mkdir()
            l = device.Lights(root=root, brightness=128, fb_ring=str(root / "fb0"))
            l.fill(255, 0, 10)
            self.assertEqual((root / "cucumber:red:ring5/brightness").read_text(), "128")
            self.assertEqual((root / "cucumber:blue:ring5/brightness").read_text(), "5")
            l.backend, l.order = "fb", "bgr"
            l.fill(255, 0, 0)
            frame = (root / "fb0").read_bytes()
            self.assertEqual(len(frame), device.RING * 3)
            self.assertEqual(frame[:3], bytes([0, 0, 128]))
            clk = device.Clock(root)
            self.assertEqual(clk.segments(), ["a1", "deg"])
            self.assertEqual(clk.set(None, True), 2)


# ---------------- полный диалог с фейковым сервером ----------------

class FakePlayer:
    def __init__(self):
        self.pcm = bytearray()

    async def write(self, pcm, rate):
        self.pcm += pcm

    async def finish(self):
        pass

    async def stop(self):
        pass

    async def play_pcm(self, pcm, rate):
        pass


class SessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_dialog(self):
        try:
            opus.lib()
        except OSError as e:
            self.skipTest(str(e))
        from alice_station.app import App, IDLE, LISTENING, SPEAKING
        from alice_station.audio import tone

        received = {"json": [], "audio": 0, "headers": None}
        enc = opus.Encoder(24000)
        mcp_done = asyncio.Event()
        listen_again = asyncio.Event()

        async def handler(ws):
            received["headers"] = dict(ws.request.headers)
            async for raw in ws:
                if isinstance(raw, bytes):
                    received["audio"] += 1
                    continue
                msg = json.loads(raw)
                received["json"].append(msg)
                if msg["type"] == "hello":
                    await ws.send(json.dumps({"type": "hello", "transport": "websocket", "session_id": "s1",
                                              "audio_params": {"sample_rate": 24000, "frame_duration": 60}}))
                    await ws.send(json.dumps({"type": "mcp", "payload": {
                        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "self.voice.list_profiles", "arguments": {}}}}))
                elif msg["type"] == "mcp":
                    mcp_done.set()
                elif msg["type"] == "listen" and msg["state"] == "start":
                    if len([m for m in received["json"] if m.get("state") == "start"]) == 1:
                        await ws.send(json.dumps({"type": "stt", "text": "привет"}))
                        await ws.send(json.dumps({"type": "tts", "state": "start"}))
                        await ws.send(json.dumps({"type": "tts", "state": "sentence_start", "text": "Здравствуй!"}))
                        for _ in range(3):
                            await ws.send(enc.encode(tone(440, 60, 24000)))
                        await ws.send(json.dumps({"type": "tts", "state": "stop"}))
                    else:
                        listen_again.set()

        async with websockets.serve(handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            with tempfile.TemporaryDirectory() as d:
                os.environ["ALICE_STATION_DIR"] = d
                cfg = config.load(Path(d) / "config.toml")
                cfg.data["led"]["enabled"] = False
                cfg.data["panel"]["enabled"] = False
                cfg.data["device"]["mac"] = "aa:bb:cc:00:11:22"
                cfg.state["websocket"] = {"url": f"ws://127.0.0.1:{port}/", "token": "tok"}
                app = App(cfg)
                app.player = FakePlayer()
                await app.start_session()
                self.assertEqual(app.state, LISTENING)
                # микрофон: шлём кадры, пока слушаем
                for _ in range(2):
                    await app.conn.send_audio(app._encoder.encode(tone(300, 60, 16000)))
                await asyncio.wait_for(mcp_done.wait(), 5)
                await asyncio.wait_for(listen_again.wait(), 5)
                self.assertEqual(app.state, LISTENING)
                self.assertEqual(app.last_stt, "привет")
                self.assertEqual(app.last_reply, "Здравствуй!")
                self.assertEqual(len(app.player.pcm), 3 * 1440 * 2)
                await app.end_session()
                self.assertEqual(app.state, IDLE)

        h = received["headers"]
        self.assertEqual(h["authorization"], "Bearer tok")
        self.assertEqual(h["device-id"], "aa:bb:cc:00:11:22")
        hello = received["json"][0]
        self.assertEqual(hello["type"], "hello")
        listen = [m for m in received["json"] if m["type"] == "listen"][0]
        self.assertEqual(listen, {"session_id": "s1", "type": "listen", "state": "start", "mode": "auto"})
        mcp = [m for m in received["json"] if m["type"] == "mcp"][0]
        self.assertIn("cloud", mcp["payload"]["result"]["content"][0]["text"])
        self.assertEqual(received["audio"], 2)


if __name__ == "__main__":
    unittest.main()
