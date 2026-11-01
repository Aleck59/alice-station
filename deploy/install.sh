#!/bin/sh
# Установка Alice Station на Станцию Миди. Запускать НА КОЛОНКЕ из корня репозитория:
#   sh deploy/install.sh            — установить/обновить
#   AS_NO_AUTOSTART=1 sh deploy/install.sh — без автозапуска
# Нужно: root, SSH, Entware в /opt (hackalice.me/recipes/packagemanagers/).
set -eu

SRC=$(cd "$(dirname "$0")/.." && pwd)
DIR=/data/opt/alice-station
DATA=/data/alice-station
INIT=/etc/init.d/S99zzalice-station

say() { echo "alice-station: $*"; }
die() { echo "alice-station: ОШИБКА: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "нужен root"
[ -x /opt/bin/opkg ] || die "нет Entware (/opt/bin/opkg). Поставьте его по hackalice.me/recipes/packagemanagers/"
grep -q cucumber /proc/device-tree/compatible 2>/dev/null || say "ВНИМАНИЕ: это не Станция Миди (cucumber) — пути к LED/кнопкам могут не совпасть"

say "пакеты Entware"
/opt/bin/opkg update >/dev/null || say "opkg update не удался, пробую с текущими списками"
for p in python3 python3-pip libopus alsa-utils; do
    /opt/bin/opkg list-installed | grep -q "^$p " || /opt/bin/opkg install "$p"
done

say "файлы в $DIR"
mkdir -p "$DIR" "$DATA"
rm -rf "$DIR/agent" "$DIR/deploy"
cp -r "$SRC/agent" "$SRC/deploy" "$DIR/"
rm -rf "$DIR/agent/tests"

say "Python-зависимости в $DIR/vendor"
/opt/bin/python3 -m pip install --quiet --upgrade --target "$DIR/vendor" -r "$SRC/agent/requirements.txt"

[ -f "$DATA/config.toml" ] || { cp "$SRC/deploy/config.example.toml" "$DATA/config.toml"; say "создан $DATA/config.toml"; }
cp "$SRC/deploy/asoundrc" "$DATA/.asoundrc"
chmod 700 "$DATA"

say "проверка захвата микрофона (1 с, 16 кГц через alice_mic)"
if HOME=$DATA arecord -q -D alice_mic -f S16_LE -c 1 -r 16000 -d 1 -t raw /tmp/as-mic.raw 2>/tmp/as-mic.err; then
    say "микрофон ок ($(wc -c < /tmp/as-mic.raw) байт)"
else
    say "ВНИМАНИЕ: захват не удался: $(cat /tmp/as-mic.err)"
fi
rm -f /tmp/as-mic.raw /tmp/as-mic.err
grep -q "rate: 96000" /proc/asound/card0/pcm2c/sub0/hw_params 2>/dev/null \
    && say "Алиса продолжает захват на 96 кГц — ок" \
    || say "проверьте, что Алиса отвечает (hw_params pcm2c не 96000 или недоступен)"

if [ "${AS_NO_AUTOSTART:-0}" != 1 ]; then
    say "автозапуск $INIT"
    mount -o remount,rw / || die "не удалось перемонтировать / на запись"
    cp "$SRC/deploy/S99zzalice-station" "$INIT"
    chmod 755 "$INIT"
    cp "$SRC/deploy/S99zzalice-station" "$DATA/S99zzalice-station.installed"
    sync
fi

[ -x "$INIT" ] && "$INIT" restart || true
IP=$(ip -4 addr show wlan0 2>/dev/null | awk '/inet /{sub(/\/.*/,"",$2);print $2}')
say "готово. Панель: http://${IP:-<IP-колонки>}:8060/  Лог: $DATA/agent.log"
say "при первом запуске в панели появится код активации — введите его на xiaozhi.me"
