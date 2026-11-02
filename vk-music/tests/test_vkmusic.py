import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "deploy"))

from vkmusic import vk  # noqa: E402
from vkmusic.alice import Ducker  # noqa: E402
from vkmusic.player import Player  # noqa: E402
from vkmusic.server import DEFAULTS, Service, make_server  # noqa: E402
import scenarios  # noqa: E402


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class TokenTest(unittest.TestCase):
    def test_parse_blank_url(self):
        t = vk.parse_token("https://oauth.vk.com/blank.html#access_token=vk1.a.abc&expires_in=0&user_id=42")
        self.assertEqual(t, {"access_token": "vk1.a.abc", "user_id": 42})

    def test_parse_bare_token(self):
        tok = "vk1.a." + "x" * 60
        self.assertEqual(vk.parse_token(tok)["access_token"], tok)

    def test_parse_garbage(self):
        for bad in ("", "привет", "https://vk.com/feed"):
            with self.assertRaises(ValueError):
                vk.parse_token(bad)

    def test_auth_url(self):
        u = vk.auth_url()
        q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
        self.assertEqual(q["scope"], ["audio,offline"])
        self.assertEqual(q["response_type"], ["token"])

    def test_mp3_url(self):
        m3u8 = "https://cs1-2v4.vkuseraudio.net/s/v1/ac/AbCd/0a1b2c/audios/ff00aa11/index.m3u8?extra=x"
        self.assertEqual(vk.mp3_url(m3u8),
                         "https://cs1-2v4.vkuseraudio.net/s/v1/ac/AbCd/audios/ff00aa11.mp3?extra=x")
        self.assertEqual(vk.mp3_url("https://x/y.mp3"), "https://x/y.mp3")


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def item(i, url="https://x/{}.mp3"):
    return {"id": i, "owner_id": 1, "artist": f"A{i}", "title": f"T{i}", "duration": 100,
            "url": url.format(i) if url else ""}


class ClientTest(unittest.TestCase):
    def make(self, handler):
        calls = []

        def opener(req, timeout):
            params = urllib.parse.parse_qs(req.data.decode())
            method = req.full_url.rsplit("/", 1)[1]
            calls.append((method, params, req.headers.get("User-agent")))
            return FakeResp(json.dumps(handler(method, params)).encode())
        return vk.VkClient("tok", 1, opener=opener), calls

    def test_search_skips_unavailable(self):
        c, calls = self.make(lambda m, p: {"response": {"items": [item(1), item(2, url="")]}})
        tracks = c.search("кино")
        self.assertEqual([t.title for t in tracks], ["T1"])
        self.assertEqual(calls[0][1]["q"], ["кино"])
        self.assertIn("KateMobile", calls[0][2])

    def test_mix_falls_back_to_recommendations(self):
        def h(m, p):
            if m == "audio.getStreamMixAudios":
                return {"error": {"error_code": 3, "error_msg": "Unknown method"}}
            return {"response": {"items": [item(7)]}}
        c, calls = self.make(h)
        self.assertEqual([t.title for t in c.mix()], ["T7"])
        self.assertEqual([x[0] for x in calls], ["audio.getStreamMixAudios", "audio.getRecommendations"])

    def test_auth_error(self):
        c, _ = self.make(lambda m, p: {"error": {"error_code": 5, "error_msg": "invalid token"}})
        with self.assertRaises(vk.VkAuthError):
            c.my_tracks()


def tracks(n, start=0):
    return [vk.Track(f"T{i}", "A", f"u{i}", 1, i) for i in range(start, start + n)]


