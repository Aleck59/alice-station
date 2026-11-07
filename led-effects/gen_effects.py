#!/usr/bin/env python3
"""Генератор эффектов кольца Станции Миди (.led для maind).

Формат (проверен на колонке 2026-11-05): одна строка — один кадр,
24 цвета RRGGBB через пробел и длительность кадра в мс. Строка `loop`
отмечает начало повторяющейся части; кадры выше неё играют один раз.

Запуск:  python3 gen_effects.py [папка_вывода [папка_штатных_ring]]
         (по умолчанию ./build; со второй папкой ещё и перекрашивает штатные
         intonation_spotting / alice_listening_out под каждую тему)
Результат: build/themes/<тема>/ring/<имя>.led — темы для состояний Алисы
и build/extra/ring/<имя>.led — отдельные эффекты-подсветки.
Только стандартная библиотека, работает и на колонке (/opt/bin/python3).
"""
from __future__ import annotations

import colorsys
import math
import os
import random
import sys

N = 24          # светодиодов в кольце
FRAME_MS = 20   # 50 кадров/с, как у штатных анимаций

Color = tuple  # (r, g, b), 0..1


def hexc(c: Color) -> str:
    return "".join(f"{max(0, min(255, round(v * 255))):02x}" for v in c)


def hsv(h: float, s: float = 1.0, v: float = 1.0) -> Color:
    return colorsys.hsv_to_rgb(h % 1.0, s, v)


def scale(c: Color, k: float) -> Color:
    return (c[0] * k, c[1] * k, c[2] * k)


def mix(a: Color, b: Color, t: float) -> Color:
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))  # type: ignore[return-value]


def ring_dist(i: float, j: float) -> float:
    d = abs(i - j) % N
    return min(d, N - d)


class Pattern:
    def __init__(self) -> None:
        self.intro: list[str] = []
        self.loop: list[str] = []

    def add(self, frame: list[Color], ms: int = FRAME_MS, intro: bool = False) -> None:
        assert len(frame) == N
        line = " ".join(hexc(c) for c in frame) + f" {ms}"
        (self.intro if intro else self.loop).append(line)

    def text(self) -> str:
        lines = self.intro + (["loop"] + self.loop if self.loop else [])
        return "\n".join(lines) + "\n"


# --- строительные блоки -------------------------------------------------

def fill_intro(p: Pattern, color_at, frames: int = 20) -> None:
    """Кольцо «набирается» от 12 часов в обе стороны (как штатное слушание)."""
    for f in range(frames):
        reach = (f + 1) / frames * (N / 2)
        frame = []
        for i in range(N):
            d = ring_dist(i, 0)
            k = max(0.0, min(1.0, reach - d + 1))
            # отрицательные кадры: цвета вступления плавно переходят в цикл
            frame.append(scale(color_at(i, f - frames), k))
        p.add(frame, intro=True)


def comet(color_at, frames: int, tail: float = 8, heads: int = 1, base: float = 0.08) -> Pattern:
    p = Pattern()
    for f in range(frames):
        pos = f / frames * N
        frame = []
        for i in range(N):
            k = base
            for h in range(heads):
                hp = (pos + h * N / heads) % N
                d = (hp - i) % N  # хвост позади головы
                if d < tail:
                    k = max(k, (1 - d / tail) ** 2)
            frame.append(scale(color_at(i, f), k))
        p.add(frame)
    return p


def breathe(color_at, frames: int, lo: float = 0.15, hi: float = 1.0) -> Pattern:
    p = Pattern()
    for f in range(frames):
        # начинаем с максимума: так цикл стыкуется со вступлением «набор кольца»
        k = lo + (hi - lo) * (0.5 + 0.5 * math.cos(2 * math.pi * f / frames))
        p.add([scale(color_at(i, f), k) for i in range(N)])
    return p


def wave(color_at, frames: int, waves: int = 2, lo: float = 0.1) -> Pattern:
    p = Pattern()
    for f in range(frames):
        ph = 2 * math.pi * f / frames
        frame = []
        for i in range(N):
            k = lo + (1 - lo) * (0.5 + 0.5 * math.sin(2 * math.pi * waves * i / N - ph))
            frame.append(scale(color_at(i, f), k))
        p.add(frame)
    return p


