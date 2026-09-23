"""Synthetic unit tests. Mocked models do not establish ASR/diarization quality."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

from audio import pipeline as p
from contracts import validate_transcript


def segment(start, end, text, words=None):
    return SimpleNamespace(start=start, end=end, text=text, words=words)


class AudioPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qonimai-audio-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audio = self.root / "synthetic.wav"
        self.audio.write_bytes(b"synthetic input; decoder is mocked")
        self.asr = self.root / "fake-local-asr"
        self.asr.mkdir()
        for name in ("model.bin", "config.json", "tokenizer.json"):
            (self.asr / name).write_bytes(b"test fixture only")
        self.speaker = self.root / "fake-speaker.onnx"
        self.speaker.write_bytes(b"test fixture only")
        self.meeting = {"started_at": None, "timezone": None}
        self.people = [{"id": "p1", "name": "Вымышленный участник"}]

    def call(self, **kwargs):
        return p.transcribe(self.audio, self.meeting, self.people,
                            asr_model_path=self.asr, speaker_model_path=self.speaker, **kwargs)

    def mock_pipeline(self, *, speech=True, recognized=None):
        if recognized is None:
            recognized = [segment(0, 2, "Синтетическая реплика первого."),
                          segment(3, 5, "Синтетическая реплика второго.")]
        replacements = {
            "_decode_audio": Mock(return_value=np.zeros(6 * p.SAMPLE_RATE, dtype=np.float32)),
            "_speech_present": Mock(return_value=speech),
            "_load_asr": Mock(return_value=object()),
            "_recognize": Mock(return_value=recognized),
            "_load_speaker": Mock(return_value=object()),
            "_embedding": Mock(side_effect=[np.array([1.0, 0.0]), np.array([0.0, 1.0])]),
        }
        for name, replacement in replacements.items():
            guard = patch.object(p, name, replacement)
            guard.start()
            self.addCleanup(guard.stop)
        return replacements

    def test_success_obeys_contract_and_never_assigns_names(self):
        self.mock_pipeline()
        before = deepcopy((self.meeting, self.people))
        result = self.call()
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(validate_transcript(result["data"]), [])
        self.assertEqual((self.meeting, self.people), before)
        self.assertEqual([s["speaker_id"] for s in result["data"]["segments"]], ["spk1", "spk2"])
        for speaker in result["data"]["speakers"]:
            self.assertFalse(speaker["confirmed"])
            self.assertIsNone(speaker["participant_id"])
            self.assertIsNone(speaker["display_name"])

    def test_detected_silence_is_distinct_from_empty_asr(self):
        fakes = self.mock_pipeline(speech=False)
        result = self.call()
        self.assertEqual(result["status"], "no_speech")
        self.assertEqual(result["data"]["segments"], [])
        fakes["_load_asr"].assert_not_called()
        fakes["_load_speaker"].assert_not_called()

    def test_empty_asr_after_vad_speech_is_an_error(self):
        self.mock_pipeline(recognized=[])
        self.assertEqual(self.call()["error"]["code"], "EMPTY_MODEL_OUTPUT")

    def test_too_few_chunks_are_not_fake_two_speakers(self):
        self.mock_pipeline(recognized=[segment(0, 2, "Одна реплика")])
        self.assertEqual(self.call()["error"]["code"], "INSUFFICIENT_SPEECH")

    def test_one_known_speaker_is_supported(self):
        self.mock_pipeline(recognized=[segment(0, 2, "Одна реплика")])
        result = self.call(num_speakers=1)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["data"]["speakers"]), 1)

    def test_bad_speaker_count_rejected_before_processing(self):
        with patch.object(p, "_decode_audio") as decoder:
            for count in (0, 9, True, 2.0, "2"):
                self.assertEqual(self.call(num_speakers=count)["error"]["code"], "INVALID_INPUT")
            decoder.assert_not_called()

    def test_invalid_metadata_rejected(self):
        self.meeting["started_at"] = "not a date"
        self.assertEqual(self.call()["error"]["code"], "INVALID_INPUT")

    def test_empty_file_rejected(self):
        self.audio.write_bytes(b"")
        self.assertEqual(self.call()["error"]["code"], "EMPTY_FILE")

    def test_size_limit_precedes_decode(self):
        with patch.object(p, "MAX_FILE_BYTES", 3), patch.object(p, "_decode_audio") as decoder:
            self.assertEqual(self.call()["error"]["code"], "AUDIO_TOO_LARGE")
            decoder.assert_not_called()

    def test_duration_limit_precedes_models(self):
        fakes = self.mock_pipeline()
        with patch.object(p, "MAX_SECONDS", 5):
            self.assertEqual(self.call()["error"]["code"], "AUDIO_TOO_LONG")
        fakes["_load_asr"].assert_not_called()

    def test_missing_models_are_explicit_and_no_download_attempt(self):
        (self.asr / "tokenizer.json").unlink()
        with patch.object(p, "_load_asr") as model:
            self.assertEqual(self.call()["error"]["code"], "ASR_MODEL_MISSING")
            model.assert_not_called()

    def test_missing_speaker_model_is_not_single_speaker_fallback(self):
        self.speaker.unlink()
        self.assertEqual(self.call()["error"]["code"], "SPEAKER_MODEL_MISSING")

    def test_urls_rejected_without_reading(self):
        result = p.transcribe("https://example.invalid/recording.wav", self.meeting, self.people)
        self.assertEqual(result["error"]["code"], "INVALID_AUDIO")
        for url in ("https://example.invalid/model.onnx", "//server/share/model.onnx"):
            with self.assertRaises(p._AudioFailure):
                p._model_path(url, "UNUSED_TEST_ENV", directory=False)

    @unittest.skipUnless(p.os.name == "nt", "Windows drive policy")
    def test_mapped_network_drive_rejected(self):
        with patch.object(p.ctypes.windll.kernel32, "GetDriveTypeW", return_value=4):
            with self.assertRaises(ValueError):
                p._local_path(self.audio)

    @unittest.skipUnless(hasattr(Path, "is_junction"), "Python junction support")
    def test_junction_paths_rejected(self):
        with patch.object(Path, "is_junction", return_value=True):
            with self.assertRaises(ValueError):
                p._local_path(self.audio)

    def test_decoder_restricts_nested_media_protocols(self):
        factory = Mock(side_effect=RuntimeError("synthetic decoder refusal"))
        with patch.dict("sys.modules", {"av": SimpleNamespace(open=factory)}):
            with self.assertRaises(p._AudioFailure):
                p._decode_audio(self.audio)
        self.assertEqual(factory.call_args.kwargs["options"], {"protocol_whitelist": "file,pipe"})

    def test_environment_paths_work(self):
        self.mock_pipeline()
        with patch.dict(p.os.environ, {"QONIMAI_ASR_MODEL": str(self.asr), "QONIMAI_SPEAKER_MODEL": str(self.speaker)}):
            result = p.transcribe(self.audio, self.meeting, self.people)
        self.assertEqual(result["status"], "ok")

    def test_failure_does_not_expose_path_text_or_exception(self):
        fakes = self.mock_pipeline()
        fakes["_recognize"].side_effect = RuntimeError("PRIVATE_SYNTHETIC_MARKER " + str(self.audio))
        result = self.call()
        self.assertEqual(result["error"]["code"], "ASR_FAILED")
        self.assertNotIn("PRIVATE_SYNTHETIC_MARKER", json.dumps(result))
        self.assertNotIn(str(self.audio), json.dumps(result))
        self.assertIsNone(result["data"])

    def test_memory_errors_have_own_code(self):
        fakes = self.mock_pipeline()
        fakes["_load_asr"].side_effect = MemoryError()
        self.assertEqual(self.call()["error"]["code"], "RESOURCE_EXHAUSTED")

    def test_dependency_errors_have_own_code(self):
        with patch.object(p, "_decode_audio", side_effect=ImportError("private missing package")):
            self.assertEqual(self.call()["error"]["code"], "DEPENDENCY_MISSING")

    def test_keyboard_interrupt_propagates(self):
        with patch.object(p, "_decode_audio", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.call()

    def test_word_timestamps_split_without_inventing_text(self):
        words = [SimpleNamespace(start=0, end=1, word=" Әсел,"),
                 SimpleNamespace(start=1, end=2, word=" отчёт"),
                 SimpleNamespace(start=3, end=4, word=" ертең.")]
        chunks = p._split_segments([segment(0, 4, " Әсел, отчёт ертең.", words)], 5)
        self.assertEqual([row["text"] for row in chunks], ["Әсел, отчёт", "ертең."])
        self.assertEqual([(row["start"], row["end"]) for row in chunks], [(0, 2), (3, 4)])

    def test_missing_words_preserve_full_segment(self):
        self.assertEqual(p._split_segments([segment(0, 8, "Полная реплика")], 8),
                         [{"start": 0.0, "end": 8.0, "text": "Полная реплика"}])

    def test_invalid_model_timestamps_rejected(self):
        for end in (float("nan"), float("inf"), -1, 8):
            with self.assertRaises(p._AudioFailure):
                p._split_segments([segment(0, end, "Реплика")], 5)

    def test_acoustic_clustering_groups_matching_vectors_and_stable_first_voice(self):
        vectors = [[1, 0], [0, 1], [0.99, 0.01], [0.02, 0.98]]
        self.assertEqual(p._cluster_embeddings(vectors, 2), ["spk1", "spk2", "spk1", "spk2"])
        self.assertEqual(p._cluster_embeddings(vectors, 1), ["spk1"] * 4)

    def test_invalid_embeddings_fail_instead_of_guessing(self):
        for vectors in ([[0, 0], [1, 0]], [[float("nan"), 0], [1, 0]]):
            with self.assertRaises(p._AudioFailure):
                p._cluster_embeddings(vectors, 2)

    def test_asr_loader_forces_local_cpu_int8(self):
        model = SimpleNamespace(model=SimpleNamespace(is_multilingual=True))
        factory = Mock(return_value=model)
        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=factory)}):
            self.assertIs(p._load_asr(self.asr), model)
        kwargs = factory.call_args.kwargs
        self.assertTrue(kwargs["local_files_only"])
        self.assertEqual(kwargs["device"], "cpu")
        self.assertEqual(kwargs["compute_type"], "int8")

    def test_recognizer_uses_transcription_and_multilingual_mode(self):
        model = Mock()
        model.transcribe.return_value = (iter([]), None)
        p._recognize(model, np.zeros(10, dtype=np.float32))
        self.assertEqual(model.transcribe.call_args.kwargs["task"], "transcribe")
        self.assertTrue(model.transcribe.call_args.kwargs["multilingual"])
        self.assertTrue(model.transcribe.call_args.kwargs["word_timestamps"])

    @unittest.skipUnless(importlib.util.find_spec("av"), "PyAV runtime not installed")
    def test_real_pyav_decodes_stereo_resamples_and_enforces_duration(self):
        # A generated sine wave is only a decoder test, never a speech test.
        rate = 8000
        tone = (np.sin(np.arange(rate * 2) * 2 * np.pi * 440 / rate) * 1000).astype("<i2")
        stereo = np.column_stack([tone, tone]).reshape(-1)
        with wave.open(str(self.audio), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes(stereo.tobytes())
        decoded = p._decode_audio(self.audio)
        self.assertEqual(decoded.ndim, 1)
        self.assertEqual(len(decoded), p.SAMPLE_RATE * 2)
        self.assertEqual(decoded.dtype, np.float32)
        with patch.object(p, "MAX_SECONDS", 1):
            with self.assertRaises(p._AudioFailure) as caught:
                p._decode_audio(self.audio)
        self.assertEqual(caught.exception.code, "AUDIO_TOO_LONG")


if __name__ == "__main__":
    unittest.main()
