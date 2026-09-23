"""Synthetic tests of the raw model-response boundary, without a model.

Canonical inputs remain unchanged. Structural negative cases use in-memory
copies with unknown meeting metadata so they also run without an IANA database.
These checks do not establish transcription or natural-language accuracy.
"""

import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import contracts
from analysis.model_output import parse_model_output


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_cases.json"
with FIXTURE_PATH.open(encoding="utf-8") as fixture_file:
    CASES = {case["id"]: case for case in json.load(fixture_file)["cases"]}

try:
    ZoneInfo("Asia/Qyzylorda")
    HAS_TIMEZONE_DATA = True
except ZoneInfoNotFoundError:
    HAS_TIMEZONE_DATA = False


def case_copy(case_id="RU_EXPLICIT", *, unknown_meeting=True):
    case = copy.deepcopy(CASES[case_id])
    if unknown_meeting:
        case["input"]["meeting"] = {"started_at": None, "timezone": None}
    return case["input"], case["expected"]


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def replace(value, path, replacement):
    for part in path[:-1]:
        value = value[part]
    value[path[-1]] = replacement


class ModelOutputTests(unittest.TestCase):
    def assert_error(self, result, code):
        self.assertIsInstance(result, dict)
        self.assertEqual(set(result), {"status", "data", "error"})
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["data"])
        self.assertEqual(set(result["error"]), {"code", "message", "retryable"})
        self.assertEqual(result["error"]["code"], code)
        self.assertIs(result["error"]["retryable"], False)
        self.assertIsInstance(result["error"]["message"], str)
        self.assertTrue(result["error"]["message"].strip())
        return result

    def test_whitespace_around_document_is_allowed(self):
        transcript, analysis = case_copy()
        self.assertEqual(parse_model_output(" \t\r\n" + encode(analysis) + "\n\t ", transcript), analysis)

    def test_empty_or_whitespace_response_is_distinct_error(self):
        transcript, _ = case_copy()
        for raw in ("", " ", "\r\n\t "):
            with self.subTest(raw=repr(raw)):
                self.assert_error(parse_model_output(raw, transcript), "EMPTY_MODEL_OUTPUT")

    def test_nonstring_response_is_not_automatically_converted(self):
        transcript, analysis = case_copy()
        for raw in (None, False, True, 1, 1.0, [], {}, analysis, b"{}", bytearray(b"{}")):
            with self.subTest(raw_type=type(raw).__name__):
                self.assert_error(parse_model_output(raw, transcript), "INVALID_MODEL_OUTPUT")

    def test_json_root_must_be_an_object(self):
        transcript, _ = case_copy()
        for raw in ("null", "[]", "[{}]", '"text"', "0", "1.5", "true", "false"):
            with self.subTest(raw=raw):
                self.assert_error(parse_model_output(raw, transcript), "INVALID_MODEL_OUTPUT")

    def test_malformed_json_is_rejected(self):
        transcript, _ = case_copy()
        for raw in ("{", "}", '{"status":}', "{'status':'ok'}", '{"status":"ok",}',
                    '{"status":"ok" // comment\n}', '/* comment */ {}', '{"x":"\x00"}'):
            with self.subTest(raw=raw):
                self.assert_error(parse_model_output(raw, transcript), "INVALID_MODEL_JSON")

    def test_markdown_fences_are_not_unwrapped(self):
        transcript, analysis = case_copy()
        for fence in ("```json\n", "```\n", "~~~json\n"):
            with self.subTest(fence=fence):
                closing = "~~~" if fence.startswith("~~~") else "```"
                self.assert_error(parse_model_output(fence + encode(analysis) + "\n" + closing, transcript), "INVALID_MODEL_JSON")

    def test_text_around_json_is_not_extracted(self):
        transcript, analysis = case_copy()
        raw = encode(analysis)
        for wrapped in ("Вот результат: " + raw, raw + "\nГотово.", "prefix " + raw + " suffix"):
            with self.subTest(wrapped=wrapped[:20]):
                self.assert_error(parse_model_output(wrapped, transcript), "INVALID_MODEL_JSON")

    def test_multiple_documents_are_rejected(self):
        transcript, analysis = case_copy()
        raw = encode(analysis)
        for combined in (raw + raw, raw + "\n" + raw, raw + " null"):
            with self.subTest(separator=combined[len(raw):len(raw) + 5]):
                self.assert_error(parse_model_output(combined, transcript), "INVALID_MODEL_JSON")

    def test_duplicate_root_keys_even_with_identical_values_are_rejected(self):
        transcript, analysis = case_copy()
        raw = '{"status":"ok",' + encode(analysis)[1:]
        self.assert_error(parse_model_output(raw, transcript), "INVALID_MODEL_JSON")

    def test_duplicate_nested_task_keys_are_rejected(self):
        transcript, analysis = case_copy()
        raw = encode(analysis).replace('"description":', '"description":"Подмена","description":', 1)
        self.assert_error(parse_model_output(raw, transcript), "INVALID_MODEL_JSON")

    def test_duplicate_nested_data_keys_are_rejected(self):
        transcript, analysis = case_copy()
        raw = encode(analysis).replace('"user_edits":[]', '"user_edits":[],"user_edits":[]', 1)
        self.assert_error(parse_model_output(raw, transcript), "INVALID_MODEL_JSON")

    def test_escaped_equivalent_keys_are_duplicates(self):
        transcript, analysis = case_copy()
        raw = '{"\\u0073tatus":"ok",' + encode(analysis)[1:]
        self.assert_error(parse_model_output(raw, transcript), "INVALID_MODEL_JSON")

    def test_nonfinite_constants_are_invalid_json(self):
        transcript, analysis = case_copy()
        for number in ("NaN", "Infinity", "-Infinity", "+Infinity"):
            with self.subTest(number=number):
                raw = encode(analysis).replace('"source_revision":1', '"source_revision":' + number, 1)
                self.assert_error(parse_model_output(raw, transcript), "INVALID_MODEL_JSON")

    def test_numeric_float_overflow_is_invalid_json(self):
        transcript, analysis = case_copy()
        for number in ("1e400", "-1e400", "1E999999", "-9.99e9999"):
            with self.subTest(number=number):
                raw = encode(analysis).replace('"source_revision":1', '"source_revision":' + number, 1)
                self.assert_error(parse_model_output(raw, transcript), "INVALID_MODEL_JSON")

    def test_nonfinite_like_text_is_preserved_as_text(self):
        transcript, analysis = case_copy()
        analysis["data"]["summary"][0]["text"] = "Обсудили обозначения NaN, Infinity и -Infinity."
        self.assertEqual(parse_model_output(encode(analysis), transcript), analysis)

    def test_deep_valid_json_returns_error_instead_of_recursion_traceback(self):
        transcript, _ = case_copy()
        # The C decoder's recursion budget can exceed Python's recursion limit.
        depth = 10000
        for opening, closing in (("[", "]"), ('{"nested":', "}")):
            with self.subTest(container=opening):
                raw = opening * depth + "0" + closing * depth
                self.assert_error(parse_model_output(raw, transcript), "INVALID_MODEL_JSON")

    def test_integer_decoder_digit_limit_returns_json_error(self):
        limit = sys.get_int_max_str_digits()
        if not limit:
            self.skipTest("This interpreter has no integer decoding digit limit")
        transcript, _ = case_copy()
        raw = '{"source_revision":' + "7" * (limit + 1) + "}"
        self.assert_error(parse_model_output(raw, transcript), "INVALID_MODEL_JSON")

    def test_invalid_transcript_has_priority_over_every_response_category(self):
        transcript, analysis = case_copy()
        transcript["revision"] = True
        for raw in (None, "", "{bad JSON", encode(analysis)):
            with self.subTest(raw_type=type(raw).__name__):
                self.assert_error(parse_model_output(raw, transcript), "INVALID_INPUT")

    def test_invalid_transcript_does_not_decode_response(self):
        transcript, analysis = case_copy()
        transcript["segments"][0]["speaker_id"] = "missing"
        with mock.patch("analysis.model_output.json.loads") as decoder:
            self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_INPUT")
        decoder.assert_not_called()

    def test_invalid_transcript_shapes_return_input_error(self):
        for transcript in (None, False, 1, "not a transcript", [], {}, {"segments": []}):
            with self.subTest(transcript=transcript):
                self.assert_error(parse_model_output("{}", transcript), "INVALID_INPUT")

    @unittest.skipIf(HAS_TIMEZONE_DATA, "Tests the actual missing IANA database environment")
    def test_missing_iana_database_precedes_response_decoding(self):
        transcript, analysis = case_copy("TOMORROW_YEAR", unknown_meeting=False)
        with mock.patch("analysis.model_output.json.loads") as decoder:
            self.assert_error(parse_model_output(encode(analysis), transcript), "TIMEZONE_DATA_UNAVAILABLE")
        decoder.assert_not_called()

    @unittest.skipUnless(HAS_TIMEZONE_DATA, "Real IANA timezone database unavailable")
    def test_invalid_zone_or_offset_are_input_errors(self):
        for change in ({"timezone": "Invented/Does_Not_Exist"}, {"started_at": "2026-09-23T10:00:00+06:00"}):
            with self.subTest(change=change):
                transcript, analysis = case_copy(unknown_meeting=False)
                transcript["meeting"].update(change)
                self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_INPUT")

    def test_required_fields_are_not_invented(self):
        paths = [("status",), ("data", "summary"), ("data", "decisions"), ("data", "tasks"),
                 ("data", "source_revision"), ("data", "user_edits"), ("error",)]
        for path in paths:
            with self.subTest(path=path):
                transcript, analysis = case_copy()
                current = analysis
                for part in path[:-1]:
                    current = current[part]
                del current[path[-1]]
                self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")

    def test_unknown_fields_are_rejected(self):
        for path in ((), ("data",), ("data", "tasks", 0)):
            with self.subTest(path=path):
                transcript, analysis = case_copy()
                current = analysis
                for part in path:
                    current = current[part]
                current["unexpected"] = True
                self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")

    def test_wrong_schema_types_are_not_coerced(self):
        mutations = [(("status",), []), (("data",), None), (("error",), {}),
                     (("data", "source_revision"), True), (("data", "source_revision"), 1.0),
                     (("data", "summary"), {}), (("data", "decisions"), ""),
                     (("data", "tasks"), None), (("data", "user_edits"), False),
                     (("data", "tasks", 0, "assignee_id"), []),
                     (("data", "tasks", 0, "author_speaker_id"), {}),
                     (("data", "tasks", 0, "requires_clarification"), 0)]
        for path, value in mutations:
            with self.subTest(path=path, value=value):
                transcript, analysis = case_copy()
                replace(analysis, path, value)
                self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")

    def test_model_error_is_rejected_even_when_envelope_is_valid(self):
        transcript, _ = case_copy()
        for code in ("MODEL_UNAVAILABLE", "RESOURCE_EXHAUSTED", "FUTURE_ADAPTER_ERROR"):
            with self.subTest(code=code):
                model_error = {"status": "error", "data": None,
                    "error": {"code": code, "message": "SYNTHETIC_PRIVATE_ERROR_MARKER", "retryable": True}}
                self.assertEqual(contracts.validate_snapshot(transcript, model_error), [])
                result = self.assert_error(parse_model_output(encode(model_error), transcript), "INVALID_MODEL_OUTPUT")
                self.assertNotIn("SYNTHETIC_PRIVATE_ERROR_MARKER", encode(result))

    def test_no_speech_cannot_hide_a_nonempty_transcript(self):
        transcript, _ = case_copy()
        _, analysis = case_copy("NO_SPEECH")
        self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")

    def test_empty_transcript_requires_no_speech_not_ok(self):
        transcript, analysis = case_copy("NO_SPEECH")
        analysis["status"] = "ok"
        self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")

    def test_no_speech_result_must_still_have_valid_structure(self):
        transcript, analysis = case_copy("NO_SPEECH")
        analysis["data"]["source_revision"] = 2
        self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")

    def test_bad_author_assignee_and_source_references_are_rejected(self):
        mutations = [("author_speaker_id", "missing"), ("assignee_id", "missing"),
                     ("source_segment_ids", ["missing"]), ("source_segment_ids", []),
                     ("source_segment_ids", ["S1"])]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                transcript, analysis = case_copy()
                analysis["data"]["tasks"][0][field] = value
                self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")

    def test_broken_summary_source_is_rejected(self):
        transcript, analysis = case_copy()
        analysis["data"]["summary"][0]["source_segment_ids"] = ["missing"]
        self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")

    def test_invalid_calendar_date_and_wrong_normalization_are_rejected(self):
        for due_date in ("2026-02-30", "2026-9-30", "2026-10-01"):
            with self.subTest(due_date=due_date):
                transcript, analysis = case_copy()
                analysis["data"]["tasks"][0]["due_date"] = due_date
                self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")

    @unittest.skipUnless(HAS_TIMEZONE_DATA, "Real IANA timezone database unavailable")
    def test_wrong_tomorrow_date_is_not_silently_fixed(self):
        transcript, analysis = case_copy("TOMORROW_YEAR", unknown_meeting=False)
        analysis["data"]["tasks"][0]["due_date"] = "2026-12-31"
        self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")

    def test_stale_and_future_revisions_are_not_rewritten(self):
        for source_revision in (1, 3):
            with self.subTest(source_revision=source_revision):
                transcript, analysis = case_copy()
                transcript["revision"] = 2
                analysis["data"]["source_revision"] = source_revision
                self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")
                self.assertEqual(analysis["data"]["source_revision"], source_revision)

    def test_user_edits_wrong_types_or_nonempty_records_are_rejected(self):
        for edits in (None, {}, "", False, [None], [{}]):
            with self.subTest(edits=edits):
                transcript, analysis = case_copy()
                analysis["data"]["user_edits"] = edits
                self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")

    def test_valid_user_edit_snapshot_is_not_a_trusted_model_response(self):
        transcript, analysis = case_copy("USER_CLARIFIED_DUE")
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])
        self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")

    def test_nonfixture_wording_and_unicode_are_returned_unchanged(self):
        transcript, analysis = case_copy()
        analysis["data"]["summary"][0]["text"] = "Ирина поручила Данияру подготовить таблицу расходов. Ә Ғ Қ Ң Ө Ұ Ү Һ І"
        self.assertEqual(parse_model_output(encode(analysis), transcript), analysis)

    def test_result_has_no_approval_or_other_inserted_fields(self):
        transcript, analysis = case_copy("NO_DUE")
        result = parse_model_output(encode(analysis), transcript)
        self.assertEqual(result, analysis)
        self.assertNotIn("reviewed_revision", result)
        self.assertNotIn("reviewed_revision", result["data"])
        self.assertIsNone(result["data"]["tasks"][0]["due_date"])

    def test_transcript_is_unchanged_after_success_and_failure(self):
        transcript, analysis = case_copy()
        before = copy.deepcopy(transcript)
        raw = encode(analysis)
        self.assertEqual(parse_model_output(raw, transcript), analysis)
        self.assertEqual(transcript, before)
        self.assert_error(parse_model_output(raw + " trailing text", transcript), "INVALID_MODEL_JSON")
        self.assertEqual(transcript, before)
        transcript["revision"] = False
        before_invalid = copy.deepcopy(transcript)
        self.assert_error(parse_model_output(raw, transcript), "INVALID_INPUT")
        self.assertEqual(transcript, before_invalid)

    def test_errors_neither_echo_nor_print_private_markers(self):
        marker = "SYNTHETIC_SECRET_C:\\private\\invented-recording.wav"
        transcript, analysis = case_copy()
        private_error = {"status": "error", "data": None,
                         "error": {"code": "MODEL_UNAVAILABLE", "message": marker, "retryable": True}}
        bad_reference = copy.deepcopy(analysis)
        bad_reference["data"]["tasks"][0]["assignee_id"] = marker
        malformed = '{"' + marker + '":'
        private_input = copy.deepcopy(transcript)
        private_input["segments"][0]["speaker_id"] = marker
        cases = [(encode(private_error), transcript, "INVALID_MODEL_OUTPUT"),
                 (encode(bad_reference), transcript, "INVALID_MODEL_OUTPUT"),
                 (malformed, transcript, "INVALID_MODEL_JSON"),
                 (encode(analysis), private_input, "INVALID_INPUT")]
        for raw, current_transcript, code in cases:
            with self.subTest(code=code):
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), mock.patch("logging.Logger._log") as logger:
                    result = parse_model_output(raw, current_transcript)
                self.assert_error(result, code)
                self.assertNotIn(marker, encode(result))
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")
                logger.assert_not_called()

    def test_error_message_is_fixed_independently_of_model_error_text(self):
        transcript, _ = case_copy()
        results = []
        for text in ("SYNTHETIC_SECRET_ONE", "SYNTHETIC_SECRET_TWO"):
            analysis = {"status": "error", "data": None,
                        "error": {"code": text, "message": text, "retryable": True}}
            results.append(parse_model_output(encode(analysis), transcript))
        self.assert_error(results[0], "INVALID_MODEL_OUTPUT")
        self.assertEqual(results[0], results[1])

    def test_process_interrupts_are_not_suppressed(self):
        transcript, analysis = case_copy()
        raw = encode(analysis)
        for exception in (KeyboardInterrupt, SystemExit):
            for dependency in ("analysis.model_output.json.loads", "analysis.model_output.contracts.validate_snapshot"):
                with self.subTest(exception=exception.__name__, dependency=dependency):
                    with mock.patch(dependency, side_effect=exception):
                        with self.assertRaises(exception):
                            parse_model_output(raw, transcript)


def canonical_test(case_id):
    def test(self):
        transcript, analysis = case_copy(case_id, unknown_meeting=False)
        if transcript["meeting"]["timezone"] is not None and not HAS_TIMEZONE_DATA:
            self.skipTest("Real IANA timezone database unavailable; canonical input unchanged")
        before = copy.deepcopy(transcript)
        if case_id == "USER_CLARIFIED_DUE":
            self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])
            self.assert_error(parse_model_output(encode(analysis), transcript), "INVALID_MODEL_OUTPUT")
        else:
            self.assertEqual(parse_model_output(encode(analysis), transcript), analysis)
        self.assertEqual(transcript, before)
    test.__doc__ = "Canonical synthetic response boundary: " + case_id
    return test


for case_id in CASES:
    setattr(ModelOutputTests, "test_canonical_" + case_id.lower(), canonical_test(case_id))


if __name__ == "__main__":
    unittest.main()
