# План приёмки QonimAI

**DRAFT — для согласования координатором.** Основание: CONTRACT_PROPOSAL.md версии `qonimai.analysis.draft-1`. Приложение, модели и DOCX пока отсутствуют. Синтетические ожидания не являются результатами модели. Проверки продукта ниже **не запускались**; фактически выполненные проверки документов перечисляются отдельно в REPORT.md.

## 1. Как использовать план

Сначала координатор утверждает контракт и оборудование, затем участники подключают реальные модули. Участник 2 проверяет анализ, участник 1 — аудио и говорящих, участник 3 — редактирование и экспорт; координатор сводит результаты. Человек со знанием казахского проверяет казахский текст и смысл ожиданий до использования их как языкового эталона. Саммари сравнивают по фактам, пропускам и необоснованным добавлениям, а не буквальному совпадению строк.

Все записи для этих испытаний смоделированы специально. Аудио и полученные транскрипты обрабатываются локально или в закрытом контуре, хранятся вне Git/Live Share и не передаются в ИИ-чаты или внешние API. До записи людей уведомляют о записи и обработке ИИ. В общий отчёт попадают только код, заранее вымышленные текстовые примеры и диагностика без чувствительных данных.

## 2. Матрица будущих проверок

| Сценарий | Вход | Ожидаемое поведение | Способ проверки | Фактический статус |
|---|---|---|---|---|
| Автор и исполнитель | RU_EXPLICIT | spk1/p1 поручает p2; дата 2026-09-30; перекрытие реплик допустимо | Запустить analyze; проверить факты, ссылки, назначение, дату | не запускалось |
| Казахский | KK_EXPLICIT | После языковой проверки — отчёт, p2, 2026-09-30 | Проверка носителем/знающим язык человеком, затем analyze; пока language_review pending | не запускалось |
| Смешанная речь | MIXED | Согласие на казахском связано с поручением на русском | Языковая проверка, затем analyze без потери фактов между языками | не запускалось |
| Нет срока | NO_DUE | Оба срока null, missing_due; UI и DOCX показывают уточнение | Проверить поля, отображение и экспорт | не запускалось |
| Исполнитель не говорит | NO_DUE | Назначение p2 допустимо по явному обращению к подтверждённому участнику | Проверить отсутствие реплик p2 и сохранение назначения | не запускалось |
| Относительный срок | TOMORROW_YEAR, TOMORROW_MONTH | 2027-01-01 и 2026-10-01; используется локальный день | Запустить анализ; сверить независимый календарный расчёт; поменять дату компьютера без изменения входа | не запускалось |
| Недостаток даты/зоны | MISSING_CONTEXT | due_date null, missing_meeting_context | Проверить отсутствие подстановки текущей даты | не запускалось |
| Неоднозначная пятница | AMBIGUOUS_FRIDAY | Исходный срок сохранён, дата null | Проверить ambiguous_due и отсутствие догадки | не запускалось |
| Нет поручений | NO_TASKS | ok, tasks = [], decisions = []; саммари обсуждения | Проверить отсутствие выдуманных действий/решений | не запускалось |
| Неизвестный исполнитель | UNKNOWN_ASSIGNEE | assignee_id null, unknown_assignee | Проверить, что автор не назначен автоматически | не запускалось |
| Одинаковые имена | DUPLICATE_NAME | assignee_id null, ambiguous_assignee | Переставить участников в списке; неоднозначность сохраняется | не запускалось |
| Неподтверждённое «я» | UNCONFIRMED_SELF | assignee_id null, unconfirmed_assignee | Проверить, что подсказка имени не стала личностью; после подтверждения повторить проверку | не запускалось |
| Отмена и гипотеза | CANCELLED, HYPOTHETICAL | tasks = []; отмена в decisions только в CANCELLED | Проверить обе исходные реплики; отсутствие действующего поручения | не запускалось |
| Нет речи | NO_SPEECH и будущая непустая запись тишины | no_speech; пустые списки, экспорт NO_CONTENT | Отдельно проверить структурный вход и настоящий локальный запуск аудио | не запускалось |
| Невалидный вход | Копия fixture в памяти: дубли id, битая ссылка, отрицательный start, end < start, NaN, неизвестный speaker_id, неверный тип/null, дата 2026-02-30, неверная зона/смещение | INVALID_INPUT, понятная причина без чувствительных данных | По одному нарушению на запуск; корректное перекрытие оставить положительным контролем | не запускалось |
| Ошибка вывода модели | Подставленный на границе анализатора повреждённый JSON, неверная схема, пустая строка, необоснованный факт | INVALID_MODEL_JSON, INVALID_MODEL_OUTPUT, EMPTY_MODEL_OUTPUT соответственно; data null | Контролируемая инъекция ошибки после реализации адаптера; не выдавать fixture как успешный ответ | не запускалось |
| Файлы и ресурсы | Пустой файл, неподдерживаемый формат, повреждённая запись, недоступные веса, контролируемая ошибка памяти | EMPTY_FILE, UNSUPPORTED_FORMAT, INVALID_INPUT, MODEL_UNAVAILABLE, RESOURCE_EXHAUSTED | Изолированные ошибки; нехватку памяти сначала имитировать на границе модуля, не исчерпывать всю RAM машины | не запускалось |
| ASR: три языковых режима | Три короткие смоделированные записи ru/kk/ru-kk, минимум два говорящих в каждой; эталонный текст с проверкой человеком | Получены локальные сегменты, сохранены смысл, числа, имена, даты | Зафиксировать пропуски/замены; отдельно оценить слова с поручениями; пороги согласовать | не запускалось |
| Диаризация | Те же записи, размеченные смены/перекрытия говорящих | Голоса разделены; автоматические номера не стали именами | Сопоставить границы и голоса с разметкой отдельно от ошибок ASR; человек подтверждает связи | не запускалось |
| Правки источника | В RU_EXPLICIT изменить текст s1, затем отдельно удалить/разделить s1 | revision увеличена; анализ устарел; битые/смыслово устаревшие ссылки замечены | Экспорт до повторной проверки даёт STALE_ANALYSIS; проверить даже сохранённый id с изменённым текстом | не запускалось |
| Правки имён и даты | Изменить подтверждение «я»; переименовать/удалить участника; изменить дату TOMORROW_YEAR | Проверены назначения/ссылки; относительный срок пересчитан; reviewed_revision сброшена | Проверить согласованность display_name, assignee_id и сроков; затем новое подтверждение | не запускалось |
| Правки поручений | Изменить описание, срок или порядок в текущем снимке | Правка сохранена после проверки источников; неподтверждённые добавления не маскируются извлечением | Проверить новый source_revision, REVIEW_REQUIRED до подтверждения; повторный анализ не затирает правки без просмотра | не запускалось |
| Сквозной путь | Смоделированная запись → сегменты → анализ → правки → DOCX | В файле последний подтверждённый текст, саммари, решения и поручения | Открыть готовый DOCX, вручную сопоставить с актуальным снимком и исходной вымышленной записью | не запускалось |
| Unicode и порядок | После реализации: вымышленный текст с Ә Ғ Қ Ң Ө Ұ Ү Һ І ә ғ қ ң ө ұ ү һ і; не менее двух поручений с разными сроками/исполнителями | Символы без замены; порядок, имена, даты и уточнения сохранены | Извлечь текст DOCX и визуально открыть файл; проверить таблицы, переносы, исправленную реплику | не запускалось |
| Ошибка экспорта | Недоступная для записи папка либо контролируемый отказ записи | EXPORT_FAILED, отсутствует ложное сообщение о готовом DOCX | Проверить UI, наличие/целостность файла, возможность повторить после исправления пути | не запускалось |
| Скорость и память | Фиксированная запись, выбранные координатором локальные модели | Реальные замеры каждого этапа и ограничения | Протокол замера из раздела 3 | не запускалось |
| Чистая установка | Новое окружение по будущему README, заранее подготовленные веса | Повторяемый запуск без неописанных ручных действий | Участник, не делавший установку, следует README и фиксирует команды/версии | не запускалось |
| Без внешнего интернета | Отдельный запуск после подготовки среды и весов | Все этапы и DOCX работают при отключённом внешнем интернете | Отключить внешний доступ/проверить блокировку, перезапустить приложение и модели с холодного состояния; проверить отсутствие скрытых загрузок/обращений | не запускалось |
| Резерв демонстрации | Результат действительно выполненного запуска либо запись экрана | Явная подпись «Сохранённый запуск», известны дата и версии | Открыть резерв без сети и отличить от живого запуска; не выдавать fixture за модель | не запускалось |

