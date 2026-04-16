import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


def load_locustfile():
    locust_stub = types.ModuleType("locust")

    class HttpUser:
        pass

    def task(weight):
        def decorator(func):
            func.locust_task_weight = weight
            return func

        return decorator

    def between(min_wait, max_wait):
        return (min_wait, max_wait)

    class _RequestEvent:
        def add_listener(self, func):
            return func

    class _Events:
        request = _RequestEvent()

    locust_stub.HttpUser = HttpUser
    locust_stub.task = task
    locust_stub.between = between
    locust_stub.events = _Events()

    original = sys.modules.get("locust")
    sys.modules["locust"] = locust_stub
    try:
        if "locustfile" in sys.modules:
            del sys.modules["locustfile"]
        return importlib.import_module("locustfile")
    finally:
        if original is not None:
            sys.modules["locust"] = original
        else:
            del sys.modules["locust"]


class LocustfileHelpersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.locustfile = load_locustfile()

    def test_extract_openai_error_info(self):
        payload = {
            "error": {
                "message": "Your IP is not allowed",
                "type": "new_api_error",
                "code": "access_denied",
                "param": "ip",
            }
        }

        error_info = self.locustfile.extract_openai_error_info(payload)

        self.assertEqual(error_info["message"], "Your IP is not allowed")
        self.assertEqual(error_info["type"], "new_api_error")
        self.assertEqual(error_info["code"], "access_denied")
        self.assertEqual(error_info["param"], "ip")

    def test_extract_usage_metrics_prefers_usage_fields(self):
        payload = {
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 45,
                "total_tokens": 165,
            }
        }

        usage = self.locustfile.extract_usage_metrics(payload)

        self.assertEqual(
            usage,
            {
                "prompt_tokens": 120,
                "completion_tokens": 45,
                "total_tokens": 165,
                "reasoning_tokens": 0,
            },
        )

    def test_extract_responses_output_text(self):
        payload = {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "第一段"},
                        {"type": "output_text", "text": "第二段"},
                    ],
                }
            ]
        }

        text = self.locustfile.extract_responses_output_text(payload)

        self.assertEqual(text, "第一段\n第二段")

    def test_extract_reasoning_content_detects_nested_reasoning_tokens(self):
        payload = {
            "reasoning": {"effort": "medium"},
            "usage": {
                "output_tokens_details": {
                    "reasoning_tokens": 32,
                }
            },
        }

        reasoning = self.locustfile.extract_reasoning_content(payload)

        self.assertEqual(reasoning, "[REASONING_MODE:medium]")

    def test_get_task_weight_reads_config(self):
        original_traffic_ratio = self.locustfile.CONFIG.get("traffic_ratio")
        self.locustfile.CONFIG["traffic_ratio"] = {
            "chat_stream": 7,
            "chat_non_stream": 2,
            "responses": 2,
            "embeddings": 1,
        }
        try:
            self.assertEqual(self.locustfile.get_task_weight("chat_stream"), 7)
            self.assertEqual(self.locustfile.get_task_weight("chat_non_stream"), 2)
            self.assertEqual(self.locustfile.get_task_weight("responses"), 2)
            self.assertEqual(self.locustfile.get_task_weight("embeddings"), 1)
        finally:
            self.locustfile.CONFIG["traffic_ratio"] = original_traffic_ratio

    def test_record_exception_failure_emits_structured_error(self):
        class DummyResponse:
            status_code = 200

            def __init__(self):
                self.failure_message = None

            def failure(self, message):
                self.failure_message = message

        resp = DummyResponse()
        user = self.locustfile.AIGatewayUser()
        captured = {}
        original_emit_structured_log = self.locustfile.emit_structured_log

        def capture_emit(metric, **kwargs):
            captured["metric"] = metric
            captured.update(kwargs)

        self.locustfile.emit_structured_log = capture_emit
        try:
            user.record_exception_failure(
                resp,
                "chat_stream",
                "/v1/chat/completions",
                "demo-model",
                ValueError("bad chunk"),
                "stream exception: bad chunk",
            )
        finally:
            self.locustfile.emit_structured_log = original_emit_structured_log

        self.assertEqual(captured["metric"], "chat_stream")
        self.assertEqual(captured["event"], "failure")
        self.assertEqual(captured["path"], "/v1/chat/completions")
        self.assertEqual(captured["model"], "demo-model")
        self.assertEqual(captured["http_status"], 200)
        self.assertEqual(captured["error_type"], "unexpected_exception")
        self.assertEqual(captured["error_code"], "ValueError")
        self.assertEqual(captured["error_message"], "bad chunk")
        self.assertEqual(resp.failure_message, "stream exception: bad chunk")

    def test_append_jsonl_record_writes_one_json_line(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "metrics.jsonl"

            self.locustfile.append_jsonl_record(
                target,
                {"metric": "chat_stream", "event": "success", "ttft_ms": 1000},
            )

            lines = target.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["metric"], "chat_stream")
            self.assertEqual(payload["ttft_ms"], 1000)


if __name__ == "__main__":
    unittest.main()
