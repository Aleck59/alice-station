# Доработки сторонних пакетов под Станцию Миди

## ytm-alice (SPAWNRYS-ban/ytm-alice 0.7.4)

`ytm-alice/ytm-alice` собран из 0.7.4 с патчем `voiceloop-midi.patch`
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
