"""Contract checks on invented data only; no model, audio or DOCX execution.

Canonical fixtures stay untouched.  Tests without a timezone database still
exercise structure on copies with unknown meeting metadata; canonical cases
that require real IANA data are explicitly skipped, never reported as passed.
"""

import copy
import json
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import contracts


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


def at(value, path):
    for part in path:
        value = value[part]
    return value


def replace(value, path, replacement):
    at(value, path[:-1])[path[-1]] = replacement


class ContractTests(unittest.TestCase):
    def assert_errors(self, errors, code=None):
        self.assertIsInstance(errors, list)
        self.assertTrue(errors, "Expected validation errors")
        for error in errors:
            self.assertIsInstance(error, dict)
            self.assertEqual(set(error), {"code", "path", "message"})
            for field in ("code", "path", "message"):
                self.assertIsInstance(error[field], str)
                self.assertTrue(error[field].strip())
        if code is not None:
            self.assertIn(code, [error["code"] for error in errors])
        return errors

    def assert_analysis_invalid(self, transcript, analysis):
        self.assert_errors(
            contracts.validate_analysis(transcript, analysis),
            "INVALID_MODEL_OUTPUT",
        )

    def test_schema_version(self):
        self.assertEqual(contracts.SCHEMA_VERSION, "qonimai.analysis.v1")

    def test_fixture_inventory(self):
        self.assertEqual(len(CASES), 16)
        self.assertIn("USER_CLARIFIED_DUE", CASES)

    def test_unknown_meeting_allows_explicit_date(self):
        transcript, analysis = case_copy()
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])

    def test_transcript_required_and_unknown_keys(self):
        paths = [(), ("meeting",), ("participants", 0), ("speakers", 0), ("segments", 0)]
        for path in paths:
            transcript, _ = case_copy()
            for key in tuple(at(transcript, path)):
                with self.subTest(path=path, missing=key):
                    changed = copy.deepcopy(transcript)
                    del at(changed, path)[key]
                    self.assert_errors(contracts.validate_transcript(changed), "INVALID_INPUT")
            with self.subTest(path=path, extra=True):
                at(transcript, path)["unexpected"] = "value"
                self.assert_errors(contracts.validate_transcript(transcript), "INVALID_INPUT")

    def test_transcript_wrong_types(self):
        mutations = [
            (("schema_version",), None), (("schema_version",), "old-draft"),
            (("revision",), True), (("revision",), 1.0), (("revision",), 0),
            (("revision",), -1), (("revision",), "1"),
            (("meeting",), []), (("meeting", "started_at"), 1),
            (("meeting", "timezone"), False),
            (("participants",), {}), (("speakers",), None), (("segments",), ""),
            (("participants", 0), None), (("speakers", 0), []),
            (("segments", 0), "bad"),
            (("participants", 0, "id"), []), (("participants", 0, "name"), 42),
            (("speakers", 0, "speaker_id"), {}),
            (("speakers", 0, "participant_id"), []),
            (("speakers", 0, "display_name"), {}),
            (("speakers", 0, "confirmed"), 1),
            (("segments", 0, "id"), []), (("segments", 0, "speaker_id"), {}),
            (("segments", 0, "text"), None),
        ]
        for path, value in mutations:
            with self.subTest(path=path, value=value):
                transcript, _ = case_copy()
                replace(transcript, path, value)
                self.assert_errors(contracts.validate_transcript(transcript), "INVALID_INPUT")

    def test_transcript_nonempty_strings(self):
        paths = [
            ("participants", 0, "id"), ("participants", 0, "name"),
            ("speakers", 0, "speaker_id"), ("speakers", 0, "display_name"),
            ("segments", 0, "id"), ("segments", 0, "text"),
        ]
        for path in paths:
            for value in ("", " \t\n"):
                with self.subTest(path=path, blank=repr(value)):
                    transcript, _ = case_copy()
                    replace(transcript, path, value)
                    self.assert_errors(contracts.validate_transcript(transcript), "INVALID_INPUT")

    def test_timecodes_are_finite_nonnegative_numbers_not_bool(self):
        for field in ("start", "end"):
            for value in (True, False, "1", None, [], -1, float("nan"), float("inf"), -float("inf")):
                with self.subTest(field=field, value=value):
                    transcript, _ = case_copy()
                    transcript["segments"][0][field] = value
                    self.assert_errors(contracts.validate_transcript(transcript), "INVALID_INPUT")

    def test_reversed_timecode_is_invalid(self):
        transcript, _ = case_copy()
        transcript["segments"][0].update(start=2, end=1)
        self.assert_errors(contracts.validate_transcript(transcript), "INVALID_INPUT")

    def test_segment_start_order_is_required(self):
        transcript, _ = case_copy()
        transcript["segments"].reverse()
        self.assert_errors(contracts.validate_transcript(transcript), "INVALID_INPUT")

    def test_overlapping_equal_and_zero_duration_segments_are_allowed(self):
        transcript, _ = case_copy()
        transcript["segments"][0].update(start=0.5, end=0.5)
        transcript["segments"][1].update(start=0.5, end=8.25)
        before = copy.deepcopy(transcript)
        self.assertEqual(contracts.validate_transcript(transcript), [])
        self.assertEqual(transcript, before)

    def test_duplicate_ids_in_transcript_are_invalid(self):
        for collection in ("participants", "speakers", "segments"):
            with self.subTest(collection=collection):
                transcript, _ = case_copy()
                transcript[collection].append(copy.deepcopy(transcript[collection][0]))
                self.assert_errors(contracts.validate_transcript(transcript), "INVALID_INPUT")

    def test_ids_are_case_sensitive(self):
        transcript, _ = case_copy()
        transcript["participants"].append({"id": "P2", "name": "Данияр"})
        self.assertEqual(contracts.validate_transcript(transcript), [])
        transcript["segments"][0]["speaker_id"] = "SPK1"
        self.assert_errors(contracts.validate_transcript(transcript), "INVALID_INPUT")

    def test_speaker_links_and_confirmed_names(self):
        changes = [
            {"participant_id": None}, {"participant_id": "missing"},
            {"display_name": None}, {"display_name": "Не то имя"},
            {"confirmed": False},
        ]
        for change in changes:
            with self.subTest(change=change):
                transcript, _ = case_copy()
                transcript["speakers"][0].update(change)
                self.assert_errors(contracts.validate_transcript(transcript), "INVALID_INPUT")

    def test_unconfirmed_hint_or_null_is_allowed(self):
        for hint in (None, "Неподтверждённое имя"):
            with self.subTest(hint=hint):
                transcript, _ = case_copy()
                transcript["speakers"][0].update(
                    confirmed=False, participant_id=None, display_name=hint,
                )
                self.assertEqual(contracts.validate_transcript(transcript), [])

    def test_multiple_voices_can_refer_to_one_participant(self):
        transcript, _ = case_copy()
        extra = copy.deepcopy(transcript["speakers"][0])
        extra["speaker_id"] = "spk3"
        transcript["speakers"].append(extra)
        self.assertEqual(contracts.validate_transcript(transcript), [])

    def test_started_at_requires_real_date_time_and_numeric_offset(self):
        invalid = ["", "2026-09-23", "2026-09-23T10:00:00", "2026-09-23T10:00:00Z",
                   "2026-02-30T10:00:00+05:00", "2026-09-23T25:00:00+05:00",
                   "2026-09-23T10:00:00+25:00"]
        for started_at in invalid:
            with self.subTest(started_at=started_at):
                transcript, _ = case_copy()
                transcript["meeting"]["started_at"] = started_at
                self.assert_errors(contracts.validate_transcript(transcript), "INVALID_INPUT")
        # Invalid metadata must not be mistaken for intentionally unknown context.
        transcript, analysis = case_copy("TOMORROW_YEAR")
        transcript["meeting"]["started_at"] = "2026-02-30T10:00:00+05:00"
        errors = contracts.validate_analysis(transcript, analysis)
        self.assert_errors(errors, "INVALID_INPUT")
        self.assertNotIn("INVALID_MODEL_OUTPUT", [error["code"] for error in errors])

    @unittest.skipUnless(HAS_TIMEZONE_DATA, "Real IANA timezone database unavailable")
    def test_nonexistent_zone_is_invalid_input(self):
        transcript, _ = case_copy(unknown_meeting=False)
        transcript["meeting"]["timezone"] = "Invented/Does_Not_Exist"
        self.assert_errors(contracts.validate_transcript(transcript), "INVALID_INPUT")

    @unittest.skipUnless(HAS_TIMEZONE_DATA, "Real IANA timezone database unavailable")
    def test_offset_must_match_zone_at_meeting_time(self):
        transcript, _ = case_copy(unknown_meeting=False)
        transcript["meeting"]["started_at"] = "2026-09-23T10:00:00+06:00"
        self.assert_errors(contracts.validate_transcript(transcript), "INVALID_INPUT")

    @unittest.skipUnless(HAS_TIMEZONE_DATA, "Real IANA timezone database unavailable")
    def test_iana_database_handles_historical_qyzylorda_offset(self):
        transcript, _ = case_copy(unknown_meeting=False)
        transcript["meeting"]["started_at"] = "2017-09-23T10:00:00+06:00"
        self.assertEqual(contracts.validate_transcript(transcript), [])
        transcript["meeting"]["started_at"] = "2017-09-23T10:00:00+05:00"
        self.assert_errors(contracts.validate_transcript(transcript), "INVALID_INPUT")

    @unittest.skipIf(HAS_TIMEZONE_DATA, "Tests actual missing-database environment only")
    def test_missing_timezone_database_is_reported_as_environment_error(self):
        transcript, _ = case_copy(unknown_meeting=False)
        self.assert_errors(contracts.validate_transcript(transcript), "TIMEZONE_DATA_UNAVAILABLE")
        transcript, analysis = case_copy("TOMORROW_YEAR", unknown_meeting=False)
        errors = contracts.validate_analysis(transcript, analysis)
        self.assert_errors(errors, "TIMEZONE_DATA_UNAVAILABLE")
        self.assertEqual({error["code"] for error in errors}, {"TIMEZONE_DATA_UNAVAILABLE"})

    def test_analysis_required_and_unknown_keys(self):
        paths = [(), ("data",), ("data", "summary", 0), ("data", "tasks", 0)]
        for path in paths:
            transcript, analysis = case_copy()
            for key in tuple(at(analysis, path)):
                with self.subTest(path=path, missing=key):
                    changed = copy.deepcopy(analysis)
                    del at(changed, path)[key]
                    self.assert_analysis_invalid(transcript, changed)
            with self.subTest(path=path, extra=True):
                at(analysis, path)["unexpected"] = True
                self.assert_analysis_invalid(transcript, analysis)

    def test_analysis_wrong_types(self):
        mutations = [
            (("status",), []), (("status",), "success"), (("data",), []),
            (("error",), {}), (("data", "source_revision"), True),
            (("data", "source_revision"), 1.0), (("data", "source_revision"), 0),
            (("data", "summary"), None), (("data", "decisions"), {}),
            (("data", "tasks"), ""), (("data", "user_edits"), {}),
            (("data", "summary", 0), None), (("data", "tasks", 0), []),
            (("data", "summary", 0, "id"), []),
            (("data", "summary", 0, "text"), 1),
            (("data", "summary", 0, "source_segment_ids"), "s1"),
            (("data", "tasks", 0, "id"), {}),
            (("data", "tasks", 0, "description"), None),
            (("data", "tasks", 0, "author_speaker_id"), []),
            (("data", "tasks", 0, "assignee_id"), {}),
            (("data", "tasks", 0, "due_text"), 1),
            (("data", "tasks", 0, "due_date"), 20260930),
            (("data", "tasks", 0, "requires_clarification"), 0),
            (("data", "tasks", 0, "clarification_reasons"), None),
        ]
        for path, value in mutations:
            with self.subTest(path=path, value=value):
                transcript, analysis = case_copy()
                replace(analysis, path, value)
                self.assert_analysis_invalid(transcript, analysis)

    def test_analysis_empty_strings(self):
        for path in [("data", "summary", 0, "id"), ("data", "summary", 0, "text"),
                     ("data", "tasks", 0, "id"), ("data", "tasks", 0, "description"),
                     ("data", "tasks", 0, "due_text")]:
            with self.subTest(path=path):
                transcript, analysis = case_copy()
                replace(analysis, path, " \n")
                self.assert_analysis_invalid(transcript, analysis)

    def test_summary_wording_is_not_compared_to_fixture(self):
        transcript, analysis = case_copy()
        analysis["data"]["summary"][0]["text"] = "Ирина попросила Данияра подготовить таблицу расходов к 30 сентября 2026 года."
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])

    def test_nonempty_transcript_requires_summary(self):
        transcript, analysis = case_copy("NO_TASKS")
        analysis["data"]["summary"] = []
        self.assert_analysis_invalid(transcript, analysis)

    def test_analysis_duplicate_ids_are_invalid_within_each_collection(self):
        for case_id, collection in [("RU_EXPLICIT", "summary"), ("RU_EXPLICIT", "tasks"), ("CANCELLED", "decisions")]:
            with self.subTest(collection=collection):
                transcript, analysis = case_copy(case_id)
                analysis["data"][collection].append(copy.deepcopy(analysis["data"][collection][0]))
                self.assert_analysis_invalid(transcript, analysis)

    def test_source_references_are_required_existing_and_case_sensitive(self):
        for case_id, collection in [("RU_EXPLICIT", "summary"), ("RU_EXPLICIT", "tasks"), ("CANCELLED", "decisions")]:
            for references in ([], ["missing"], ["S1"], [None], [[]], [{}]):
                with self.subTest(collection=collection, references=references):
                    transcript, analysis = case_copy(case_id)
                    analysis["data"][collection][0]["source_segment_ids"] = references
                    self.assert_analysis_invalid(transcript, analysis)

    def test_author_must_be_present_in_evidence(self):
        transcript, analysis = case_copy()
        analysis["data"]["tasks"][0]["source_segment_ids"] = ["s2"]
        self.assert_analysis_invalid(transcript, analysis)

    def test_unknown_author_or_assignee_is_invalid(self):
        for field in ("author_speaker_id", "assignee_id"):
            with self.subTest(field=field):
                transcript, analysis = case_copy()
                analysis["data"]["tasks"][0][field] = "missing"
                self.assert_analysis_invalid(transcript, analysis)

    def test_assignee_need_not_have_spoken_or_have_a_speaker(self):
        transcript, analysis = case_copy("NO_DUE")
        transcript["speakers"] = transcript["speakers"][:1]
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])

    def test_due_text_must_be_exact_source_substring(self):
        transcript, analysis = case_copy()
        analysis["data"]["tasks"][0]["due_text"] = "к 30 сентября 2026 года"
        self.assert_analysis_invalid(transcript, analysis)

    def test_due_date_is_real_calendar_date_only(self):
        for due_date in ("2026-02-30", "2026-09-31", "2026-9-30", "20260930", "2026-09-30T12:00:00", "0000-01-01", ""):
            with self.subTest(due_date=due_date):
                transcript, analysis = case_copy()
                analysis["data"]["tasks"][0]["due_date"] = due_date
                self.assert_analysis_invalid(transcript, analysis)

    def test_full_russian_date_must_match_due_date(self):
        transcript, analysis = case_copy()
        analysis["data"]["tasks"][0]["due_date"] = "2026-10-01"
        self.assert_analysis_invalid(transcript, analysis)

    def test_date_without_year_stays_ambiguous(self):
        transcript, analysis = case_copy()
        transcript["segments"][0]["text"] = "Данияр, подготовь таблицу расходов до 30 сентября."
        task = analysis["data"]["tasks"][0]
        task.update(due_text="до 30 сентября", due_date=None, requires_clarification=True,
                    clarification_reasons=[{"code": "ambiguous_due", "message": "Год не назван."}])
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])
        task.update(due_date="2026-09-30", requires_clarification=False, clarification_reasons=[])
        self.assert_analysis_invalid(transcript, analysis)

    def test_kazakh_date_without_year_stays_ambiguous(self):
        # A deterministic calendar pattern, not a claim of language quality.
        transcript, analysis = case_copy("KK_EXPLICIT")
        transcript["segments"][0]["text"] = "Ерлан, есепті 30 қыркүйекке дейін дайында."
        task = analysis["data"]["tasks"][0]
        task["due_text"] = "30 қыркүйекке дейін"
        self.assert_analysis_invalid(transcript, analysis)
        task.update(due_date=None, requires_clarification=True,
                    clarification_reasons=[{"code": "ambiguous_due", "message": "Год не назван."}])
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])

    @unittest.skipUnless(HAS_TIMEZONE_DATA, "Real IANA timezone database unavailable")
    def test_tomorrow_wrong_month_and_year_boundaries_are_rejected(self):
        for case_id, wrong in [("TOMORROW_YEAR", "2026-12-31"), ("TOMORROW_MONTH", "2026-09-30")]:
            with self.subTest(case_id=case_id):
                transcript, analysis = case_copy(case_id, unknown_meeting=False)
                analysis["data"]["tasks"][0]["due_date"] = wrong
                self.assert_analysis_invalid(transcript, analysis)

    @unittest.skipUnless(HAS_TIMEZONE_DATA, "Real IANA timezone database unavailable")
    def test_tomorrow_handles_leap_day(self):
        transcript, analysis = case_copy("TOMORROW_MONTH", unknown_meeting=False)
        transcript["meeting"]["started_at"] = "2028-02-28T23:30:00+05:00"
        analysis["data"]["tasks"][0]["due_date"] = "2028-02-29"
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])

    def test_tomorrow_with_missing_context_cannot_have_inferred_date(self):
        transcript, analysis = case_copy("MISSING_CONTEXT")
        analysis["data"]["tasks"][0].update(
            due_date="2026-09-24", requires_clarification=False, clarification_reasons=[],
        )
        self.assert_analysis_invalid(transcript, analysis)

    def test_clarification_flag_is_exactly_presence_of_reasons(self):
        for case_id, flag in [("RU_EXPLICIT", True), ("NO_DUE", False)]:
            with self.subTest(case_id=case_id):
                transcript, analysis = case_copy(case_id)
                analysis["data"]["tasks"][0]["requires_clarification"] = flag
                self.assert_analysis_invalid(transcript, analysis)

    def test_clarification_reason_shape_and_codes(self):
        invalid = [None, [], {}, {"code": "missing_due"},
                   {"code": "missing_due", "message": ""},
                   {"code": [], "message": "Причина"},
                   {"code": "invented_reason", "message": "Причина"},
                   {"code": "missing_due", "message": "Причина", "extra": True}]
        for reason in invalid:
            with self.subTest(reason=reason):
                transcript, analysis = case_copy("NO_DUE")
                analysis["data"]["tasks"][0]["clarification_reasons"] = [reason]
                self.assert_analysis_invalid(transcript, analysis)

    def test_clarification_reason_duplicate_is_invalid(self):
        transcript, analysis = case_copy("NO_DUE")
        reasons = analysis["data"]["tasks"][0]["clarification_reasons"]
        reasons.append(copy.deepcopy(reasons[0]))
        self.assert_analysis_invalid(transcript, analysis)

    def test_missing_due_requires_missing_due_code(self):
        transcript, analysis = case_copy("NO_DUE")
        analysis["data"]["tasks"][0]["clarification_reasons"][0]["code"] = "ambiguous_due"
        self.assert_analysis_invalid(transcript, analysis)

    def test_known_values_forbid_reasons_in_their_category(self):
        for code in ("missing_due", "ambiguous_due", "missing_meeting_context", "unknown_assignee", "ambiguous_assignee", "unconfirmed_assignee"):
            with self.subTest(code=code):
                transcript, analysis = case_copy()
                analysis["data"]["tasks"][0].update(requires_clarification=True,
                    clarification_reasons=[{"code": code, "message": "Уточнение"}])
                self.assert_analysis_invalid(transcript, analysis)

    def test_unknown_values_need_exactly_one_reason_per_category(self):
        variants = [[], ["unknown_assignee", "ambiguous_assignee"], ["missing_due", "ambiguous_due"]]
        for codes in variants:
            with self.subTest(codes=codes):
                transcript, analysis = case_copy("NO_DUE")
                task = analysis["data"]["tasks"][0]
                task["assignee_id"] = None
                task["clarification_reasons"] = [{"code": code, "message": "Уточнение"} for code in codes]
                task["requires_clarification"] = bool(codes)
                self.assert_analysis_invalid(transcript, analysis)

    def test_two_independent_unknowns_can_have_two_reasons(self):
        transcript, analysis = case_copy("NO_DUE")
        task = analysis["data"]["tasks"][0]
        task["assignee_id"] = None
        task["clarification_reasons"].append({"code": "unknown_assignee", "message": "Исполнитель не определён."})
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])

    def test_error_envelope_can_be_structurally_valid_but_not_exported(self):
        transcript, _ = case_copy()
        analysis = {"status": "error", "data": None,
                    "error": {"code": "MODEL_UNAVAILABLE", "message": "Модель недоступна.", "retryable": True}}
        self.assertEqual(contracts.validate_analysis(transcript, analysis), [])
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])
        self.assert_errors(contracts.validate_export(transcript, analysis, 1), "INVALID_MODEL_OUTPUT")

    def test_error_envelope_required_fields_types_and_exclusivity(self):
        transcript, normal = case_copy()
        errors = [None, {}, [], {"code": "", "message": "Ошибка", "retryable": True},
                  {"code": "INVALID_INPUT", "message": "", "retryable": True},
                  {"code": "INVALID_INPUT", "message": "Ошибка", "retryable": 1},
                  {"code": "INVALID_INPUT", "message": "Ошибка", "retryable": True, "extra": "value"}]
        for error in errors:
            with self.subTest(error=error):
                self.assert_analysis_invalid(transcript, {"status": "error", "data": None, "error": error})
        self.assert_analysis_invalid(transcript, {"status": "error", "data": normal["data"],
            "error": {"code": "INVALID_INPUT", "message": "Ошибка", "retryable": True}})

    def test_error_code_list_is_not_an_undocumented_closed_enum(self):
        transcript, _ = case_copy()
        analysis = {"status": "error", "data": None,
                    "error": {"code": "FUTURE_ADAPTER_ERROR", "message": "Ошибка адаптера.", "retryable": False}}
        self.assertEqual(contracts.validate_analysis(transcript, analysis), [])

    def test_no_speech_cannot_hide_segments_or_content(self):
        transcript, analysis = case_copy()
        analysis["status"] = "no_speech"
        self.assert_analysis_invalid(transcript, analysis)
        transcript, analysis = case_copy("NO_SPEECH")
        analysis["data"]["summary"] = [{"id": "f1", "text": "Вымышленное содержание", "source_segment_ids": ["missing"]}]
        self.assert_analysis_invalid(transcript, analysis)

    def test_valid_user_clarified_date_needs_no_transcript_change(self):
        transcript, analysis = case_copy("USER_CLARIFIED_DUE")
        before = copy.deepcopy(transcript)
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])
        self.assertEqual(contracts.validate_export(transcript, analysis, 2), [])
        self.assertEqual(transcript, before)

    def test_manual_date_without_edit_record_is_invalid(self):
        transcript, analysis = case_copy("USER_CLARIFIED_DUE")
        analysis["data"]["user_edits"] = []
        self.assert_analysis_invalid(transcript, analysis)

    def test_user_edit_required_and_unknown_keys(self):
        transcript, original_analysis = case_copy("USER_CLARIFIED_DUE")
        for key in original_analysis["data"]["user_edits"][0]:
            with self.subTest(missing=key):
                analysis = copy.deepcopy(original_analysis)
                del analysis["data"]["user_edits"][0][key]
                self.assert_analysis_invalid(transcript, analysis)
        original_analysis["data"]["user_edits"][0]["extra"] = True
        self.assert_analysis_invalid(transcript, original_analysis)

    def test_user_edit_invalid_target_reason_and_original(self):
        changes = [("target_type", "participant"), ("target_type", []), ("target_id", "missing"),
                   ("target_id", []), ("reason", " \n"), ("reason", None),
                   ("original", {}), ("original", []), ("original", None)]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                transcript, analysis = case_copy("USER_CLARIFIED_DUE")
                analysis["data"]["user_edits"][0][field] = value
                self.assert_analysis_invalid(transcript, analysis)

    def test_user_edit_changed_fields_are_exact_unique_nonempty_strings(self):
        variants = [[], None, "due_date", [[]], ["due_date"],
                    ["due_date", "requires_clarification", "clarification_reasons", "description"],
                    ["due_date", "requires_clarification", "clarification_reasons", "due_date"]]
        for fields in variants:
            with self.subTest(fields=fields):
                transcript, analysis = case_copy("USER_CLARIFIED_DUE")
                analysis["data"]["user_edits"][0]["changed_fields"] = fields
                self.assert_analysis_invalid(transcript, analysis)

    def test_user_edit_changed_fields_order_does_not_matter(self):
        transcript, analysis = case_copy("USER_CLARIFIED_DUE")
        analysis["data"]["user_edits"][0]["changed_fields"].reverse()
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])

    def test_user_edit_duplicate_target_is_invalid(self):
        transcript, analysis = case_copy("USER_CLARIFIED_DUE")
        edits = analysis["data"]["user_edits"]
        edits.append(copy.deepcopy(edits[0]))
        self.assert_analysis_invalid(transcript, analysis)

    def test_user_edit_original_is_validated_including_links(self):
        changes = [("source_segment_ids", ["missing"]), ("due_date", "2026-02-30"),
                   ("assignee_id", "missing"), ("id", "different"),
                   ("author_speaker_id", "missing"), ("user_edits", [])]
        for field, value in changes:
            with self.subTest(field=field):
                transcript, analysis = case_copy("USER_CLARIFIED_DUE")
                analysis["data"]["user_edits"][0]["original"][field] = value
                self.assert_analysis_invalid(transcript, analysis)

    def test_user_edit_cannot_hide_changed_due_text_or_sources_or_author(self):
        for field, value in [("due_text", "до 30 сентября 2026 года"),
                             ("source_segment_ids", ["s2", "s1"]), ("author_speaker_id", "spk2")]:
            with self.subTest(field=field):
                transcript, analysis = case_copy()
                task = analysis["data"]["tasks"][0]
                original = copy.deepcopy(task)
                if field == "due_text":
                    # Both phrases are real substrings: rejection must concern provenance.
                    original["due_text"] = "30 сентября 2026 года"
                else:
                    task[field] = value
                analysis["data"]["user_edits"] = [{"target_type": "task", "target_id": task["id"],
                    "original": original, "changed_fields": [field], "reason": "Ручная правка"}]
                self.assert_analysis_invalid(transcript, analysis)

    def test_user_edit_cannot_change_task_id(self):
        transcript, analysis = case_copy()
        task = analysis["data"]["tasks"][0]
        original = copy.deepcopy(task)
        task["id"] = "new-task-id"
        analysis["data"]["user_edits"] = [{"target_type": "task", "target_id": task["id"],
            "original": original, "changed_fields": ["id"], "reason": "Ручная правка"}]
        self.assert_analysis_invalid(transcript, analysis)

    def test_manual_date_override_preserves_original_explicit_date(self):
        transcript, analysis = case_copy()
        task = analysis["data"]["tasks"][0]
        original = copy.deepcopy(task)
        task["due_date"] = "2026-10-02"
        analysis["data"]["user_edits"] = [{"target_type": "task", "target_id": task["id"],
            "original": original, "changed_fields": ["due_date"], "reason": "Пользователь перенёс срок."}]
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])
        self.assertEqual(task["due_text"], original["due_text"])

    def test_unrelated_manual_edit_does_not_bypass_original_date_check(self):
        transcript, analysis = case_copy()
        task = analysis["data"]["tasks"][0]
        task["due_date"] = "2026-10-02"
        original = copy.deepcopy(task)
        task["description"] = "Подготовить таблицу расходов в новом формате"
        analysis["data"]["user_edits"] = [{"target_type": "task", "target_id": task["id"],
            "original": original, "changed_fields": ["description"], "reason": "Пользователь уточнил формат."}]
        self.assert_analysis_invalid(transcript, analysis)

    def test_user_edit_cannot_only_rewrite_derived_clarification_message(self):
        transcript, analysis = case_copy("NO_DUE")
        task = analysis["data"]["tasks"][0]
        original = copy.deepcopy(task)
        task["clarification_reasons"][0]["message"] = "Другое объяснение без изменения поручения."
        analysis["data"]["user_edits"] = [{"target_type": "task", "target_id": task["id"],
            "original": original, "changed_fields": ["clarification_reasons"], "reason": "Ручная правка"}]
        self.assert_analysis_invalid(transcript, analysis)

    def test_user_edit_reverted_to_original_must_be_removed(self):
        transcript, analysis = case_copy("USER_CLARIFIED_DUE")
        edit = analysis["data"]["user_edits"][0]
        analysis["data"]["tasks"][0] = copy.deepcopy(edit["original"])
        edit["changed_fields"] = []
        self.assert_analysis_invalid(transcript, analysis)
        analysis["data"]["user_edits"] = []
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])

    def test_repeat_manual_edit_keeps_first_original(self):
        transcript, analysis = case_copy("USER_CLARIFIED_DUE")
        transcript["revision"] = 3
        analysis["data"]["source_revision"] = 3
        analysis["data"]["tasks"][0]["due_date"] = "2026-10-02"
        self.assertEqual(contracts.validate_export(transcript, analysis, 3), [])
        self.assertIsNone(analysis["data"]["user_edits"][0]["original"]["due_date"])

    def test_summary_and_decision_text_can_be_manually_edited(self):
        for target_type, collection in [("summary", "summary"), ("decision", "decisions")]:
            with self.subTest(target_type=target_type):
                transcript, analysis = case_copy("CANCELLED")
                current = analysis["data"][collection][0]
                original = copy.deepcopy(current)
                current["text"] = "Пользователь уточнил формулировку отмены поручения."
                analysis["data"]["user_edits"] = [{"target_type": target_type,
                    "target_id": current["id"], "original": original,
                    "changed_fields": ["text"], "reason": "Уточнение формулировки"}]
                self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])
                current["source_segment_ids"] = ["s1"]
                analysis["data"]["user_edits"][0]["changed_fields"].append("source_segment_ids")
                self.assert_analysis_invalid(transcript, analysis)

    def test_user_can_change_task_description_and_assignee(self):
        transcript, analysis = case_copy()
        task = analysis["data"]["tasks"][0]
        original = copy.deepcopy(task)
        task["description"] = "Подготовить таблицу расходов в согласованном формате"
        task["assignee_id"] = "p1"
        analysis["data"]["user_edits"] = [{"target_type": "task", "target_id": "t1",
            "original": original, "changed_fields": ["description", "assignee_id"],
            "reason": "Пользователь уточнил исполнителя и описание."}]
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])

    def test_stale_analysis_is_blocked_for_snapshot_and_export(self):
        transcript, analysis = case_copy()
        transcript["revision"] = 2
        self.assert_errors(contracts.validate_snapshot(transcript, analysis), "STALE_ANALYSIS")
        self.assert_errors(contracts.validate_export(transcript, analysis, 2), "STALE_ANALYSIS")

    def test_review_is_required_for_current_revision_and_not_bool(self):
        for reviewed_revision in (None, 0, 2, True, False, 1.0, "1", [], {}):
            with self.subTest(reviewed_revision=reviewed_revision):
                transcript, analysis = case_copy()
                self.assert_errors(contracts.validate_export(transcript, analysis, reviewed_revision), "REVIEW_REQUIRED")

    def test_no_due_export_is_allowed_after_review(self):
        transcript, analysis = case_copy("NO_DUE")
        self.assertEqual(contracts.validate_export(transcript, analysis, 1), [])

    def test_unknown_assignee_export_is_allowed_after_review(self):
        transcript, analysis = case_copy("UNKNOWN_ASSIGNEE")
        self.assertEqual(contracts.validate_export(transcript, analysis, 1), [])

    def test_no_tasks_export_is_allowed_after_review(self):
        transcript, analysis = case_copy("NO_TASKS")
        self.assertEqual(contracts.validate_export(transcript, analysis, 1), [])

    def test_no_speech_export_is_blocked_even_after_review(self):
        transcript, analysis = case_copy("NO_SPEECH")
        self.assert_errors(contracts.validate_export(transcript, analysis, 1), "NO_CONTENT")

    def test_review_does_not_override_invalid_references(self):
        transcript, analysis = case_copy()
        analysis["data"]["summary"][0]["source_segment_ids"] = ["missing"]
        self.assert_errors(contracts.validate_export(transcript, analysis, 1), "INVALID_MODEL_OUTPUT")

    def test_valid_and_invalid_inputs_are_not_mutated(self):
        for invalid in (False, True):
            with self.subTest(invalid=invalid):
                transcript, analysis = case_copy("USER_CLARIFIED_DUE")
                if invalid:
                    transcript["segments"][0]["speaker_id"] = "missing"
                    analysis["data"]["user_edits"][0]["changed_fields"].append("invented")
                before = copy.deepcopy((transcript, analysis))
                contracts.validate_transcript(transcript)
                contracts.validate_analysis(transcript, analysis)
                contracts.validate_snapshot(transcript, analysis)
                contracts.validate_export(transcript, analysis, 2)
                self.assertEqual((transcript, analysis), before)

    def test_malformed_json_values_return_errors_without_traceback(self):
        values = [None, False, 1, 1.5, "raw JSON text belongs to adapter", [], {}, [None], {"data": []}]
        for value in values:
            with self.subTest(value=value):
                transcript, analysis = case_copy()
                self.assert_errors(contracts.validate_transcript(value))
                self.assert_errors(contracts.validate_analysis(transcript, value))
                self.assert_errors(contracts.validate_snapshot(value, analysis))
                self.assert_errors(contracts.validate_export(value, value, None))

    def test_error_messages_do_not_echo_input_text_or_sensitive_values(self):
        marker = "SYNTHETIC_SECRET_C:\\private\\invented-recording.wav"
        transcript, analysis = case_copy()
        transcript["participants"][0]["id"] = marker
        transcript["segments"][0]["text"] = marker
        transcript["segments"][0]["speaker_id"] = marker
        transcript["unexpected_" + marker] = marker
        analysis["data"]["tasks"][0]["assignee_id"] = marker
        errors = contracts.validate_snapshot(transcript, analysis)
        self.assert_errors(errors)
        for error in errors:
            self.assertNotIn(marker, error["message"])


def canonical_test(case_id):
    def test(self):
        transcript, analysis = case_copy(case_id, unknown_meeting=False)
        if transcript["meeting"]["timezone"] is not None and not HAS_TIMEZONE_DATA:
            self.skipTest("Real IANA timezone database unavailable; canonical fixture unchanged")
        original = copy.deepcopy((transcript, analysis))
        self.assertEqual(contracts.validate_transcript(transcript), [])
        self.assertEqual(contracts.validate_analysis(transcript, analysis), [])
        self.assertEqual(contracts.validate_snapshot(transcript, analysis), [])
        if case_id == "NO_SPEECH":
            self.assert_errors(contracts.validate_export(transcript, analysis, transcript["revision"]), "NO_CONTENT")
        else:
            self.assertEqual(contracts.validate_export(transcript, analysis, transcript["revision"]), [])
        self.assertEqual((transcript, analysis), original)
    test.__doc__ = "Canonical invented case: " + case_id
    return test


for case_id in CASES:
    setattr(ContractTests, "test_canonical_" + case_id.lower(), canonical_test(case_id))


if __name__ == "__main__":
    unittest.main()
