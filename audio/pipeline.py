"""Local-only CPU ASR and acoustic speaker embeddings; no model downloads.

Speaker labels are clusters, never names. The caller supplies the known number
of speakers and must review labels and transcription before analysis/export.
"""

from copy import deepcopy
import ctypes
import math
import os
from pathlib import Path

from contracts import validate_transcript

SAMPLE_RATE = 16000
MAX_SECONDS = 300
MAX_FILE_BYTES = 50 * 1024 * 1024
_MESSAGES = {
    "INVALID_INPUT": "Проверьте дату, часовой пояс, участников и число говорящих (1–8).",
    "TIMEZONE_DATA_UNAVAILABLE": "Для проверки даты нужна установленная база часовых поясов tzdata.",
    "INVALID_AUDIO": "Не удалось прочитать аудио. Выберите исправную локальную запись.",
    "EMPTY_FILE": "Файл записи пуст.",
    "AUDIO_TOO_LARGE": "Размер записи превышает 50 МиБ.",
    "AUDIO_TOO_LONG": "Для этой версии выберите запись продолжительностью не более 5 минут.",
    "ASR_MODEL_MISSING": "Не найдена локальная модель распознавания: задайте QONIMAI_ASR_MODEL.",
    "SPEAKER_MODEL_MISSING": "Не найдена локальная ONNX-модель голосов: задайте QONIMAI_SPEAKER_MODEL.",
    "ASR_MODEL_INVALID": "Не удалось загрузить локальную многоязычную модель распознавания.",
    "SPEAKER_MODEL_INVALID": "Не удалось загрузить локальную модель голосов.",
    "DEPENDENCY_MISSING": "Не установлены зависимости аудиомодуля; выполните установку по README.",
    "ASR_FAILED": "Локальное распознавание завершилось ошибкой. Попробуйте более короткую запись.",
    "EMPTY_MODEL_OUTPUT": "Речь обнаружена, но распознавание не вернуло текст; результат не считается тишиной.",
    "DIARIZATION_FAILED": "Не удалось разделить говорящих по голосам. Результат не опубликован.",
    "INSUFFICIENT_SPEECH": "Слишком мало пригодных речевых фрагментов для указанного числа говорящих. Проверьте число голосов или используйте более длинную запись.",
    "RESOURCE_EXHAUSTED": "Недостаточно памяти для локальной обработки. Закройте другие программы или используйте меньшую локальную модель.",
}


class _AudioFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _error(code):
    return {"status": "error", "data": None,
            "error": {"code": code, "message": _MESSAGES[code], "retryable": False}}


def _local_path(value):
    """Reject URLs/UNC before opening anything; local model IDs aren't accepted."""
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw.strip() or "://" in raw or raw.startswith(("\\\\", "//")):
        raise ValueError("Expected local filesystem path")
    path = Path(raw).expanduser().absolute()
    if os.name == "nt":
        if path.is_reserved() or any(":" in part for part in path.parts[1:]):
            raise ValueError("Device names and alternate streams are unsupported")
        if ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(path.anchor)) == 4:
            raise ValueError("Mapped network drive is unsupported")
    if any(candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction())
           for candidate in (path, *path.parents)):
        raise ValueError("Linked paths are unsupported")
    return path


