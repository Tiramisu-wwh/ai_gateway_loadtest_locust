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
# 传统模式标准（非思考模式）
STANDARD_TARGETS = {
    "success_rate": {"label": "成功率", "target": ">= 99.9%", "op": "ge", "value": 99.9},
    "error_rate": {"label": "错误率", "target": "<= 0.1%", "op": "le", "value": 0.1},
    "timeout_rate": {"label": "超时率", "target": "<= 0.5%", "op": "le", "value": 0.5},
    "p95_ttft_ms": {"label": "P95 TTFT", "target": "<= 2000 ms", "op": "le", "value": 2000},
    "p99_ttft_ms": {"label": "P99 TTFT", "target": "<= 3000 ms", "op": "le", "value": 3000},
    "avg_tokens_per_sec": {"label": "平均 Token 速率", "target": ">= 15 tokens/s", "op": "ge", "value": 15},
}

# 思考模式标准（更宽松的TTFT要求）
THINKING_MODE_TARGETS = {
    "success_rate": {"label": "成功率", "target": ">= 99.9%", "op": "ge", "value": 99.9},
    "error_rate": {"label": "错误率", "target": "<= 0.1%", "op": "le", "value": 0.1},
    "timeout_rate": {"label": "超时率", "target": "<= 0.5%", "op": "le", "value": 0.5},
    "p95_ttft_ms": {"label": "P95 思考时长", "target": "<= 15000 ms", "op": "le", "value": 15000},
    "p99_ttft_ms": {"label": "P99 思考时长", "target": "<= 30000 ms", "op": "le", "value": 30000},
    "avg_tokens_per_sec": {"label": "平均输出速率", "target": ">= 20 tokens/s", "op": "ge", "value": 20},
}

# 思考模式专用指标标准（用于思考模式分析章节）
THINKING_MODE_DETAILED_TARGETS = {
    "thinking_duration_p95_ms": {"label": "思考时长 P95", "target": "<= 15000 ms", "op": "le", "value": 15000},
    "thinking_duration_p99_ms": {"label": "思考时长 P99", "target": "<= 30000 ms", "op": "le", "value": 30000},
    "thinking_duration_avg_ms": {"label": "平均思考时长", "target": "<= 12000 ms", "op": "le", "value": 12000},
    "output_tps_avg": {"label": "平均输出速率", "target": ">= 20 tokens/s", "op": "ge", "value": 20},
    "thinking_ratio_avg": {"label": "平均思考时间占比", "target": "<= 95%", "op": "le", "value": 95.0},
}

