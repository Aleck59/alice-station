#!/bin/sh
# ledtheme — переключатель эффектов кольца Станции Миди.
#
# Как устроено: штатные эффекты лежат в /system/vendor/quasar/ledpatterns
# (раздел только для чтения). Мы держим копию в /data/ledpatterns и
# подключаем её поверх оригинала через mount --bind. Прошивка не меняется.
#
#   ledtheme list              темы и подсветки
#   ledtheme <тема>            поставить тему для Алисы (слушаю/думаю/говорю)
#   ledtheme random            случайная тема
#   ledtheme stock             вернуть штатные эффекты (папка остаётся подключённой)
#   ledtheme mount | umount    подключить / отключить копию поверх штатной папки
#   ledtheme status            что подключено и какая тема стоит
#   ledtheme lamp <подсветка> [candle|lavalamp|night_light]
#                              поставить подсветку в режим лампы (по умолчанию lavalamp);
#                              включается как обычно: «Алиса, включи лавовую лампу» / в приложении
#   ledtheme lamp stock        вернуть штатные лампы
#   ledtheme ringproxy on|off|status
#                              эквалайзер Алисы и штатные фиолетовые анимации в цветах темы
#                              (служба ringproxy.py перекрашивает кадры fb0 на лету)
#   ledtheme restart           перезапустить maind --platformd (кольцо гаснет на пару секунд)
#
# maind читает эффекты только при запуске, поэтому после смены темы или лампы
# ledtheme сам перезапускает maind (кольцо гаснет на пару секунд).
# Не чаще раза в ~10 минут (так настроен перезапуск в launch.json).
# Флаг --no-restart: только положить файлы (например, из S96ledtheme до старта quasar).
#
# Подсветки лежат в $BASE/extra/ring (party, romantic, polar_lights, ...).

SYS=/system/vendor/quasar/ledpatterns
ACT=/data/ledpatterns
ORIG=/data/ledpatterns.orig
BASE=/data/led-effects
STATE=$BASE/current

die() { echo "ledtheme: $*" >&2; exit 1; }

is_mounted() { grep -q " $SYS " /proc/mounts; }

do_mount() {
    [ -d "$ORIG" ] || { cp -a "$SYS" "$ORIG" || die "не удалось сохранить оригинал"; }
    [ -d "$ACT" ] || { cp -a "$ORIG" "$ACT" || die "не удалось создать $ACT"; }
    is_mounted || mount --bind "$ACT" "$SYS" || die "mount --bind не удался"
}

reset_to_stock() {
    # содержимое ACT = оригинал (не трогая сам каталог — он подключён)
    rm -rf "$ACT/ring.new" && cp -a "$ORIG/ring" "$ACT/ring.new" || die "копирование не удалось"
    rm -rf "$ACT/ring.old"; mv "$ACT/ring" "$ACT/ring.old" && mv "$ACT/ring.new" "$ACT/ring" && rm -rf "$ACT/ring.old"
}

apply_theme() {
    t=$1
    [ "$t" = stock ] || [ -d "$BASE/themes/$t/ring" ] || die "нет темы '$t' (ledtheme list)"
    do_mount
    reset_to_stock
    [ "$t" = stock ] || cp "$BASE/themes/$t/ring/"*.led "$ACT/ring/"
    echo "$t" > "$STATE"
    # палитра для эквалайзера ytm-alice: 24 цвета темы по кругу
    if [ "$t" = stock ]; then
        cp "$BASE/palette.stock.json" "$BASE/palette.json"; cp "$BASE/palette.stock.txt" "$BASE/palette.txt"
    else
        cp "$BASE/themes/$t/palette.json" "$BASE/palette.json"; cp "$BASE/themes/$t/palette.txt" "$BASE/palette.txt"
    fi
    # лампы, выбранные раньше, сохраняем
    for f in "$BASE"/lamp.*; do
        [ -f "$f" ] || continue
        slot=${f##*/lamp.}; eff=$(cat "$f")
        [ -f "$BASE/extra/ring/$eff.led" ] && cp "$BASE/extra/ring/$eff.led" "$ACT/ring/$slot.led"
    done
    sync
    echo "тема: $t"
}

set_lamp() {
    eff=$1; slot=${2:-lavalamp}
    case "$slot" in candle|lavalamp|night_light) ;; *) die "лампа: candle, lavalamp или night_light" ;; esac
    do_mount
    if [ "$eff" = stock ]; then
        for s in candle lavalamp night_light; do cp "$ORIG/ring/$s.led" "$ACT/ring/$s.led"; rm -f "$BASE/lamp.$s"; done
        sync; echo "лампы штатные"; return
    fi
    [ -f "$BASE/extra/ring/$eff.led" ] || die "нет подсветки '$eff' (ledtheme list)"
    cp "$BASE/extra/ring/$eff.led" "$ACT/ring/$slot.led" && echo "$eff" > "$BASE/lamp.$slot"
    sync
    echo "лампа $slot: $eff"
}