def _model_path(value, environment, *, directory):
    code = "ASR_MODEL_MISSING" if directory else "SPEAKER_MODEL_MISSING"
    try:
        path = _local_path(value if value is not None else os.environ.get(environment, ""))
        if directory:
            # tokenizer.json prevents a tokenizer fallback to a remote hub.
            if not path.is_dir() or not all((path / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
                raise ValueError("Incomplete local ASR directory")
        elif not path.is_file() or path.suffix.lower() != ".onnx":
            raise ValueError("Missing local ONNX")
        return path
    except (OSError, TypeError, ValueError):
        raise _AudioFailure(code) from None


def _decode_audio(path):
    """Decode with bundled FFmpeg libraries; stop after the duration limit."""
    import av
    import numpy as np

    pieces, count = [], 0
    try:
        # A local playlist may reference remote media. Limit libavformat's
        # protocols as well as validating the outer path, before probing input.
        with av.open(str(path), mode="r", options={"protocol_whitelist": "file,pipe"}) as container:
            if not container.streams.audio:
                raise _AudioFailure("INVALID_AUDIO")
            resampler = av.audio.resampler.AudioResampler(format="fltp", layout="mono", rate=SAMPLE_RATE)
            def append(frame):
                nonlocal count
                values = frame.to_ndarray().reshape(-1).astype(np.float32, copy=False)
                count += len(values)
                if count > MAX_SECONDS * SAMPLE_RATE:
                    raise _AudioFailure("AUDIO_TOO_LONG")
                pieces.append(values)
            for frame in container.decode(audio=0):
                for output in resampler.resample(frame):
                    append(output)
            for output in resampler.resample(None):
                append(output)
        if not count:
            raise _AudioFailure("INVALID_AUDIO")
        samples = np.concatenate(pieces)
        if not np.isfinite(samples).all():
            raise _AudioFailure("INVALID_AUDIO")
        return samples
    except (MemoryError, _AudioFailure):
        raise
    except Exception:
        raise _AudioFailure("INVALID_AUDIO") from None


def _speech_present(samples):
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    # The Silero ONNX weights are part of the installed faster-whisper package.
    return bool(get_speech_timestamps(samples, VadOptions(min_silence_duration_ms=300), sampling_rate=SAMPLE_RATE))


def _load_asr(path):
    from faster_whisper import WhisperModel
    model = WhisperModel(str(path), device="cpu", compute_type="int8", cpu_threads=4,
                         num_workers=1, local_files_only=True)
    if not model.model.is_multilingual:
        raise _AudioFailure("ASR_MODEL_INVALID")
    return model


def _load_speaker(path):
    import sherpa_onnx
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(path), num_threads=2, debug=False, provider="cpu")
    if not config.validate():
        raise _AudioFailure("SPEAKER_MODEL_INVALID")
    return sherpa_onnx.SpeakerEmbeddingExtractor(config)


def _recognize(model, samples):
    segments, _info = model.transcribe(
        samples, task="transcribe", language=None, multilingual=True,
        beam_size=3, word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        condition_on_previous_text=False, temperature=0.0,
    )
    return list(segments)


def _split_segments(segments, duration):
    """Use word timestamps so long ASR spans may contain different speakers.

    Do not invent proportional word timings. If timestamps are unavailable,
    retain the full ASR segment and document the coarser diarization granularity.
    """
    chunks = []
    for segment in segments:
        text = str(segment.text).strip()
        if not text:
            continue
        start, end = float(segment.start), float(segment.end)
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration + 0.1):
            raise _AudioFailure("ASR_FAILED")
        end = min(end, duration)
        words = getattr(segment, "words", None)
        # Use words only when they preserve the complete recognized text.
        if not words or "".join(str(word.word) for word in words).strip() != text:
            chunks.append({"start": start, "end": end, "text": text})
            continue
        pending = []
        for word in words:
            a, b = float(word.start), float(word.end)
            if not (math.isfinite(a) and math.isfinite(b) and start - 0.1 <= a <= b <= end + 0.1):
                raise _AudioFailure("ASR_FAILED")
            a, b = max(start, a), min(end, max(b, a))
            if pending and (a - pending[-1][1] >= 0.35 or b - pending[0][0] > 2.5):
                chunks.append({"start": pending[0][0], "end": pending[-1][1],
                               "text": "".join(w[2] for w in pending).strip()})
                pending = []
            pending.append((a, b, str(word.word)))
        if pending:
            chunks.append({"start": pending[0][0], "end": pending[-1][1],
                           "text": "".join(w[2] for w in pending).strip()})
    chunks.sort(key=lambda row: row["start"])
    # Zero-length word groups cannot provide an acoustic observation.
    if any(row["end"] <= row["start"] or not row["text"] for row in chunks):
        raise _AudioFailure("ASR_FAILED")
    return chunks


def _embedding(extractor, samples, chunk):
    import numpy as np
    first = max(0, int(chunk["start"] * SAMPLE_RATE))
    last = min(len(samples), int(chunk["end"] * SAMPLE_RATE))
    waveform = np.ascontiguousarray(samples[first:last], dtype=np.float32)
    if len(waveform) < int(0.2 * SAMPLE_RATE):
        raise _AudioFailure("INSUFFICIENT_SPEECH")
    stream = extractor.create_stream()
    stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=waveform)
    stream.input_finished()
    if not extractor.is_ready(stream):
        raise _AudioFailure("INSUFFICIENT_SPEECH")
    vector = np.asarray(extractor.compute(stream), dtype=np.float64).reshape(-1)
    if not vector.size or not np.isfinite(vector).all() or np.linalg.norm(vector) <= 1e-12:
        raise _AudioFailure("DIARIZATION_FAILED")
    return vector / np.linalg.norm(vector)


def _cluster_embeddings(vectors, num_speakers):
    """Deterministic average-linkage cosine clustering; no identity inference."""
    import numpy as np
    if len(vectors) < num_speakers:
        raise _AudioFailure("INSUFFICIENT_SPEECH")
    matrix = np.asarray(vectors, dtype=np.float64)
    if matrix.ndim != 2 or not np.isfinite(matrix).all():
        raise _AudioFailure("DIARIZATION_FAILED")
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms <= 1e-12):
        raise _AudioFailure("DIARIZATION_FAILED")
    matrix = matrix / norms[:, None]
    distance = np.clip(1.0 - matrix @ matrix.T, 0.0, 2.0)
    np.fill_diagonal(distance, np.inf)
    clusters = [[index] for index in range(len(matrix))]
    while len(clusters) > num_speakers:
        i, j = np.unravel_index(np.argmin(distance), distance.shape)
        if i > j:
            i, j = j, i
        a, b = len(clusters[i]), len(clusters[j])
        row = (distance[i] * a + distance[j] * b) / (a + b)
        distance[i, :] = row
        distance[:, i] = row
        clusters[i].extend(clusters[j])
        del clusters[j]
        distance = np.delete(np.delete(distance, j, axis=0), j, axis=1)
        np.fill_diagonal(distance, np.inf)
    clusters.sort(key=min)
    labels = [None] * len(matrix)
    for label, members in enumerate(clusters, 1):
        for index in members:
            labels[index] = f"spk{label}"
    return labels


