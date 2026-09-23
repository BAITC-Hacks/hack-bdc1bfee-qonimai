"""Smoke helper safety and stage tests; no real model is called here."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import smoke_local as smoke


class SmokeCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qonimai-cli-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        change = patch.object(smoke, "ROOT", self.root)
        change.start()
        self.addCleanup(change.stop)
        fixture = self.root / "fixtures" / "audio"
        fixture.mkdir(parents=True)
        (fixture / "synthetic_ru_two_speakers.wav").write_bytes(b"synthetic mock input")
        self.meeting = {"started_at": None, "timezone": None}
        self.people = [{"id": "p1", "name": "Тест"}]
        (fixture / "synthetic_ru_two_speakers.json").write_text(json.dumps({
            "meeting": self.meeting, "participants": self.people,
            "expected_tasks": "THIS MUST NEVER ENTER MODEL INPUT",
        }), encoding="utf-8")
        self.output = self.root / "runtime" / "smoke"
        self.transcript = {"schema_version": "qonimai.analysis.v1", "revision": 1,
                           "meeting": self.meeting, "participants": self.people,
                           "speakers": [{"speaker_id": "spk1", "participant_id": None,
                                         "display_name": None, "confirmed": False}],
                           "segments": [{"id": "s1", "start": 0, "end": 2,
                                         "speaker_id": "spk1", "text": "Обсудили макет."}]}
        self.analysis = {"status": "ok", "data": {"source_revision": 1,
            "summary": [{"id": "f1", "text": "Обсудили макет.", "source_segment_ids": ["s1"]}],
            "decisions": [], "tasks": [], "user_edits": []}, "error": None}

    def cli(self, args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = smoke.main(args)
        return result, out.getvalue(), err.getvalue()

    def reviews(self):
        self.output.mkdir(parents=True, exist_ok=True)
        first = self.output / "transcript-reviewed.json"
        second = self.output / "analysis-reviewed.json"
        first.write_text(json.dumps(self.transcript, ensure_ascii=False), encoding="utf-8")
        second.write_text(json.dumps(self.analysis, ensure_ascii=False), encoding="utf-8")
        return first, second

    def test_unknown_audio_and_metadata_rejected_before_content_read(self):
        for option in ("--audio", "--meeting-meta"):
            with patch.object(Path, "read_bytes", side_effect=AssertionError("Sensitive read")) as reader:
                result = self.cli(["--stage", "transcribe", option, "private/PRIVATE_MARKER.wav"])
            self.assertEqual(result[0], 2)
            reader.assert_not_called()
            self.assertNotIn("PRIVATE_MARKER", result[1] + result[2])

    def test_external_output_and_url_are_rejected(self):
        for value in (str(self.root.parent / "elsewhere"), "https://example.invalid/out", "runtime/smoke/../outside"):
            with patch.object(Path, "read_bytes") as reader:
                self.assertEqual(self.cli(["--stage", "transcribe", "--output-dir", value])[0], 2)
                reader.assert_not_called()

    def test_analyze_and_export_require_explicit_review_before_content_read(self):
        for stage in ("analyze", "export"):
            with patch.object(Path, "read_bytes") as reader:
                self.assertEqual(self.cli(["--stage", stage])[0], 2)
                reader.assert_not_called()

    def test_unknown_review_path_rejected_before_read(self):
        with patch.object(Path, "read_bytes") as reader:
            result = self.cli(["--stage", "analyze", "--confirm-reviewed", "--reviewed-transcript",
                               str(self.root / "private" / "transcript-reviewed.json")])
            self.assertEqual(result[0], 2)
            reader.assert_not_called()

    def test_transcribe_uses_actual_module_and_preserves_unconfirmed_output(self):
        expected = {"status": "ok", "data": deepcopy(self.transcript), "error": None}
        with patch("audio.transcribe", return_value=expected) as transcriber:
            result = self.cli(["--stage", "transcribe"])
        self.assertEqual(result[0], 0, result)
        self.assertEqual(json.loads((self.output / "transcript.json").read_text(encoding="utf-8")), self.transcript)
        self.assertEqual(transcriber.call_args.args[1:], (self.meeting, self.people))
        self.assertNotIn("expected_tasks", json.dumps(transcriber.call_args.kwargs))
        self.assertFalse(json.loads((self.output / "transcribe-run.json").read_text())["speakers_confirmed"])

    def test_existing_transcript_is_never_overwritten_and_models_not_called(self):
        self.output.mkdir(parents=True)
        target = self.output / "transcript.json"
        target.write_bytes(b"KEEP ORIGINAL")
        with patch("audio.transcribe") as transcriber:
            self.assertEqual(self.cli(["--stage", "transcribe"])[0], 2)
            transcriber.assert_not_called()
        self.assertEqual(target.read_bytes(), b"KEEP ORIGINAL")

    def test_failed_asr_is_not_saved_as_success(self):
        failure = {"status": "error", "data": None,
                   "error": {"code": "ASR_MODEL_MISSING", "message": "private", "retryable": False}}
        with patch("audio.transcribe", return_value=failure):
            result = self.cli(["--stage", "transcribe"])
        self.assertEqual(result[0], 1)
        self.assertNotIn("private", result[2])
        self.assertFalse((self.output / "transcript.json").exists())

    def test_analyze_preserves_review_file_and_exact_model_result(self):
        transcript, _ = self.reviews()
        original = transcript.read_bytes()
        with patch("analysis.local_model.analyze", return_value=deepcopy(self.analysis)) as analyzer:
            result = self.cli(["--stage", "analyze", "--reviewed-transcript", str(transcript), "--confirm-reviewed"])
        self.assertEqual(result[0], 0, result)
        self.assertEqual(transcript.read_bytes(), original)
        self.assertEqual((self.output / "analysis-source.json").read_bytes(), original)
        self.assertEqual(json.loads((self.output / "analysis.json").read_text(encoding="utf-8")), self.analysis)
        self.assertEqual(analyzer.call_args.args[0], self.transcript)
        self.assertEqual(analyzer.call_args.kwargs["base_url"], "http://127.0.0.1:8081")
        run = json.loads((self.output / "analysis-run.json").read_text())
        self.assertFalse(run["server_model_identity_verified"])

    def test_failed_model_result_creates_no_analysis_success(self):
        transcript, _ = self.reviews()
        failure = {"status": "error", "data": None,
                   "error": {"code": "INVALID_MODEL_OUTPUT", "message": "private", "retryable": False}}
        with patch("analysis.local_model.analyze", return_value=failure):
            result = self.cli(["--stage", "analyze", "--reviewed-transcript", str(transcript), "--confirm-reviewed"])
        self.assertEqual(result[0], 1)
        self.assertFalse((self.output / "analysis.json").exists())

    def test_export_uses_current_review_and_synthetic_audio_notice(self):
        transcript, analysis = self.reviews()
        with patch("ui_helpers.export_document_bytes", return_value=b"SYNTHETIC MOCK DOCX") as exporter:
            result = self.cli(["--stage", "export", "--reviewed-transcript", str(transcript),
                               "--reviewed-analysis", str(analysis), "--confirm-reviewed"])
        self.assertEqual(result[0], 0, result)
        self.assertTrue(exporter.call_args.kwargs["synthetic_audio"])
        self.assertEqual(exporter.call_args.args[2], self.transcript["revision"])
        self.assertEqual((self.output / "protocol-synthetic.docx").read_bytes(), b"SYNTHETIC MOCK DOCX")

    def test_stale_analysis_blocks_export(self):
        self.analysis["data"]["source_revision"] = 99
        transcript, analysis = self.reviews()
        with patch("ui_helpers.export_document_bytes") as exporter:
            self.assertEqual(self.cli(["--stage", "export", "--reviewed-transcript", str(transcript),
                                       "--reviewed-analysis", str(analysis), "--confirm-reviewed"])[0], 2)
            exporter.assert_not_called()

    def test_parser_errors_do_not_echo_unknown_private_argument(self):
        output = StringIO()
        with redirect_stderr(output), self.assertRaises(SystemExit) as raised:
            smoke.main(["--stage", "transcribe", "--PRIVATE_MARKER"])
        self.assertEqual(raised.exception.code, 2)
        self.assertNotIn("PRIVATE_MARKER", output.getvalue())

    def test_process_interrupts_are_not_hidden(self):
        with patch("audio.transcribe", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.cli(["--stage", "transcribe"])


if __name__ == "__main__":
    unittest.main()
