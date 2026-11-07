# Quasar Bridge + Mistral на Станции Миди

[Quasar Bridge](https://github.com/skrashevich/quasar-bridge) (Apache-2.0) работает
на самой колонке на `127.0.0.1:8443` и встаёт между `aliced` и облаком Яндекса.
Распознавание и озвучка остаются у Яндекса. Маршруты (2026-11-06, по просьбе
«перехватывай все запросы кроме погоды, музыки»):

- `default_route: llm` — по умолчанию отвечает модель;
- к Алисе: погода, музыка/радио/YouTube и управление плеером («включи …»,
  «стоп», «дальше»), громкость, таймеры, будильники, напоминания, умный дом,
  «который час» — модель этим управлять не может;
- «спроси нейросеть …» всегда уходит в модель.

Модель — `ministral-14b-latest`: на бесплатном тарифе у `mistral-small`/`medium` квота 0 (429), ministral-14b — 30 запросов в минуту, ответ ~0,5 с.

`config.mistral.json` — конфиг для `/data/opt/quasar-bridge/config.json`:

- `mode: observe` на первом запуске; после проверки — `hybrid`.
- `llm_chat_parameters.chat_template_kwargs: null` — Mistral не знает этого поля.
- инструменты: время (Москва) и чтение веб-страниц; обновления и смена
  конфига голосом выключены.

Ключ — только в `/data/opt/quasar-bridge/runtime.env` (`chmod 600`):

```sh
QUASAR_LLM_API_KEY='...'
```

## Проверено с колонки (2026-11-06)

Доступны без VPN: Mistral, DeepSeek, YandexGPT, GigaChat (нужен сертификат
Минцифры), Hugging Face. Закрыты: Groq, OpenRouter, Cerebras, Together, OpenAI,
Anthropic.

## План установки

1. Скачать релиз `quasar-bridge-linux-arm64.tar.gz` на ноутбук, сверить SHA-256,
   передать по adb в `/data/`, распаковать (`gzip -dc | tar -xf -`) и запустить
   `install.sh` (создаёт observe-конфиг и `/etc/init.d/S96quasar-bridge`).
2. Положить `config.mistral.json` и `runtime.env`, `control.sh check-backends`.
3. `control.sh apply` (бэкап endpoint в `endpoint-backup.json`) и перезагрузка.
   Проверить, что Алиса отвечает как раньше.
4. `mode: hybrid`, `control.sh restart`, проверка голосом и задержки.

Откат: `control.sh rollback`, перезагрузка, затем `uninstall.sh`.

## Сторож: возврат к Алисе, если модель недоступна

`llm-watchdog.sh` каждые 30 с шлёт модели запрос на 1 токен (`/v1/chat/completions`; `/v1/models` отвечает 200 даже при нулевой квоте) с ключом из
`runtime.env` и `127.0.0.1:8443/healthz`:

- Mistral не отвечает 60 с → `mode: observe` + `control.sh restart`: все запросы
  снова обслуживает Алиса. Модель вернулась → `mode: hybrid`.
- мост не отвечает 60 с → `control.sh restart`.

Установка: `llm-watchdog.sh` в `/data/opt/quasar-bridge/`, `S97zllm-watchdog`
в `/etc/init.d/`. Журнал — `/data/opt/quasar-bridge/llm-watchdog.log`.
Режим задаётся только в `config.json` (в `runtime.env` не должно быть `QUASAR_MODE`).

## mistral-large-4 (2026-11-07)

На тарифе Free доступна `mistral-large-4` (именно это имя; `mistral-large-latest` → 403),
50 запросов в минуту, ~2,5 с. Это модель с рассуждением: `reasoning_effort` бывает
только `low|medium|high`, а `content` приходит массивом чанков `thinking` + `text`.
Мост v0.7.4 ждёт строку и падает с «invalid LLM response», поэтому
`mistral-content-chunks.patch` (поверх тега v0.7.4) склеивает чанки `text` и
отбрасывает `thinking`. Сборка: `bin/quasar-bridge-arm64` (Go 1.27.3,
`CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -ldflags '-s -w -X
quasarbridge/internal/update.Version=0.7.4' ./cmd/quasar-bridge`), SHA-256 в `bin/SHA256SUMS`.
На колонке заменяет `/data/opt/quasar-bridge/app/bin/quasar-bridge` (оригинал `.orig`).
Внимание: встроенное обновление моста вернёт штатный бинарник.
