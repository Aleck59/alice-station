#!/bin/sh
# Запуск агента с логом. Вызывается из /etc/init.d/S99zzalice-station.
DIR=/data/opt/alice-station
DATA=/data/alice-station
LOG=$DATA/agent.log
export PATH=/opt/bin:/opt/sbin:$PATH
export PYTHONPATH=$DIR/agent:$DIR/vendor
export ALICE_STATION_DIR=$DATA
[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 1048576 ] && mv "$LOG" "$LOG.1"
exec /opt/bin/python3 -m alice_station --config "$DATA/config.toml" >>"$LOG" 2>&1
