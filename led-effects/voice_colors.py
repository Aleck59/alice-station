#!/usr/bin/env python3
"""Цвета кольца, когда Алиса говорит (визуализатор голоса maind).

Это не .led-файл: цвета берутся из quasar.cfg →
spectrogramAnimation → presets → voice (baseColor r/g/b, градиент в h/s/v).
Мы держим копию конфига в /data/led-effects/quasar.cfg, подключённую
mount --bind поверх /system/vendor/quasar/quasar.cfg, и меняем в ней
только оттенки пресета voice. Оригинал — /data/led-effects/quasar.cfg.orig.

  voice_colors.py <тема|stock> [orig] [copy]

Копия переписывается на месте (тот же inode — иначе bind-mount потеряется).
"""
from __future__ import annotations

import json
import sys

ORIG = "/data/led-effects/quasar.cfg.orig"
COPY = "/data/led-effects/quasar.cfg"

# base: (r, g, b); hues: center, middle, side; grad: оттенки gradientColors по порядку
PALETTES = {
    "aurora": {"base": (0, 255, 160), "hues": (270, 200, 165), "grad": (270, 270, 200, 165)},
    "fire": {"base": (255, 60, 0), "hues": (10, 30, 45), "grad": (5, 10, 30, 45)},
    "ocean": {"base": (0, 120, 255), "hues": (225, 200, 180), "grad": (230, 225, 200, 180)},
    "rainbow": {"base": (255, 0, 128), "hues": (0, 120, 240), "grad": (0, 90, 180, 270)},
    "sakura": {"base": (255, 48, 128), "hues": (330, 315, 300), "grad": (335, 330, 315, 300)},
}


def find_voice(cfg):
    """Ищет блок presets.voice внутри spectrogramAnimation на любом уровне."""
    stack = [cfg]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "spectrogramAnimation" in node:
                sa = node["spectrogramAnimation"]
                presets = sa.get("presets", {}) if isinstance(sa, dict) else {}
                if isinstance(presets, dict) and "voice" in presets:
                    return presets["voice"]
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    raise SystemExit("voice_colors: не нашёл spectrogramAnimation.presets.voice")


def find_colors(voice):
    """Блок, где лежат baseColor и градиент (может быть voice или voice.colors)."""
    for cand in (voice, voice.get("colors") if isinstance(voice, dict) else None):
        if isinstance(cand, dict) and "baseColor" in cand:
            return cand
    raise SystemExit("voice_colors: в пресете voice нет baseColor")


def apply(cfg, pal) -> None:
    c = find_colors(find_voice(cfg))
    r, g, b = pal["base"]
    c["baseColor"].update({"r": r, "g": g, "b": b})
    for key, h in zip(("gradientCenterColor", "gradientMiddleColor", "gradientSideColor"), pal["hues"]):
        if isinstance(c.get(key), dict) and "h" in c[key]:
            c[key]["h"] = h
    grad = c.get("gradientColors")
    if isinstance(grad, list):
        for item, h in zip(grad, pal["grad"]):
            if isinstance(item, dict) and "h" in item:
                item["h"] = h


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    theme = sys.argv[1]
    orig = sys.argv[2] if len(sys.argv) > 2 else ORIG
    copy = sys.argv[3] if len(sys.argv) > 3 else COPY
    with open(orig, "rb") as fh:
        raw = fh.read()
    if theme == "stock":
        data = raw
    else:
        if theme not in PALETTES:
            raise SystemExit(f"voice_colors: нет палитры '{theme}'")
        cfg = json.loads(raw)
        apply(cfg, PALETTES[theme])
        data = json.dumps(cfg, ensure_ascii=False, indent=4).encode() + b"\n"
        json.loads(data)  # проверка
    with open(copy, "r+b") as fh:  # на месте, без смены inode
        fh.write(data)
        fh.truncate()
    print(f"голос: {theme}")


if __name__ == "__main__":
    main()