restart_maind() {
    pid=$(ps w 2>/dev/null | grep '[m]aind --platformd' | awk '{print $1}' | head -1)
    [ -n "$pid" ] || die "maind --platformd не найден"
    kill "$pid"
    i=0
    while [ $i -lt 15 ]; do
        sleep 1; i=$((i + 1))
        new=$(ps w | grep '[m]aind --platformd' | awk '{print $1}' | head -1)
        [ -n "$new" ] && [ "$new" != "$pid" ] && { echo "maind перезапущен ($pid -> $new)"; return 0; }
    done
    die "maind не поднялся за 15 с — перезагрузите колонку"
}

PROXY_PID=/var/run/ringproxy.pid

proxy_running() { [ -f "$PROXY_PID" ] && kill -0 "$(cat "$PROXY_PID")" 2>/dev/null; }

ringproxy() {
    case "$1" in
        on)
            proxy_running && { echo "ringproxy уже работает"; return 0; }
            [ -f "$BASE/palette.json" ] || echo "ledtheme: нет palette.json — сначала выберите тему" >&2
            nohup /opt/bin/python3 "$BASE/ringproxy.py" >/data/led-effects/ringproxy.log 2>&1 &
            echo $! > "$PROXY_PID"
            touch "$BASE/ringproxy_on"
            echo "ringproxy включён" ;;
        off)
            proxy_running && kill "$(cat "$PROXY_PID")"
            sleep 1
            rm -f "$PROXY_PID" "$BASE/ringproxy_on"
            # на случай падения службы: вернуть триггер всем светодиодам кольца
            for f in /sys/class/leds/cucumber:*:ring*/trigger; do echo ledfb-ring > "$f"; done
            echo "ringproxy выключен, кольцо снова повторяет fb0" ;;
        status) proxy_running && echo "ringproxy: работает" || echo "ringproxy: выключен" ;;
        *) die "ledtheme ringproxy on|off|status" ;;
    esac
}

RESTART=1
if [ "$1" = --no-restart ]; then RESTART=0; shift; fi
maybe_restart() { [ "$RESTART" = 1 ] && restart_maind; return 0; }

case "$1" in
    list)
        echo "темы:"; ls "$BASE/themes" 2>/dev/null | sed 's/^/  /'; echo "  stock"
        echo "подсветки:"; ls "$BASE/extra/ring" 2>/dev/null | sed 's/\.led$//; s/^/  /' ;;
    random)
        n=$(ls "$BASE/themes" | wc -l); [ "$n" -gt 0 ] || die "темы не установлены"
        cur=$(cat "$STATE" 2>/dev/null)
        pick=$cur
        while [ "$pick" = "$cur" ] && [ "$n" -gt 1 ] || [ -z "$pick" ]; do
            r=$(( $(od -An -N2 -tu2 /dev/urandom | tr -d ' ') % n + 1 ))
            pick=$(ls "$BASE/themes" | sed -n "${r}p")
        done
        apply_theme "$pick"; maybe_restart ;;
    stock) apply_theme stock; maybe_restart ;;
    mount) do_mount; echo "подключено" ;;
    umount) is_mounted && umount "$SYS"; echo "отключено, работают штатные эффекты" ;;
    status)
        is_mounted && echo "копия подключена" || echo "копия НЕ подключена"
        echo "тема: $(cat "$STATE" 2>/dev/null || echo stock)"
        for f in "$BASE"/lamp.*; do [ -f "$f" ] && echo "лампа ${f##*/lamp.}: $(cat "$f")"; done
        ringproxy status ;;
    lamp) shift; [ -n "$1" ] || die "ledtheme lamp <подсветка> [лампа]"; set_lamp "$@"; maybe_restart ;;
    restart) restart_maind ;;
    ringproxy) ringproxy "$2" ;;
    ""|-h|--help) sed -n '2,31p' "$0" ;;
    *) apply_theme "$1"; maybe_restart ;;
esac
