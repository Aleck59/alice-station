#!/opt/bin/python3
"""Голосовые команды VK Музыки как сценарии MidiPult.

    python3 scenarios.py install   — создать/обновить сценарии «ВК: …»
    python3 scenarios.py remove    — удалить их
    python3 scenarios.py list      — показать, что будет создано

Сценарий MidiPult срабатывает, если фраза (без «Алиса,») входит в распознанный
текст, и открывает адрес службы vk-music. ?voice=1 — служба гасит ответ и
музыку Алисы и озвучивает ошибки.
"""
import json
import sys
import urllib.request

PULT = "http://127.0.0.1:8055/cgi-bin/scenarios.py"
SVC = "http://127.0.0.1:8061"

VK = ("вк", "в контакте", "вконтакте", "vk")


def ph(*templates):
    return [t.format(vk=v) for t in templates for v in VK]


SCENARIOS = [
    ("ВК: моя волна", "/play/wave",
     ph("включи мою волну {vk}", "включи волну {vk}", "включи {vk} волну", "включи {vk} микс",
        "включи музыку {vk}", "включи {vk} музыку", "включи музыку из {vk}")),
    ("ВК: мои треки", "/play/mine?shuffle=1",
     ph("включи мои треки {vk}", "включи мою музыку {vk}", "включи мои песни {vk}")),
    ("ВК: пауза", "/pause", ph("пауза {vk}", "поставь {vk} на паузу", "останови {vk}")),
    ("ВК: продолжить", "/resume", ph("продолжи {vk}", "продолжай {vk}", "сними {vk} с паузы")),
    ("ВК: следующий", "/next", ph("следующий трек {vk}", "дальше {vk}", "переключи {vk}")),
    ("ВК: предыдущий", "/prev", ph("предыдущий трек {vk}", "назад {vk}")),
    ("ВК: выключить", "/stop", ph("выключи {vk}", "выключи музыку {vk}", "стоп {vk}")),
    ("ВК: лайк", "/like", ph("лайк {vk}", "добавь в мои треки {vk}", "нравится {vk}")),
]


def post(body):
    req = urllib.request.Request(PULT, data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"{}")


def main(cmd):
    for name, path, phrases in SCENARIOS:
        sep = "&" if "?" in path else "?"
        url = SVC + path + sep + "voice=1"
        if cmd == "list":
            print("%-16s %-40s %s" % (name, url, ", ".join(phrases[:3]) + " …"))
        elif cmd == "install":
            post({"name": name, "phrases": phrases, "actions": [{"type": "http", "url": url}]})
            print("сценарий:", name)
        elif cmd == "remove":
            post({"delete": name})
            print("удалён:", name)
        else:
            sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "list")
