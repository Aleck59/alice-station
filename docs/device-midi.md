# Станция Миди: что важно для проекта

Выжимка из [вики hackalice.me — Станция Миди](https://hackalice.me/devices/station-midi/)
(CC BY 4.0, Yandex Station Extended). Полные данные и обновления — там.

| Что | Значение | Зачем нам |
|---|---|---|
| Кодовое имя | Cucumber | так называются LED-узлы и `maind --cucumber` |
| SoC / CPU | Amlogic A5, 4× Cortex-A55 до 2 ГГц | хватит на локальный KWS и Opus |
| ОЗУ | ~980 МБ | агент должен быть экономным |
| Ядро / userspace | Linux 5.4, Buildroot 2023.11 (на factory-6 — 2020.02, 64-бит) | целевая сборка `linux/arm64` |
| Хранилище | eMMC, `/data` ~2.8 ГБ | наши файлы — в `/data` |
| NPU / DSP | Vivante VIPNano-Q, HiFi4 + аппаратный VAD | на будущее: ускорение KWS |
| Звук | 2× FS2105 (вуфер + твитеры), ALSA `AML-AUGESOUND` | вывод в `default` |
| Микрофоны | 4× PDM, `hw:0,1` | **только 96 кГц**, иначе Алиса глохнет |
| Эхо-референс | `LOOPBACK-A` | для AEC |
| Свет | кольцо 24 RGB (`/dev/fb0`), логотип 6 RGB (`/dev/fb1`), часы `cucumber:white:clock-*` | индикация, код активации |
| Кнопки | `KEY_VOICECOMMAND`, громкость, `KEY_MUTE`, `SW_MUTE_DEVICE` | запуск/прерывание диалога |
| Root | DNS-метод (проверен) | этап 0 |

## Предупреждения из вики

- Запись в `hw:0,1` на частоте, отличной от 96000 Гц, перенастраивает PDM и ломает
  Алису до следующего открытия на 96 кГц.
- Разделы eMMC открыты на запись — **никогда не `dd` в блочные устройства**.
- В `ota`, `env`, `reserved`, `tee`, `/data/quasar/devices.json` — личные данные и ключи.
- Правки `launch.json` — только через копию с проверкой JSON на ПК; оригинал хранить.

## Готовые наработки сообщества для Миди

- [MidiPult](https://github.com/RoBiN19666/midipult) — веб-панель, Luna-события
  («Алиса, включи нейросеть»), OpenAI-совместимые LLM. Хороший образец запуска
  по голосовой команде.
- [Quasar Bridge](https://github.com/skrashevich/quasar-bridge) — установка и автозапуск
  на Миди проверены; подмена `uniProxyUrl`.
- [quasard](https://github.com/amirilivice/quasard) — свой uniproxy вместо облака
  Яндекса; Zigbee на Миди.
