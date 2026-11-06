# Quasar Bridge + Mistral на Станции Миди

[Quasar Bridge](https://github.com/skrashevich/quasar-bridge) (Apache-2.0) работает
на самой колонке на `127.0.0.1:8443` и встаёт между `aliced` и облаком Яндекса.
Распознавание и озвучка остаются у Яндекса. В модель Mistral уходят:

- вопросы, на которые Алиса отвечает отказом («я не знаю», «пока не умею» и т. п.);
- явные просьбы «Алиса, спроси нейросеть …».

Остальное идёт в Яндекс без классификатора, так что лишних запросов к модели нет.

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

`llm-watchdog.sh` каждые 15 с проверяет `api.mistral.ai/v1/models` с ключом из
`runtime.env` и `127.0.0.1:8443/healthz`:

- Mistral не отвечает 60 с → `mode: observe` + `control.sh restart`: все запросы
  снова обслуживает Алиса. Модель вернулась → `mode: hybrid`.
- мост не отвечает 60 с → `control.sh restart`.

Установка: `llm-watchdog.sh` в `/data/opt/quasar-bridge/`, `S97zllm-watchdog`
в `/etc/init.d/`. Журнал — `/data/opt/quasar-bridge/llm-watchdog.log`.
Режим задаётся только в `config.json` (в `runtime.env` не должно быть `QUASAR_MODE`).
