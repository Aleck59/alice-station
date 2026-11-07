#!/usr/bin/env python3
"""Проверка стыков .led: последний кадр цикла → первый и вступление → цикл
не должны «прыгать» сильнее обычного шага между соседними кадрами.
  python3 seamcheck.py file.led ...   (код возврата 1, если есть скачки)"""
import sys


def frames(path):
    out, loop_at = [], None
    for ln in open(path):
        p = ln.split()
        if p == ["loop"]:
            loop_at = len(out)
        elif len(p) == 25:
            out.append([int(h[k:k + 2], 16) for h in p[:24] for k in (0, 2, 4)])
    return out, loop_at


def d(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


bad = 0
for path in sys.argv[1:]:
    fr, la = frames(path)
    if la is None or len(fr) - la < 2:
        continue
    lp = fr[la:]
    step = max(d(lp[k], lp[k + 1]) for k in range(len(lp) - 1))
    seam = d(lp[-1], lp[0])
    msg = []
    if seam > max(step * 1.5, 12):
        msg.append(f"стык цикла {seam} (шаг {step})")
    if la > 0 and d(fr[la - 1], fr[la]) > max(step * 1.5, 12):
        msg.append(f"вступление→цикл {d(fr[la - 1], fr[la])} (шаг {step})")
    if msg:
        bad += 1
        print(path, "; ".join(msg))
sys.exit(1 if bad else 0)