class PlayerTest(unittest.TestCase):
    def make(self, secs="5"):
        return Player(["sh", "-c", f"sleep {secs}", "{url}"], env={}, cwd=None)

    def test_queue_next_stop(self):
        p = self.make()
        p.play(tracks(3), "mine")
        self.assertTrue(wait_for(lambda: p.current and p.current.title == "T0"))
        p.next()
        self.assertTrue(wait_for(lambda: p.current and p.current.title == "T1"))
        p.pause()
        self.assertTrue(p.paused and not p.playing)
        p.resume()
        self.assertTrue(p.playing)
        p.prev()
        self.assertTrue(wait_for(lambda: p.current and p.current.title == "T0"))
        p.stop()
        self.assertTrue(wait_for(lambda: p.current is None))
        self.assertEqual(p.status()["queue_len"], 0)

    def test_refill_wave(self):
        p = self.make("0.05")
        batches = [tracks(2, 10), tracks(2, 20)]
        p.play(tracks(1), "wave", refill=lambda: batches.pop(0) if batches else [])
        self.assertTrue(wait_for(lambda: p.current is None and not batches and not p.queue, 5))
        self.assertEqual([t.title for t in p.history], ["T0", "T10", "T11", "T20", "T21"])

    def test_broken_tracks_stop(self):
        p = Player(["sh", "-c", "exit 1", "{url}"], env={}, cwd=None)
        p.play(tracks(10))
        self.assertTrue(wait_for(lambda: p.current is None and not p.queue, 5))
        self.assertIn("5 треков", p.last_error)


class DuckTest(unittest.TestCase):
    def test_duck(self):
        p = Player(["sh", "-c", "sleep 5", "{url}"], env={}, cwd=None)
        p.play(tracks(1))
        wait_for(lambda: p.current is not None)
        d = Ducker(None, p)
        d.step({"alice": "SPEAKING", "playing": False})
        self.assertTrue(p.paused)
        d.step({"alice": "IDLE", "playing": False})
        self.assertTrue(p.playing)
        d.step({"alice": "LISTENING"})
        d.step({"alice": "IDLE", "playing": True})  # Алиса включила своё — уступаем
        self.assertTrue(wait_for(lambda: p.current is None))
        p.stop()


class FakeVk:
    def __init__(self, token, uid, **kw):
        if token == "bad":
            raise AssertionError("не должно вызываться с плохим токеном")
        self.token, self.user_id = token, uid
        self.added = []

    def whoami(self):
        if self.token == "expired":
            raise vk.VkAuthError("вход недействителен")
        return {"id": 42, "name": "Тест"}

    def my_tracks(self, count=200):
        return tracks(3)

    def search(self, q, count=30):
        return tracks(2, 100) if q != "пусто" else []

    def mix(self, count=30):
        return tracks(2, 200)

    def add(self, t):
        self.added.append(t.key)


