"""Explicit staged smoke helper for the repository's fictional audio only.

Run from the repository root. Never use this helper with private recordings.
Each stage refuses to overwrite earlier outputs; choose a fresh subdirectory
of runtime/smoke for a new run. Review copies are supplied by the human.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import validate_export, validate_snapshot, validate_transcript

NOTICE = "Только штатная синтетическая запись; реальные модели вызываются локально."
MODEL_NOTICE = "Ожидается Qwen3-4B-Q4_K_M.gguf; alias local сам по себе не подтверждает загруженные веса."


class SmokeError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Некорректные параметры smoke. Используйте --help.\n")


def _absolute(value):
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw.strip() or "://" in raw or raw.startswith(("//", "\\\\")):
        raise SmokeError()
    path = Path(raw)
    return (path if path.is_absolute() else ROOT / path).absolute()


def _not_linked(path):
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise SmokeError()
    return path


def _fixture(value, filename):
    path = _absolute(value)
    expected = ROOT / "fixtures" / "audio" / filename
    # Check allowlist before touching contents of an arbitrary supplied path.
    if path != expected:
        raise SmokeError()
    _not_linked(path)
    if not path.is_file():
        raise SmokeError()
    return path


def _output_directory(value):
    path = _absolute(value)
    allowed = ROOT / "runtime" / "smoke"
    if ".." in path.parts or not path.is_relative_to(allowed):
        raise SmokeError()
    return _not_linked(path)


def _review_file(value):
    if value is None:
        raise SmokeError()
    path = _absolute(value)
    if path.suffix.lower() != ".json" or path.name not in ("transcript-reviewed.json", "analysis-reviewed.json"):
        raise SmokeError()
    _output_directory(path.parent)
    _not_linked(path)
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise SmokeError()
    return path


def _read_json(path):
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    return value, raw


def _write_new(path, content):
    # Exclusive creation preserves the original ASR output and review files.
    with path.open("xb") as output:
        output.write(content)


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _require_new(directory, names):
    if any((directory / name).exists() for name in names):
        raise SmokeError()


def _model_failure(result):
    code = result.get("error", {}).get("code", "MODEL_FAILED") if isinstance(result.get("error"), dict) else "MODEL_FAILED"
    print(code if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,64}", code) else "MODEL_FAILED", file=sys.stderr)
    return 1


def run(arguments):
    directory = _output_directory(arguments.output_dir)
    # Even analyze/export are restricted to the known fictional fixture context.
    audio = _fixture(arguments.audio, "synthetic_ru_two_speakers.wav")
    metadata_file = _fixture(arguments.meeting_meta, "synthetic_ru_two_speakers.json")
    if arguments.stage in ("analyze", "export") and not arguments.confirm_reviewed:
        raise SmokeError()
    if arguments.stage == "transcribe":
        _require_new(directory, ("transcript.json", "transcribe-run.json"))
        metadata, _ = _read_json(metadata_file)
        # Only these two metadata fields are used; expected/reference data
        # never enter the recognizer or text analysis prompt.
        meeting, people = metadata["meeting"], metadata["participants"]
        check = {"schema_version": "qonimai.analysis.v1", "revision": 1,
                 "meeting": meeting, "participants": people, "speakers": [], "segments": []}
        if validate_transcript(check):
            raise SmokeError()
        from audio import transcribe
        started = perf_counter()
        result = transcribe(audio, meeting, people,
                            asr_model_path=os.environ.get("QONIMAI_ASR_MODEL", str(ROOT / "models" / "whisper-small")),
                            speaker_model_path=os.environ.get("QONIMAI_SPEAKER_MODEL", str(ROOT / "models" / "speaker" / "wespeaker_en_voxceleb_resnet34_LM.onnx")),
                            num_speakers=2)
        seconds = perf_counter() - started
        if result["status"] not in ("ok", "no_speech"):
            return _model_failure(result)
        if validate_transcript(result["data"]):
            raise SmokeError()
        if any(speaker["confirmed"] or speaker["participant_id"] is not None or speaker["display_name"] is not None
               for speaker in result["data"]["speakers"]):
            raise SmokeError()
        directory.mkdir(parents=True, exist_ok=True)
        _write_new(directory / "transcript.json", _json_bytes(result["data"]))
        _write_new(directory / "transcribe-run.json", _json_bytes({
            "synthetic_audio": True, "stage": "transcribe", "status": result["status"],
            "seconds": round(seconds, 6), "fixture": "synthetic_ru_two_speakers.wav",
            "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
            "speakers_confirmed": False,
        }))
        print("Сохранён transcript.json. Создайте отдельную transcript-reviewed.json и вручную проверьте текст и голоса.")
        return 0

    transcript_file = _review_file(arguments.reviewed_transcript)
    if transcript_file.name != "transcript-reviewed.json":
        raise SmokeError()
    transcript, transcript_raw = _read_json(transcript_file)
    if validate_transcript(transcript):
        raise SmokeError()
    if arguments.stage == "analyze":
        _require_new(directory, ("analysis.json", "analysis-source.json", "analysis-run.json"))
        from analysis.local_model import analyze
        started = perf_counter()
        result = analyze(transcript, base_url="http://127.0.0.1:8081", model="local", timeout=180)
        seconds = perf_counter() - started
        if result["status"] not in ("ok", "no_speech"):
            return _model_failure(result)
        if validate_snapshot(transcript, result):
            raise SmokeError()
        # Save the actual model result unchanged; no expected fixture is loaded.
        directory.mkdir(parents=True, exist_ok=True)
        _write_new(directory / "analysis.json", _json_bytes(result))
        _write_new(directory / "analysis-source.json", transcript_raw)
        _write_new(directory / "analysis-run.json", _json_bytes({
            "synthetic_audio": True, "stage": "analyze", "seconds": round(seconds, 6),
            "model_alias": "local", "endpoint": "http://127.0.0.1:8081",
            "expected_model_file": "Qwen3-4B-Q4_K_M.gguf", "server_model_identity_verified": False,
            "source_sha256": hashlib.sha256(transcript_raw).hexdigest(),
            "source_revision": transcript["revision"], "content_review_confirmed": True,
        }))
        print("Сохранён analysis.json. Создайте analysis-reviewed.json и отдельно сверьте поручения с репликами.")
        return 0

    analysis_file = _review_file(arguments.reviewed_analysis)
    if analysis_file.name != "analysis-reviewed.json":
        raise SmokeError()
    analysis, _ = _read_json(analysis_file)
    if validate_export(transcript, analysis, transcript["revision"]):
        raise SmokeError()
    _require_new(directory, ("protocol-synthetic.docx",))
    from ui_helpers import export_document_bytes
    content = export_document_bytes(transcript, analysis, transcript["revision"], synthetic_audio=True)
    directory.mkdir(parents=True, exist_ok=True)
    _write_new(directory / "protocol-synthetic.docx", content)
    print("Сохранён protocol-synthetic.docx с явной маркировкой синтетической аудиозаписи.")
    return 0


def main(argv=None):
    # Keep Russian CLI output readable when Windows redirects it to a UTF-8 log.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = _Parser(description=NOTICE + " " + MODEL_NOTICE)
    parser.add_argument("--stage", required=True, choices=("transcribe", "analyze", "export"))
    parser.add_argument("--audio", default="fixtures/audio/synthetic_ru_two_speakers.wav")
    parser.add_argument("--meeting-meta", default="fixtures/audio/synthetic_ru_two_speakers.json")
    parser.add_argument("--output-dir", default="runtime/smoke")
    parser.add_argument("--reviewed-transcript", help="runtime/smoke/.../transcript-reviewed.json")
    parser.add_argument("--reviewed-analysis", help="runtime/smoke/.../analysis-reviewed.json")
    parser.add_argument("--confirm-reviewed", action="store_true")
    arguments = parser.parse_args(argv)
    print(NOTICE)
    try:
        return run(arguments)
    except Exception:
        print("Smoke не выполнен: проверьте --help, разрешённые синтетические пути, отдельные просмотренные JSON, версии и отсутствие старых выходных файлов. Содержимое и внутренние пути не выводятся.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
