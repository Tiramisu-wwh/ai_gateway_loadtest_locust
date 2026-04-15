import json
import tempfile
import unittest
from pathlib import Path

import generate_report


class ReportGenerationTest(unittest.TestCase):
    def test_evaluate_summary_returns_pass_fail_judgement(self):
        summary = {
            "success_rate": 99.95,
            "error_rate": 0.05,
            "timeout_rate": 0.0,
            "p95_ttft_ms": 1200,
            "p99_ttft_ms": 1800,
            "avg_tokens_per_sec": 18.5,
        }

        evaluation = generate_report.evaluate_summary(summary)

        self.assertEqual(evaluation["overall_status"], "PASS")
        self.assertEqual(evaluation["metrics"]["success_rate"]["status"], "PASS")
        self.assertEqual(evaluation["metrics"]["p95_ttft_ms"]["status"], "PASS")

    def test_summarize_run_counts_metrics_and_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "20260414-170000"
            run_dir.mkdir(parents=True)

            (run_dir / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "20260414-170000",
                        "base_url": "http://10.22.31.68:8009/v1",
                        "models": {
                            "chat_model": "/models/Qwen3-8B",
                            "responses_model": "/models/Qwen3-8B",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            metrics = [
                {
                    "metric": "chat_stream",
                    "event": "success",
                    "ttft_ms": 1200,
                    "ttlt_ms": 4200,
                    "tokens_per_sec": 18.5,
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
                {
                    "metric": "responses_non_stream",
                    "event": "success",
                    "ttlt_ms": 1800,
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                    "total_tokens": 100,
                },
            ]
            errors = [
                {
                    "metric": "responses_non_stream",
                    "event": "failure",
                    "http_status": 403,
                    "error_type": "new_api_error",
                    "error_code": "access_denied",
                    "error_message": "Your IP is not allowed",
                }
            ]

            (run_dir / "metrics.jsonl").write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in metrics),
                encoding="utf-8",
            )
            (run_dir / "errors.jsonl").write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in errors),
                encoding="utf-8",
            )

            summary = generate_report.summarize_run(run_dir)

            self.assertEqual(summary["total_requests"], 3)
            self.assertEqual(summary["success_count"], 2)
            self.assertEqual(summary["failure_count"], 1)
            self.assertAlmostEqual(summary["success_rate"], 66.67, places=2)
            self.assertEqual(summary["http_status_counts"][403], 1)
            self.assertEqual(summary["error_type_counts"]["new_api_error"], 1)
            self.assertEqual(summary["error_code_counts"]["access_denied"], 1)
            self.assertEqual(summary["token_totals"]["total_tokens"], 250)

    def test_render_report_contains_key_sections(self):
        summary = {
            "run_id": "20260414-170000",
            "base_url": "http://10.22.31.68:8009/v1",
            "models": {
                "chat_model": "/models/Qwen3-8B",
                "responses_model": "/models/Qwen3-8B",
                "embedding_model": "embedding-model",
            },
            "traffic_ratio": {"chat_stream": 7, "responses": 2},
            "total_requests": 3,
            "success_count": 2,
            "failure_count": 1,
            "success_rate": 66.67,
            "error_rate": 33.33,
            "timeout_rate": 0.0,
            "p95_ttft_ms": 1200,
            "p99_ttft_ms": 1200,
            "p95_ttlt_ms": 4200,
            "avg_tokens_per_sec": 18.5,
            "token_totals": {
                "prompt_tokens": 180,
                "completion_tokens": 70,
                "total_tokens": 250,
            },
            "http_status_counts": {403: 1},
            "error_type_counts": {"new_api_error": 1},
            "error_code_counts": {"access_denied": 1},
            "error_samples": [
                {
                    "http_status": 403,
                    "error_type": "new_api_error",
                    "error_code": "access_denied",
                    "error_message": "Your IP is not allowed",
                }
            ],
            "metrics_covered": ["chat_stream", "responses_non_stream"],
        }

        rendered = generate_report.render_report(summary)

        self.assertIn("# AI 网关性能测试报告", rendered)
        self.assertIn("20260414-170000", rendered)
        self.assertIn("66.67%", rendered)
        self.assertIn("new_api_error", rendered)
        self.assertIn("access_denied", rendered)
        self.assertIn("达标判断", rendered)
        self.assertIn("总体结论", rendered)


if __name__ == "__main__":
    unittest.main()