class FakeAlice:
    def __init__(self):
        self.said = []
        self.quiet = 0

    def say(self, t):
        self.said.append(t)

    def quiet_stock(self, **kw):
        self.quiet += 1


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cfg = dict(DEFAULTS, listen="127.0.0.1", port=0, data_dir=self.tmp, voice_confirm=True)
        self.player = Player(["sh", "-c", "sleep 5", "{url}"], env={}, cwd=None)
        self.alice = FakeAlice()
        self.svc = Service(cfg, player=self.player, alice=self.alice, client_factory=FakeVk)
        self.httpd = make_server(self.svc)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port

    def tearDown(self):
        self.player.stop()
        self.httpd.shutdown()
        self.httpd.server_close()

    def req(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data,
                                   headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def login(self):
        return self.req("/api/auth", {"url": "https://oauth.vk.com/blank.html#access_token=good&user_id=42"})

    def test_panel(self):
        with urllib.request.urlopen(self.base + "/", timeout=5) as r:
            self.assertIn("VK Музыка", r.read().decode())

    def test_not_connected_voice(self):
        code, d = self.req("/play/wave?voice=1")
        self.assertEqual(code, 401)
        self.assertTrue(self.alice.said)

    def test_login_saves_token_600(self):
        code, d = self.login()
        self.assertEqual((code, d["account"]["id"]), (200, 42))
        path = os.path.join(self.tmp, "token.json")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertTrue(self.req("/api/status")[1]["connected"])
        self.assertNotIn("good", json.dumps(self.req("/api/status")[1]))
        # после перезапуска токен подхватывается
        svc2 = Service(self.svc.cfg, player=self.player, alice=self.alice, client_factory=FakeVk)
        self.assertIsNotNone(svc2.vk)
        self.assertEqual(self.req("/api/logout", {})[0], 200)
        self.assertFalse(os.path.exists(path))

    def test_login_expired(self):
        code, d = self.req("/api/auth", {"url": "https://oauth.vk.com/blank.html#access_token=expired&user_id=1"})
        self.assertEqual(code, 401)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "token.json")))

    def test_voice_wave_and_controls(self):
        self.login()
        code, d = self.req("/play/wave?voice=1")
        self.assertEqual(code, 202)
        self.assertTrue(wait_for(lambda: self.player.current is not None))
        self.assertEqual(self.player.mode, "wave")
        self.assertEqual(self.alice.said, ["Включаю мою волну ВК"])
        self.assertEqual(self.alice.quiet, 1)
        self.assertTrue(self.req("/pause")[1]["paused"])
        self.assertEqual(self.req("/like")[0], 200)
        self.assertEqual(self.svc.vk.added, ["1_200"])
        self.req("/stop")
        self.assertTrue(wait_for(lambda: self.player.current is None))

    def test_search_and_play_keys(self):
        self.login()
        code, d = self.req("/api/search?q=" + urllib.parse.quote("кино"))
        self.assertEqual([t["key"] for t in d["tracks"]], ["1_100", "1_101"])
        self.assertEqual(self.req("/api/play", {"keys": ["1_101"]})[0], 200)
        self.assertTrue(wait_for(lambda: self.player.current and self.player.current.key == "1_101"))

    def test_voice_search_empty_says_error(self):
        self.login()
        self.req("/play/search?voice=1&q=" + urllib.parse.quote("пусто"))
        self.assertTrue(wait_for(lambda: any("ничего не нашлось" in s for s in self.alice.said)))

    def test_pin_required_from_lan(self):
        self.svc.cfg["pin"] = "1234"
        h = self.httpd.RequestHandlerClass
        orig = h._local
        h._local = lambda self: False
        try:
            self.assertEqual(self.login()[0], 403)
            self.assertEqual(self.req("/api/auth?pin=1234", {"url": "access_token=good&user_id=1"})[0], 200)
        finally:
            h._local = orig


def mp_normalize(text):
    """Копия normalize() из MidiPult service/voice_daemon.py."""
    import re
    low = (text or "").lower().replace("ё", "е")
    low = re.sub(r"[^a-zа-я0-9 ]+", " ", low)
    low = re.sub(r"\s+", " ", low).strip()
    low = re.sub(r"^(алиса|алеса|алекс|алиса )\s*", "", low)
    return low


class ScenarioPhrasesTest(unittest.TestCase):
    def find(self, text):
        n = mp_normalize(text)
        for name, path, phrases in scenarios.SCENARIOS:
            if any(mp_normalize(p) in n for p in phrases):
                return path
        return None

    def test_matching(self):
        self.assertEqual(self.find("Алиса, включи мою волну ВК"), "/play/wave")
        self.assertEqual(self.find("алиса включи музыку вконтакте"), "/play/wave")
        self.assertEqual(self.find("Алиса, включи мои треки в контакте"), "/play/mine?shuffle=1")
        self.assertEqual(self.find("Алиса, выключи ВК"), "/stop")
        self.assertEqual(self.find("Алиса, следующий трек ВК"), "/next")

    def test_stock_alice_untouched(self):
        for text in ("Алиса, включи мою волну", "Алиса, следующий трек", "Алиса, пауза",
                     "Алиса, включи музыку", "Алиса, выключи музыку"):
            self.assertIsNone(self.find(text), text)

    def test_names_fit_midipult(self):
        for name, _, phrases in scenarios.SCENARIOS:
            self.assertTrue(2 <= len(name) <= 40)
            self.assertTrue(phrases)


if __name__ == "__main__":
    unittest.main()
