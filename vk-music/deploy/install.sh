#!/bin/sh
# Установка VK Музыки на Станцию Миди (нужны root, Entware /opt/bin/python3 и MidiPult).
# Запуск на колонке из каталога vk-music:  sh deploy/install.sh
set -e
SRC=$(cd "$(dirname "$0")/.." && pwd)
DATA=/data/vk-music
INIT=/opt/etc/init.d/S98vkmusic
SHIM=/etc/init.d/S99zzvkmusic

[ -x /opt/bin/python3 ] || { echo "нет /opt/bin/python3 (Entware)" >&2; exit 1; }
/opt/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' \
    || { echo "нужен Python 3.9+" >&2; exit 1; }

[ -x "$INIT" ] && "$INIT" stop || true

mkdir -p "$DATA/app"
chmod 700 "$DATA"
rm -rf "$DATA/app/vkmusic"
cp -r "$SRC/vkmusic" "$DATA/app/"
cp "$SRC/deploy/scenarios.py" "$DATA/app/"
[ -f "$DATA/config.json" ] || cp "$SRC/deploy/config.example.json" "$DATA/config.json"
chmod 600 "$DATA/config.json"

mkdir -p /opt/etc/init.d
cp "$SRC/deploy/S98vkmusic" "$INIT"
chmod 755 "$INIT"

write_shim() {
    printf '#!/bin/sh\n# Автозапуск VK Музыки (alice-station)\n[ -x %s ] && %s start\nexit 0\n' "$INIT" "$INIT" >"$SHIM"
    chmod 755 "$SHIM"
}
if ! { [ -x "$SHIM" ] && grep -q "$INIT" "$SHIM"; }; then
    if [ -w /etc/init.d ]; then
        write_shim
    elif mount -o remount,rw / 2>/dev/null; then
        write_shim; sync; mount -o remount,ro / 2>/dev/null || true
    else
        echo "ВНИМАНИЕ: не удалось записать $SHIM — автозапуска не будет" >&2
    fi
fi

"$INIT" start
sleep 3
if /opt/bin/python3 "$DATA/app/scenarios.py" install; then
    echo "голосовые команды добавлены в сценарии MidiPult"
else
    echo "ВНИМАНИЕ: MidiPult недоступен, голосовые команды не добавлены" >&2
fi
IP=$(ip -4 addr show wlan0 2>/dev/null | awk '/inet /{sub(/\/.*/,"",$2);print $2;exit}')
echo "готово: откройте http://${IP:-<ip-колонки>}:8061 и войдите во ВКонтакте"
