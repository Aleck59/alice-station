# Эффекты кольца Станции Миди

Как устроена подсветка (разведка на колонке 2026-11-05):

- Кольцо: 24 RGB-светодиода на двух AW21036, в системе это `/dev/fb0` (24×1, RGB).
  Рисует только `maind --platformd`.
- Штатные эффекты — текстовые файлы `/system/vendor/quasar/ledpatterns/ring/<имя>.led`.
  Строка = кадр: 24 цвета `rrggbb` (строчные, через пробел) и длительность кадра в мс.
  Строка `loop` — начало повторяющейся части (выше неё — вступление, играет один раз).
  Есть ещё `smooth 1|0` и `no_size_optimization`. Ошибка формата = эффект не загрузится.
- Раздел системы только для чтения, поэтому копия лежит в `/data/ledpatterns`
  и подключается поверх оригинала `mount --bind`. Оригинал — `/data/ledpatterns.orig`.

## Что здесь

| Файл | Что делает |
|---|---|
| `gen_effects.py` | генерирует темы для Алисы (`alice_listening/thinking/speaking`) и подсветки |
| `ledtheme.sh` | переключатель на колонке: `ledtheme fire`, `ledtheme random`, `ledtheme lamp party` |
| `S96ledtheme` | init-скрипт: подключает копию до старта quasar |
| `ringproxy.py` | служба-перекраска кадров fb0 в цвета темы (эквалайзер) |
| `seamcheck.py` | проверка стыков циклов (конец цикла → начало, вступление → цикл) |
| `install.sh` | установка в `/data/led-effects` (`--boot` — ещё и автозапуск) |

Темы: `aurora` (сияние), `fire`, `ocean`, `rainbow`, `sakura`, `stock`.
Подсветки: `party`, `romantic`, `polar_lights`, `color_shine`, `summer`, `game`,
`rainbow_spin`, `candle_soft`. Их запускает режим лампы Алисы: `ledtheme lamp party`
кладёт эффект вместо лавовой лампы (`candle`, `night_light` — другие слоты).

`maind` читает эффекты только при запуске (проверено 2026-11-05), поэтому
`ledtheme` после смены темы/лампы сам делает `ledtheme restart`.

Кольцо, когда Алиса говорит, — не файл, а визуализатор `ring_spectrogram`.
Его цвета приходят с сервера Яндекса (кэш `/data/quasar/data/user_config.dat`)
и перекрывают `quasar.cfg`, поэтому локально их не поменять (проверено 2026-11-05).

## Эквалайзер в цветах темы (ringproxy)

Во время музыки Алиса сама рисует настоящий эквалайзер (`ring_spectrogram`,
пресеты ball/blink/disco/polar), но только фиолетовым. `ledtheme ringproxy on`
запускает `ringproxy.py`: он снимает со светодиодов триггер `ledfb-ring`, читает
кадр `/dev/fb0`, перекрашивает фиолетовые пиксели в цвета темы
(`/data/led-effects/palette.json`) и пишет в `/sys/class/leds/cucumber:*:ringN`.
`ledtheme ringproxy off` (или перезагрузка без автозапуска) возвращает триггер.

### Цвет по тому, кто отвечает

ringproxy читает журнал maind (`Play animations: [ ring_spectrogram ]`,
`Creating  spectrogram: pulsing_voice`) и знает, что на кольце: речь, музыка
или .led-анимация. Речь перекрашивается целиком (цвет речи Яндекс меняет на
сервере): **ассистент — розовый**, если `/var/run/alice-route` моложе 45 с и
содержит `assistant` (пишет Quasar Bridge); иначе **Алиса — фиолетовый 370fff**.
Музыка — цвета темы. С `RING_EMOTIONS=1` речь ассистента берёт цвет эмоции из
`/var/run/alice-emotion` (joy/sad/wow/angry/love).

## Откат

`ledtheme stock` — штатные эффекты; `ledtheme umount` или перезагрузка без
`S96ledtheme` — штатная папка. Удалить автозапуск: `mount -o remount,rw /;
rm /etc/init.d/S96ledtheme; mount -o remount,ro /`.
