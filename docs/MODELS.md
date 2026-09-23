# Локальные модели, источники и подготовка без передачи данных

Состояние источников и загрузок проверено 23 сентября 2026 года. Этот документ различает наличие весов, успешный запуск и правильность результата. Поддержка языка в карточке модели сама по себе не подтверждает качество на наших записях.

## Выбор и фактическая готовность

| Этап | Основной вариант | Один запасной вариант | Что проверено |
|---|---|---|---|
| Распознавание | Многоязычный `Systran/faster-whisper-small`, CPU/int8 | Многоязычный `Systran/faster-whisper-base`, CPU/int8 | Small реально обработал синтетическую русскую запись. Base не скачивался и не проверялся |
| Голоса | `wespeaker_en_voxceleb_resnet34_LM.onnx` через sherpa-onnx + локальная кластеризация NumPy | `3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx` через тот же extractor | WeSpeaker дал ожидаемые роли 6/6 реплик на двух синтетических TTS-голосах. Запасной ERes2Net не проверялся |
| Анализ | `Qwen3-4B-Q4_K_M.gguf`, локальный llama.cpp CPU; содержательная приёмка открыта | `Qwen2.5-1.5B-Instruct-Q4_K_M`, локальный llama.cpp CPU | Qwen3: 3/3 поручения контрольной русской TTS-записи верны после проверки ASR, 68,86 с. KK_EXPLICIT/TOMORROW_YEAR на финальном промпте отклонены; универсальная готовность не подтверждена. 1.5B непригодна по результатам проверки смысла |

Запасные варианты не переключаются скрыто. При замене модели повторяются контрольные случаи и фиксируется имя/версия. Более маленький ASR может уменьшить время и память, но ухудшить распознавание, особенно казахской и смешанной речи. У запасной LLM уже обнаружены ошибки смысла: считать её достаточной только из-за меньшего размера нельзя.

## Оборудование и ресурсы

Проверенная машина: Windows 11, Intel Core i7-13620H, около 15,7 ГБ RAM, без NVIDIA. CPython 3.12.14. CUDA и PyTorch для выбранной цепочки не нужны. Используются CTranslate2 CPU/int8, PyAV с библиотеками FFmpeg внутри wheel, sherpa-onnx CPU и готовый Windows CPU runtime llama.cpp.

В текущей среде реально загружены: faster-whisper 1.2.1, CTranslate2 4.8.2, sherpa-onnx 1.13.8, PyAV 18.1.0, NumPy 2.3.5. Основные прямые зависимости фиксируются в `requirements-app.txt`; номера здесь описывают проверенную среду, а не все возможные платформы.

