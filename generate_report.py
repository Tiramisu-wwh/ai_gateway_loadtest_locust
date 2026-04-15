import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


CONFIG = load_config()
DEFAULT_LOG_ROOT = BASE_DIR / CONFIG.get("log_dir", "logs")
DEFAULT_REPORT_ROOT = BASE_DIR / CONFIG.get("report_dir", "reports")
STANDARD_TARGETS = {
    "success_rate": {"label": "成功率", "target": ">= 99.9%", "op": "ge", "value": 99.9},
    "error_rate": {"label": "错误率", "target": "<= 0.1%", "op": "le", "value": 0.1},
    "timeout_rate": {"label": "超时率", "target": "<= 0.5%", "op": "le", "value": 0.5},
    "p95_ttft_ms": {"label": "P95 TTFT", "target": "<= 2000 ms", "op": "le", "value": 2000},
    "p99_ttft_ms": {"label": "P99 TTFT", "target": "<= 3000 ms", "op": "le", "value": 3000},
    "avg_tokens_per_sec": {"label": "平均 Token 速率", "target": ">= 15 tokens/s", "op": "ge", "value": 15},
}


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def percentile(values: Iterable[float], pct: float) -> Optional[float]:
    values = sorted(float(v) for v in values if v is not None)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    if lower == upper:
        return values[lower]
    fraction = rank - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def round_or_none(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def find_latest_run_dir(log_root: Path) -> Path:
    run_dirs = [path for path in log_root.iterdir() if path.is_dir()]
    if not run_dirs:
        raise FileNotFoundError(f"No run directories found in {log_root}")
    return max(run_dirs, key=lambda path: path.stat().st_mtime)


def summarize_run(run_dir: Path) -> Dict[str, Any]:
    metrics = load_jsonl(run_dir / "metrics.jsonl")
    errors = load_jsonl(run_dir / "errors.jsonl")
    metadata_path = run_dir / "run_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}

    total_requests = len(metrics) + len(errors)
    success_count = len(metrics)
    failure_count = len(errors)
    success_rate = round(success_count * 100 / total_requests, 2) if total_requests else 0.0
    error_rate = round(failure_count * 100 / total_requests, 2) if total_requests else 0.0
    timeout_rate = round(
        sum(1 for item in errors if item.get("error_code") == "channel:response_time_exceeded") * 100 / total_requests,
        2,
    ) if total_requests else 0.0

    ttft_values = [item.get("ttft_ms") for item in metrics if item.get("ttft_ms") is not None]
    ttlt_values = [item.get("ttlt_ms") for item in metrics if item.get("ttlt_ms") is not None]
    tokens_per_sec_values = [item.get("tokens_per_sec") for item in metrics if item.get("tokens_per_sec") is not None]

    token_totals = {
        "prompt_tokens": sum(int(item.get("prompt_tokens") or 0) for item in metrics),
        "completion_tokens": sum(int(item.get("completion_tokens") or 0) for item in metrics),
        "total_tokens": sum(int(item.get("total_tokens") or 0) for item in metrics),
    }

    http_status_counts = Counter(int(item.get("http_status")) for item in errors if item.get("http_status") is not None)
    error_type_counts = Counter(item.get("error_type") for item in errors if item.get("error_type"))
    error_code_counts = Counter(item.get("error_code") for item in errors if item.get("error_code"))

    return {
        "run_id": metadata.get("run_id", run_dir.name),
        "started_at": metadata.get("started_at", ""),
        "base_url": metadata.get("base_url", ""),
        "models": metadata.get("models", {}),
        "traffic_ratio": metadata.get("traffic_ratio", {}),
        "paths": metadata.get("paths", {}),
        "metrics_covered": sorted({item.get("metric") for item in metrics + errors if item.get("metric")}),
        "total_requests": total_requests,
        "success_count": success_count,
        "failure_count": failure_count,
        "success_rate": success_rate,
        "error_rate": error_rate,
        "timeout_rate": timeout_rate,
        "p95_ttft_ms": round_or_none(percentile(ttft_values, 0.95), 2),
        "p99_ttft_ms": round_or_none(percentile(ttft_values, 0.99), 2),
        "p95_ttlt_ms": round_or_none(percentile(ttlt_values, 0.95), 2),
        "p99_ttlt_ms": round_or_none(percentile(ttlt_values, 0.99), 2),
        "avg_tokens_per_sec": round_or_none(sum(tokens_per_sec_values) / len(tokens_per_sec_values), 2) if tokens_per_sec_values else None,
        "token_totals": token_totals,
        "http_status_counts": dict(http_status_counts),
        "error_type_counts": dict(error_type_counts),
        "error_code_counts": dict(error_code_counts),
        "error_samples": errors[:10],
    }


def compare_value(actual: Optional[float], op: str, expected: float) -> str:
    if actual is None:
        return "N/A"
    if op == "ge":
        return "PASS" if actual >= expected else "FAIL"
    if op == "le":
        return "PASS" if actual <= expected else "FAIL"
    raise ValueError(f"Unsupported op: {op}")


def build_risk_items(summary: Dict[str, Any], metrics_evaluation: Dict[str, Dict[str, Any]]) -> List[str]:
    risks = []
    for metric_key, result in metrics_evaluation.items():
        if result["status"] == "FAIL":
            risks.append(f"{result['label']}未达标，实际值为 {result['actual_display']}，目标为 {result['target']}")

    top_error_type = max(summary.get("error_type_counts", {}).items(), key=lambda item: item[1], default=None)
    if top_error_type:
        risks.append(f"主要错误类型为 `{top_error_type[0]}`，需结合错误明细进一步定位。")

    top_error_code = max(summary.get("error_code_counts", {}).items(), key=lambda item: item[1], default=None)
    if top_error_code:
        risks.append(f"主要错误码为 `{top_error_code[0]}`，建议优先核查对应网关或上游处理逻辑。")

    if not risks:
        risks.append("本轮关键指标均已达标，未发现明显高风险项。")

    return risks[:5]


def evaluate_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    metrics: Dict[str, Dict[str, Any]] = {}
    statuses: List[str] = []

    for key, spec in STANDARD_TARGETS.items():
        actual = summary.get(key)
        status = compare_value(actual, spec["op"], spec["value"])
        statuses.append(status)
        actual_display = "-" if actual is None else f"{actual}"
        metrics[key] = {
            "label": spec["label"],
            "target": spec["target"],
            "actual": actual,
            "actual_display": actual_display,
            "status": status,
        }

    overall_status = "PASS"
    if any(status == "FAIL" for status in statuses):
        overall_status = "FAIL"
    elif any(status == "N/A" for status in statuses):
        overall_status = "PARTIAL"

    risks = build_risk_items(summary, metrics)
    if overall_status == "PASS":
        overall_conclusion = "本轮关键指标整体达标，可作为进一步容量评估和正式评审的基础。"
    elif overall_status == "PARTIAL":
        overall_conclusion = "本轮部分关键指标缺少有效数据，建议补齐场景与采样后再做正式结论。"
    else:
        overall_conclusion = "本轮关键指标存在未达标项，当前结果更适合作为问题定位与优化依据。"

    return {
        "overall_status": overall_status,
        "overall_conclusion": overall_conclusion,
        "metrics": metrics,
        "risks": risks,
    }


def format_counter_table(counter_data: Dict[Any, int], headers: List[str]) -> str:
    if not counter_data:
        return f"| {' | '.join(headers)} |\n| {' | '.join(['---'] * len(headers))} |\n| 无 | 0 | 0.00% |  |\n"

    total = sum(counter_data.values())
    lines = [
        f"| {' | '.join(headers)} |",
        f"| {' | '.join(['---'] * len(headers))} |",
    ]
    for key, count in sorted(counter_data.items(), key=lambda item: item[1], reverse=True):
        ratio = round(count * 100 / total, 2) if total else 0.0
        lines.append(f"| {key} | {count} | {ratio}% |  |")
    return "\n".join(lines) + "\n"


def render_report(summary: Dict[str, Any]) -> str:
    evaluation = evaluate_summary(summary)
    token_totals = summary["token_totals"]
    models = summary.get("models", {})
    traffic_ratio = json.dumps(summary.get("traffic_ratio", {}), ensure_ascii=False)
    metrics_covered = ", ".join(summary.get("metrics_covered", [])) or "-"

    error_samples_lines = [
        "| HTTP 状态码 | error.type | error.code | error.message | 备注 |",
        "| --- | --- | --- | --- | --- |",
    ]
    if summary["error_samples"]:
        for item in summary["error_samples"]:
            error_samples_lines.append(
                f"| {item.get('http_status', '')} | {item.get('error_type', '')} | {item.get('error_code', '')} | {item.get('error_message', '')} |  |"
            )
    else:
        error_samples_lines.append("| 无 |  |  |  |  |")

    judgement_lines = [
        "| 指标 | 目标值 | 实际值 | 状态 |",
        "| --- | --- | ---: | --- |",
    ]
    for metric in evaluation["metrics"].values():
        judgement_lines.append(
            f"| {metric['label']} | {metric['target']} | {metric['actual_display']} | {metric['status']} |"
        )

    risk_lines = [f"- {risk}" for risk in evaluation["risks"]]

    return f"""# AI 网关性能测试报告

## 1. 测试概述

- Run ID：{summary.get('run_id', '')}
- 启动时间：{summary.get('started_at', '')}
- 网关地址：{summary.get('base_url', '')}
- 覆盖指标：{metrics_covered}

## 2. 请求配置

| 项目 | 内容 |
| --- | --- |
| `chat_model` | {models.get('chat_model', '')} |
| `responses_model` | {models.get('responses_model', '')} |
| `embedding_model` | {models.get('embedding_model', '')} |
| 流量比例 | `{traffic_ratio}` |

## 3. 关键指标结果

| 指标 | 实际值 |
| --- | ---: |
| 总请求数 | {summary['total_requests']} |
| 成功请求数 | {summary['success_count']} |
| 失败请求数 | {summary['failure_count']} |
| 成功率 | {summary['success_rate']}% |
| 错误率 | {summary['error_rate']}% |
| 超时率 | {summary['timeout_rate']}% |
| P95 TTFT | {summary.get('p95_ttft_ms') or '-'} ms |
| P99 TTFT | {summary.get('p99_ttft_ms') or '-'} ms |
| P95 TTLT | {summary.get('p95_ttlt_ms') or '-'} ms |
| P99 TTLT | {summary.get('p99_ttlt_ms') or '-'} ms |
| 平均 Token 速率 | {summary.get('avg_tokens_per_sec') or '-'} tokens/s |
| Prompt Tokens | {token_totals['prompt_tokens']} |
| Completion Tokens | {token_totals['completion_tokens']} |
| Total Tokens | {token_totals['total_tokens']} |

## 4. 达标判断

- 总体状态：**{evaluation['overall_status']}**
- 总体结论：{evaluation['overall_conclusion']}

{chr(10).join(judgement_lines)}

## 5. 错误分类分析

### 5.1 HTTP 状态码分布

{format_counter_table(summary['http_status_counts'], ['状态码', '次数', '占比', '说明']).rstrip()}

### 5.2 `error.type` 分布

{format_counter_table(summary['error_type_counts'], ['error.type', '次数', '占比', '说明']).rstrip()}

### 5.3 `error.code` 分布

{format_counter_table(summary['error_code_counts'], ['error.code', '次数', '占比', '说明']).rstrip()}

### 5.4 典型错误样例

{chr(10).join(error_samples_lines)}

## 6. 风险与建议

### 6.1 风险摘要

{chr(10).join(risk_lines)}

### 6.2 建议

- 当前结构化日志已经覆盖 `chat/completions`、`responses`、`embeddings` 的核心链路。
- 建议结合本次压测场景补充并发、持续时间、资源监控截图后，用于正式评审。
- 若需形成完整评审报告，可在此基础上补充资源指标、峰值拐点和容错验证结论。
"""


def generate_report(run_dir: Path, output_path: Path) -> Path:
    summary = summarize_run(run_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_report(summary), encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Markdown report from Locust JSONL logs.")
    parser.add_argument("--run-dir", type=str, help="Specific run directory under logs/")
    parser.add_argument("--output", type=str, help="Output Markdown report path")
    args = parser.parse_args()

    log_root = DEFAULT_LOG_ROOT
    if args.run_dir:
        run_dir = Path(args.run_dir)
        if not run_dir.is_absolute():
            run_dir = log_root / run_dir
    else:
        run_dir = find_latest_run_dir(log_root)

    output_path = Path(args.output) if args.output else (DEFAULT_REPORT_ROOT / f"{run_dir.name}_report.md")
    report_path = generate_report(run_dir, output_path)
    print(report_path)


if __name__ == "__main__":
    main()
