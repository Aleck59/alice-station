#!/bin/sh
# Удаление Alice Station. Запускать на колонке.
#   sh /data/opt/alice-station/deploy/uninstall.sh          — удалить программу, настройки оставить
#   sh /data/opt/alice-station/deploy/uninstall.sh --purge  — удалить и /data/alice-station (токены, конфиг)
#   sh .../uninstall.sh --dry-run                           — только показать план
set -eu
DIR=/data/opt/alice-station
DATA=/data/alice-station
INIT=/etc/init.d/S99zzalice-station
PURGE=0; DRY=0
for a in "$@"; do
    case "$a" in --purge) PURGE=1 ;; --dry-run) DRY=1 ;; esac
done
run() { echo "+ $*"; [ "$DRY" = 1 ] || "$@"; }

[ -x "$INIT" ] && run "$INIT" stop || true
if [ -e "$INIT" ]; then
    if [ -f "$DATA/S99zzalice-station.installed" ] && ! cmp -s "$INIT" "$DATA/S99zzalice-station.installed"; then
        echo "ВНИМАНИЕ: $INIT изменён вручную — не удаляю, проверьте сами"
    else
        run mount -o remount,rw /
        run rm -f "$INIT"
        run sync
    fi
fi
run rm -rf "$DIR"
if [ "$PURGE" = 1 ]; then run rm -rf "$DATA"; fi
echo "Alice Station удалён. Штатная Алиса не затрагивалась; пакеты Entware оставлены."