# 思考模式专用指标标准
THINKING_MODE_TARGETS = {
    "thinking_duration_p95_ms": {"label": "思考时长 P95", "target": "<= 30000 ms", "op": "le", "value": 30000},
    "thinking_duration_p99_ms": {"label": "思考时长 P99", "target": "<= 60000 ms", "op": "le", "value": 60000},
    "thinking_duration_avg_ms": {"label": "平均思考时长", "target": "<= 20000 ms", "op": "le", "value": 20000},
    "output_ttft_p95_ms": {"label": "输出TTFT P95", "target": "<= 1000 ms", "op": "le", "value": 1000},
    "output_tps_avg": {"label": "平均输出速率", "target": ">= 20 tokens/s", "op": "ge", "value": 20},
    "thinking_ratio_avg": {"label": "平均思考时间占比", "target": "<= 80%", "op": "le", "value": 80.0},
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

    # 收集TTFT/TTLT数据（支持传统模式和思考模式）
    ttft_values = []
    ttlt_values = []
    tokens_per_sec_values = []

    for item in metrics:
        # 传统模式指标
        if item.get("ttft_ms") is not None:
            ttft_values.append(item["ttft_ms"])
        if item.get("ttlt_ms") is not None:
            ttlt_values.append(item["ttlt_ms"])
        if item.get("tokens_per_sec") is not None:
            tokens_per_sec_values.append(item["tokens_per_sec"])

        # 思考模式指标转换
        metric_name = item.get("metric", "")
        if "thinking" in metric_name:
            # 对于思考模式，TTFT = 思考时长，TTLT = 端到端总时长
            if item.get("thinking_duration_ms") is not None:
                ttft_values.append(item["thinking_duration_ms"])
            if item.get("total_end_to_end_ms") is not None:
                ttlt_values.append(item["total_end_to_end_ms"])
            if item.get("output_tps") is not None:
                tokens_per_sec_values.append(item["output_tps"])

    # 收集思考模式相关数据
    thinking_metrics = [item for item in metrics if item.get("metric", "").startswith("responses_thinking")]
    thinking_by_effort = {}
    for item in thinking_metrics:
        effort = item.get("reasoning_effort", "unknown")
        if effort not in thinking_by_effort:
            thinking_by_effort[effort] = []
        thinking_by_effort[effort].append(item)

    # 收集场景维度数据
    scenario_metrics = [item for item in metrics if item.get("scenario") and item.get("scenario") != "default"]
    scenarios_by_context = {}  # 按上下文长度分组
    scenarios_by_complexity = {}  # 按复杂度分组
    scenario_performance = {}  # 场景性能统计

    for item in scenario_metrics:
        context_length = item.get("context_length", "unknown")
        complexity = item.get("complexity", "unknown")
        scenario = item.get("scenario", "unknown")

        # 按上下文长度分组
        if context_length not in scenarios_by_context:
            scenarios_by_context[context_length] = []
        scenarios_by_context[context_length].append(item)

        # 按复杂度分组
        if complexity not in scenarios_by_complexity:
            scenarios_by_complexity[complexity] = []
        scenarios_by_complexity[complexity].append(item)

        # 场景性能统计
        if scenario not in scenario_performance:
            scenario_performance[scenario] = []
        scenario_performance[scenario].append(item)

    token_totals = {
        "prompt_tokens": sum(int(item.get("prompt_tokens") or 0) for item in metrics),
        "completion_tokens": sum(int(item.get("completion_tokens") or 0) for item in metrics),
        "total_tokens": sum(int(item.get("total_tokens") or 0) for item in metrics),
    }

    http_status_counts = Counter(int(item.get("http_status")) for item in errors if item.get("http_status") is not None)
    error_type_counts = Counter(item.get("error_type") for item in errors if item.get("error_type"))
    error_code_counts = Counter(item.get("error_code") for item in errors if item.get("error_code"))

    # 统计思考模式数据
    thinking_summary = {}
    if thinking_metrics:
        # 全局思考模式统计
        thinking_durations = [item.get("thinking_duration_ms") for item in thinking_metrics if item.get("thinking_duration_ms") is not None]
        output_tps_values = [item.get("output_tps") for item in thinking_metrics if item.get("output_tps") is not None]
        thinking_ratios = [item.get("thinking_ratio") for item in thinking_metrics if item.get("thinking_ratio") is not None]

        thinking_summary = {
            "total_thinking_requests": len(thinking_metrics),
            "thinking_duration_p95_ms": round_or_none(percentile(thinking_durations, 0.95), 2),
            "thinking_duration_p99_ms": round_or_none(percentile(thinking_durations, 0.99), 2),
            "thinking_duration_avg_ms": round_or_none(sum(thinking_durations) / len(thinking_durations), 2) if thinking_durations else None,
            "output_tps_avg": round_or_none(sum(output_tps_values) / len(output_tps_values), 2) if output_tps_values else None,
            "thinking_ratio_avg": round_or_none(sum(thinking_ratios) / len(thinking_ratios) * 100, 2) if thinking_ratios else None,
        }

        # 按推理等级分组统计
        thinking_by_effort_summary = {}
        for effort, effort_metrics in thinking_by_effort.items():
            effort_durations = [item.get("thinking_duration_ms") for item in effort_metrics if item.get("thinking_duration_ms") is not None]
            effort_tps = [item.get("output_tps") for item in effort_metrics if item.get("output_tps") is not None]
            effort_ratios = [item.get("thinking_ratio") for item in effort_metrics if item.get("thinking_ratio") is not None]
            effort_total_time = [item.get("total_end_to_end_ms") for item in effort_metrics if item.get("total_end_to_end_ms") is not None]

            thinking_by_effort_summary[effort] = {
                "count": len(effort_metrics),
                "thinking_duration_avg_ms": round_or_none(sum(effort_durations) / len(effort_durations), 2) if effort_durations else None,
                "output_tps_avg": round_or_none(sum(effort_tps) / len(effort_tps), 2) if effort_tps else None,
                "thinking_ratio_avg_pct": round_or_none(sum(effort_ratios) / len(effort_ratios) * 100, 2) if effort_ratios else None,
                "total_end_to_end_avg_ms": round_or_none(sum(effort_total_time) / len(effort_total_time), 2) if effort_total_time else None,
            }

        thinking_summary["by_effort"] = thinking_by_effort_summary
    else:
        thinking_summary = {
            "total_thinking_requests": 0,
            "thinking_duration_p95_ms": None,
            "thinking_duration_p99_ms": None,
            "thinking_duration_avg_ms": None,
            "output_tps_avg": None,
            "thinking_ratio_avg": None,
            "by_effort": {},
        }

    # 统计场景维度数据
    scenario_summary = {}
    if scenario_metrics:
        scenario_summary["total_scenario_requests"] = len(scenario_metrics)

        # 按上下文长度统计
        context_analysis = {}
        for context_len, items in scenarios_by_context.items():
            thinking_times = [item.get("thinking_duration_ms") for item in items if item.get("thinking_duration_ms") is not None]
            input_tokens = [item.get("actual_input_tokens", item.get("prompt_tokens", 0)) for item in items]
            context_analysis[context_len] = {
                "count": len(items),
                "avg_thinking_duration_ms": round_or_none(sum(thinking_times) / len(thinking_times), 2) if thinking_times else None,
                "avg_input_tokens": round_or_none(sum(input_tokens) / len(input_tokens), 2) if input_tokens else None,
            }

        scenario_summary["by_context_length"] = context_analysis

        # 按复杂度统计
        complexity_analysis = {}
        for complexity, items in scenarios_by_complexity.items():
            thinking_times = [item.get("thinking_duration_ms") for item in items if item.get("thinking_duration_ms") is not None]
            total_times = [item.get("total_end_to_end_ms") for item in items if item.get("total_end_to_end_ms") is not None]
            complexity_analysis[complexity] = {
                "count": len(items),
                "avg_thinking_duration_ms": round_or_none(sum(thinking_times) / len(thinking_times), 2) if thinking_times else None,
                "avg_total_time_ms": round_or_none(sum(total_times) / len(total_times), 2) if total_times else None,
            }

        scenario_summary["by_complexity"] = complexity_analysis

        # 场景性能详情
        scenario_details = {}
        for scenario_name, items in scenario_performance.items():
            thinking_times = [item.get("thinking_duration_ms") for item in items if item.get("thinking_duration_ms") is not None]
            output_tps = [item.get("output_tps") for item in items if item.get("output_tps") is not None]
            total_times = [item.get("total_end_to_end_ms") for item in items if item.get("total_end_to_end_ms") is not None]

            # 从第一个item获取场景元数据
            first_item = items[0] if items else {}
            scenario_details[scenario_name] = {
                "count": len(items),
                "context_length": first_item.get("context_length", "unknown"),
                "complexity": first_item.get("complexity", "unknown"),
                "avg_thinking_duration_ms": round_or_none(sum(thinking_times) / len(thinking_times), 2) if thinking_times else None,
                "avg_output_tps": round_or_none(sum(output_tps) / len(output_tps), 2) if output_tps else None,
                "avg_total_time_ms": round_or_none(sum(total_times) / len(total_times), 2) if total_times else None,
            }

        scenario_summary["scenario_details"] = scenario_details
    else:
        scenario_summary = {
            "total_scenario_requests": 0,
            "by_context_length": {},
            "by_complexity": {},
            "scenario_details": {},
        }

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
        "thinking_summary": thinking_summary,
        "scenario_summary": scenario_summary,
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


def is_thinking_metric(metric_name: str) -> bool:
    return metric_name.startswith("responses_thinking") or metric_name.startswith("scenario_")


def evaluate_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    metrics: Dict[str, Dict[str, Any]] = {}
    statuses: List[str] = []

    # 根据是否包含思考模式数据动态构建标准
    thinking_summary = summary.get("thinking_summary", {})
    has_thinking_mode = thinking_summary.get("total_thinking_requests", 0) > 0
    metrics_covered = summary.get("metrics_covered", [])
    thinking_only_run = bool(metrics_covered) and all(
        is_thinking_metric(metric_name) for metric_name in metrics_covered
    )

    if has_thinking_mode and thinking_only_run:
        # 思考模式标准（更宽松的TTFT要求）
        targets = {
            "success_rate": {"label": "成功率", "target": ">= 99.9%", "op": "ge", "value": 99.9},
            "error_rate": {"label": "错误率", "target": "<= 0.1%", "op": "le", "value": 0.1},
            "timeout_rate": {"label": "超时率", "target": "<= 0.5%", "op": "le", "value": 0.5},
            "p95_ttft_ms": {"label": "P95 思考时长", "target": "<= 15000 ms", "op": "le", "value": 15000},
            "p99_ttft_ms": {"label": "P99 思考时长", "target": "<= 30000 ms", "op": "le", "value": 30000},
            "avg_tokens_per_sec": {"label": "平均输出速率", "target": ">= 20 tokens/s", "op": "ge", "value": 20},
        }
    else:
        # 传统模式标准
        targets = {
            "success_rate": {"label": "成功率", "target": ">= 99.9%", "op": "ge", "value": 99.9},
            "error_rate": {"label": "错误率", "target": "<= 0.1%", "op": "le", "value": 0.1},
            "timeout_rate": {"label": "超时率", "target": "<= 0.5%", "op": "le", "value": 0.5},
            "p95_ttft_ms": {"label": "P95 TTFT", "target": "<= 2000 ms", "op": "le", "value": 2000},
            "p99_ttft_ms": {"label": "P99 TTFT", "target": "<= 3000 ms", "op": "le", "value": 3000},
            "avg_tokens_per_sec": {"label": "平均 Token 速率", "target": ">= 15 tokens/s", "op": "ge", "value": 15},
        }

    for key, spec in targets.items():
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


def generate_thinking_mode_section(thinking_summary: Dict[str, Any]) -> str:
    """
    生成思考模式性能分析章节

    Args:
        thinking_summary: 思考模式统计数据

    Returns:
        Markdown格式的思考模式分析章节
    """
    if not thinking_summary.get("total_thinking_requests", 0):
        return """## 5. 思考模式性能分析

本次测试未包含思考模式场景，或思考模式请求数量为0。

"""

    total_thinking = thinking_summary.get("total_thinking_requests", 0)
    thinking_duration_p95 = thinking_summary.get("thinking_duration_p95_ms") or "-"
    thinking_duration_p99 = thinking_summary.get("thinking_duration_p99_ms") or "-"
    thinking_duration_avg = thinking_summary.get("thinking_duration_avg_ms") or "-"
    output_tps_avg = thinking_summary.get("output_tps_avg") or "-"
    thinking_ratio_avg = thinking_summary.get("thinking_ratio_avg") or "-"

    # 生成不同推理等级对比表
    by_effort = thinking_summary.get("by_effort", {})
    if by_effort:
        comparison_lines = [
            "| 推理等级 | 请求数 | 平均思考时长 | 输出速率 | 思考时间占比 | 平均端到端延迟 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]

        for effort in ["off", "low", "medium", "high"]:
            if effort in by_effort:
                data = by_effort[effort]
                comparison_lines.append(
                    f"| {effort.upper()} | {data['count']} | {data['thinking_duration_avg_ms'] or '-'} ms | "
                    f"{data['output_tps_avg'] or '-'} tokens/s | {data['thinking_ratio_avg_pct'] or '-'}% | "
                    f"{data['total_end_to_end_avg_ms'] or '-'} ms |"
                )
            else:
                comparison_lines.append(f"| {effort.upper()} | - | - | - | - | - |")
    else:
        comparison_lines = ["未收集到不同推理等级的对比数据。"]

    # 生成性能建议
    recommendations = []

    # 检查思考时长
    if thinking_duration_avg != "-":
        if thinking_duration_avg > 30000:  # 超过30秒
            recommendations.append(f"⚠️ 平均思考时长较长 ({thinking_duration_avg}ms)，建议检查上游推理性能或考虑降低推理等级。")
        elif thinking_duration_avg < 5000:  # 少于5秒
            recommendations.append(f"✅ 平均思考时长合理 ({thinking_duration_avg}ms)，思考性能良好。")

    # 检查输出速率
    if output_tps_avg != "-":
        if output_tps_avg < 15:
            recommendations.append(f"⚠️ 输出阶段Token速率偏低 ({output_tps_avg} tokens/s)，建议优化输出阶段性能。")
        else:
            recommendations.append(f"✅ 输出阶段Token速率正常 ({output_tps_avg} tokens/s)。")

    # 检查思考时间占比
    if thinking_ratio_avg != "-":
        if thinking_ratio_avg > 85:
            recommendations.append(f"⚠️ 思考时间占比较高 ({thinking_ratio_avg}%)，输出阶段占比较小，请确认是否符合预期。")
        elif thinking_ratio_avg < 50:
            recommendations.append(f"ℹ️ 思考时间占比较低 ({thinking_ratio_avg}%)，可能思考程度不够或配置有误。")

    if not recommendations:
        recommendations.append("✅ 思考模式各项指标表现良好，未发现明显性能问题。")

    recommendation_lines = [f"- {rec}" for rec in recommendations]

    return f"""## 5. 思考模式性能分析

### 5.1 思考模式概况

- 思考模式请求数：{total_thinking}
- 平均思考时长：{thinking_duration_avg} ms
- P95 思考时长：{thinking_duration_p95} ms
- P99 思考时长：{thinking_duration_p99} ms
- 平均输出速率：{output_tps_avg} tokens/s
- 平均思考时间占比：{thinking_ratio_avg}%

### 5.2 不同推理等级对比

{chr(10).join(comparison_lines)}

### 5.3 思考模式性能建议

{chr(10).join(recommendation_lines)}

"""


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


def generate_scenario_analysis_section(scenario_summary: Dict[str, Any]) -> str:
    """生成场景维度分析章节"""
    if not scenario_summary.get("total_scenario_requests", 0):
        return "## 6. 场景维度分析\n\n本次测试未包含场景化测试数据。\n\n"

    total_scenario = scenario_summary.get("total_scenario_requests", 0)
    by_context = scenario_summary.get("by_context_length", {})
    by_complexity = scenario_summary.get("by_complexity", {})
    scenario_details = scenario_summary.get("scenario_details", {})

    # 生成上下文长度影响分析
    context_lines = ["| 上下文长度 | 请求数 | 平均思考时长 | 平均输入Tokens |", "| --- | ---: | ---: | ---: |"]
    for context_len in ["short", "medium", "long"]:
        if context_len in by_context:
            data = by_context[context_len]
            context_lines.append(
                f"| {context_len.upper()} | {data['count']} | "
                f"{data['avg_thinking_duration_ms'] or '-'} ms | "
                f"{int(data['avg_input_tokens']) if data['avg_input_tokens'] else '-'} tokens |"
            )
        else:
            context_lines.append(f"| {context_len.upper()} | - | - | - |")

    # 生成复杂度影响分析
    complexity_lines = ["| 问题复杂度 | 请求数 | 平均思考时长 | 平均总延迟 |", "| --- | ---: | ---: | ---: |"]
    for complexity in ["simple", "moderate", "complex"]:
        if complexity in by_complexity:
            data = by_complexity[complexity]
            complexity_lines.append(
                f"| {complexity.capitalize()} | {data['count']} | "
                f"{data['avg_thinking_duration_ms'] or '-'} ms | "
                f"{data['avg_total_time_ms'] or '-'} ms |"
            )
        else:
            complexity_lines.append(f"| {complexity.capitalize()} | - | - | - |")

    # 生成场景性能对比表
    scenario_lines = ["| 场景 | 上下文 | 复杂度 | 请求数 | 思考时长 | 输出速率 | 总延迟 |", "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for scenario_name in ["short_simple", "short_moderate", "medium_moderate", "long_complex"]:
        if scenario_name in scenario_details:
            data = scenario_details[scenario_name]
            scenario_lines.append(
                f"| {scenario_name} | {data['context_length']} | {data['complexity']} | "
                f"{data['count']} | {data['avg_thinking_duration_ms'] or '-'} ms | "
                f"{data['avg_output_tps'] or '-'} tokens/s | "
                f"{data['avg_total_time_ms'] or '-'} ms |"
            )
        else:
            scenario_lines.append(f"| {scenario_name} | - | - | - | - | - | - |")

    return f"""## 6. 场景维度分析

### 6.1 场景测试概况

- 场景化请求数：{total_scenario}
- 测试场景数：{len(scenario_details)}

### 6.2 上下文长度影响分析

{chr(10).join(context_lines)}

### 6.3 问题复杂度影响分析

{chr(10).join(complexity_lines)}

### 6.4 场景性能对比

{chr(10).join(scenario_lines)}

### 6.5 分级路由建议

基于场景分析结果，建议采用以下分级策略：

**按上下文长度分级**：
- 短上下文：使用 LOW 或 OFF 等级，优先响应速度
- 中等上下文：使用 MEDIUM 等级，平衡质量和速度
- 长上下文：使用 HIGH 等级，确保质量，可接受较长延迟

**按问题复杂度分级**：
- 简单问题：禁用思考或使用 LOW 等级
- 中等问题：使用 MEDIUM 等级，标准推理能力
- 复杂问题：使用 HIGH 等级，充分利用推理能力

"""


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

    # 生成思考模式分析章节
    thinking_section = generate_thinking_mode_section(summary.get("thinking_summary", {}))

    # 生成场景维度分析章节
    scenario_section = generate_scenario_analysis_section(summary.get("scenario_summary", {}))

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

{thinking_section}
{scenario_section}
## 7. 错误分类分析

### 5.1 HTTP 状态码分布

{format_counter_table(summary['http_status_counts'], ['状态码', '次数', '占比', '说明']).rstrip()}

### 5.2 `error.type` 分布

{format_counter_table(summary['error_type_counts'], ['error.type', '次数', '占比', '说明']).rstrip()}

### 5.3 `error.code` 分布

{format_counter_table(summary['error_code_counts'], ['error.code', '次数', '占比', '说明']).rstrip()}

### 6.4 典型错误样例

{chr(10).join(error_samples_lines)}

## 8. 风险与建议

### 8.1 风险摘要

{chr(10).join(risk_lines)}

### 8.2 建议

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