У разработчика faster-whisper есть ориентир small/int8 около 1,5 ГБ RAM на другом Intel CPU. Наш собственный пик RAM пока не измерен. Размер GGUF/ONNX на диске не равен пику памяти процесса; для LLM добавляются контекст, KV-кэш и служебные буферы. Не запускайте одновременно несколько обработок. Последовательная обработка освобождает ASR перед загрузкой extractor голосов. [Официальный benchmark и CPU/int8](https://github.com/SYSTRAN/faster-whisper).

Для подготовки оставьте ориентировочно 10 ГБ свободного диска под веса, временные загрузки, Python-окружение, wheels и результаты. Набор small + WeSpeaker + Qwen3 занимает около 3,01 ГБ только весов/сопутствующих файлов, без среды; сохранённый запасной Qwen2.5 добавляет ещё 1,12 ГБ. Это планирование места, не измерение RAM.

Единственный завершённый аудио-замер: 29,018 секунды синтетического русского диалога обработаны холодным вызовом за 101,111 секунды. Две ошибки имени и две ошибки числа существительного требуют просмотра. Подробности — [audio.md](audio.md). Эти результаты нельзя переносить на живую, казахскую или смешанную речь.

## Распознавание: исходники, версии, лицензия

Основные веса — [официальная конверсия Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) из OpenAI Whisper small. Карточка конверсии указывает MIT; [исходная лицензия Whisper](https://github.com/openai/whisper/blob/main/LICENSE). Конверсия сохранена в FP16, во время загрузки используется CPU `compute_type="int8"`.

Зафиксированный снимок: `536b0662742c02347bc0e980a01041f333bce120`. Полный URL каждого скачанного файла записан в локальном `models/download-manifest.json`.

| Файл | Фактически скачано, байт | SHA-256 фактического файла |
|---|---:|---|
| `models/whisper-small/model.bin` | 483 546 902 | `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671` |
| `models/whisper-small/config.json` | 2 370 | `b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828` |
| `models/whisper-small/tokenizer.json` | 2 203 239 | `fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab` |
| `models/whisper-small/vocabulary.txt` | 459 861 | `34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913` |
| `models/whisper-small/README.md` | 1 998 | `329373481008c7c38654aff8ecdcf0163c211557cc7ba8e2ef6f2f84b4f75ec8` |

Запасной вариант — [Systran/faster-whisper-base](https://huggingface.co/Systran/faster-whisper-base), также MIT и многоязычный. Проверенный по официальному API снимок — `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`, `model.bin` 145 217 532 байта, опубликованный LFS SHA-256 `d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9`. Эти сведения получены из метаданных, не из локального скачивания. Нужны также config/tokenizer/vocabulary из этого же снимка. Base ещё не проходил наши тесты. Модели с суффиксом `.en` не подходят.

## Голоса: WeSpeaker и запасной ERes2Net

Основной файл скачан из [официального релиза sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models):

- [wespeaker_en_voxceleb_resnet34_LM.onnx](https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx).
- Размер: **26 530 550 байт**.
- Локальный SHA-256: `e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012`.
- [Исходная карточка WeSpeaker](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM) указывает **CC-BY-4.0**. [Условия лицензии](https://creativecommons.org/licenses/by/4.0/).

Атрибуция используемых весов: **WeSpeaker contributors, wespeaker-voxceleb-resnet34-LM; исходная модель WeSpeaker, преобразование ONNX опубликовано k2-fsa/sherpa-onnx; CC-BY-4.0. Команда QonimAI веса не дообучала.** Сохраните эту атрибуцию и ссылки при передаче модели. Лицензии кода WeSpeaker/sherpa-onnx не заменяют лицензию весов.

Запасной конкретный файл — [3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx](https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx), **26 485 263 байта** по GitHub API. [Исходная модель iic/ERes2Net на ModelScope](https://modelscope.cn/models/iic/speech_eres2net_sv_en_voxceleb_16k); поле License официального API содержит **Apache License 2.0**, карточка описывает обучение на VoxCeleb2 и вход 16 кГц. Код [3D-Speaker](https://github.com/modelscope/3D-Speaker) также Apache-2.0. Этот ONNX не скачан и не запускался; локального SHA и подтверждённого результата нет. До замены нужно скачать, вычислить SHA, проверить загрузку extractor и повторить записи с двумя голосами.

У release-tag sherpa список assets может пополняться: для воспроизводимости важен SHA самого файла. [Документация extractor](https://k2-fsa.github.io/sherpa/onnx/speaker-identification/index.html) и [официальный пример Python](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/speaker-identification.py) описывают используемый API. Обе модели обучались на данных с английской речью; применимость к русско-казахским встречам требует измерений. Имена людей ни одна модель не устанавливает.

## Анализ: статус кандидатов и llama.cpp

Первоначально скачана [Qwen2.5-1.5B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF), Apache-2.0, снимок `91cad51170dc346986eccefdc2dd33a9da36ead9`:

- `qwen2.5-1.5b-instruct-q4_k_m.gguf`: **1 117 320 736 байт**.
- Фактический SHA-256: `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`.
- Сохранён `models/qwen/LICENSE` (11 343 байта).
- Первый содержательный запуск не прошёл: обнаружены ошибки смысла. Это неприемлемый результат для автоматической сдачи; JSON-валидатор не доказывает полноту и истинность извлечённых поручений.

Более сильный кандидат — [официальная Qwen3-4B-GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF), Apache-2.0. Зафиксированный снимок `bc640142c66e1fdd12af0bd68f40445458f3869b`, [файл Qwen3-4B-Q4_K_M.gguf](https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf):

- **2 497 280 256 байт** — размер скачанного файла, совпадает с официальным API.
- Фактический SHA-256 совпал с LFS-метаданными: `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5`.
- Сохранён `models/qwen/Qwen3-LICENSE` (11 544 байта), SHA-256 `5de36594c10839788a8c589443a8ef9d8b8d17c65a1b5807206ae037fc36c6bd`.
- Подготовка и целостность файла подтверждены обновлённым `models/download-manifest.json`. Это ещё не утверждение о качестве анализа.
- Принят для ограниченного демонстрационного сценария: три поручения из синтетической русской записи извлечены правильно за 68,86 с после явной проверки ASR. Отдельные KK_EXPLICIT и TOMORROW_YEAR на финальном промпте не прошли. Качество не принято в общем случае; см. docs/local_analysis.md. Автозамена ответов модели эталонным fixture запрещена.

Локальный runtime — [llama.cpp b11125, Windows CPU x64](https://github.com/ggml-org/llama.cpp/releases/tag/b11125), [MIT-лицензия проекта](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE). Фактически скачан архив `llama-b11125-bin-win-cpu-x64.zip`: **18 558 152 байта**, SHA-256 `de1f437d53c74cdbda8e2071e3f468e31eee26b12a9d73b12d4969c4f0271372`. Runtime и веса модели имеют отдельные лицензии.

HTTP API анализатора слушает только `127.0.0.1:8081`; это процесс на том же компьютере, внешний облачный API не используется. Приложение разрешает loopback-адреса, отключает использование HTTP proxy и не следует перенаправлениям. Ссылка на локальный API не означает разрешение выставлять его в интернет.

## Первоначальное скачивание и последующий запуск

Первоначальная подготовка требует интернета **только для загрузки кода, библиотек и публичных весов**. Наши записи и транскрипты в эту процедуру не входят. Перечисленные основные файлы получены по публичным ссылкам без аккаунта и согласования доступа; если источник впоследствии потребует доступ, подготовку останавливают и явно сообщают об этом.

Из корня репозитория, PowerShell, в установленном окружении:

```powershell
& '.\.venv\Scripts\python.exe' scripts/setup_models.py
Get-Content -LiteralPath models/download-manifest.json
Get-FileHash -LiteralPath models/whisper-small/model.bin -Algorithm SHA256
Get-FileHash -LiteralPath models/speaker/wespeaker_en_voxceleb_resnet34_LM.onnx -Algorithm SHA256
```

Текущий downloader скачивает small, WeSpeaker и Qwen3-4B; `scripts/start-local.ps1` также указывает на локальный Qwen3-4B GGUF. В downloader настроено сравнение ожидаемого SHA для `model.bin`, WeSpeaker ONNX, Qwen3 GGUF и архива llama.cpp. Остальные файлы перечисляются с фактическими SHA в манифесте. Перед переносом проверьте целостность всего набора; старый Qwen2.5 не должен запускаться незаметно вместо указанного Qwen3.

Для машины без интернета заранее подготовьте wheels под её ОС, архитектуру и версию Python, а также каталоги моделей и runtime. Например, для той же Windows x64/CPython 3.12:

```powershell
& '.\.venv\Scripts\python.exe' -m pip download --only-binary=:all: -r requirements-lock.txt --dest wheels/app
```

После переноса этого набора на целевую машину создайте чистое окружение её локальным Python и установите пакеты без package index:

```powershell
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install --no-index --find-links wheels/app -r requirements-lock.txt
```

Полная чистая установка 56 закреплённых пакетов проверена в .venv-clean через --no-index: pip check, импорты и 299 тестов прошли, 2 пропущены. Подготовка wheels использовала интернет/кэш. Это не проверка модели при отключённой сети ОС; журнал — docs/submission/evidence/clean-install.txt.

При обработке ASR получает существующий локальный каталог и `local_files_only=True`; наличие `tokenizer.json` обязательно. VAD поставляется внутри faster-whisper. sherpa-onnx загружает существующий `.onnx`, llama.cpp получает путь к локальному `.gguf`. PyAV ограничен протоколами `file,pipe`. Приложение не вызывает downloader и не должно использовать имена Hugging Face вместо локальных путей.

Контроль после подготовки: отключить внешний интернет на демонстрационной машине, сохранив loopback; запустить локальный анализатор и UI; провести вымышленную запись до DOCX; зафиксировать время и ошибки. Программная блокировка отдельных Python socket-методов сама по себе не является такой проверкой ОС.

## Критерий приёмки моделей

Нужны отдельные записи RU, KK и RU/KK с известным текстом, двумя говорящими и ожидаемыми поручениями. Для каждой проверяются текст, переключения голосов, обращения к другому человеку, отсутствующие/относительные сроки, отсутствие придуманных решений, время, экспорт и потребление ресурсов. Казахские примеры проверяет понимающий язык участник. До этого качество этих языковых режимов считается неизвестным, а ошибки модели не маскируются валидным JSON или сохранённой демонстрацией.
