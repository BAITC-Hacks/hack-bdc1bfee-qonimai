"""Read-only structural validation of docs/CONTRACT_V1.md.

No model, network, audio or export I/O. Errors contain fixed schema paths and
messages, never input values. Success does not prove natural-language facts or
user consent. In particular, the model adapter must reject nonempty user_edits
before calling these validators on a raw model response.
"""

import math
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SCHEMA_VERSION = "qonimai.analysis.v1"

_TRANSCRIPT = {"schema_version", "revision", "meeting", "participants", "speakers", "segments"}
_TASK = {"id", "description", "author_speaker_id", "assignee_id", "due_text", "due_date",
         "source_segment_ids", "requires_clarification", "clarification_reasons"}
_FACT = {"id", "text", "source_segment_ids"}
_EDIT = {"target_type", "target_id", "original", "changed_fields", "reason"}
_TASK_EDITABLE = {"description", "assignee_id", "due_date", "requires_clarification", "clarification_reasons"}
_ASSIGNEE_REASONS = {"unknown_assignee", "ambiguous_assignee", "unconfirmed_assignee"}
_DUE_REASONS = {"missing_due", "ambiguous_due", "missing_meeting_context"}
_RU_MONTHS = dict(zip(
    "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split(),
    range(1, 13),
))
_KK_MONTHS = dict(zip(
    "қаңтар ақпан наурыз сәуір мамыр маусым шілде тамыз қыркүйек қазан қараша желтоқсан".split(),
    range(1, 13),
))


def _text(value):
    return type(value) is str and bool(value.strip())


def _integer(value):
    return type(value) is int and value > 0


