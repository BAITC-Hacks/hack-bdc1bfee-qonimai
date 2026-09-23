"""Validate one raw response from a future local model without invoking it.

This boundary rejects model-supplied user edits and error messages. It does not
prove natural-language facts or approve the result for export. No input/output,
logging, repair, retries, or fixture substitution takes place here.
"""

import json
import math

import contracts


_MESSAGES = {
    "INVALID_INPUT": "Входной транскрипт не соответствует контракту.",
    "TIMEZONE_DATA_UNAVAILABLE": "База часовых поясов IANA недоступна; требуется настройка среды.",
    "EMPTY_MODEL_OUTPUT": "Локальная модель вернула пустой ответ.",
    "INVALID_MODEL_JSON": "Ответ модели не является одним допустимым строгим JSON-объектом.",
    "INVALID_MODEL_OUTPUT": "Ответ модели не соответствует требованиям к результату анализа.",
}


class _InvalidJSON(ValueError):
    """An unsupported JSON representation, without including its contents."""


def _error(code):
    return {
        "status": "error",
        "data": None,
        "error": {"code": code, "message": _MESSAGES[code], "retryable": False},
    }


def _object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJSON()
        result[key] = value
    return result


def _reject_constant(_value):
    # The stdlib decoder otherwise accepts NaN and signed Infinity.
    raise _InvalidJSON()


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        # Valid numeric syntax such as 1e999 must not silently become Infinity.
        raise _InvalidJSON()
    return parsed


def parse_model_output(raw_text, transcript) -> dict:
    """Return AnalysisResult or a fixed, non-retryable error envelope.

    Transcript validation precedes any response handling. The only accepted
    successful states are ok for nonempty segments and no_speech for empty
    segments. A successful result is newly decoded, never repaired or reviewed.
    """
    input_errors = contracts.validate_transcript(transcript)
    if input_errors:
        code = "TIMEZONE_DATA_UNAVAILABLE" if any(
            issue["code"] == "TIMEZONE_DATA_UNAVAILABLE" for issue in input_errors
        ) else "INVALID_INPUT"
        return _error(code)

    if type(raw_text) is not str:
        return _error("INVALID_MODEL_OUTPUT")
    if not raw_text.strip():
        return _error("EMPTY_MODEL_OUTPUT")

    try:
        parsed = json.loads(
            raw_text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (ValueError, RecursionError, OverflowError):
        # Includes JSONDecodeError, duplicate/nonfinite values, integer digit
        # limits and decoder depth limits. Never echo the exception or response.
        # Process interrupts and unexpected application exceptions propagate.
        return _error("INVALID_MODEL_JSON")

    if type(parsed) is not dict:
        return _error("INVALID_MODEL_OUTPUT")
    required_status = "ok" if transcript["segments"] else "no_speech"
    if parsed.get("status") != required_status:
        return _error("INVALID_MODEL_OUTPUT")
    data = parsed.get("data")
    if type(data) is not dict or type(data.get("user_edits")) is not list or data["user_edits"]:
        return _error("INVALID_MODEL_OUTPUT")

    snapshot_errors = contracts.validate_snapshot(transcript, parsed)
    if snapshot_errors:
        # Preserve environment failure if the database became unavailable after
        # the initial validation. Other snapshot failures remain model errors.
        code = "TIMEZONE_DATA_UNAVAILABLE" if any(
            issue["code"] == "TIMEZONE_DATA_UNAVAILABLE" for issue in snapshot_errors
        ) else "INVALID_MODEL_OUTPUT"
        return _error(code)
    return parsed
