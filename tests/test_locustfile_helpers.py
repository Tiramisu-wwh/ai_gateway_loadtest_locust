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

    def test_get_task_weight_reads_config(self):
        self.assertEqual(self.locustfile.get_task_weight("chat_stream"), 7)
        self.assertEqual(self.locustfile.get_task_weight("chat_non_stream"), 2)
        self.assertEqual(self.locustfile.get_task_weight("responses"), 2)
        self.assertEqual(self.locustfile.get_task_weight("embeddings"), 1)

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
