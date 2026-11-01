# Запуск

## 1. Сначала на компьютере (эмулятор)

Так проверяется связка с xiaozhi.me без колонки: микрофон и динамик компьютера.
Нужны Linux, Python 3.11+, `alsa-utils` (arecord/aplay), `libopus`.

```sh
git clone https://github.com/Aleck59/alice-station
cd alice-station
pip install -r agent/requirements.txt
ALICE_STATION_DIR=./.local PYTHONPATH=agent python3 -m alice_station --pc
```

1. В логе появится `КОД АКТИВАЦИИ: 123456`. Он же — на панели <http://127.0.0.1:8060/>.
2. Зайдите на [xiaozhi.me](https://xiaozhi.me) → консоль → добавить устройство → введите код.
   Там же выберите модель, роль (системный промпт) и **голос** агента.
3. Через несколько секунд агент напишет «устройство активировано».
4. На панели нажмите «Говорить» и задайте вопрос. Повторное нажатие во время ответа
   перебивает, во время слушания — заканчивает диалог.

Токен и UUID сохраняются в `./.local/state.json` — это «личность» устройства в
xiaozhi.me, не публикуйте этот файл.

## 2. На Станции Миди

Нужно: [root и SSH](https://hackalice.me/start/root/),
[отключённые обновления](https://hackalice.me/start/ssh/),
[Entware в /opt](https://hackalice.me/recipes/packagemanagers/).

```sh
# с компьютера
scp -P 2222 -r alice-station root@<IP-колонки>:/tmp/
ssh -p 2222 root@<IP-колонки>
# на колонке
cd /tmp/alice-station && sh deploy/install.sh
```

Установщик:

- ставит из Entware `python3`, `python3-pip`, `libopus`, `alsa-utils`;
- кладёт агент в `/data/opt/alice-station`, конфиг и состояние — в `/data/alice-station`;
- проверяет захват микрофона через `alice_mic` (96 кГц на железе → 16 кГц для нас) и
  что Алиса продолжает слышать;
- ставит автозапуск `/etc/init.d/S99zzalice-station` (перемонтирует `/` на запись).

Дальше — как на компьютере: панель `http://<IP-колонки>:8060/`, код активации, xiaozhi.me.
Диалог запускается **нажатием кнопки Алисы** или из панели.

!!! warning "Кнопка Алисы"
    Кнопку `KEY_VOICECOMMAND` слушает и штатная Алиса, она тоже может отозваться.
    Если мешает — поменяйте `trigger.key_code` или запускайте из панели. Свой запуск
    голосом — этап 5 роадмапа.

### Управление

```sh
/etc/init.d/S99zzalice-station restart     # перезапуск
tail -f /data/alice-station/agent.log      # лог
touch /data/alice-station/disabled         # не запускать при загрузке
```

### Откат

```sh
sh /data/opt/alice-station/deploy/uninstall.sh --dry-run   # показать план
sh /data/opt/alice-station/deploy/uninstall.sh             # удалить, настройки оставить
sh /data/opt/alice-station/deploy/uninstall.sh --purge     # удалить вместе с токенами
```

Штатные файлы Алисы (`quasar.cfg`, `launch.json`) агент не меняет.

## 3. Голоса

В `config.toml` (`/data/alice-station/config.toml`) — раздел `[voice.profiles.*]`, примеры
в [deploy/config.example.toml](../deploy/config.example.toml):

| engine | Что это |
|---|---|
| `cloud` | голос, выбранный в xiaozhi.me |
| `command` | любая программа: текст в stdin → PCM S16 моно в stdout (Piper `--output-raw`) |
| `http` | OpenAI-совместимый `/audio/speech` |

Сменить голос: в панели, или голосом — «смени голос на …» (MCP-инструмент
`self.voice.set_profile`).

## 4. Что умеет голосом (MCP-инструменты)

| Инструмент | Пример фразы |
|---|---|
| `self.audio_speaker.set_volume` | «сделай погромче», «громкость 30» |
| `self.light.set_rgb`, `self.light.off` | «включи зелёную подсветку» |
| `self.timer.set/list/cancel` | «таймер на пять минут» |
| `self.voice.list_profiles/set_profile` | «какие есть голоса?» |
| `music.radio.list/play`, `music.pause/resume/stop/next` | «включи Европу Плюс» |
| `music.vk.*` | пока отвечает, что ВК не подключён |

Модель в xiaozhi.me сама решает, когда вызвать инструмент, — по описанию инструмента.