def _number(value):
    # Python integers are finite even when too large to convert to a float.
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _same(left, right):
    """JSON equality without Python's True == 1 coercion."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_same(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


class _Check:
    def __init__(self, code):
        self.code = code
        self.errors = []

    def fail(self, path, message, code=None):
        self.errors.append({"code": code or self.code, "path": path, "message": message})

    def obj(self, value, keys, path):
        if type(value) is not dict:
            self.fail(path, "Ожидается объект.")
            return False
        if set(value) != keys:
            # Do not interpolate unknown field names: they may contain private data.
            self.fail(path, "Набор обязательных полей не совпадает с контрактом.")
        return True

    def string(self, value, path, nullable=False):
        if nullable and value is None:
            return True
        if not _text(value):
            self.fail(path, "Ожидается непустая строка.")
            return False
        return True

    def array(self, value, path):
        if type(value) is not list:
            self.fail(path, "Ожидается массив.")
            return []
        return value

    def items(self, value, path, key="id"):
        rows = self.array(value, path)
        index = {}
        for i, row in enumerate(rows):
            loc = f"{path}[{i}]"
            if type(row) is not dict:
                self.fail(loc, "Ожидается объект.")
                continue
            identity = row.get(key)
            if self.string(identity, f"{loc}.{key}"):
                if identity in index:
                    self.fail(f"{loc}.{key}", "Идентификатор повторяется в списке.")
                else:
                    index[identity] = row
        return rows, index

    def reference(self, value, index, path, nullable=False):
        if nullable and value is None:
            return
        if self.string(value, path) and value not in index:
            self.fail(path, "Ссылка не найдена.")

    def date(self, value, path):
        if value is None:
            return None
        if type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        self.fail(path, "Ожидается существующая дата YYYY-MM-DD или null.")
        return None


def _meeting(check, value, path):
    if not check.obj(value, {"started_at", "timezone"}, path):
        return None
    instant = None
    raw = value.get("started_at")
    if raw is not None:
        if type(raw) is str and re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
            r"(?:\.[0-9]+)?[+-](?:[01][0-9]|2[0-3]):[0-5][0-9]", raw
        ):
            try:
                instant = datetime.fromisoformat(raw)
            except ValueError:
                pass
        if instant is None:
            check.fail(path + ".started_at", "Ожидается существующая дата и время с числовым смещением.")
    zone = value.get("timezone")
    if zone is None or not check.string(zone, path + ".timezone"):
        return None
    try:
        tz = ZoneInfo(zone)
    except ZoneInfoNotFoundError:
        # A missing database is an environment failure, not a valid unknown zone.
        try:
            ZoneInfo("UTC")
        except (ZoneInfoNotFoundError, OSError, ValueError):
            check.fail(path + ".timezone", "База IANA недоступна; требуется настройка среды.", "TIMEZONE_DATA_UNAVAILABLE")
        else:
            check.fail(path + ".timezone", "Зона не найдена в базе IANA.")
        return None
    except ValueError:
        check.fail(path + ".timezone", "Недопустимый идентификатор зоны IANA.")
        return None
    except OSError:
        check.fail(path + ".timezone", "База IANA не читается; требуется настройка среды.", "TIMEZONE_DATA_UNAVAILABLE")
        return None
    if instant is not None:
        try:
            local = instant.astimezone(tz)
        except (OverflowError, ValueError):
            check.fail(path + ".started_at", "Дата вне допустимого диапазона для указанной зоны.")
            return None
        if local.utcoffset() != instant.utcoffset():
            check.fail(path + ".started_at", "Смещение не соответствует зоне в момент совещания.")
        return local.date()
    return None


def _transcript(check, transcript):
    context = {"participants": {}, "speakers": {}, "segments": {}, "day": None, "meeting_valid": False}
    if not check.obj(transcript, _TRANSCRIPT, "transcript"):
        return context
    if transcript.get("schema_version") != SCHEMA_VERSION:
        check.fail("transcript.schema_version", "Неподдерживаемая версия контракта.")
    if not _integer(transcript.get("revision")):
        check.fail("transcript.revision", "Ожидается положительное целое число, не bool.")
    before_meeting = len(check.errors)
    context["day"] = _meeting(check, transcript.get("meeting"), "transcript.meeting")
    context["meeting_valid"] = len(check.errors) == before_meeting
    people, context["participants"] = check.items(transcript.get("participants"), "transcript.participants")
    for i, person in enumerate(people):
        path = f"transcript.participants[{i}]"
        if check.obj(person, {"id", "name"}, path):
            check.string(person.get("name"), path + ".name")
    speakers, context["speakers"] = check.items(transcript.get("speakers"), "transcript.speakers", "speaker_id")
    for i, speaker in enumerate(speakers):
        path = f"transcript.speakers[{i}]"
        if not check.obj(speaker, {"speaker_id", "participant_id", "display_name", "confirmed"}, path):
            continue
        confirmed = speaker.get("confirmed")
        if type(confirmed) is not bool:
            check.fail(path + ".confirmed", "Ожидается boolean.")
        check.string(speaker.get("display_name"), path + ".display_name", nullable=confirmed is not True)
        person_id = speaker.get("participant_id")
        if confirmed is True:
            check.reference(person_id, context["participants"], path + ".participant_id")
            person = context["participants"].get(person_id) if _text(person_id) else None
            if person and speaker.get("display_name") != person.get("name"):
                check.fail(path + ".display_name", "Подтверждённое имя не совпадает с именем участника.")
        elif person_id is not None:
            check.fail(path + ".participant_id", "Для неподтверждённого голоса требуется null.")
    segments, context["segments"] = check.items(transcript.get("segments"), "transcript.segments")
    previous = -1
    for i, segment in enumerate(segments):
        path = f"transcript.segments[{i}]"
        if not check.obj(segment, {"id", "start", "end", "speaker_id", "text"}, path):
            continue
        start, end = segment.get("start"), segment.get("end")
        for key, value in (("start", start), ("end", end)):
            if not _number(value):
                check.fail(path + "." + key, "Ожидается конечное число, не bool.")
        if _number(start):
            if start < 0 or start < previous:
                check.fail(path + ".start", "Начало отрицательно или нарушен порядок сегментов.")
            previous = start
            if _number(end) and end < start:
                check.fail(path + ".end", "Конец раньше начала сегмента.")
        check.reference(segment.get("speaker_id"), context["speakers"], path + ".speaker_id")
        check.string(segment.get("text"), path + ".text")
    return context


def _evidence(check, item, context, path):
    references = check.array(item.get("source_segment_ids"), path + ".source_segment_ids")
    if not references:
        check.fail(path + ".source_segment_ids", "Нужна хотя бы одна ссылка на источник.")
    rows = []
    for i, ref in enumerate(references):
        loc = f"{path}.source_segment_ids[{i}]"
        check.reference(ref, context["segments"], loc)
        if _text(ref):
            if ref in context["segments"]:
                rows.append(context["segments"][ref])
    return rows


def _fact(check, item, context, path):
    if check.obj(item, _FACT, path):
        check.string(item.get("id"), path + ".id")
        check.string(item.get("text"), path + ".text")
        _evidence(check, item, context, path)


def _due_rule(text):
    """Recognize bounded date forms, not arbitrary natural-language truth.

    Returns (kind, date): tomorrow, explicit, ambiguous, invalid or unchecked.
    Unknown language/phrasing is left for semantic review, never normalized here.
    """
    words = text.casefold()
    if re.search(r"\bследующ\w*\s+пятниц\w*", words):
        return "ambiguous", None
    found = []
    for year, month, day in re.findall(r"(?<!\d)([0-9]{4})-([0-9]{2})-([0-9]{2})(?!\d)", words):
        found.append((int(year), int(month), int(day)))
    for day, month, year in re.findall(r"(?<!\d)([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{4})(?!\d)", words):
        found.append((int(year), int(month), int(day)))
    ru = "|".join(_RU_MONTHS)
    for day, month, year in re.findall(r"(?<!\d)([0-9]{1,2})\s+(" + ru + r")\s+([0-9]{4})(?!\d)", words):
        found.append((int(year), _RU_MONTHS[month], int(day)))
    kk = "|".join(_KK_MONTHS)
    for year, day, month in re.findall(r"(?<!\d)([0-9]{4})\s+жылғы\s+([0-9]{1,2})\s+(" + kk + r")", words):
        found.append((int(year), _KK_MONTHS[month], int(day)))
    tomorrow = bool(re.search(r"\bзавтра\b", words))
    if found:
        try:
            dates = {date(*parts) for parts in found}
        except ValueError:
            return "invalid", None
        if len(dates) != 1 or tomorrow:
            return "ambiguous", None
        return "explicit", dates.pop()
    if tomorrow:
        return "tomorrow", None
    if (re.search(r"(?<!\d)[0-9]{1,2}\s+(?:" + ru + r")\b", words)
            or re.search(r"(?<!\d)[0-9]{1,2}\s+(?:" + kk + r")", words)
            or re.search(r"(?<!\d)[0-9]{1,2}\.[0-9]{1,2}(?![.\d])", words)):
        return "ambiguous", None
    return "unchecked", None


def _task(check, item, context, path, edited_due=False):
    if not check.obj(item, _TASK, path):
        return
    check.string(item.get("id"), path + ".id")
    check.string(item.get("description"), path + ".description")
    rows = _evidence(check, item, context, path)
    author = item.get("author_speaker_id")
    check.reference(author, context["speakers"], path + ".author_speaker_id")
    if _text(author) and not any(row.get("speaker_id") == author for row in rows):
        check.fail(path + ".author_speaker_id", "Автор отсутствует среди подтверждающих реплик.")
    check.reference(item.get("assignee_id"), context["participants"], path + ".assignee_id", nullable=True)
    due_text = item.get("due_text")
    check.string(due_text, path + ".due_text", nullable=True)
    if _text(due_text) and not any(type(row.get("text")) is str and due_text in row["text"] for row in rows):
        check.fail(path + ".due_text", "Формулировка срока отсутствует в подтверждающих репликах.")
    parsed_date = check.date(item.get("due_date"), path + ".due_date")
    reasons = check.array(item.get("clarification_reasons"), path + ".clarification_reasons")
    codes = set()
    for i, reason in enumerate(reasons):
        loc = f"{path}.clarification_reasons[{i}]"
        if not check.obj(reason, {"code", "message"}, loc):
            continue
        code = reason.get("code")
        if type(code) is not str or code not in _ASSIGNEE_REASONS | _DUE_REASONS:
            check.fail(loc + ".code", "Неизвестный код причины.")
        elif code in codes:
            check.fail(loc + ".code", "Код причины повторяется.")
        else:
            codes.add(code)
        check.string(reason.get("message"), loc + ".message")
    flag = item.get("requires_clarification")
    if type(flag) is not bool or flag != bool(reasons):
        check.fail(path + ".requires_clarification", "Флаг должен соответствовать наличию причин уточнения.")
    for field, category in (("assignee_id", _ASSIGNEE_REASONS), ("due_date", _DUE_REASONS)):
        required = 1 if item.get(field) is None else 0
        if len(codes & category) != required:
            check.fail(path + ".clarification_reasons", "Причины не соответствуют известным и неизвестным полям.")
    if due_text is None:
        if item.get("due_date") is None:
            if "missing_due" not in codes:
                check.fail(path + ".clarification_reasons", "При отсутствии исходного срока требуется missing_due.")
        elif not edited_due:
            check.fail(path + ".due_date", "Дата без исходного срока требует корректного пользовательского уточнения.")
        return
    if not _text(due_text):
        return
    if "missing_due" in codes:
        check.fail(path + ".clarification_reasons", "При наличии исходного срока missing_due недопустим.")
    if edited_due:
        # The original is validated separately; an explicit user change is not
        # claimed to have been spoken in the recording.
        return
    kind, expected = _due_rule(due_text)
    reason = None
    if kind == "invalid":
        check.fail(path + ".due_text", "Исходный срок содержит несуществующую календарную дату.")
        return
    if kind == "ambiguous":
        reason = "ambiguous_due"
    elif kind == "tomorrow":
        if not context["meeting_valid"]:
            # Invalid/unavailable metadata was reported as a prerequisite error.
            # It is not evidence that the model's calendar result is wrong.
            return
        if context["day"] is None:
            reason = "missing_meeting_context"
        else:
            try:
                expected = context["day"] + timedelta(days=1)
            except OverflowError:
                check.fail(path + ".due_date", "Относительный срок вне диапазона календаря.")
                return
    if reason is not None:
        if item.get("due_date") is not None or reason not in codes:
            check.fail(path + ".due_date", "Неоднозначный срок или отсутствующий контекст требует null и соответствующей причины.")
    elif expected is not None and parsed_date != expected:
        check.fail(path + ".due_date", "Дата не соответствует исходному сроку и контексту совещания.")
    elif "missing_meeting_context" in codes and context["day"] is not None:
        check.fail(path + ".clarification_reasons", "Контекст даты совещания известен.")


def _edits(check, rows, indexes, context):
    seen, edited_dates = set(), set()
    for i, edit in enumerate(rows):
        path = f"analysis.data.user_edits[{i}]"
        before = len(check.errors)
        if not check.obj(edit, _EDIT, path):
            continue
        target_type, target_id = edit.get("target_type"), edit.get("target_id")
        if type(target_type) is not str or target_type not in indexes:
            check.fail(path + ".target_type", "Неизвестный тип изменяемого объекта.")
            continue
        check.reference(target_id, indexes[target_type], path + ".target_id")
        check.string(edit.get("reason"), path + ".reason")
        original = edit.get("original")
        if target_type == "task":
            _task(check, original, context, path + ".original")
        else:
            _fact(check, original, context, path + ".original")
        fields = check.array(edit.get("changed_fields"), path + ".changed_fields")
        allowed = _TASK_EDITABLE if target_type == "task" else {"text"}
        field_set = set()
        if not fields:
            check.fail(path + ".changed_fields", "Список изменений не может быть пустым.")
        for j, field in enumerate(fields):
            loc = f"{path}.changed_fields[{j}]"
            if type(field) is not str or field not in allowed:
                check.fail(loc, "Поле нельзя изменять пользовательской правкой.")
            elif field in field_set:
                check.fail(loc, "Изменённое поле повторяется.")
            else:
                field_set.add(field)
        if not _text(target_id):
            continue
        key = (target_type, target_id)
        if key in seen:
            check.fail(path + ".target_id", "Для объекта уже есть запись правки.")
        seen.add(key)
        current = indexes[target_type].get(target_id)
        if type(original) is dict and current is not None:
            keys = _TASK if target_type == "task" else _FACT
            differences = {k for k in keys if not _same(original.get(k), current.get(k))}
            if not differences or differences != field_set or not differences <= allowed:
                check.fail(path + ".changed_fields", "Список должен точно совпадать с разрешёнными отличиями от оригинала.")
            if target_type == "task" and not differences & {"description", "assignee_id", "due_date"}:
                check.fail(path + ".changed_fields", "Причины и флаг пересчитываются при изменении самого поручения.")
            if original.get("id") != target_id:
                check.fail(path + ".original.id", "Оригинал относится к другому объекту.")
            if len(check.errors) == before and "due_date" in field_set:
                edited_dates.add(target_id)
    return edited_dates


def _analysis(check, analysis, context):
    if not check.obj(analysis, {"status", "data", "error"}, "analysis"):
        return
    status = analysis.get("status")
    if type(status) is not str or status not in {"ok", "no_speech", "error"}:
        check.fail("analysis.status", "Неизвестное состояние результата.")
        return
    if status == "error":
        if analysis.get("data") is not None:
            check.fail("analysis.data", "При ошибке data должно быть null.")
        error = analysis.get("error")
        if check.obj(error, {"code", "message", "retryable"}, "analysis.error"):
            check.string(error.get("code"), "analysis.error.code")
            check.string(error.get("message"), "analysis.error.message")
            if type(error.get("retryable")) is not bool:
                check.fail("analysis.error.retryable", "Ожидается boolean.")
        return
    if analysis.get("error") is not None:
        check.fail("analysis.error", "При успехе error должно быть null.")
    data = analysis.get("data")
    if not check.obj(data, {"source_revision", "summary", "decisions", "tasks", "user_edits"}, "analysis.data"):
        return
    if not _integer(data.get("source_revision")):
        check.fail("analysis.data.source_revision", "Ожидается положительное целое число, не bool.")
    summaries, summary_index = check.items(data.get("summary"), "analysis.data.summary")
    decisions, decision_index = check.items(data.get("decisions"), "analysis.data.decisions")
    tasks, task_index = check.items(data.get("tasks"), "analysis.data.tasks")
    edits = check.array(data.get("user_edits"), "analysis.data.user_edits")
    edited_dates = _edits(check, edits, {"task": task_index, "summary": summary_index, "decision": decision_index}, context)
    for name, rows in (("summary", summaries), ("decisions", decisions)):
        for i, item in enumerate(rows):
            _fact(check, item, context, f"analysis.data.{name}[{i}]")
    for i, task in enumerate(tasks):
        identity = task.get("id") if type(task) is dict else None
        _task(check, task, context, f"analysis.data.tasks[{i}]", _text(identity) and identity in edited_dates)
    if status == "no_speech":
        if context["segments"] or summaries or decisions or tasks or edits:
            check.fail("analysis.status", "no_speech требует пустого транскрипта и пустых списков результата.")
    elif not context["segments"] or not summaries:
        check.fail("analysis.status", "ok требует непустого транскрипта и саммари.")


def validate_transcript(transcript) -> list[dict]:
    """Validate input structure, references and the actual IANA time zone."""
    check = _Check("INVALID_INPUT")
    _transcript(check, transcript)
    return check.errors


def validate_analysis(transcript, analysis) -> list[dict]:
    """Validate a result (including UI edits) and its transcript prerequisites.

    Does not check revision equality; use validate_snapshot for that gate.
    Does not infer whether user_edits actually originated from a user action.
    """
    check = _Check("INVALID_INPUT")
    context = _transcript(check, transcript)
    check.code = "INVALID_MODEL_OUTPUT"
    _analysis(check, analysis, context)
    return check.errors


def validate_snapshot(transcript, analysis) -> list[dict]:
    """Validate a snapshot without modifying it or granting human approval."""
    errors = validate_analysis(transcript, analysis)
    if type(transcript) is dict and type(analysis) is dict:
        data = analysis.get("data")
        if analysis.get("status") in ("ok", "no_speech") and type(data) is dict:
            revision, source = transcript.get("revision"), data.get("source_revision")
            if _integer(revision) and _integer(source) and revision != source:
                errors.append({"code": "STALE_ANALYSIS", "path": "analysis.data.source_revision", "message": "Анализ относится к другой версии снимка."})
    return errors


def validate_export(transcript, analysis, reviewed_revision) -> list[dict]:
    """Gate a future export; unresolved values with valid reasons are allowed."""
    errors = validate_snapshot(transcript, analysis)
    if type(analysis) is dict:
        if analysis.get("status") == "no_speech":
            errors.append({"code": "NO_CONTENT", "path": "analysis.status", "message": "Нет речи для экспорта."})
        elif analysis.get("status") == "error":
            errors.append({"code": "INVALID_MODEL_OUTPUT", "path": "analysis.status", "message": "Результат с ошибкой нельзя экспортировать."})
    revision = transcript.get("revision") if type(transcript) is dict else None
    if not _integer(reviewed_revision) or not _integer(revision) or reviewed_revision != revision:
        errors.append({"code": "REVIEW_REQUIRED", "path": "reviewed_revision", "message": "Нужен просмотр пользователем текущей версии."})
    return errors
