"""Режимы «Алиса ⇄ ассистент», IPC Алисы и слово активации — без колонки."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alice_station import config, kws, quasar_ipc as q  # noqa: E402
from alice_station.app import App, MODE_ALICE, MODE_ASSISTANT, phrase_match  # noqa: E402


def state_frame(status: int, text: str = "", rid: str = "") -> bytes:
    inner = b""
    if status:
        inner += q.pb_int(1, status)
    if text:
        inner += q.pb_bytes(2, text.encode())
    if rid:
        inner += q.pb_bytes(7, rid.encode())
    return q.frame(q.pb_bytes(252, inner) + q.pb_int(253, 3))


class ProtoTest(unittest.TestCase):
    def test_parse_state(self):
        body = state_frame(3, "алиса включи ассистента", "r1")[4:]
        ev = q.parse_event(body)
        self.assertEqual((ev.status, ev.text, ev.request_id), (3, "алиса включи ассистента", "r1"))
        self.assertEqual(q.parse_event(state_frame(0)[4:]).status, 0)
        self.assertIsNone(q.parse_event(q.vocalize_frame("x")[4:]))

    def test_vocalize_matches_midipult(self):
        # байты из MidiPult cgi-bin/speak.py (проверено там на Миди)
        f = q.vocalize_frame("Привет")
        self.assertEqual(int.from_bytes(f[:4], "little"), len(f) - 4)
        top = q.pb_parse(f[4:])
        self.assertEqual(top[253], 210)
        remote = q.pb_parse(top[252])
        self.assertEqual(remote[7], b"AliceCapability")
        call = q.pb_parse(remote[8])
        self.assertEqual((call[1], call[3]), (18, 1))
        self.assertEqual(q.pb_parse(call[13])[1], "Привет".encode())


class PhraseTest(unittest.TestCase):
    def test_match(self):
        self.assertTrue(phrase_match("Алиса, включи ассистента!", ["включи ассистента"]))
        self.assertTrue(phrase_match("Катя, верни к Алисе", ["верни к алисе"]))
        self.assertTrue(phrase_match("верни  к   Алисе", ["верни к алисе"]))
        self.assertFalse(phrase_match("включи музыку", ["включи ассистента"]))

    def test_kws_pick(self):
        words = ["катя", "лариса"]
        res = {"result": [{"word": "Катя", "conf": 0.9}], "text": "катя"}
        self.assertEqual(kws.pick(res, words, 0.75), "катя")
        self.assertIsNone(kws.pick({"result": [{"word": "катя", "conf": 0.5}]}, words, 0.75))
        self.assertIsNone(kws.pick({"result": [{"word": "[unk]", "conf": 1}]}, words, 0.75))
        self.assertIsInstance(kws.create({"engine": "none"}), kws.NullSpotter)
        self.assertIsInstance(kws.create({"engine": "vosk", "model": "/nope", "words": words}), kws.NullSpotter)


class FakeVolume:
    def __init__(self):
        self.value, self.history = 50, []

    def get(self):
        return self.value

    def set(self, v):
        self.value = v
        self.history.append(v)
        return v


class FakePlayer:
    async def play_pcm(self, *a):
        pass

    async def stop(self):
        pass

    async def finish(self):
        pass


class ModeSwitchTest(unittest.IsolatedAsyncioTestCase):
    async def test_alice_phrase_enables_assistant_and_back(self):
        sent: list[bytes] = []
        clients: list[asyncio.StreamWriter] = []

        async def handler(reader, writer):
            clients.append(writer)
            try:
                while True:
                    n = int.from_bytes(await reader.readexactly(4), "little")
                    sent.append(await reader.readexactly(n))
            except asyncio.IncompleteReadError:
                pass

        srv = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load(Path(d) / "config.toml")
            cfg.data["assistant"]["ipc_port"] = port
            cfg.data["wakeword"]["engine"] = "none"
            cfg.data["led"]["enabled"] = False
            app = App(cfg)
            app.volume = FakeVolume()
            app.player = FakePlayer()
            self.assertEqual(app.mode, MODE_ALICE)
            self.assertFalse(app._mic_wanted.is_set())
            ipc_task = asyncio.create_task(app.ipc.run())
            for _ in range(50):
                if clients:
                    break
                await asyncio.sleep(0.02)
            w = clients[0]
            # Алиса слушает → распознала фразу (дважды, как partial+final) → свободна
            w.write(state_frame(1))
            w.write(state_frame(3, "алиса включи ассистента", "req-1"))
            w.write(state_frame(3, "алиса включи ассистента", "req-1"))
            await w.drain()
            await asyncio.sleep(0.2)
            self.assertEqual(app.volume.history, [0])  # ответ Алисы заглушён
            w.write(state_frame(0))
            await w.drain()
            for _ in range(100):
                if app.mode == MODE_ASSISTANT:
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(app.mode, MODE_ASSISTANT)
            self.assertEqual(app.volume.history, [0, 50])  # громкость вернулась
            self.assertTrue(app._mic_wanted.is_set())
            self.assertIn(q.CANCEL_FRAME[4:], sent)

            def vocalized():
                envs = [q.pb_parse(b) for b in sent]
                return [e for e in envs if e.get(253) == 210 and 8 in q.pb_parse(e[252])
                        and q.pb_parse(q.pb_parse(e[252])[8]).get(1) == 18]

            # режим переключается до того, как кадр озвучки дойдёт до сервера
            for _ in range(100):
                if vocalized():
                    break
                await asyncio.sleep(0.02)
            vocal = vocalized()
            self.assertEqual(len(vocal), 1)  # «Катя на связи…»
            self.assertIn("Катя на связи".encode(), sent[-1])

            # ассистент услышал «Катя, верни к Алисе»
            await app._on_json({"type": "stt", "text": "Катя, верни к Алисе."})
            for _ in range(100):
                if app.mode == MODE_ALICE:
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(app.mode, MODE_ALICE)
            self.assertFalse(app._mic_wanted.is_set())
            for _ in range(100):
                if sent and "Возвращаю Алису".encode() in sent[-1]:
                    break
                await asyncio.sleep(0.02)
            self.assertIn("Возвращаю Алису".encode(), sent[-1])

            # в режиме Алисы обычные фразы не трогаем
            n = len(sent)
            w.write(state_frame(3, "какая погода", "req-2"))
            await w.drain()
            await asyncio.sleep(0.1)
            self.assertEqual(len(sent), n)
            self.assertEqual(app.mode, MODE_ALICE)

            ipc_task.cancel()
            srv.close()

    async def test_mcp_tool_returns_to_alice(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load(Path(d) / "config.toml")
            cfg.data["wakeword"]["engine"] = "none"
            cfg.data["led"]["enabled"] = False
            cfg.data["assistant"]["ipc_port"] = 1
            app = App(cfg)
            app.player = FakePlayer()
            await app.set_mode(MODE_ASSISTANT, announce=False)
            r = await app.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "self.assistant.return_to_alice", "arguments": {}}})
            self.assertFalse(r["result"]["isError"])
            await asyncio.sleep(0.05)
            self.assertEqual(app.mode, MODE_ALICE)


if __name__ == "__main__":
    unittest.main()
