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
| `install.sh` | установка в `/data/led-effects` (`--boot` — ещё и автозапуск) |

Темы: `aurora` (сияние), `fire`, `ocean`, `rainbow`, `sakura`, `stock`.
Подсветки: `party`, `romantic`, `polar_lights`, `color_shine`, `summer`, `game`,
`rainbow_spin`, `candle_soft`. Их запускает режим лампы Алисы: `ledtheme lamp party`
кладёт эффект вместо лавовой лампы (`candle`, `night_light` — другие слоты).

## Откат

`ledtheme stock` — штатные эффекты; `ledtheme umount` или перезагрузка без
`S96ledtheme` — штатная папка. Удалить автозапуск: `mount -o remount,rw /;
rm /etc/init.d/S96ledtheme; mount -o remount,ro /`.
