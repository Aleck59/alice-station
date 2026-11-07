#!/opt/bin/python3
"""ringproxy — эквалайзер Алисы и прочие штатные анимации в цветах темы.

maind рисует кольцо в /dev/fb0 (24×1, RGB), а светодиоды обычно сами
повторяют fb0 через триггер ядра `ledfb-ring`. Во время музыки maind
рисует настоящий эквалайзер (ring_spectrogram: ball/blink/disco/polar),
но всегда фиолетовым — цвета приходят с сервера.

Служба снимает триггер со светодиодов кольца, сама читает кадр из fb0
~50 раз в секунду, перекрашивает фиолетовые пиксели в цвета активной темы
(/data/led-effects/palette.json, яркость пикселя сохраняется) и пишет
результат в /sys/class/leds/cucumber:{red,green,blue}:ringN/brightness.
Непурпурные пиксели (наши темы, красная ошибка и т.п.) проходят как есть.

При выходе (SIGTERM/SIGINT) триггер возвращается. Если служба упала —
`ledtheme ringproxy off` или перезагрузка возвращают штатное поведение.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time

N = 24
FB = "/dev/fb0"
LEDS = "/sys/class/leds"
PALETTE = "/data/led-effects/palette.json"
TRIGGER = "ledfb-ring"
# Кто отвечает: мост (Quasar Bridge) пишет сюда "assistant" или "alice"
# на каждом решении маршрута (сторож в режиме observe пишет "alice").
# Кольцо РЕЧИ красится в тему только при свежем (< ROUTE_TTL с) "assistant",
# иначе остаётся штатным. Музыкальный эквалайзер и прочее — всегда в теме.
ROUTE = os.environ.get("QUASAR_ROUTE_FILE", "/var/run/alice-route")
ROUTE_TTL = 45
# Что сейчас рисует maind (речь или музыка) — по его журналу:
# "Creating spectrogram: pulsing_voice" — речь, "...: ball/disco/..." — музыка.
MAIND_LOG = "/data/quasar/daemons_logs/maind.log"


class LogTail:
    """Дочитывает новые строки журнала maind, переживает ротацию."""

    def __init__(self, path: str) -> None:
        self.path, self.fh, self.ino, self.buf = path, None, None, b""

    def lines(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        if self.fh is None or st.st_ino != self.ino or st.st_size < self.fh.tell():
            if self.fh:
                self.fh.close()
            self.fh, self.ino = open(self.path, "rb"), st.st_ino
            self.fh.seek(0, os.SEEK_END)
            return []
        data = self.fh.read(65536)
        if not data:
            return []
        self.buf += data
        *done, self.buf = self.buf.split(b"\n")
        return done


def assistant_route() -> bool:
    try:
        if time.time() - os.stat(ROUTE).st_mtime >= ROUTE_TTL:
            return False
        with open(ROUTE) as fh:
            return fh.read(16).strip() == "assistant"
    except OSError:
        return False
FPS = 50
CH = ("red", "green", "blue")


def led_path(ch: str, i: int, attr: str) -> str:
    return f"{LEDS}/cucumber:{ch}:ring{i}/{attr}"


def set_triggers(value: str) -> None:
    for i in range(N):
        for ch in CH:
            try:
                with open(led_path(ch, i, "trigger"), "w") as fh:
                    fh.write(value)
            except OSError as e:
                print(f"ringproxy: trigger {ch}{i}: {e}", file=sys.stderr)


def load_palette(path: str) -> list[tuple[int, int, int]] | None:
    try:
        with open(path) as fh:
            ring = json.load(fh)["ring"]
        pal = [tuple(int(h[k:k + 2], 16) for k in (0, 2, 4)) for h in ring]
        return pal if len(pal) == N else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def is_stock_purple(r: int, g: int, b: int) -> bool:
    """Штатный цвет Алисы 370fff (и его затемнения): синий доминирует,
    зелёного почти нет, красного заметно меньше синего."""
    return b > 8 and g * 3 <= b and r * 10 <= b * 6


def recolor(frame: bytes, pal) -> list[int]:
    out = []
    for i in range(N):
        r, g, b = frame[3 * i], frame[3 * i + 1], frame[3 * i + 2]
        if pal is not None and is_stock_purple(r, g, b):
            k = b / 255  # у 370fff максимум — синий канал
            pr, pg, pb = pal[i]
            out += [round(pr * k), round(pg * k), round(pb * k)]
        else:
            out += [r, g, b]
    return out


def main() -> None:
    fb = os.open(FB, os.O_RDONLY)
    fds = [[os.open(led_path(ch, i, "brightness"), os.O_WRONLY) for ch in CH] for i in range(N)]
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    pal, pal_mtime, pal_check = None, 0.0, 0.0
    last: list[int] = []
    full_at = 0.0
    log, voice = LogTail(MAIND_LOG), False
    set_triggers("none")
    try:
        period = 1.0 / FPS
        while running:
            t0 = time.monotonic()
            if t0 - pal_check > 2:
                pal_check = t0
                try:
                    m = os.stat(PALETTE).st_mtime
                except OSError:
                    m = -1
                if m != pal_mtime:
                    pal_mtime, pal = m, load_palette(PALETTE)
            for ln in log.lines():
                k = ln.find(b"Creating spectrogram: ")
                if k >= 0:
                    voice = b"voice" in ln[k:]
            stock_now = voice and not assistant_route()
            frame = os.pread(fb, 3 * N, 0)
            if len(frame) == 3 * N:
                vals = recolor(frame, None if stock_now else pal)
                # Пишем только изменившиеся каналы (быстро, без задержки кадра),
                # а раз в 0,5 с — все 72, на случай если контроллер что-то не применил.
                full = t0 - full_at > 0.5
                if full:
                    full_at = t0
                for j, v in enumerate(vals):
                    if full or not last or v != last[j]:
                        os.pwrite(fds[j // 3][j % 3], str(v).encode(), 0)
                last = vals
            dt = period - (time.monotonic() - t0)
            if dt > 0:
                time.sleep(dt)
    finally:
        set_triggers(TRIGGER)
        for row in fds:
            for fd in row:
                os.close(fd)
        os.close(fb)


if __name__ == "__main__":
    main()
