"""Local Streamlit UI. Run: python -m streamlit run app.py"""

from copy import deepcopy
from datetime import date, time
import json
import os
from pathlib import Path
import tempfile
from time import perf_counter

import streamlit as st

from contracts import validate_snapshot, validate_transcript
# A running Streamlit session can still hold the pre-history helper module
# after this file changes. Load its new functions without restarting sessions.
import ui_helpers as _ui_helpers
if not hasattr(_ui_helpers, "retain_snapshot"):
    from importlib import reload
    reload(_ui_helpers)
from ui_helpers import (UIValidationError, accept_content_review, apply_user_edits,
                        export_document_bytes, meeting_metadata, participants_from_text,
                        replace_transcript, retain_snapshot)

st.set_page_config(page_title="QonimAI · Протокол совещания", page_icon="📝", layout="wide")
st.markdown("""<style>.block-container{max-width:1150px;padding-top:2rem}
[data-testid=stMetric]{background:#f2f6f9;border-radius:12px;padding:15px}
.stButton>button{border-radius:8px}h1{letter-spacing:-.04em}</style>""", unsafe_allow_html=True)


def clear_result():
    st.session_state["snapshot_history"] = retain_snapshot(
        st.session_state.get("snapshot_history", []), st.session_state.get("transcript"),
        st.session_state.get("analysis"), pending_analysis=st.session_state.get("pending_analysis"),
        reviewed_revision=st.session_state.get("reviewed_revision"),
        synthetic=st.session_state.get("synthetic", False),
        synthetic_audio=st.session_state.get("synthetic_audio", False))
    for name in ("transcript", "analysis", "pending_analysis", "reviewed_revision", "docx_bytes", "timing", "synthetic", "synthetic_audio"):
        st.session_state.pop(name, None)
    st.session_state["generation"] = st.session_state.get("generation", 0) + 1


def save_snapshot(transcript, analysis=None, synthetic=False, synthetic_audio=False):
    clear_result()
    st.session_state["transcript"] = deepcopy(transcript)
    st.session_state["analysis"] = deepcopy(analysis)
    st.session_state["synthetic"] = synthetic
    st.session_state["synthetic_audio"] = synthetic_audio


def invalidate_review():
    st.session_state["reviewed_revision"] = None
    st.session_state.pop("docx_bytes", None)
    st.session_state["generation"] = st.session_state.get("generation", 0) + 1


def fixed_failure(message="Операция не завершена. Проверьте локальные модели и настройки среды."):
    st.error(message)


def module_failure(result):
    # Both adapters return fixed, private-safe error messages; never display exceptions.
    error = result.get("error") or {}
    fixed_failure(error.get("message") or "Локальный модуль не завершил обработку.")
    if error.get("code"):
        st.text("Код: " + error["code"])


def process_audio(path, metadata, people, count, asr_path, speaker_path, synthetic_audio=False):
    started = perf_counter()
    clear_result()
    with st.spinner("Локальное распознавание и разделение говорящих… На CPU это может занять несколько минут."):
        from audio.pipeline import transcribe
        result = transcribe(str(path), metadata, people, asr_model_path=asr_path or None,
                            speaker_model_path=speaker_path or None, num_speakers=int(count))
    if result.get("status") == "error":
        module_failure(result)
    elif validate_transcript(result.get("data")):
        fixed_failure("Распознанный результат не прошёл проверку контракта.")
    elif result.get("status") == "no_speech":
        st.info("Речь не обнаружена. Пустой протокол не экспортируется.")
    else:
        save_snapshot(result["data"], synthetic_audio=synthetic_audio)
        st.session_state["timing"] = {"audio": perf_counter() - started}
        st.rerun()


