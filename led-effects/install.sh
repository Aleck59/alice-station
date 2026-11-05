#!/bin/sh
# Установка эффектов кольца на Станцию Миди (запускать на колонке от root,
# из папки, куда скопированы gen_effects.py, ledtheme.sh и S96ledtheme).
# Ничего не пишет в прошивку, кроме init-скрипта S96ledtheme (по желанию: --boot).
set -e
BASE=/data/led-effects
PY=/opt/bin/python3
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$BASE"
$PY "$HERE/gen_effects.py" "$BASE" /data/ledpatterns.orig/ring
cp "$HERE/ledtheme.sh" "$BASE/ledtheme" && chmod 755 "$BASE/ledtheme"
ln -sf "$BASE/ledtheme" /opt/bin/ledtheme 2>/dev/null || true
"$BASE/ledtheme" mount
if [ "$1" = --boot ]; then
    mount -o remount,rw /
    cp "$HERE/S96ledtheme" /etc/init.d/S96ledtheme && chmod 755 /etc/init.d/S96ledtheme
    mount -o remount,ro /
    echo "автозапуск: /etc/init.d/S96ledtheme"
fi
"$BASE/ledtheme" list
