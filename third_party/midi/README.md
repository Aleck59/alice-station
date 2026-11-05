# Доработки сторонних пакетов под Станцию Миди

## ytm-alice (SPAWNRYS-ban/ytm-alice 0.7.4)

`ytm-alice/ytm-alice` собран из 0.7.4 с патчем `ytm-alice-midi.patch`
(`CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -ldflags='-s -w'`):

- одно постоянное подключение к aliced :9875 без 2-секундного ReadDeadline.
  Исходная версия переподключалась каждые 2 с, а на Миди maind при сторонних
  подписчиках выдаёт части ответа с задержкой около 11 с;
- отмена ответа Алисы сразу на том же соединении, как только распознана
  музыкальная команда. Иначе облако успевает сказать про подписку или
  переключить штатное радио на «дальше»;
- голосовое «стоп» ставит паузу (выключают «выключи музыку»/«выключи ютуб»);
- после остановки YouTube ещё 30 минут считается активным источником:
  «продолжи» перезапускает текущий трек, а не радио Алисы.

- кольцо Миди (`/dev/fb0`, 24×1 RGB): во время воспроизведения плавный
  эквалайзер из 12 полос (зеркально, бас внизу), цвета из
  `/data/led-effects/palette.json` темы ledtheme, иначе радуга; жесты на «дальше» (по часовой),
  «назад» (против), «лайк» (розовая пульсация), пауза (затухание). Пока Алиса
  слушает или говорит, кольцо не трогается. Выключается настройкой
  `playback_led false`;
- голосовой лайк: «лайк», «поставь лайк», «лайкни», «мне нравится» (нужен
  вход в аккаунт в панели).

`ytm-alice/ytm-alice-player` — armhf-плеер из `native/player.c` с элементом `level` или (на Миди, где его нет) `spectrum` по басовым полосам;
все новые функции прошивки берутся через dlsym, без них плеер просто играет без уровня. Элемент
`level`: печатает `{"event":"level","level":0..1}` каждые 40 мс. Собран
`zig cc -target arm-linux-gnueabihf.2.17` против заглушек libquasar_gstreamer/libglib
(как `native/link-stubs.py`), заголовки glib/gstreamer 2.80/1.24.

Установка: сохранить `/data/yam/bin/ytm-alice` как `ytm-alice.orig`, заменить,
затем `ytm-alice daemon stop; YAM_ROOT=/data/yam ytm-alice daemon start`.

## zapret2 (lr1ne/alice-zapret2)

`zapret2/start.sh` заменяет `/data/opt/zapret2/start.sh`:

- подсети Яндекса не идут в nfqws2;
- в ядре Миди нет `connbytes`, он эмулируется через connmark: в очередь
  попадают пакеты соединения до третьего пакета с данными включительно.
  Цепочка `ZAPRET2Q`.

`stop.sh` должен дополнительно удалять `ZAPRET2Q`:
`iptables -t mangle -F ZAPRET2Q; iptables -t mangle -X ZAPRET2Q`.

## Медиаслой (alice-media) на Миди

`ytm-alice/alice-media` — скрипт медиаслоя 0.7.4, где список Bluetooth-служб
находится автоматически (`S40bluetoothd`, `S52bluealsa`, `S53bluealsa-aplay`,
`S54default-bluez-agent` на Миди) вместо зашитых `S51…S54`. Ставится вместо
`/data/yam/bin/alice-media`, затем `ytm-alice daemon install`. Откат:
`alice-media uninstall` и перезагрузка.

Кольцо ytm-alice уступает, если в `/dev/fb0` кто-то перерисовал кадр
(например, визуализатор Алисы для Bluetooth), и ждёт 3 с.
