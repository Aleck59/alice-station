#!/bin/sh
# Удаление VK Музыки. Вход во ВК (/data/vk-music/token.json) удаляется только с --purge.
INIT=/opt/etc/init.d/S98vkmusic
SHIM=/etc/init.d/S99zzvkmusic
[ -x "$INIT" ] && "$INIT" stop
[ -f /data/vk-music/app/scenarios.py ] && /opt/bin/python3 /data/vk-music/app/scenarios.py remove || true
rm -f "$INIT"
if [ -e "$SHIM" ]; then
    rm -f "$SHIM" 2>/dev/null || { mount -o remount,rw / && rm -f "$SHIM"; sync; mount -o remount,ro /; }
fi
rm -rf /data/vk-music/app
[ "$1" = "--purge" ] && rm -rf /data/vk-music
echo "VK Музыка удалена"