def transcribe(audio_path, meeting, participants, *, asr_model_path=None,
               speaker_model_path=None, num_speakers=2) -> dict:
    """Return TranscriptResult; errors never expose paths, input text or traces."""
    transcript = {"schema_version": "qonimai.analysis.v1", "revision": 1,
                  "meeting": deepcopy(meeting), "participants": deepcopy(participants),
                  "speakers": [], "segments": []}
    if type(num_speakers) is not int or not 1 <= num_speakers <= 8:
        return _error("INVALID_INPUT")
    errors = validate_transcript(transcript)
    if errors:
        return _error("TIMEZONE_DATA_UNAVAILABLE" if any(e["code"] == "TIMEZONE_DATA_UNAVAILABLE" for e in errors) else "INVALID_INPUT")
    try:
        try:
            path = _local_path(audio_path)
            if not path.is_file():
                raise ValueError("Missing local recording")
            size = path.stat().st_size
        except (OSError, TypeError, ValueError):
            raise _AudioFailure("INVALID_AUDIO") from None
        if size == 0:
            raise _AudioFailure("EMPTY_FILE")
        if size > MAX_FILE_BYTES:
            raise _AudioFailure("AUDIO_TOO_LARGE")
        asr_path = _model_path(asr_model_path, "QONIMAI_ASR_MODEL", directory=True)
        speaker_path = _model_path(speaker_model_path, "QONIMAI_SPEAKER_MODEL", directory=False)
        samples = _decode_audio(path)
        duration = len(samples) / SAMPLE_RATE
        if duration > MAX_SECONDS:
            raise _AudioFailure("AUDIO_TOO_LONG")
        if duration <= 0:
            raise _AudioFailure("INVALID_AUDIO")
        if not _speech_present(samples):
            return {"status": "no_speech", "data": transcript, "error": None}
        try:
            model = _load_asr(asr_path)
        except (ImportError, MemoryError, _AudioFailure):
            raise
        except Exception:
            raise _AudioFailure("ASR_MODEL_INVALID") from None
        try:
            recognized = _recognize(model, samples)
        except (ImportError, MemoryError, _AudioFailure):
            raise
        except Exception:
            raise _AudioFailure("ASR_FAILED") from None
        # Release CT2 buffers before loading speaker embeddings.
        del model
        chunks = _split_segments(recognized, duration)
        if not chunks:
            raise _AudioFailure("EMPTY_MODEL_OUTPUT")
        if len(chunks) < num_speakers:
            raise _AudioFailure("INSUFFICIENT_SPEECH")
        try:
            extractor = _load_speaker(speaker_path)
        except (ImportError, MemoryError, _AudioFailure):
            raise
        except Exception:
            raise _AudioFailure("SPEAKER_MODEL_INVALID") from None
        try:
            vectors = [_embedding(extractor, samples, chunk) for chunk in chunks]
            labels = _cluster_embeddings(vectors, num_speakers)
        except (ImportError, MemoryError, _AudioFailure):
            raise
        except Exception:
            raise _AudioFailure("DIARIZATION_FAILED") from None
        # Merge adjacent same-speaker chunks while retaining observed timings.
        rows = []
        for chunk, label in zip(chunks, labels):
            if rows and rows[-1]["speaker_id"] == label and chunk["start"] - rows[-1]["end"] < 0.8:
                rows[-1]["end"] = max(rows[-1]["end"], chunk["end"])
                rows[-1]["text"] += " " + chunk["text"]
            else:
                rows.append({"id": f"s{len(rows) + 1}", "start": round(chunk["start"], 3),
                             "end": round(chunk["end"], 3), "speaker_id": label, "text": chunk["text"]})
        transcript["segments"] = rows
        transcript["speakers"] = [{"speaker_id": f"spk{i}", "participant_id": None,
                                    "display_name": None, "confirmed": False}
                                   for i in range(1, num_speakers + 1)]
        if validate_transcript(transcript):
            raise _AudioFailure("ASR_FAILED")
        return {"status": "ok", "data": transcript, "error": None}
    except _AudioFailure as error:
        return _error(error.code)
    except ImportError:
        return _error("DEPENDENCY_MISSING")
    except MemoryError:
        return _error("RESOURCE_EXHAUSTED")
    except Exception:
        return _error("ASR_FAILED")
