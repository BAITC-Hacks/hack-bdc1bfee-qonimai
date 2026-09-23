"""Synthetic loopback HTTP tests. No model, external service, or real transcript."""

import copy
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import unittest
from unittest import mock
from urllib import error

import contracts
from analysis import local_model


CASES = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_cases.json").read_text(encoding="utf-8"))["cases"]


def fixture(case_id="RU_EXPLICIT"):
    case = copy.deepcopy(next(c for c in CASES if c["id"] == case_id))
    case["input"]["meeting"] = {"started_at": None, "timezone": None}
    return case["input"], case["expected"]


def reply(result, *, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {
        "role": "assistant", "content": json.dumps(result, ensure_ascii=False)}}]}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.received.append((self.path, json.loads(body)))
        self.send_response(self.server.reply_status)
        for name, value in self.server.reply_headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(self.server.reply_body)))
        self.end_headers()
        self.wfile.write(self.server.reply_body)

    def log_message(self, *_args):
        pass


class LocalModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        self.transcript, self.expected = fixture()
        self.server.received = []
        self.server.reply_status = 200
        self.server.reply_headers = {"Content-Type": "application/json"}
        self.set_reply(reply(self.expected))

    def set_reply(self, value):
        self.server.reply_body = json.dumps(value, ensure_ascii=False).encode("utf-8")

    def analyze(self, **kwargs):
        return local_model.analyze(self.transcript, base_url=self.url, **kwargs)

    def assert_error(self, result, code):
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["data"])
        self.assertEqual(result["error"]["code"], code)
        self.assertIsInstance(result["error"]["retryable"], bool)
        return result

    def test_actual_loopback_request_returns_valid_result_unchanged(self):
        before = copy.deepcopy(self.transcript)
        self.assertEqual(self.analyze(), self.expected)
        self.assertEqual(self.transcript, before)
        self.assertEqual(len(self.server.received), 1)
        path, sent = self.server.received[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(sent["temperature"], 0)
        self.assertFalse(sent["stream"])
        self.assertEqual(sent["max_tokens"], local_model.MAX_OUTPUT_TOKENS)
        self.assertIn("Транскрипт JSON", sent["messages"][-1]["content"])
        self.assertEqual(sent["messages"][0]["role"], "system")
        self.assertEqual(sent["messages"][-1]["role"], "user")

    def test_response_grammar_constrains_revision_edits_and_ids(self):
        self.analyze()
        payload = self.server.received[0][1]
        self.assertEqual(payload["response_format"]["type"], "json_object")
        data = payload["response_format"]["schema"]["properties"]["data"]["properties"]
        self.assertEqual(data["source_revision"]["enum"], [1])
        self.assertEqual(data["user_edits"]["maxItems"], 0)
        task = data["tasks"]["items"]["properties"]
        self.assertEqual(task["source_segment_ids"]["items"]["enum"], ["s1", "s2"])

    def test_grammar_excludes_speakers_without_source_utterances(self):
        self.transcript, self.expected = fixture("NO_DUE")
        self.set_reply(reply(self.expected))
        self.assertEqual(self.analyze(), self.expected)
        data = self.server.received[0][1]["response_format"]["schema"]["properties"]["data"]["properties"]
        self.assertEqual(data["tasks"]["items"]["properties"]["author_speaker_id"]["enum"], ["spk1"])

    def test_examples_are_context_only_and_never_a_fallback(self):
        self.set_reply({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "not JSON"}}]})
        result = self.assert_error(self.analyze(), "INVALID_MODEL_JSON")
        self.assertNotIn("ex-p", json.dumps(result))

    def test_empty_transcript_does_not_contact_a_model(self):
        transcript, expected = fixture("NO_SPEECH")
        self.assertEqual(local_model.analyze(transcript, base_url=self.url), expected)
        self.assertEqual(self.server.received, [])

    def test_invalid_input_precedes_endpoint_and_network(self):
        self.transcript["revision"] = False
        self.assert_error(local_model.analyze(self.transcript, base_url="https://example.invalid"), "INVALID_INPUT")
        self.assertEqual(self.server.received, [])

    def test_no_iana_is_fixed_environment_error(self):
        with mock.patch.object(contracts, "validate_transcript", return_value=[{"code": "TIMEZONE_DATA_UNAVAILABLE"}]):
            self.assert_error(self.analyze(), "TIMEZONE_DATA_UNAVAILABLE")
        self.assertEqual(self.server.received, [])

    def test_external_hosts_and_url_confusion_are_rejected_before_network(self):
        urls = ["https://127.0.0.1", "http://localhost", "http://127.1", "http://2130706433",
                "http://127.0.0.1.example.invalid", "http://user@127.0.0.1", "http://127.0.0.1@evil.invalid",
                "http://127.0.0.2", "http://[::ffff:127.0.0.1]", "http://127.0.0.1:0",
                "http://127.0.0.1:65536", "http://127.0.0.1?url=evil", "http://127.0.0.1/#evil",
                "http://127.0.0.1/v1", "http://127.0.0.1\n", " http://127.0.0.1", None]
        with mock.patch.object(local_model.request, "build_opener") as network:
            for url in urls:
                with self.subTest(url=url):
                    self.assert_error(local_model.analyze(self.transcript, base_url=url), "LOCAL_ENDPOINT_REQUIRED")
            network.assert_not_called()

    def test_ipv6_literal_is_allowed_without_hostname_resolution(self):
        self.assertEqual(local_model._endpoint("http://[::1]:8081/"), "http://[::1]:8081/v1/chat/completions")

    def test_invalid_model_or_timeout_is_rejected(self):
        for timeout in (0, -1, 601, None, True, "10", float("inf"), float("nan"), 10 ** 400):
            with self.subTest(timeout=timeout):
                self.assert_error(self.analyze(timeout=timeout), "INVALID_MODEL_CONFIG")
        for model in (None, "", " ", "x" * 129):
            with self.subTest(model=model):
                self.assert_error(self.analyze(model=model), "INVALID_MODEL_CONFIG")
        self.assertEqual(self.server.received, [])

    def test_environment_proxy_is_not_used(self):
        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "http_proxy": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": "", "no_proxy": ""}):
            self.assertEqual(self.analyze(), self.expected)

    def test_redirect_is_rejected_without_second_request(self):
        self.server.reply_status = 307
        self.server.reply_headers["Location"] = self.url + "/unexpected"
        self.assert_error(self.analyze(), "MODEL_UNAVAILABLE")
        self.assertEqual(len(self.server.received), 1)

    def test_external_redirect_is_rejected(self):
        self.server.reply_status = 302
        self.server.reply_headers["Location"] = "https://example.invalid/never-contact"
        self.assert_error(self.analyze(), "MODEL_UNAVAILABLE")
        self.assertEqual(len(self.server.received), 1)

    def test_http_error_does_not_echo_server_message(self):
        self.server.reply_status = 503
        self.server.reply_body = b"SYNTHETIC_PRIVATE_MARKER"
        result = self.assert_error(self.analyze(), "MODEL_UNAVAILABLE")
        self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", json.dumps(result))

    def test_http_gateway_timeout_is_reported(self):
        self.server.reply_status = 504
        self.assert_error(self.analyze(), "MODEL_TIMEOUT")

    def test_socket_timeout_is_reported_without_details(self):
        with mock.patch.object(local_model.request.OpenerDirector, "open", side_effect=TimeoutError("SYNTHETIC_PRIVATE_MARKER")):
            result = self.assert_error(self.analyze(), "MODEL_TIMEOUT")
        self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", json.dumps(result))

    def test_connection_error_is_reported(self):
        with mock.patch.object(local_model.request.OpenerDirector, "open", side_effect=error.URLError("SYNTHETIC_PRIVATE_MARKER")):
            self.assert_error(self.analyze(), "MODEL_UNAVAILABLE")

    def test_incomplete_http_response_does_not_expose_body(self):
        with mock.patch.object(local_model.request.OpenerDirector, "open", side_effect=IncompleteRead(b"SYNTHETIC_PRIVATE_MARKER", 100)):
            result = self.assert_error(self.analyze(), "MODEL_UNAVAILABLE")
        self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", json.dumps(result))

    def test_input_limit_does_not_truncate_or_send(self):
        self.transcript["segments"][0]["text"] = "я" * local_model.MAX_TRANSCRIPT_CHARS
        self.assert_error(self.analyze(), "MODEL_CONTEXT_LIMIT")
        self.assertEqual(self.server.received, [])

    def test_segment_limit_does_not_truncate_or_send(self):
        original = self.transcript["segments"][0]
        self.transcript["segments"] = [dict(original, id=str(i), start=i, end=i + 1) for i in range(81)]
        self.assert_error(self.analyze(), "MODEL_CONTEXT_LIMIT")
        self.assertEqual(self.server.received, [])

    def test_oversized_response_is_not_parsed(self):
        self.server.reply_body = b" " * (local_model.MAX_RESPONSE_BYTES + 1)
        self.assert_error(self.analyze(), "MODEL_OUTPUT_LIMIT")

    def test_truncated_generation_is_not_accepted_even_if_json_complete(self):
        self.set_reply(reply(self.expected, finish="length"))
        self.assert_error(self.analyze(), "MODEL_OUTPUT_LIMIT")

    def test_server_envelope_is_validated(self):
        values = [{}, [], {"choices": []}, {"choices": [None]}, {"choices": [{}]},
                  {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": None}}]},
                  {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "{}"}}]}]
        for value in values:
            with self.subTest(value=value):
                self.set_reply(value)
                self.assert_error(self.analyze(), "INVALID_MODEL_OUTPUT")

    def test_malformed_server_json_is_rejected(self):
        for body in (b"{", b'{"choices": [], "choices": []}', b"\xff", b'{"x":NaN}'):
            with self.subTest(body=body):
                self.server.reply_body = body
                self.assert_error(self.analyze(), "INVALID_MODEL_OUTPUT")

    def test_model_error_cannot_become_trusted_error(self):
        result = {"status": "error", "data": None, "error": {"code": "PRIVATE", "message": "SYNTHETIC_PRIVATE_MARKER", "retryable": True}}
        self.set_reply(reply(result))
        actual = self.assert_error(self.analyze(), "INVALID_MODEL_OUTPUT")
        self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", json.dumps(actual))

    def test_model_user_edits_are_rejected(self):
        self.transcript, edited = fixture("USER_CLARIFIED_DUE")
        self.set_reply(reply(edited))
        self.assert_error(self.analyze(), "INVALID_MODEL_OUTPUT")

    def test_wrong_revision_is_rejected_not_rewritten(self):
        self.expected["data"]["source_revision"] = 999
        self.set_reply(reply(self.expected))
        self.assert_error(self.analyze(), "INVALID_MODEL_OUTPUT")

    def test_empty_model_text_is_not_no_tasks(self):
        self.set_reply({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": " "}}]})
        self.assert_error(self.analyze(), "EMPTY_MODEL_OUTPUT")

    def test_keyboard_interrupt_is_not_swallowed(self):
        with mock.patch.object(local_model.request.OpenerDirector, "open", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.analyze()


if __name__ == "__main__":
    unittest.main()