## 3. Протокол будущего замера и решение о приёмке

Для каждого запуска записывать: commit приложения, ОС, CPU, RAM, GPU и выделенную VRAM (не путать с памятью встроенной графики), свободную память перед запуском; имена/версии/размеры файлов выбранных моделей, версии зависимостей, CPU/GPU и режим вычислений. Указать длительность/формат/язык записи, число говорящих и наличие перекрытий. Измерять отдельно загрузку моделей, подготовку аудио, ASR, диаризацию, анализ и экспорт, итоговое время, пиковые RAM/VRAM и ошибки. Ручную проверку учитывать отдельно от вычислений.

Отделять холодный запуск от повторного с уже загруженными моделями. Записывать число повторов и разброс; единичный короткий запуск не подтверждает скорость длинной записи. Если инструмент не измеряет VRAM, писать «не измерено», а не 0. Отношение времени обработки к длительности записи можно использовать как описательную метрику. Численных порогов жюри здесь нет; любые будущие пороги качества и времени утверждает координатор с учётом оборудования и ТЗ.

Приёмку проводить по фактам: подтверждаемые поручения/решения, корректные неизвестные значения, явные ошибки вместо подмены результатом, сохранность правок и успешная воспроизводимая цепочка. Результаты языковых проверок подписывает проверяющий человек. Изменять status кейса с pending до его проверки нельзя.