st.title("QonimAI")
st.caption("Из обсуждения — в понятный протокол. Проверяйте слова, людей и сроки перед экспортом.")
with st.sidebar:
    st.subheader("Рабочий режим")
    mode = st.radio("Источник", ["Аудиозапись", "Синтетические примеры"], label_visibility="collapsed")
    st.caption("Обработка только локально. Интернет и облачные API для данных совещаний не используются.")
    if st.button("Убрать текущий результат в историю", use_container_width=True):
        clear_result()
        st.rerun()
    st.divider()
    st.caption("MVP • автоматически полученные результаты требуют проверки человеком.")

if st.session_state.get("active_mode") != mode:
    clear_result()
    st.session_state["active_mode"] = mode

history = st.session_state.get("snapshot_history", [])
if history:
    with st.expander(f"Сохранённые версии этого сеанса: {len(history)}"):
        st.caption("При смене записи, метаданных или текста прежний транскрипт, анализ и ручные правки сохраняются здесь. История находится только в памяти сеанса: скачайте JSON перед закрытием или обновлением вкладки. Исходное аудио в историю не включается.")
        selected_history = st.selectbox("Версия для просмотра и сохранения", list(range(len(history))),
            index=len(history) - 1,
            format_func=lambda i: f"Сохранение {i + 1} · версия снимка {history[i]['transcript']['revision']}",
            key=f"history_choice_{len(history)}")
        preserved = history[selected_history]
        st.download_button("Скачать сохранённый снимок JSON",
            json.dumps(preserved, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name=f"qonimai_snapshot_{selected_history + 1}.json", mime="application/json")
        with st.expander("Просмотреть сохранённый снимок"):
            st.json(preserved)
        erase_history = st.checkbox("Я скачал нужные версии и хочу безвозвратно удалить всю историю этого сеанса",
                                    key=f"erase_history_{len(history)}")
        if st.button("Удалить сохранённые версии", disabled=not erase_history):
            st.session_state["snapshot_history"] = []
            st.rerun()

with st.expander("Запись и обработка с помощью ИИ", expanded="transcript" not in st.session_state):
    st.write("Перед записью уведомите участников: разговор записывается и будет обработан ИИ локально или в согласованном закрытом контуре. Сообщите место хранения, круг доступа и срок удаления. ИИ может ошибаться; протокол проверяет человек.")
    st.write("Реальные записи и транскрипты не отправляйте в GitHub, Live Share, облачные API или ИИ-чаты. После обработки временный файл аудио удаляется; текущие данные и история правок хранятся в памяти сеанса. Кнопка скачивания сохраняет DOCX или выбранный снимок JSON на вашем устройстве.")
    consent = st.checkbox("Участники уведомлены; у меня есть разрешение на обработку выбранных данных", key="consent")

if mode == "Синтетические примеры":
    st.warning("Синтетический режим: заранее подготовленные вымышленные данные. Модели не запускаются; это не проверка распознавания речи.")
    cases = json.loads((Path(__file__).parent / "fixtures" / "synthetic_cases.json").read_text(encoding="utf-8"))["cases"]
    case_id = st.selectbox("Контрольный пример", [case["id"] for case in cases], on_change=clear_result)
    if st.button("Открыть вымышленный пример", type="primary", disabled=not consent):
        case = next(c for c in cases if c["id"] == case_id)
        if validate_snapshot(case["input"], case["expected"]):
            fixed_failure("Контрольный пример не прошёл проверку среды или контракта.")
        else:
            save_snapshot(case["input"], case["expected"], synthetic=True)
            st.rerun()
else:
    st.subheader("1. Загрузите запись")
    uploaded = st.file_uploader("WAV, MP3, M4A или OGG • до 50 МБ и 5 минут", type=["wav", "mp3", "m4a", "ogg"],
                                key=f"audio_upload_{st.session_state.get('upload_epoch', 0)}", on_change=clear_result)
    if uploaded is not None and 0 < uploaded.size <= 50 * 1024 * 1024:
        st.audio(uploaded)
    col_a, col_b = st.columns(2)
    with col_a:
        roster = st.text_area("Участники: по одному подтверждённому имени в строке", placeholder="Имена участников вашей записи", height=120, on_change=clear_result)
        speakers_count = st.number_input("Ожидаемое число говорящих", min_value=1, max_value=8, value=2, on_change=clear_result)
        st.caption("Число голосов задаёт пользователь: кластеризация формирует указанное количество групп. Короткие реплики и одновременная речь могут разделяться неверно — проверяйте голоса вручную.")
    with col_b:
        date_known = st.checkbox("Дата и время совещания известны", value=False, on_change=clear_result)
        meeting_day = st.date_input("Дата совещания", value=date.today(), disabled=not date_known, on_change=clear_result)
        meeting_time = st.time_input("Время начала", value=time(10, 0), disabled=not date_known, on_change=clear_result)
        timezone = st.text_input("Часовой пояс IANA", value="Asia/Qyzylorda", disabled=not date_known, on_change=clear_result)
    with st.expander("Локальные модели и сервер анализа"):
        default_asr = Path(__file__).parent / "models" / "whisper-small"
        default_speaker = Path(__file__).parent / "models" / "speaker" / "wespeaker_en_voxceleb_resnet34_LM.onnx"
        asr_path = st.text_input("Каталог локальной модели распознавания", value=os.environ.get("QONIMAI_ASR_MODEL", str(default_asr) if default_asr.is_dir() else ""))
        speaker_path = st.text_input("Путь локальной модели говорящих", value=os.environ.get("QONIMAI_SPEAKER_MODEL", str(default_speaker) if default_speaker.is_file() else ""))
        analysis_url = st.text_input("Локальный адрес анализатора", value=os.environ.get("QONIMAI_ANALYSIS_URL", "http://127.0.0.1:8081"))
        st.caption("Веса скачиваются заранее. Обработка не должна автоматически скачивать их. Качество русского, казахского и смешанной речи оценивается на ваших тестовых записях.")
    if st.button("Распознать и разделить говорящих", type="primary", disabled=not consent or uploaded is None):
        if uploaded.size > 50 * 1024 * 1024 or uploaded.size == 0:
            fixed_failure("Выберите непустой файл размером не более 50 МБ.")
        else:
            try:
                metadata = meeting_metadata(meeting_day, meeting_time, timezone, date_known)
                people = participants_from_text(roster)
                with tempfile.TemporaryDirectory(prefix="qonimai-audio-") as directory:
                    audio_path = Path(directory) / ("input" + Path(uploaded.name).suffix.lower())
                    audio_path.write_bytes(uploaded.getvalue())
                    process_audio(audio_path, metadata, people, speakers_count, asr_path, speaker_path)
            except UIValidationError as error:
                fixed_failure(str(error))
            except Exception:
                fixed_failure()
    synthetic_audio_path = Path(__file__).parent / "fixtures" / "audio" / "synthetic_ru_two_speakers.wav"
    if synthetic_audio_path.is_file():
        with st.expander("Проверить настоящую цепочку на вымышленной аудиозаписи"):
            st.caption("29 секунд русского диалога двумя синтезированными голосами. Ниже запускаются настоящие локальные модели; эталонные ответы не подставляются. Это не проверка казахского или качества реальных голосов.")
            st.audio(str(synthetic_audio_path))
            sample_confirmed = st.checkbox("Использую вымышленную запись синтезированных голосов: реальные люди не записывались", key="sample_audio_confirmed")
            if st.button("Испытать на синтетической аудиозаписи", disabled=not consent or not sample_confirmed):
                try:
                    reference = json.loads(synthetic_audio_path.with_suffix(".json").read_text(encoding="utf-8"))
                    process_audio(synthetic_audio_path, reference["meeting"], reference["participants"], 2,
                                  asr_path, speaker_path, synthetic_audio=True)
                except Exception:
                    fixed_failure("Не удалось обработать вымышленную аудиозапись локальными моделями.")

transcript = st.session_state.get("transcript")
if transcript is None:
    st.info("Выберите запись или вымышленный пример, чтобы начать. Данные из одного режима не подменяют результаты другого.")
    st.stop()

analysis = st.session_state.get("analysis")
synthetic = st.session_state.get("synthetic", False)
synthetic_audio = st.session_state.get("synthetic_audio", False)
people = {p["id"]: p["name"] for p in transcript["participants"]}
key_prefix = f"g{st.session_state.get('generation', 0)}r{transcript['revision']}"

if synthetic:
    st.caption("Источник текущего результата: синтетический эталон, модели не запускались.")
elif synthetic_audio:
    st.info("Источник: вымышленная аудиозапись. Транскрипт получен локальной моделью; имена говорящих нужно подтвердить вручную.")
st.text("Контекст текущего снимка: " + (transcript["meeting"]["started_at"] or "дата неизвестна") + " · " + (transcript["meeting"]["timezone"] or "зона неизвестна"))
metrics = st.columns(3)
metrics[0].metric("Реплики", len(transcript["segments"]))
metrics[1].metric("Голоса", len(transcript["speakers"]))
metrics[2].metric("Версия снимка", transcript["revision"])
for stage, seconds in st.session_state.get("timing", {}).items():
    st.caption(f"{'Аудио' if stage == 'audio' else 'Анализ'}: {seconds:.1f} с на текущем компьютере.")

st.subheader("2. Проверьте голоса и транскрипт")
speaker_names = {s["speaker_id"]: people.get(s["participant_id"], "Не подтверждён") for s in transcript["speakers"]}
if not synthetic:
    with st.form(f"transcript_form_{key_prefix}"):
        st.caption("Номер голоса не устанавливает личность. Выберите имя только после прослушивания. Несколько голосов могут относиться к одному человеку.")
        mapping = {}
        options = [None] + list(people)
        for speaker in transcript["speakers"]:
            current = speaker["participant_id"] if speaker["confirmed"] else None
            mapping[speaker["speaker_id"]] = st.selectbox(speaker["speaker_id"], options,
                index=options.index(current) if current in options else 0,
                format_func=lambda value: "Не подтверждён" if value is None else f"{people[value]} ({value})",
                key=f"map_{key_prefix}_{speaker['speaker_id']}")
        editable = []
        for segment in transcript["segments"]:
            text = st.text_area(f"{segment['id']} · {segment['start']:.1f}–{segment['end']:.1f} с · {segment['speaker_id']}",
                                value=segment["text"], key=f"segment_{key_prefix}_{segment['id']}", height=85)
            editable.append({"id": segment["id"], "text": text})
        save_transcript = st.form_submit_button("Сохранить подтверждения и текст")
    if save_transcript:
        try:
            revised = replace_transcript(transcript, mapping, {row["id"]: row["text"] for row in editable})
            if revised != transcript:
                saved_timing = st.session_state.get("timing", {}).copy()
                save_snapshot(revised, synthetic_audio=synthetic_audio)
                st.session_state["timing"] = saved_timing
                st.rerun()
            st.info("Изменений нет. Можно запускать анализ.")
        except UIValidationError as error:
            fixed_failure(str(error))
else:
    st.caption("Транскрипт контрольного примера показан без изменения эталона. Исправления результата можно проверить ниже.")
    with st.expander("Реплики с таймкодами", expanded=True):
        for segment in transcript["segments"]:
            st.text(f"{segment['id']} · {segment['start']:.1f}–{segment['end']:.1f} с · {segment['speaker_id']}")
            st.text(segment["text"])
    st.text("Голоса: " + "; ".join(f"{key} → {value}" for key, value in speaker_names.items()))

if not synthetic:
    replace_analysis = True
    if analysis is not None:
        replace_analysis = st.checkbox("Подготовить новый анализ для сравнения с текущим результатом и ручными правками", key=f"replace_{key_prefix}")
    if st.button("Выделить поручения и подготовить саммари", type="primary", disabled=not consent or not replace_analysis):
        invalidate_review()
        started = perf_counter()
        try:
            with st.spinner("Локальная модель анализирует текст…"):
                from analysis.local_model import analyze
                new_analysis = analyze(transcript, base_url=analysis_url, model="local", timeout=180)
            if new_analysis.get("status") == "error":
                module_failure(new_analysis)
                st.caption("Прежний результат не заменён.")
            elif validate_snapshot(transcript, new_analysis):
                fixed_failure("Анализ не прошёл проверку контракта; прежний результат не заменён.")
            else:
                if analysis is None:
                    st.session_state["analysis"] = new_analysis
                else:
                    st.session_state["pending_analysis"] = new_analysis
                st.session_state.setdefault("timing", {})["analysis"] = perf_counter() - started
                st.rerun()
        except Exception:
            fixed_failure("Локальный анализ не завершён. Проверьте работающий сервер модели и его адрес.")

pending_analysis = st.session_state.get("pending_analysis")
if pending_analysis is not None:
    st.warning("Новый анализ готов к сравнению. Текущий результат и ручные правки пока сохранены.")
    left, right = st.columns(2)
    for column, heading, candidate in ((left, "Текущий результат", analysis), (right, "Новый результат модели", pending_analysis)):
        with column:
            st.subheader(heading)
            for fact in candidate["data"]["summary"]:
                st.text(fact["text"])
            for decision in candidate["data"]["decisions"]:
                st.text("Решение: " + decision["text"])
            for task in candidate["data"]["tasks"]:
                st.text(f"{task['description']} | {people.get(task['assignee_id'], 'Не определён')} | {task['due_date'] or 'Срок не указан'}")
            st.caption(f"Пользовательских уточнений: {len(candidate['data']['user_edits'])}")
            with st.expander("Полные поля и источники"):
                st.json(candidate["data"])
    replacement_confirmed = st.checkbox("Я сравнил результаты; принимаю новый вариант вместо текущего, включая замену ручных правок", key=f"accept_new_{key_prefix}")
    if st.button("Принять новый анализ", disabled=not replacement_confirmed):
        st.session_state["analysis"] = pending_analysis
        st.session_state.pop("pending_analysis", None)
        invalidate_review()
        st.rerun()
    if st.button("Оставить текущий результат"):
        st.session_state.pop("pending_analysis", None)
        st.rerun()
    st.stop()

analysis = st.session_state.get("analysis")
if analysis is None:
    st.info("После проверки транскрипта запустите анализ. Отсутствие результата не означает отсутствие поручений.")
    st.stop()
if analysis["status"] == "no_speech":
    st.info("В примере нет речи; экспорт пустого протокола запрещён.")
    st.stop()

st.subheader("3. Саммари, решения и поручения")
data = analysis["data"]
for fact in data["summary"]:
    st.text(fact["text"])
    st.text("Источники: " + ", ".join(fact["source_segment_ids"]))
if data["decisions"]:
    with st.expander("Принятые решения"):
        for fact in data["decisions"]:
            st.text(fact["text"])
            st.text("Источники: " + ", ".join(fact["source_segment_ids"]))
if not data["tasks"]:
    st.info("Поручения не выявлены. Саммари и транскрипт доступны для экспорта.")
for task in data["tasks"]:
    with st.container(border=True):
        st.text(f"{task['id']} · {task['description']}")
        st.text("Исполнитель: " + people.get(task["assignee_id"], "Не определён"))
        st.text(f"Автор реплики: {speaker_names.get(task['author_speaker_id'], task['author_speaker_id'])} • Исходный срок: {task['due_text'] or 'Не указан'} • Дата: {task['due_date'] or 'Не определена'}")
        st.text("Источники: " + ", ".join(task["source_segment_ids"]))
        if task["requires_clarification"]:
            st.warning("Требует уточнения")
            st.text(" ".join(r["message"] for r in task["clarification_reasons"]))
if data["user_edits"]:
    st.info("В результате есть пользовательские уточнения. Их оригиналы и объяснения войдут в DOCX.")

with st.expander("Исправить результат после проверки источников"):
    st.caption("Изменение записывается отдельно от транскрипта. Укажите, что именно уточнили; пустая дата означает неизвестный срок.")
    with st.form(f"edits_{key_prefix}"):
        changes = []
        for task in data["tasks"]:
            st.write(f"Поручение {task['id']}")
            description = st.text_input("Описание", value=task["description"], key=f"desc_{key_prefix}_{task['id']}")
            options = [None] + list(people)
            assignee = st.selectbox("Исполнитель", options, index=options.index(task["assignee_id"]),
                format_func=lambda value: "Не определён" if value is None else f"{people[value]} ({value})", key=f"assignee_{key_prefix}_{task['id']}")
            due = st.text_input("Дата ГГГГ-ММ-ДД или пусто", value=task["due_date"] or "", key=f"due_{key_prefix}_{task['id']}")
            changes.append({"target_type": "task", "target_id": task["id"], "values": {"description": description.strip(), "assignee_id": assignee, "due_date": due.strip() or None}})
        for kind, collection, title in (("summary", "summary", "Саммари"), ("decision", "decisions", "Решение")):
            for fact in data[collection]:
                text = st.text_area(f"{title} {fact['id']}", value=fact["text"], key=f"fact_{key_prefix}_{kind}_{fact['id']}")
                changes.append({"target_type": kind, "target_id": fact["id"], "values": {"text": text.strip()}})
        reason = st.text_input("Объяснение исправлений", key=f"reason_{key_prefix}")
        submitted = st.form_submit_button("Сохранить правки и сбросить подтверждение")
    if submitted:
        try:
            revised_transcript, revised_analysis = apply_user_edits(transcript, analysis, changes, reason)
            if revised_transcript != transcript or revised_analysis != analysis:
                saved_timing = st.session_state.get("timing", {}).copy()
                save_snapshot(revised_transcript, revised_analysis, synthetic, synthetic_audio)
                st.session_state["timing"] = saved_timing
                st.rerun()
            st.info("Изменений нет.")
        except UIValidationError as error:
            fixed_failure(str(error))

if analysis["data"]["source_revision"] != transcript["revision"]:
    st.warning("После правки нужен повторный просмотр: результат пока не разрешён к экспорту.")
    checked = st.checkbox("Я сверил изменённые поручения, саммари и решения с репликами; новые уточнения отражены верно", key=f"content_{key_prefix}")
    if st.button("Завершить проверку исправленного снимка", disabled=not checked):
        try:
            st.session_state["analysis"] = accept_content_review(transcript, analysis, confirmed=checked)
            invalidate_review()
            st.rerun()
        except UIValidationError as error:
            fixed_failure(str(error))
    st.stop()

st.subheader("4. Экспорт протокола")
reviewed = st.checkbox("Я просмотрел текущий результат, проверил ответственных и сроки; неясности можно оставить видимыми", key=f"export_review_{key_prefix}")
if not reviewed:
    st.session_state["reviewed_revision"] = None
    st.session_state.pop("docx_bytes", None)
else:
    st.session_state["reviewed_revision"] = transcript["revision"]
if st.button("Подготовить DOCX", type="primary", disabled=not reviewed or not consent):
    try:
        with st.spinner("Формируем документ локально…"):
            st.session_state["docx_bytes"] = export_document_bytes(transcript, analysis, st.session_state["reviewed_revision"], synthetic, synthetic_audio)
    except UIValidationError as error:
        fixed_failure(str(error))
    except Exception:
        fixed_failure("Не удалось сформировать DOCX. Данные остались в текущем сеансе.")
if st.session_state.get("docx_bytes") and reviewed and consent:
    st.download_button("Скачать протокол DOCX", st.session_state["docx_bytes"],
                       file_name="qonimai_synthetic.docx" if synthetic or synthetic_audio else "qonimai_protocol.docx",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True)
    st.caption("Проверьте оформление документа перед передачей другим людям.")