def speak(color_at, frames: int, seed: int) -> Pattern:
    """«Речь»: мягко пульсирующее кольцо с бегущей волной, нерегулярная амплитуда."""
    rnd = random.Random(seed)
    assert frames % 8 == 0
    knots = [rnd.uniform(0.35, 1.0) for _ in range(frames // 8 + 1)]
    knots[-1] = knots[0]  # кадр frames совпадает с кадром 0 → бесшовный цикл
    p = Pattern()
    for f in range(frames):
        x = f / 8
        a = knots[int(x)] + (knots[int(x) + 1] - knots[int(x)]) * (x - int(x))
        ph = 2 * math.pi * f / frames
        frame = []
        for i in range(N):
            w = 0.5 + 0.5 * math.sin(2 * math.pi * 3 * i / N + ph * 3)
            frame.append(scale(color_at(i, f), 0.15 + 0.85 * a * (0.55 + 0.45 * w)))
        p.add(frame)
    return p


# --- темы для состояний Алисы ------------------------------------------

def retime(color_at, native: float, frames: int):
    """Подогнать период цветовой анимации (native кадров) под длину цикла,
    чтобы последний кадр цикла плавно переходил в первый."""
    return lambda i, f: color_at(i, f * native / frames)


def theme(colors_listen, colors_think, colors_speak, seed: int) -> dict[str, Pattern]:
    colors_listen = retime(colors_listen, 100, 75)
    listen = breathe(colors_listen, 75, lo=0.55)  # ~1,5 с, как у штатной
    fill_intro(listen, colors_listen)
    think = comet(colors_think, 60, tail=10, heads=2, base=0.05)
    sp = speak(colors_speak, 160, seed)
    return {"alice_listening": listen, "alice_thinking": think, "alice_speaking": sp}


def grad(a: Color, b: Color, drift: int = 0):
    """Градиент по кругу a→b→a, медленно вращается (drift кадров на оборот)."""
    def f(i: int, fr: int) -> Color:
        off = (fr / drift * N) if drift else 0
        t = 0.5 - 0.5 * math.cos(2 * math.pi * ((i + off) % N) / N)
        return mix(a, b, t)
    return f


def rainbow(speed: int = 0, sat: float = 1.0):
    def f(i: int, fr: int) -> Color:
        return hsv(i / N + (fr / speed if speed else 0), sat)
    return f


def solid(c: Color):
    return lambda i, fr: c


HEX = lambda s: tuple(int(s[k:k + 2], 16) / 255 for k in (0, 2, 4))  # noqa: E731

THEMES = {
    # северное сияние: бирюза и фиолет
    "aurora": theme(grad(HEX("00ffa0"), HEX("6a00ff"), 100), grad(HEX("00e5ff"), HEX("8000ff")),
                    grad(HEX("00ff80"), HEX("b000ff"), 160), 1),
    # огонь: оранжевый, красный, жёлтый
    "fire": theme(grad(HEX("ff3000"), HEX("ffa000"), 100), solid(HEX("ff5000")),
                  grad(HEX("ff1800"), HEX("ffc000"), 160), 2),
    # океан: синий и голубой
    "ocean": theme(grad(HEX("0030ff"), HEX("00d0ff"), 100), solid(HEX("00a0ff")),
                   grad(HEX("0010ff"), HEX("00ffd0"), 160), 3),
    # радуга
    "rainbow": theme(rainbow(100), rainbow(60), rainbow(160), 4),
    # сакура: розовый и белый
    "sakura": theme(grad(HEX("ff3080"), HEX("ffc0e0"), 100), solid(HEX("ff4090")),
                    grad(HEX("ff2070"), HEX("ffb0ff"), 160), 5),
}


# --- отдельные эффекты-подсветки ----------------------------------------

def party() -> Pattern:
    # все движения — целое число оборотов за 150 кадров, поэтому цикл бесшовный
    p = Pattern()
    for f in range(150):
        t = f / 150
        frame = []
        for i in range(N):
            k = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(2 * math.pi * (6 * t + i / 6)))
            h = i / N + t + 0.18 * math.sin(2 * math.pi * (3 * t + i / 5))
            frame.append(scale(hsv(h), k))
        p.add(frame)
    return p


def romantic() -> Pattern:
    return wave(grad(HEX("ff0040"), HEX("ff60a0"), 200), 200, waves=1, lo=0.25)


def polar_lights() -> Pattern:
    p = Pattern()
    for f in range(250):
        t = 2 * math.pi * f / 250
        frame = []
        for i in range(N):
            a = 2 * math.pi * i / N
            v = (math.sin(a * 2 + t) + math.sin(a * 3 - 2 * t) + 2) / 4
            c = mix(HEX("00ff60"), HEX("7000ff"), 0.5 + 0.5 * math.sin(a - t))
            frame.append(scale(c, 0.1 + 0.9 * v ** 1.5))
        p.add(frame)
    return p


def color_shine() -> Pattern:
    return comet(rainbow(200), 200, tail=14, heads=3, base=0.12)


def summer() -> Pattern:
    return wave(grad(HEX("ffb000"), HEX("ff5000"), 150), 150, waves=1, lo=0.4)


def game() -> Pattern:
    p = Pattern()
    cols = [HEX("ff0000"), HEX("00ff00"), HEX("0060ff"), HEX("ffff00")]
    for f in range(96):  # 48 шагов сдвига = 2 оборота по 4 цвета → бесшовно
        frame = []
        for i in range(N):
            q = (i + f // 2) // 6 % 4
            blink = 1.0 if (f // 15) % 2 == 0 or i % 6 else 0.3
            frame.append(scale(cols[q], blink))
        p.add(frame)
    return p


def candle() -> Pattern:
    rnd = random.Random(11)
    lv = [0.7] * N
    levels = []
    for f in range(200):
        g = 0.75 + 0.25 * math.sin(f / 7) * rnd.uniform(0.5, 1)
        for i in range(N):
            lv[i] += (g * rnd.uniform(0.8, 1.05) - lv[i]) * 0.3
        levels.append(list(lv))
    # бесшовный цикл: последние 25 кадров плавно переходят в первый
    for k in range(25):
        f = len(levels) - 25 + k
        t = (k + 1) / 25
        levels[f] = [a + (b - a) * t for a, b in zip(levels[f], levels[0])]
    p = Pattern()
    for row in levels:
        p.add([scale(HEX("ff7010"), max(0.2, min(1, v))) for v in row], ms=40)
    return p


EXTRA = {
    "party": party(),
    "romantic": romantic(),
    "polar_lights": polar_lights(),
    "color_shine": color_shine(),
    "summer": summer(),
    "game": game(),
    "rainbow_spin": comet(rainbow(120), 120, tail=24, heads=1, base=1.0),
    "candle_soft": candle(),
}


# --- перекраска штатных анимаций под тему -------------------------------
# Пробуждение (intonation_spotting) и затухание (alice_listening_out) берём из
# штатных файлов и перекрашиваем: яркость каждого светодиода сохраняется,
# оттенок берётся из темы. Так тайминги остаются родными.
RECOLOR = ("intonation_spotting", "alice_listening_out", "alice_listening",
           "bluetooth_connected", "bluetooth_disconnected", "bluetooth_pairing",
           "bluetooth_playing", "player_continue", "player_next_track",
           "player_pause", "player_previous_track")


def bt_playing_loop(color_at) -> str:
    """Штатный bluetooth_playing — вспышка и застывшее синее кольцо.
    У нас после вступления — медленная волна цветов темы, пока играет Bluetooth."""
    p = wave(retime(color_at, 100, 150), 150, waves=1, lo=0.6)
    return "loop\n" + "\n".join(p.loop) + "\n"


def crossfade(last_line: str, first_line: str, steps: int = 20) -> str:
    """Кадры плавного перехода между двумя строками .led (для вступление→цикл)."""
    a = [x for x in last_line.split()[:N]]
    b = [x for x in first_line.split()[:N]]
    ms = first_line.split()[N]
    rows = []
    for k in range(1, steps + 1):
        t = k / (steps + 1)
        row = []
        for ha, hb in zip(a, b):
            ca = [int(ha[j:j + 2], 16) for j in (0, 2, 4)]
            cb = [int(hb[j:j + 2], 16) for j in (0, 2, 4)]
            row.append("".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb)))
        rows.append(" ".join(row) + " " + ms)
    return "\n".join(rows) + "\n"

TINT = {
    "aurora": grad(HEX("00ffa0"), HEX("6a00ff"), 100),
    "fire": grad(HEX("ff3000"), HEX("ffa000"), 100),
    "ocean": grad(HEX("0030ff"), HEX("00d0ff"), 100),
    "rainbow": rainbow(100),
    "sakura": grad(HEX("ff3080"), HEX("ffc0e0"), 100),
}


def recolor(text: str, color_at, native: float = 100) -> str:
    """Цвета движутся с периодом, равным длине цикла (после `loop`), а кадры
    вступления — «до нуля», поэтому и переход вступление→цикл, и стык цикла плавные."""
    lines = text.splitlines()
    frames = [k for k, ln in enumerate(lines) if len(ln.split()) == N + 1]
    loop_at = lines.index("loop") if "loop" in lines else -1
    intro = sum(1 for k in frames if k < loop_at)
    period = max(1, len(frames) - intro)
    out, f = [], 0
    for line in lines:
        parts = line.split()
        if len(parts) != N + 1:
            out.append(line)  # loop, smooth 1, no_size_optimization ...
            continue
        frame = []
        for i, h in enumerate(parts[:N]):
            k = max(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)) / 255
            frame.append(hexc(scale(color_at(i, (f - intro) * native / period), k)))
        out.append(" ".join(frame) + " " + parts[N])
        f += 1
    return "\n".join(out) + "\n"