## 4. Воспроизводимая статическая проверка черновика

Следующий блок запускается из корня репозитория в доступном PowerShell 7.6.5 без установки зависимостей. Он проверяет текущий набор примеров; это не полноценный валидатор будущего продукта и не проверка правильности казахского языка. В REPORT.md фиксируется вывод реально выполненной команды.

<!-- fixture-check:start -->
```powershell
$ErrorActionPreference = 'Stop'
$root = Get-Content docs/agent-b/SYNTHETIC_CASES.json -Raw | ConvertFrom-Json -DateKind String
$script:checks = 0
function Check($ok, $why) {
    $script:checks++
    if (-not $ok) { throw $why }
}
function Unique($items, $key, $where) {
    $ids = @($items | ForEach-Object { $_.$key })
    Check (@($ids | Where-Object { [string]::IsNullOrWhiteSpace($_) }).Count -eq 0) "$where empty id"
    Check (@($ids | Sort-Object -Unique).Count -eq $ids.Count) "$where duplicate id"
}
function Keys($obj, $names, $where) {
    Check ((@($obj.PSObject.Properties.Name | Sort-Object) -join ',') -ceq (@($names -split ',' | Sort-Object) -join ',')) "$where keys"
}
Check ($root.metadata.synthetic -eq $true -and $root.metadata.model_output -eq $false) 'synthetic metadata'
Check ($root.cases.Count -ge 8) 'minimum cases'
Unique $root.cases 'id' 'cases'
$allowedReasons = @('missing_due','ambiguous_due','missing_meeting_context','unknown_assignee','ambiguous_assignee','unconfirmed_assignee')
foreach ($case in $root.cases) {
    $tx = $case.input; $out = $case.expected; $data = $out.data
    Keys $tx 'schema_version,revision,meeting,participants,speakers,segments' $case.id
    Keys $tx.meeting 'started_at,timezone' 'meeting'
    Keys $out 'status,data,error' 'result'
    Keys $data 'source_revision,summary,decisions,tasks' 'data'
    Check ($tx.schema_version -ceq 'qonimai.analysis.draft-1') 'schema'
    Check ($tx.revision -ge 1 -and $tx.revision -is [long] -and $data.source_revision -eq $tx.revision) 'revision'
    Check ($out.status -in @('ok','no_speech') -and $null -eq $out.error) 'fixture success state'
    Check (-not [string]::IsNullOrWhiteSpace($case.check)) 'case explanation'
    if ($case.language -in @('kk','ru-kk')) { Check ($case.language_review -ceq 'pending') 'language review' }
    Unique $tx.participants 'id' 'participants'
    Unique $tx.speakers 'speaker_id' 'speakers'
    Unique $tx.segments 'id' 'segments'
    $pids = @($tx.participants.id); $sids = @($tx.speakers.speaker_id); $refs = @($tx.segments.id)
    foreach ($p in $tx.participants) { Keys $p 'id,name' 'person'; Check (-not [string]::IsNullOrWhiteSpace($p.name)) 'participant name' }
    foreach ($s in $tx.speakers) {
        Keys $s 'speaker_id,participant_id,display_name,confirmed' 'speaker'
        Check ($s.confirmed -is [bool]) 'confirmed type'
        if ($s.confirmed) {
            Check ($s.participant_id -cin $pids) 'confirmed participant'
            $person = $tx.participants | Where-Object id -CEQ $s.participant_id
            Check ($s.display_name -ceq $person.name) 'confirmed name'
        } else { Check ($null -eq $s.participant_id) 'unconfirmed participant must be null' }
    }
    $last = -1
    foreach ($g in $tx.segments) {
        Keys $g 'id,start,end,speaker_id,text' 'segment'
        Check ($g.start -is [ValueType] -and $g.end -is [ValueType] -and [double]::IsFinite($g.start) -and [double]::IsFinite($g.end)) 'numeric finite times'
        Check ($g.start -ge 0 -and $g.end -ge $g.start -and $g.start -ge $last) 'time order'
        $last = $g.start
        Check ($g.speaker_id -cin $sids) 'segment speaker'
        Check (-not [string]::IsNullOrWhiteSpace($g.text)) 'segment text'
    }
    foreach ($list in @('summary','decisions','tasks')) {
        Check ($data.$list -is [array]) 'list type'
        Unique $data.$list 'id' $list
        foreach ($item in $data.$list) {
            Check ($item.source_segment_ids -is [array] -and $item.source_segment_ids.Count -gt 0) 'nonempty evidence'
            Check (@($item.source_segment_ids | Sort-Object -Unique).Count -eq $item.source_segment_ids.Count) 'duplicate evidence'
            foreach ($ref in $item.source_segment_ids) { Check ($ref -cin $refs) 'evidence exists' }
            if ($list -ne 'tasks') { Keys $item 'id,text,source_segment_ids' 'fact'; Check (-not [string]::IsNullOrWhiteSpace($item.text)) 'fact text' }
        }
    }
    $localDate = $null
    if ($null -ne $tx.meeting.timezone) { $tz = [TimeZoneInfo]::FindSystemTimeZoneById($tx.meeting.timezone) }
    if ($null -ne $tx.meeting.started_at) {
        Check ($tx.meeting.started_at -cmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$') 'timestamp format'
        $instant = [DateTimeOffset]::ParseExact($tx.meeting.started_at, "yyyy-MM-dd'T'HH:mm:sszzz", [cultureinfo]::InvariantCulture)
        if ($null -ne $tx.meeting.timezone) {
            $local = [TimeZoneInfo]::ConvertTime($instant,$tz)
            Check ($local.Offset -eq $instant.Offset) 'timezone offset'
            $localDate = $local.Date
        }
    }
    foreach ($task in $data.tasks) {
        Keys $task 'id,description,author_speaker_id,assignee_id,due_text,due_date,source_segment_ids,requires_clarification,clarification_reasons' 'task'
        Check (-not [string]::IsNullOrWhiteSpace($task.description)) 'task description'
        $evidence = @($tx.segments | Where-Object { $_.id -cin $task.source_segment_ids })
        Check ($task.author_speaker_id -cin @($evidence.speaker_id)) 'author evidence'
        Check ($null -eq $task.assignee_id -or $task.assignee_id -cin $pids) 'assignee reference'
        Check ($task.clarification_reasons -is [array] -and $task.requires_clarification -is [bool]) 'clarification types'
        Check ($task.requires_clarification -eq ($task.clarification_reasons.Count -gt 0)) 'clarification flag'
        Unique $task.clarification_reasons 'code' 'reasons'
        $codes = @($task.clarification_reasons.code)
        foreach ($reason in $task.clarification_reasons) {
            Keys $reason 'code,message' 'reason'
            Check ($reason.code -cin $allowedReasons -and -not [string]::IsNullOrWhiteSpace($reason.message)) 'reason content'
        }
        if ($null -eq $task.assignee_id) { Check (@($codes | Where-Object { $_ -in @('unknown_assignee','ambiguous_assignee','unconfirmed_assignee') }).Count -eq 1) 'missing assignee reason' }
        if ($null -eq $task.due_date) { Check (@($codes | Where-Object { $_ -in @('missing_due','ambiguous_due','missing_meeting_context') }).Count -eq 1) 'missing date reason' }
        else {
            Check ($task.due_date -cmatch '^\d{4}-\d{2}-\d{2}$') 'date format'
            [datetime]::ParseExact($task.due_date,'yyyy-MM-dd',[cultureinfo]::InvariantCulture) | Out-Null
        }
        if ($null -eq $task.due_text) { Check ($null -eq $task.due_date -and 'missing_due' -cin $codes) 'missing due text' }
        else { Check (@($evidence | Where-Object { $_.text.Contains($task.due_text) }).Count -gt 0) 'due text evidence' }
        if ($task.due_text -ceq 'завтра') {
            if ($null -eq $localDate) { Check ($null -eq $task.due_date -and 'missing_meeting_context' -cin $codes) 'tomorrow missing context' }
            else { Check ($task.due_date -ceq $localDate.AddDays(1).ToString('yyyy-MM-dd')) 'tomorrow calendar' }
        }
    }
    if ($out.status -eq 'no_speech') { Check ($tx.segments.Count -eq 0 -and $data.summary.Count -eq 0 -and $data.decisions.Count -eq 0 -and $data.tasks.Count -eq 0) 'no speech empty' }
    else { Check ($tx.segments.Count -gt 0 -and $data.summary.Count -gt 0) 'speech summary' }
}
$map = @{}; foreach ($case in $root.cases) { $map[$case.id] = $case }
Check ($map.RU_EXPLICIT.expected.data.tasks[0].author_speaker_id -ceq 'spk1' -and $map.RU_EXPLICIT.expected.data.tasks[0].assignee_id -ceq 'p2') 'author differs from assignee'
foreach ($id in @('NO_TASKS','CANCELLED','HYPOTHETICAL','NO_SPEECH')) { Check ($map[$id].expected.data.tasks.Count -eq 0) "$id no tasks" }
Check ($map.CANCELLED.expected.data.decisions.Count -eq 1) 'cancel decision'
foreach ($id in @('UNKNOWN_ASSIGNEE','DUPLICATE_NAME','UNCONFIRMED_SELF')) { Check ($null -eq $map[$id].expected.data.tasks[0].assignee_id) "$id null assignee" }
$dates = @{ RU_EXPLICIT='2026-09-30'; KK_EXPLICIT='2026-09-30'; MIXED='2026-09-25'; TOMORROW_YEAR='2027-01-01'; UNKNOWN_ASSIGNEE='2026-09-30'; DUPLICATE_NAME='2026-09-30'; UNCONFIRMED_SELF='2026-09-30'; TOMORROW_MONTH='2026-10-01' }
foreach ($id in $dates.Keys) { Check ($map[$id].expected.data.tasks[0].due_date -ceq $dates[$id]) "$id expected date" }
foreach ($id in @('NO_DUE','AMBIGUOUS_FRIDAY','MISSING_CONTEXT')) { Check ($null -eq $map[$id].expected.data.tasks[0].due_date) "$id null date" }
$contract = Get-Content docs/agent-b/CONTRACT_PROPOSAL.md -Raw
$sample = [regex]::Match($contract,'(?s)```json\s*(.*?)\s*```').Groups[1].Value | ConvertFrom-Json -DateKind String
Check (($sample.input | ConvertTo-Json -Depth 30 -Compress) -ceq ($map.RU_EXPLICIT.input | ConvertTo-Json -Depth 30 -Compress)) 'contract input matches'
Check (($sample.expected | ConvertTo-Json -Depth 30 -Compress) -ceq ($map.RU_EXPLICIT.expected | ConvertTo-Json -Depth 30 -Compress)) 'contract expected matches'
"PASS: $($root.cases.Count) cases; $script:checks assertions; dates, ids, references, draft example checked."
```
<!-- fixture-check:end -->

Дополнительно выполнить `Get-Content -LiteralPath docs/agent-b/SYNTHETIC_CASES.json -Raw | ConvertFrom-Json | Out-Null`, `git diff --check` и `git status --short`. До добавления новых файлов в index diff не проверяет их содержимое; в отдельной копии после `git add` четырёх разрешённых файлов используется `git diff --cached --check`. В общей папке git add запрещён: ведущий проверяет index, а новые документы проверяются чтением и JSON-парсером.
