# Своё слово вызова и смена голоса

Инструкция для Станции Миди (`192.168.0.161`) с агентом Alice Station (PR #1,
`agent/`, конфиг `/data/alice-station/config.toml`, панель `http://192.168.0.161:8060`).

1. [Слово вызова «Лариса», «Катя», «Маруся»](#1-слово-вызова)
2. [Смена голоса TTS без смены сервиса](#2-голос-tts)

Факты о прошивке взяты из вики Yandex Station Extended (hackalice.me, CC BY 4.0):
[голосовой тракт](https://hackalice.me/internals/voice/),
[Станция Миди](https://hackalice.me/devices/station-midi/),
[Станция 2 → фразы вызова](https://hackalice.me/devices/station-2/#voice).

---

## 1. Слово вызова

### 1.1. Что можно, а что нет

На колонке два независимых «слушателя»:

| Кто | Слова | Чем распознаёт | Можно поменять? |
|---|---|---|---|
| Штатная Алиса (`maind --platformd`) | «Алиса», «Яндекс» | нейросеть `spotter_models/activation/alisa` (или `yandex`) в `libyandex_evil.so` | **Нет** |
| Агент Alice Station | любые: «Катя», «Лариса», «Маруся» | Vosk (сейчас) или своя модель openWakeWord (ниже) | **Да** |

Почему штатное слово не заменить:

- Каждое слово Алисы — отдельная сеть на один класс (`words.txt`: `filler` и само слово).
  Файл `acoustic_model.nnet` — Kaldi nnet3 со своими слоями Яндекса
  (`Q16FixedAffine`, `GroupConvCorou`, `MultichannelAttention`). Штатный Kaldi его не
  читает, инструментов обучения сетей этого формата нет. Скрипт вики
  `contrib/spotter_nnet.py` модель только **разбирает**, не собирает.
- В движке есть режим «своё слово текстом» (`Spotter::CKWS`,
  `customActivationSpotterSettings`), но ему нужны NPU-модели CTC и верификатора, которых
  в прошивке нет. На проверенных колонках он выключен.
- Подсунуть чужой `acoustic_model.nnet` можно, но взять его негде, а сломанная модель
  глушит Алису.

Поэтому схема такая: **Алиса остаётся со своим словом, а «Катя/Лариса/Маруся» слушает
агент.** Чтобы новое слово было главным, держите колонку в режиме ассистента
(раздел [1.5](#15-сделать-своё-слово-главным)).

### 1.2. Выбор слов

Хорошее слово вызова: 3–4 слога, редкое в речи и по ТВ, не похоже на «Алиса».

| Слово | Оценка | Чем опасно |
|---|---|---|
| **Маруся** | хорошее | «Маруся» в песнях; так же зовут ассистента VK — в рекламе |
| **Катя** | среднее | короткое (2 слога) и частое имя → больше ложных срабатываний; «катя-» в «катятся» |
| **Лариса** | рискованное | звучит как «л-**алиса**»: штатная Алиса может просыпаться на «Ларису». В режиме ассистента это гасит `alice_deaf = true`, в режиме Алисы — будет мешать |

Рекомендация: **«Маруся»** основным, **«Катя»** запасным; «Ларису» проверить
(раздел [1.6](#16-проверка-качества)) и оставить, только если Алиса на неё не просыпается.

### 1.3. Вариант А — Vosk, без обучения (уже стоит)

Агент держит малую русскую модель Vosk в режиме грамматики: распознаватель знает только
слова из списка и `[unk]`. Обучать ничего не нужно — слово просто строка.

`/data/alice-station/config.toml`:

```toml
[wakeword]
engine = "vosk"
model = "/data/alice-station/models/vosk-model-small-ru-0.22"
words = ["маруся", "катя", "лариса"]
exit_commands = ["выключи ассистента", "верни алису"]
threshold = 0.75     # 0.6 — чутче, 0.85 — меньше ложных
```

Перезапуск:

```sh
ssh root@192.168.0.161 /etc/init.d/S99zzalice-station restart
```

Плюсы: ничего не учить, новое слово за минуту. Минусы: Vosk ждёт паузу после слова
(срабатывание с задержкой ~0,3–0,6 с), держит в памяти ~150 МБ и чаще путает похожие
слова. Если этого хватает — дальше можно не читать до [1.5](#15-сделать-своё-слово-главным).

### 1.4. Вариант Б — своя модель openWakeWord

Если Vosk срабатывает ложно или медленно, обучите отдельный детектор на каждое слово.
[openWakeWord](https://github.com/dscripka/openWakeWord) (Apache 2.0) устроен так:
общие замороженные сети (мел-спектрограмма и эмбеддинг речи Google) + маленькая
«голова» на ваше слово. Голова — файл `маруся.onnx` около 200 КБ, учится за 1–2 часа
на ПК с видеокартой или в Google Colab. Эмбеддинг не привязан к языку, русские слова он
различает, но готового русского генератора примеров в проекте нет — примеры делаем сами.

> Статус: обучение и запуск на ПК — по документации openWakeWord. **На самой Миди
> openWakeWord ещё не запускали**, и в агенте пока нет движка `openwakeword` (только
> `vosk`). Шаги на колонке ниже — план; движок в `kws.py` добавлю отдельным PR.

#### Шаг 1. Записать слово на самой колонке (самое ценное)

Записи с микрофонов Миди учат модель именно её акустике. Нужны 50–200 повторов от
каждого члена семьи, с разных мест комнаты, разной громкостью, в том числе при игре музыки.

Микрофон берите **только** через `alice_mic` (железо держится на 96 кГц). Открыть `hw:0,1`
на 16 кГц нельзя — Алиса оглохнет до перезагрузки. Агент на время записи остановите,
чтобы не делить микрофон:

```sh
ssh root@192.168.0.161
/etc/init.d/S99zzalice-station stop
mkdir -p /data/kws-rec/маруся && cd /data/kws-rec/маруся
i=0
while [ $i -lt 100 ]; do
    i=$((i+1))
    echo "[$i] скажите: Маруся"; sleep 1
    HOME=/data/alice-station arecord -q -D alice_mic -f S16_LE -c 1 -r 16000 -d 2 "pos_$i.wav"
done
/etc/init.d/S99zzalice-station start
```

Тем же способом запишите 5–10 минут обычного разговора и ТВ в комнате
(`-d 600 room_noise.wav`) — это негативы и фон.

Скачать на ПК: `scp -r root@192.168.0.161:/data/kws-rec ./kws-rec`.
Обрежьте тишину по краям (например, `sox in.wav out.wav silence 1 0.05 1% reverse silence 1 0.05 1% reverse`),
чтобы слово занимало 0,5–1,5 с.

#### Шаг 2. Нагенерировать синтетику (тысячи примеров)

Своих записей мало, поэтому добавьте синтез — разными голосами, скоростями и интонациями:

- **Piper**, русские голоса `ru_RU-irina-medium`, `ru_RU-denis-medium`,
  `ru_RU-dmitri-medium`, `ru_RU-ruslan-medium` ([rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main/ru/ru_RU)).
  Варьируйте `--length_scale 0.8…1.3`, `--noise_scale 0.4…0.9`, `--noise_w 0.6…1.0`
  и текст: «Маруся», «Маруся!», «Маруся?», «Маруся, …».
- **ElevenLabs** (ключ уже есть): 10–20 разных голосов из библиотеки по 20–50 фраз.
- Цель: 3000–5000 позитивов на слово.

Пример для Piper на ПК:

```sh
for v in irina denis dmitri ruslan; do
  for ls in 0.8 0.9 1.0 1.1 1.2 1.3; do
    for ns in 0.4 0.667 0.9; do
      for t in "Маруся" "Маруся!" "Маруся?"; do
        n=$((n+1))
        echo "$t" | piper -m ru_RU-$v-medium.onnx --length_scale $ls --noise_scale $ns \
          --output_file pos/$v-$n.wav
      done
    done
  done
done
```

Piper отдаёт 22 050 Гц — переведите всё в 16 кГц моно: `sox in.wav -r 16000 -c 1 out.wav`.

**Негативы-двойники** (самое важное против ложных срабатываний) — те же генераторы, но
похожие слова: для «Маруся» — «Маша», «Муся», «Россия», «Маруська», «мороз», «бабуся»;
для «Катя» — «катят», «Катюша», «хватит», «Кать», «кадр»; для «Лариса» — **«Алиса»**,
«Раиса», «Лиса», «Карина», «Лариска». По 1000–2000 на слово.

#### Шаг 3. Обучить

На ПК с Linux и CUDA (или в Colab по их ноутбуку `notebooks/automatic_model_training.ipynb`):

```sh
git clone https://github.com/dscripka/openWakeWord && cd openWakeWord
pip install -e .[full]
# общие признаки негативов (~2000 ч речи) и валидация на ложные срабатывания
wget https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/openwakeword_features_ACAV100M_2000_hrs_16bit.npy
wget https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/validation_set_features.npy
# импульсные отклики комнат и фоновый шум для аугментации:
#   MIT RIR (mit_rirs), AudioSet/FMA или свои room_noise.wav
```

Разложите клипы так (16 кГц, моно, WAV): тестовые — ~10 % от всех, своих записей с колонки
обязательно и в train, и в test:

```text
my_models/marusya/positive_train/   ← синтетика + записи с колонки
my_models/marusya/positive_test/
my_models/marusya/negative_train/   ← двойники + нарезка room_noise.wav
my_models/marusya/negative_test/
```

`marusya.yaml` (имена ключей сверьте с `examples/custom_model.yml` вашей версии):

```yaml
model_name: marusya
target_phrase: ["маруся"]      # нужен только генератору, клипы уже лежат
output_dir: ./my_models
n_samples: 4000                # = сколько позитивов вы положили
n_samples_val: 400
rir_paths: ["./mit_rirs"]
background_paths: ["./background", "./kws-rec/room"]
augmentation_rounds: 2
feature_data_files:
  ACAV100M_sample: ./openwakeword_features_ACAV100M_2000_hrs_16bit.npy
false_positive_validation_data_path: ./validation_set_features.npy
model_type: dnn
layer_size: 32
steps: 50000
max_negative_weight: 1500      # выше — меньше ложных, но и пропусков больше
target_false_positives_per_hour: 0.2
batch_n_per_class: {ACAV100M_sample: 1024, adversarial_negative: 50, positive: 50}
```

Шаг генерации пропускаем (клипы уже есть), делаем аугментацию и обучение:

```sh
python openwakeword/train.py --training_config marusya.yaml --augment_clips
python openwakeword/train.py --training_config marusya.yaml --train_model
# результат: my_models/marusya.onnx (+ .tflite)
```

Повторите для «Кати» и «Ларисы» — у каждого слова своя модель.

#### Шаг 4. Проверить на ПК

```python
import openwakeword, wave, numpy as np
from openwakeword.model import Model
openwakeword.utils.download_models()          # melspectrogram.onnx, embedding_model.onnx
m = Model(wakeword_models=["my_models/marusya.onnx"], inference_framework="onnx")
pcm = np.frombuffer(wave.open("test.wav").readframes(10**9), dtype=np.int16)
for i in range(0, len(pcm) - 1280, 1280):     # кадры по 80 мс
    s = m.predict(pcm[i:i + 1280])["marusya"]
    if s > 0.5: print(f"{i/16000:.2f} c  {s:.2f}")
```

Прогоните свои тестовые записи (должно ловить ≥ 90 %) и час-два ТВ/подкастов без слова
(ложных — меньше одного в час). Если ложных много — больше двойников и
`max_negative_weight`, если пропусков — больше записей с колонки.

#### Шаг 5. Поставить на колонку (план)

```sh
ssh root@192.168.0.161
/opt/bin/python3 -m pip install --target /data/opt/alice-station/vendor openwakeword onnxruntime
mkdir -p /data/alice-station/models/oww
```

С ПК: `scp my_models/*.onnx root@192.168.0.161:/data/alice-station/models/oww/` и туда же
`melspectrogram.onnx`, `embedding_model.onnx`.

Конфиг, когда в агенте появится движок:

```toml
[wakeword]
engine = "openwakeword"
models = { "маруся" = "/data/alice-station/models/oww/marusya.onnx",
           "катя"   = "/data/alice-station/models/oww/katya.onnx" }
threshold = 0.5
```

Риски именно Миди: колесо `onnxruntime` для aarch64 рассчитано на glibc 2.27+, а Entware
может оказаться старее — тогда pip не поставит его, и останется Vosk. По CPU одна голова
на Cortex-A55 должна занимать единицы процентов ядра — это оценка, не замер.

### 1.5. Сделать своё слово главным

Агент слушает своё слово только в **режиме ассистента**. Чтобы колонка после загрузки
откликалась на «Марусю», а Алиса молчала:

```toml
[assistant]
name = "Маруся"
default_mode = "assistant"   # после загрузки — сразу ассистент
alice_deaf = true            # если Алиса проснулась сама — отменить её
```

Вернуться к Алисе: «выключи ассистента» (без слова вызова) или кнопка mute. Включить
снова: «Алиса, включи ассистента».

Учтите: агент слушает **сырые** микрофоны (`alice_mic`), без эхоподавления Яндекса
(оно внутри `maind`, канал `vqe_0` на Миди пока не вытащен). Во время громкой музыки
или своего ответа слово ловится хуже, а голос ассистента, произносящий своё же имя, может
его разбудить — не вставляйте имя в ответы (оно есть в `enter_message`, это
нормально: в момент приветствия агент слово не слушает).

### 1.6. Проверка качества

1. Каждое слово по 20 раз с 1, 3 и 5 метров — считайте пропуски.
2. Час ТВ рядом с колонкой — смотрите в лог, сколько раз проснулся:
   `ssh root@192.168.0.161 'tail -f /data/alice-station/agent.log' | grep "слово активации"`.
3. Для «Ларисы»: 20 раз в **режиме Алисы** — если Алиса проснулась хотя бы 3–4 раза,
   замените слово.
4. Порог: начните с `0.75` (Vosk) / `0.5` (openWakeWord), меняйте на 0,05.

---

## 2. Голос TTS

### 2.1. Как устроено

Голос — это **профиль** в `[voice.profiles.*]`. У профиля есть движок (`engine` — это и
есть «сервис») и его параметры. Сменить голос, не меняя сервис, — значит поменять
параметры профиля или завести второй профиль на том же движке и переключаться между ними.

| `engine` | Сервис | Где голос | Где работает |
|---|---|---|---|
| `command` | Piper (или любая программа: текст → PCM) | файл модели `.onnx`, `--speaker`, скорость | **на колонке, офлайн** |
| `elevenlabs` | ElevenLabs | `voice` = voice_id, `model`, `speed` | облако |
| `http` | OpenAI-совместимый `/audio/speech` | `voice` (alloy, nova…) | облако / свой сервер |
| `cloud` | XiaoZhi | выбирается в консоли xiaozhi.me | облако |

Голос **штатной Алисы** синтезирует облако Яндекса. Как оставить этот синтез, но сменить
голос — в разделе [2.4](#24-синтез-алисы-с-другим-голосом).

Переключать профили: в панели `http://192.168.0.161:8060` (раздел «Мозг и голос»),
голосом («Маруся, смени голос на Дениса» — MCP-инструмент `self.voice.set_profile`) или
`curl -X POST http://192.168.0.161:8060/api/voice -d '{"profile":"denis"}'`.

### 2.2. Локальный Piper: сменить голос

Piper на колонку установщик пока не ставит, поэтому сначала один раз:

```sh
ssh root@192.168.0.161
mkdir -p /data/alice-station/piper && cd /data/alice-station/piper
wget https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz
gunzip -c piper_linux_aarch64.tar.gz | tar -x && rm piper_linux_aarch64.tar.gz   # busybox tar без -z
echo "привет" | ./piper/piper --model ru_RU-irina-medium.onnx --output-raw > /dev/null && echo ok
```

(если `wget` в busybox не умеет https — `/opt/bin/wget` из Entware или скачайте на ПК и `scp`).
Сборка Piper с glibc; на Миди её ещё не запускали — если не стартует, напишите, соберу.

Голоса — два файла на голос, `.onnx` и `.onnx.json`, из
[rhasspy/piper-voices → ru/ru_RU](https://huggingface.co/rhasspy/piper-voices/tree/main/ru/ru_RU):

| Голос | Пол | Файлы (medium, ~63 МБ) |
|---|---|---|
| irina | ж | `irina/medium/ru_RU-irina-medium.onnx` + `.json` |
| denis | м | `denis/medium/ru_RU-denis-medium.onnx` + `.json` |
| dmitri | м | `dmitri/medium/ru_RU-dmitri-medium.onnx` + `.json` |
| ruslan | м | `ruslan/medium/ru_RU-ruslan-medium.onnx` + `.json` |

```sh
B=https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU
for v in irina denis; do
  wget -O ru_RU-$v-medium.onnx      "$B/$v/medium/ru_RU-$v-medium.onnx"
  wget -O ru_RU-$v-medium.onnx.json "$B/$v/medium/ru_RU-$v-medium.onnx.json"
done
```

Профили — один сервис, разные голоса:

```toml
[voice]
profile = "irina"

[voice.profiles.irina]
engine = "command"
title = "Ирина"
rate = 22050                    # = audio.sample_rate из .onnx.json (medium — 22050, low — 16000)
cmd = ["/data/alice-station/piper/piper/piper",
       "--model", "/data/alice-station/piper/ru_RU-irina-medium.onnx",
       "--output-raw"]

[voice.profiles.denis]
engine = "command"
title = "Денис"
rate = 22050
cmd = ["/data/alice-station/piper/piper/piper",
       "--model", "/data/alice-station/piper/ru_RU-denis-medium.onnx",
       "--length_scale", "0.9",   # < 1 — быстрее, > 1 — медленнее
       "--output-raw"]
```

Тонкая настройка того же голоса — флаги в `cmd`:

| Флаг | Что меняет | Диапазон |
|---|---|---|
| `--length_scale` | темп | 0.7–1.4 (1.0 — как обучено) |
| `--noise_scale` | живость интонации | 0.3–0.9 (0.667) |
| `--noise_w` | разброс длительности звуков | 0.5–1.0 (0.8) |
| `--sentence_silence` | пауза между фразами, с | 0–0.5 |
| `--speaker N` | номер диктора в многоголосой модели (`num_speakers` в `.json` > 1) | 0… |

Проверка без агента: `echo "Проверка голоса" | <cmd> | aplay -q -f S16_LE -c 1 -r 22050`.
После правки конфига — `/etc/init.d/S99zzalice-station restart`.

`rate` **обязан** совпадать с частотой модели, иначе голос «бурундук» или «бас».

#### Свой голос для Piper (дообучение)

Если нужен голос, которого нет в списке, — дообучите русскую модель Piper на записях одного
диктора (свой голос или человека с его согласия):

1. 30–60 минут чистых записей: 300–1000 фраз по 3–10 с, тихая комната, один микрофон.
   Формат `metadata.csv`: `файл|Текст фразы.` (LJSpeech).
2. На ПК с видеокартой: репозиторий Piper (обучение — `piper_train`, см. `TRAINING.md`),
   `preprocess` с `--language ru --sample-rate 22050 --single-speaker`, затем
   `piper_train` с `--resume_from_checkpoint` от чекпойнта `ru_RU-irina-medium` или
   `denis` (чекпойнты лежат в датасете `rhasspy/piper-checkpoints`), 1000–2000 эпох
   поверх него.
3. Экспорт `export_onnx` → `my_voice.onnx` + скопировать `config.json` как
   `my_voice.onnx.json`.
4. Положить в `/data/alice-station/piper/` и сделать ещё один профиль `engine = "command"`.

Сервис и агент не меняются — только файл модели.

### 2.3. ElevenLabs: сменить голос

Ключ и `voice_id` вводятся в панели (хранятся в `state.json`), либо в конфиге:

```toml
[voice.profiles.eleven]
engine = "elevenlabs"
title = "ElevenLabs — Мария"
voice = "<voice_id>"              # Voices → голос → «ID»
model = "eleven_flash_v2_5"       # быстрее; eleven_multilingual_v2 — качественнее, медленнее
rate = 24000                      # 16000 | 22050 | 24000 | 44100
speed = 1.0                       # 0.7–1.2

[voice.profiles.eleven_man]
engine = "elevenlabs"
title = "ElevenLabs — Пётр"
voice = "<другой voice_id>"
model = "eleven_flash_v2_5"
rate = 24000
```

Где брать голоса: [Voice Library](https://elevenlabs.io/app/voice-library) с фильтром
Russian → «Add to my voices» → копируете ID. Свой голос — Instant Voice Clone по
1–2 минутам записи (только свой или с согласия человека). `api_key` в профиле можно
не писать: агент подставит ключ из панели, а если в профиле нет `voice` — и voice_id из
панели. Поэтому второй и следующие профили ElevenLabs задавайте с явным `voice`.

Стабильность и «похожесть» (`stability`, `similarity_boost`) агент сейчас не передаёт —
только `speed`. Если нужно, добавлю эти поля в профиль.

### 2.4. Синтез Алисы с другим голосом

Оставить сервис синтеза Яндекса и сменить только голос можно, но **не для её собственных
ответов**. Ответ на «Алиса, какая погода» приходит из облака уже готовым звуком
(`TtsSpeak`), голос выбирает сервер, и локально его не перехватить.

Сменить голос можно для **текста, который мы сами отдаём Алисе озвучить**: фразы агента,
сценарии, уведомления. Облачный TTS Яндекса понимает разметку `<speaker …>` (та же, что в
навыках Алисы; так её использует интеграция YandexStation для Home Assistant):

```text
<speaker voice="zahar">Всем привет, меня зовут Захар.
<speaker effect="megaphone">Внимание! <speaker effect="-">Обычный голос.
<speaker is_whisper="true">Хозяин, уже утро.
```

| Что | Значения |
|---|---|
| `voice` | `zahar` проверен в YandexStation. Другие голоса Яндекса (Ермил, Джейн, Омаж, Филипп, Алёна…) по id вроде `ermil`, `jane`, `omazh`, `filipp`, `alena` — **не проверены** |
| `effect` | `behind_the_wall`, `hamster`, `megaphone`, `pitch_down`, `psychodelic`, `pulse`, `train_announce`; `-` — выключить эффект |
| `is_whisper` | `true` — шёпот |

!!! warning
    По опыту YandexStation, неверное имя голоса «вешает» синтез: колонка замолкает до
    команды «стоп» или перезагрузки. Пробуйте голоса по одному.

Проверить на колонке — тем же локальным каналом `127.0.0.1:9875`, которым агент уже
озвучивает фразы (`QuasarIpc.vocalize`, команда `START_VOCALIZE`):

```sh
ssh root@192.168.0.161
cd /data/opt/alice-station/agent && /opt/bin/python3 -c '
import socket, time
from alice_station.quasar_ipc import vocalize_frame
s = socket.create_connection(("127.0.0.1", 9875))
s.sendall(vocalize_frame("<speaker voice=\"zahar\">Проверка голоса Захара."))
time.sleep(5)'
```
Работает ли разметка через `START_VOCALIZE`, а не через облачный путь Home Assistant, на Миди
ещё не проверено. Если голос сменился, можно добавить в агент профиль `engine = "alice"`
с параметром `voice`: ответы ассистента будет озвучивать синтез Яндекса нужным голосом.

### 2.5. Какой выбрать

| Нужно | Профиль |
|---|---|
| Работает без интернета, мгновенно | Piper `irina`/`denis` medium |
| Самый живой голос | ElevenLabs `eleven_multilingual_v2` |
| Быстрый живой голос | ElevenLabs `eleven_flash_v2_5` (по умолчанию) |
| Свой голос | Piper дообучение (офлайн) или ElevenLabs Voice Clone |

---

*Данные о прошивке, микрофонах и спотерах Алисы — из
[Yandex Station Extended](https://github.com/Marker284/yandex-station-extended)
(Марк Писаренко и участники, CC BY 4.0).*