def write(out: str, group: str, name: str, p: Pattern) -> None:
    d = os.path.join(out, group, "ring")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name + ".led"), "w", newline="\n") as fh:
        fh.write(p.text())


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "build")
    for t, pats in THEMES.items():
        for name, p in pats.items():
            write(out, os.path.join("themes", t), name, p)
    for name, p in EXTRA.items():
        write(out, "extra", name, p)
    # палитра темы для чужих визуализаторов (эквалайзер ytm-alice):
    # 24 цвета по кругу, полная яркость; ledtheme кладёт её в /data/led-effects/palette.json
    import json
    for t, fn in TINT.items():
        with open(os.path.join(out, "themes", t, "palette.json"), "w") as fh:
            json.dump({"theme": t, "ring": [hexc(fn(i, 0)) for i in range(N)]}, fh)
        # palette.txt: 4 цвета от баса к верхам (формат, который читает ytm-alice)
        with open(os.path.join(out, "themes", t, "palette.txt"), "w") as fh:
            fh.write("".join(hexc(fn(i, 0)) + "\n" for i in (0, 4, 8, 12)))
    with open(os.path.join(out, "palette.stock.json"), "w") as fh:
        json.dump({"theme": "stock", "ring": ["370fff"] * N}, fh)
    with open(os.path.join(out, "palette.stock.txt"), "w") as fh:
        fh.write("370fff\n1b077f\n")
    stock = sys.argv[2] if len(sys.argv) > 2 else ""
    if stock:
        for name in RECOLOR:
            src = os.path.join(stock, name + ".led")
            if not os.path.isfile(src):
                continue
            with open(src) as fh:
                text = fh.read()
            if name == "bluetooth_playing":
                # штатный: вступление, `loop` и один застывший кадр — застывшую
                # часть заменим волной темы, перекрашиваем только вступление
                ls = text.splitlines()
                if "loop" in ls:
                    text = "\n".join(ls[:ls.index("loop")]) + "\n"
            for t, fn in TINT.items():
                if name == "alice_listening":
                    continue  # у тем своя анимация слушания
                body = recolor(text, fn)
                if name == "bluetooth_playing":
                    # штатный: вступление, затем `loop` и один застывший кадр —
                    # застывшую часть заменяем волной цветов темы
                    tail = bt_playing_loop(fn)
                    last = [ln for ln in body.splitlines() if len(ln.split()) == N + 1][-1]
                    body += crossfade(last, tail.splitlines()[1]) + tail
                with open(os.path.join(out, "themes", t, "ring", name + ".led"), "w", newline="\n") as fh:
                    fh.write(body)
    print("готово:", out)


if __name__ == "__main__":
    main()
