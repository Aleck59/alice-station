#!/bin/sh
# Сторож моста: если Mistral не отвечает дольше FAIL_SEC (60 с), мост
# (два запроса подряд, шаг 30 с) переводится в observe — все запросы снова обслуживает Алиса. Когда модель
# вернётся, мост снова в hybrid. Если сам мост не отвечает — перезапуск.
# Запуск: llm-watchdog.sh start|stop|status|run
D=${QUASAR_DATA_DIR:-/data/opt/quasar-bridge}
CFG=$D/config.json
CTL="sh $D/app/control.sh"
CURL=${CURL:-/opt/bin/curl}
PIDF=$D/llm-watchdog.pid
LOG=$D/llm-watchdog.log
STEP=${STEP:-30}
MODEL=$(sed -n 's/.*"llm_model"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CFG" | head -n 1)
FAIL_SEC=${FAIL_SEC:-60}
WANT=${WANT_MODE:-hybrid}

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
mode() { sed -n 's/.*"mode"[[:space:]]*:[[:space:]]*"\([a-z]*\)".*/\1/p' "$CFG" | head -n 1; }
set_mode() {
	[ "$(mode)" = "$1" ] && return 0
	sed -i "s/\"mode\"[[:space:]]*:[[:space:]]*\"[a-z]*\"/\"mode\": \"$1\"/" "$CFG"
	log "mode -> $1 ($2)"
	[ "$1" = observe ] && echo alice > /var/run/alice-route
	$CTL restart >> "$LOG" 2>&1 || log "restart failed"
}
key() { sed -n "s/^QUASAR_LLM_API_KEY=['\"]\{0,1\}\([^'\"]*\).*/\1/p" "$D/runtime.env" | head -n 1; }
llm_ok() {
	# Настоящий запрос к модели (1 токен): /v1/models отвечает 200 и при нулевой
	# квоте, поэтому не годится. 429 и 5xx считаются отказом.
	code=$($CURL -s -o /dev/null -w '%{http_code}' -m 20 \
		-H "Authorization: Bearer $(key)" -H 'Content-Type: application/json' \
		-d "{\"model\":\"$MODEL\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}]}" \
		https://api.mistral.ai/v1/chat/completions)
	[ "$code" = 200 ]
}
bridge_ok() { $CURL -s -o /dev/null -m 5 http://127.0.0.1:8443/healthz; }

run() {
	llm_bad=0; br_bad=0
	while :; do
		if bridge_ok; then br_bad=0; else
			br_bad=$((br_bad + STEP))
			if [ $br_bad -ge $FAIL_SEC ]; then
				log "bridge down ${br_bad}s, restart"; $CTL restart >> "$LOG" 2>&1; br_bad=0
			fi
		fi
		if llm_ok; then
			llm_bad=0; set_mode "$WANT" "LLM снова доступна"
		else
			llm_bad=$((llm_bad + STEP))
			[ $llm_bad -ge $FAIL_SEC ] && set_mode observe "LLM недоступна ${llm_bad}s"
		fi
		sleep "$STEP"
	done
}

case "$1" in
start)
	[ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null && exit 0
	setsid "$0" run < /dev/null > /dev/null 2>&1 &
	echo $! > "$PIDF" ;;
stop)
	[ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null; rm -f "$PIDF" ;;
status)
	[ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null && echo "running, mode=$(mode)" || echo stopped
	tail -n 5 "$LOG" 2>/dev/null ;;
run) run ;;
*) echo "usage: $0 start|stop|status|run"; exit 1 ;;
esac
