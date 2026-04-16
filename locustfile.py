import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from locust import HttpUser, between, events, task

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

RUN_ID = time.strftime("%Y%m%d-%H%M%S")
LOG_ROOT = BASE_DIR / CONFIG.get("log_dir", "logs")
RUN_DIR = LOG_ROOT / RUN_ID
METRICS_LOG_PATH = RUN_DIR / "metrics.jsonl"
ERRORS_LOG_PATH = RUN_DIR / "errors.jsonl"
RUN_METADATA_PATH = RUN_DIR / "run_metadata.json"
_LOG_LOCK = threading.Lock()


def load_json(path: str) -> Dict[str, Any]:
    with open(BASE_DIR / path, "r", encoding="utf-8") as f:
        return json.load(f)


def deep_copy(data: Any) -> Any:
    return json.loads(json.dumps(data))


STREAM_TEMPLATE = load_json("templates/chat_stream.json")
NON_STREAM_TEMPLATE = load_json("templates/chat_non_stream.json")
RESPONSES_TEMPLATE = load_json("templates/responses.json")
EMBEDDING_TEMPLATE = load_json("templates/embeddings.json")


def build_run_metadata() -> Dict[str, Any]:
    return {
        "run_id": RUN_ID,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": CONFIG.get("base_url"),
        "models": {
            "chat_model": CONFIG.get("chat_model"),
            "responses_model": CONFIG.get("responses_model"),
            "embedding_model": CONFIG.get("embedding_model"),
        },
        "traffic_ratio": CONFIG.get("traffic_ratio", {}),
        "paths": CONFIG.get("paths", {}),
    }


def ensure_run_logging_initialized() -> None:
    with _LOG_LOCK:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        if not RUN_METADATA_PATH.exists():
            RUN_METADATA_PATH.write_text(
                json.dumps(build_run_metadata(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


def now_ms() -> int:
    return int(time.time() * 1000)


def safe_float_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def get_task_weight(task_name: str, default: int = 1) -> int:
    traffic_ratio = CONFIG.get("traffic_ratio", {})

    # 如果任务在配置中明确指定了权重（包括0），使用配置的值
    if task_name in traffic_ratio:
        weight = traffic_ratio[task_name]
        # 支持整数和浮点数权重
        if isinstance(weight, (int, float)):
            return 0 if weight == 0 else int(weight)

    # 如果任务不在配置中，使用默认值
    return default


def parse_stream_chunk(raw_line: bytes) -> Optional[str]:
    """
    兼容常见 SSE 返回：
    data: {...}
    data: [DONE]
    """
    try:
        line = raw_line.decode("utf-8", errors="ignore").strip()
        if not line or not line.startswith("data:"):
            return None
        data = line[5:].strip()
        if data == "[DONE]":
            return "[DONE]"
        return data
    except Exception:
        return None


def estimate_token_count_from_text(text: str) -> int:
    """
    粗略估算：
    - 英文按单词
    - 中文按连续中日韩字符做粗估
    仅用于缺少 usage 时的近似统计。
    """
    if not text:
        return 0
    english_words = re.findall(r"[A-Za-z0-9_]+", text)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    return len(english_words) + len(cjk_chars)


def extract_openai_error_info(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    return {
        "message": error.get("message", ""),
        "type": error.get("type", ""),
        "code": error.get("code"),
        "param": error.get("param"),
        "metadata": error.get("metadata"),
    }


def extract_usage_metrics(payload: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """提取usage指标，支持多种API格式"""
    if not isinstance(payload, dict):
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "reasoning_tokens": 0,
        }

    # 尝试从多个位置提取usage
    usage = payload.get("usage", {})
    if not usage and "response" in payload:
        usage = payload["response"].get("usage", {})

    if not isinstance(usage, dict):
        usage = {}

    # 支持不同的字段名称
    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = usage.get("total_tokens")
    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens

    # 提取reasoning_tokens
    reasoning_tokens = 0
    output_details = usage.get("output_tokens_details", {})
    if isinstance(output_details, dict):
        reasoning_tokens = int(output_details.get("reasoning_tokens") or 0)

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": int(total_tokens or 0),
        "reasoning_tokens": reasoning_tokens,
    }


def extract_chat_completion_text(payload: Optional[Dict[str, Any]]) -> str:
    if not isinstance(payload, dict):
        return ""

    choices = payload.get("choices", [])
    if not choices:
        return ""

    choice = choices[0] or {}
    message = choice.get("message", {}) or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("text")
        )
    return ""


def extract_responses_output_text(payload: Optional[Dict[str, Any]]) -> str:
    if not isinstance(payload, dict):
        return ""

    texts = []
    for output_item in payload.get("output", []):
        if not isinstance(output_item, dict):
            continue
        for content_item in output_item.get("content", []):
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text", "")
            if text:
                texts.append(text)
    return "\n".join(texts)


def extract_stream_text(payload: Optional[Dict[str, Any]]) -> str:
    if not isinstance(payload, dict):
        return ""

    choices = payload.get("choices", [])
    if not choices:
        return ""

    choice = choices[0] or {}
    delta = choice.get("delta", {}) or {}
    if isinstance(delta, dict):
        content = delta.get("content", "")
        if isinstance(content, str) and content:
            return content
        if isinstance(content, list):
            parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("text")
            ]
            if parts:
                return "\n".join(parts)

        reasoning_content = delta.get("reasoning_content", "")
        if isinstance(reasoning_content, str) and reasoning_content:
            return reasoning_content

    message = choice.get("message", {}) or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    return ""


def extract_reasoning_content(response_data: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    从响应中提取思考内容，用于区分思考阶段和输出阶段

    支持多种可能的API结构：
    1. OpenAI Responses API: output[].content[].text with reasoning
    2. 直接在顶层: reasoning_content字段
    3. 在choices结构中: choices[0].message.reasoning_content
    4. 新格式: output数组中type为reasoning的对象
    """
    if not isinstance(response_data, dict):
        return None

    # 尝试1: 检查顶层reasoning_content字段
    if "reasoning_content" in response_data:
        reasoning = response_data["reasoning_content"]
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning

    # 尝试2: 检查reasoning字段（新的API格式）
    if "reasoning" in response_data:
        reasoning = response_data["reasoning"]
        if isinstance(reasoning, dict) and reasoning.get("effort"):
            # 检查是否有reasoning_tokens来确认思考模式
            usage_metrics = extract_usage_metrics(response_data)
            if usage_metrics.get("reasoning_tokens", 0) > 0:
                # 返回标记字符串，表示检测到思考模式
                return f"[REASONING_MODE:{reasoning.get('effort', 'unknown')}]"

    # 尝试3: 检查output结构中的reasoning内容
    output = response_data.get("output", [])
    if isinstance(output, list):
        # 新格式：直接在output数组中查找type为reasoning的对象
        for output_item in output:
            if not isinstance(output_item, dict):
                continue
            # 检查是否是reasoning类型的对象
            if output_item.get("type") == "reasoning":
                # 检查summary字段
                summary = output_item.get("summary")
                if isinstance(summary, list) and len(summary) > 0:
                    # 有实际的思考内容
                    return str(summary)
                elif summary is None or (isinstance(summary, list) and len(summary) == 0):
                    # 思考对象存在但内容为空，仍然算思考模式
                    return "[REASONING_MODE:EMPTY]"

        # 兼容旧格式：检查content数组中的reasoning
        for output_item in output:
            if not isinstance(output_item, dict):
                continue
            # 检查content数组中的reasoning
            for content_item in output_item.get("content", []):
                if not isinstance(content_item, dict):
                    continue
                # 检查是否有type标识为reasoning的内容
                if content_item.get("type") == "reasoning":
                    text = content_item.get("text", "")
                    if isinstance(text, str) and text.strip():
                        return text
                # 或者检查reasoning字段
                reasoning = content_item.get("reasoning", {})
                if isinstance(reasoning, dict):
                    text = reasoning.get("text", "")
                    if isinstance(text, str) and text.strip():
                        return text

    # 尝试3: 检查choices结构（兼容chat completions格式）
    choices = response_data.get("choices", [])
    if choices and isinstance(choices[0], dict):
        choice = choices[0]
        message = choice.get("message", {})
        if isinstance(message, dict):
            reasoning = message.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                return reasoning
            # 检查reasoning字段
            reasoning_obj = message.get("reasoning", {})
            if isinstance(reasoning_obj, dict):
                text = reasoning_obj.get("text", "")
                if isinstance(text, str) and text.strip():
                    return text

    # 尝试4: 检查是否有专门的thinking字段
    if "thinking" in response_data:
        thinking = response_data["thinking"]
        if isinstance(thinking, str) and thinking.strip():
            return thinking
        if isinstance(thinking, dict):
            text = thinking.get("text", "")
            if isinstance(text, str) and text.strip():
                return text

    return None


def estimate_thinking_duration(response: Any, start_ts: float, thinking_content: Optional[str]) -> float:
    """
    估算思考阶段时长（从请求开始到思考结束的时间点）

    注意：这是基于响应时间的估算，实际精度取决于API实现
    如果API支持更精确的时间戳信息，应该优先使用API返回的时间

    Args:
        response: HTTP响应对象
        start_ts: 请求开始时间戳
        thinking_content: 提取到的思考内容

    Returns:
        思考阶段的结束时间戳（思考结束、输出开始的时间点）
    """
    # 如果没有思考内容，假设不是思考模式，直接返回开始时间
    if not thinking_content:
        return start_ts

    # 检查是否是思考模式标记字符串
    if isinstance(thinking_content, str) and thinking_content.startswith("[REASONING_MODE:"):
        try:
            # 解析响应数据获取usage信息
            response_data = try_parse_json_response(response)
            if isinstance(response_data, dict):
                usage = response_data.get("usage", {})
                if isinstance(usage, dict):
                    reasoning_tokens = usage.get("reasoning_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    total_tokens = usage.get("total_tokens", 0)

                    # 如果有reasoning_tokens，基于token比例估算思考时间
                    if reasoning_tokens > 0 and completion_tokens > 0:
                        total_elapsed = time.time() - start_ts
                        # 思考时间占比 = 推理token数 / (推理token数 + 输出token数)
                        thinking_ratio = reasoning_tokens / (reasoning_tokens + completion_tokens)
                        thinking_end_ts = start_ts + (total_elapsed * thinking_ratio)
                        return thinking_end_ts

                    # 如果没有详细的token信息，使用默认比例
                    if hasattr(response, 'elapsed'):
                        total_elapsed = response.elapsed.total_seconds()
                    else:
                        total_elapsed = time.time() - start_ts

                    # 对于新API格式，使用更保守的估计（50%）
                    thinking_ratio = 0.5
                    thinking_end_ts = start_ts + (total_elapsed * thinking_ratio)
                    return thinking_end_ts
        except Exception:
            pass

    try:
        # 尝试从响应中获取更精确的时间信息
        if hasattr(response, 'elapsed'):
            total_elapsed = response.elapsed.total_seconds()
        else:
            # 如果无法获取精确时间，使用当前时间计算
            total_elapsed = time.time() - start_ts

        # 基于启发式规则估算思考阶段时长
        # 注意：这个比例需要根据实际API调整，当前使用保守估计
        thinking_ratio = 0.75  # 假设思考阶段占总时间的75%

        thinking_end_ts = start_ts + (total_elapsed * thinking_ratio)
        return thinking_end_ts

    except Exception:
        # 如果计算失败，返回开始时间（保守估计）
        return start_ts


def try_parse_json_response(resp: Any) -> Optional[Dict[str, Any]]:
    try:
        payload = resp.json()
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def split_request_options(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    body = deep_copy(payload)
    params = body.pop("extra_query", None)
    if not isinstance(params, dict):
        params = None
    return body, params


def build_failure_message(status_code: int, error_info: Dict[str, Any], response_text: str) -> str:
    parts = [f"HTTP {status_code}"]
    if error_info.get("type"):
        parts.append(f"type={error_info['type']}")
    if error_info.get("code") not in (None, ""):
        parts.append(f"code={error_info['code']}")
    if error_info.get("message"):
        parts.append(f"message={error_info['message']}")
    elif response_text:
        parts.append(f"body={response_text[:500]}")
    return " | ".join(parts)


def append_jsonl_record(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def emit_structured_log(metric: str, **kwargs: Any) -> None:
    ensure_run_logging_initialized()
    payload = {
        "run_id": RUN_ID,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "metric": metric,
        **kwargs,
    }
    target_path = ERRORS_LOG_PATH if payload.get("event") == "failure" else METRICS_LOG_PATH
    with _LOG_LOCK:
        append_jsonl_record(target_path, payload)
    print(json.dumps(payload, ensure_ascii=False))


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, response, context, exception, start_time, url, **kwargs):
    """
    Locust 原生事件钩子。
    当前保留，后续可扩展为写文件或接入监控系统。
    """
    pass


class AIGatewayUser(HttpUser):
    host = CONFIG.get("base_url") or None
    wait_time = between(CONFIG["wait_time_min"], CONFIG["wait_time_max"])

    def on_start(self):
        self.common_headers = {
            "Content-Type": "application/json",
            "Authorization": f'Bearer {CONFIG["api_key"]}',
        }

    def build_stream_payload(self) -> Dict[str, Any]:
        payload = deep_copy(STREAM_TEMPLATE)
        payload["model"] = CONFIG["chat_model"]
        return payload

    def build_non_stream_payload(self) -> Dict[str, Any]:
        payload = deep_copy(NON_STREAM_TEMPLATE)
        payload["model"] = CONFIG["chat_model"]
        return payload

    def build_responses_payload(self, reasoning_effort: Optional[str] = None, scenario: Optional[str] = None) -> Dict[str, Any]:
        """
        构建Responses API的请求payload

        Args:
            reasoning_effort: 推理等级，可选值: "low", "medium", "high", None(禁用思考)
            scenario: 场景标识，如 "short_simple", "medium_moderate" 等
        """
        # 根据场景选择模板
        if scenario:
            template_path = f"templates/scenario_{scenario}.json"
            if (BASE_DIR / template_path).exists():
                payload = deep_copy(load_json(template_path))
            else:
                payload = deep_copy(RESPONSES_TEMPLATE)
        else:
            payload = deep_copy(RESPONSES_TEMPLATE)

        payload["model"] = CONFIG.get("responses_model") or CONFIG["chat_model"]

        # 动态设置推理等级
        if reasoning_effort is not None:
            # 如果明确指定了推理等级，使用指定的值
            if reasoning_effort in ["low", "medium", "high"]:
                payload["reasoning"] = {"effort": reasoning_effort}
            else:
                # 如果是"off"或其他值，移除reasoning字段
                payload.pop("reasoning", None)
        else:
            # 使用配置文件中的默认值
            default_effort = CONFIG.get("responses_reasoning_effort", "medium")
            if default_effort and default_effort != "off":
                payload["reasoning"] = {"effort": default_effort}
            else:
                payload.pop("reasoning", None)

        # 设置文本冗长度
        verbosity = CONFIG.get("responses_text_verbosity", "low")
        if verbosity:
            payload["text"] = {"verbosity": verbosity}
        else:
            payload.pop("text", None)

        extra_query = CONFIG.get("responses_extra_query")
        if isinstance(extra_query, dict):
            payload["extra_query"] = extra_query
        return payload

    def build_embeddings_payload(self) -> Dict[str, Any]:
        payload = deep_copy(EMBEDDING_TEMPLATE)
        payload["model"] = CONFIG["embedding_model"]
        return payload

    def record_failure(self, resp: Any, metric: str, path: str, model: str) -> None:
        body = try_parse_json_response(resp)
        error_info = extract_openai_error_info(body)
        response_text = getattr(resp, "text", "")
        emit_structured_log(
            metric=metric,
            event="failure",
            path=path,
            model=model,
            http_status=resp.status_code,
            error_type=error_info.get("type"),
            error_code=error_info.get("code"),
            error_param=error_info.get("param"),
            error_message=error_info.get("message"),
        )
        resp.failure(build_failure_message(resp.status_code, error_info, response_text))

    def record_exception_failure(
        self,
        resp: Any,
        metric: str,
        path: str,
        model: str,
        exc: Exception,
        failure_message: str,
    ) -> None:
        error_message = str(exc) or exc.__class__.__name__
        emit_structured_log(
            metric=metric,
            event="failure",
            path=path,
            model=model,
            http_status=getattr(resp, "status_code", None),
            error_type="unexpected_exception",
            error_code=exc.__class__.__name__,
            error_message=error_message,
        )
        resp.failure(failure_message)

    @task(get_task_weight("chat_stream", default=0))
    def chat_stream(self):
        payload, params = split_request_options(self.build_stream_payload())
        path = CONFIG["paths"]["chat_completions"]

        start_ts = time.time()
        first_token_ts: Optional[float] = None
        stream_chunk_count = 0
        token_count_estimated = 0
        usage_metrics = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        finish_reason = None

        with self.client.post(
            path,
            params=params,
            data=json.dumps(payload, ensure_ascii=False),
            headers=self.common_headers,
            stream=True,
            catch_response=True,
            name="chat_stream",
            timeout=CONFIG["request_timeout_seconds"],
        ) as resp:
            try:
                if resp.status_code != 200:
                    self.record_failure(resp, "chat_stream", path, payload["model"])
                    return

                for raw_line in resp.iter_lines():
                    data = parse_stream_chunk(raw_line)
                    if not data:
                        continue
                    if data == "[DONE]":
                        break

                    stream_chunk_count += 1
                    parsed = json.loads(data)

                    chunk_usage = extract_usage_metrics(parsed)
                    if chunk_usage["total_tokens"] > 0:
                        usage_metrics = chunk_usage

                    chunk_text = extract_stream_text(parsed)
                    if chunk_text and first_token_ts is None:
                        first_token_ts = time.time()
                    if chunk_text:
                        token_count_estimated += estimate_token_count_from_text(chunk_text)

                    choices = parsed.get("choices", [])
                    if choices:
                        finish_reason = choices[0].get("finish_reason") or finish_reason

                end_ts = time.time()
                ttft_ms = int((first_token_ts - start_ts) * 1000) if first_token_ts else None
                ttlt_ms = int((end_ts - start_ts) * 1000)
                gen_seconds = (end_ts - first_token_ts) if first_token_ts else 0

                completion_tokens = usage_metrics["completion_tokens"] or token_count_estimated
                total_tokens = usage_metrics["total_tokens"] or (
                    usage_metrics["prompt_tokens"] + completion_tokens
                )
                tokens_per_sec = round(safe_float_div(completion_tokens, gen_seconds), 2)

                resp.success()
                emit_structured_log(
                    metric="chat_stream",
                    event="success",
                    path=path,
                    model=payload["model"],
                    ttft_ms=ttft_ms,
                    ttlt_ms=ttlt_ms,
                    stream_chunk_count=stream_chunk_count,
                    prompt_tokens=usage_metrics["prompt_tokens"],
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    tokens_per_sec=tokens_per_sec,
                    finish_reason=finish_reason,
                )
            except Exception as e:
                self.record_exception_failure(
                    resp,
                    "chat_stream",
                    path,
                    payload["model"],
                    e,
                    f"stream exception: {e}",
                )

    @task(get_task_weight("chat_non_stream", default=0))
    def chat_non_stream(self):
        payload, params = split_request_options(self.build_non_stream_payload())
        path = CONFIG["paths"]["chat_completions"]
        start_ms = now_ms()

        with self.client.post(
            path,
            params=params,
            data=json.dumps(payload, ensure_ascii=False),
            headers=self.common_headers,
            catch_response=True,
            name="chat_non_stream",
            timeout=CONFIG["request_timeout_seconds"],
        ) as resp:
            try:
                if resp.status_code != 200:
                    self.record_failure(resp, "chat_non_stream", path, payload["model"])
                    return

                body = try_parse_json_response(resp) or {}
                ttl_ms = now_ms() - start_ms
                usage_metrics = extract_usage_metrics(body)
                content = extract_chat_completion_text(body)
                completion_tokens = usage_metrics["completion_tokens"] or estimate_token_count_from_text(content)
                total_tokens = usage_metrics["total_tokens"] or (
                    usage_metrics["prompt_tokens"] + completion_tokens
                )

                choices = body.get("choices", [])
                finish_reason = choices[0].get("finish_reason") if choices else None

                resp.success()
                emit_structured_log(
                    metric="chat_non_stream",
                    event="success",
                    path=path,
                    model=payload["model"],
                    ttlt_ms=ttl_ms,
                    prompt_tokens=usage_metrics["prompt_tokens"],
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    finish_reason=finish_reason,
                )
            except Exception as e:
                self.record_exception_failure(
                    resp,
                    "chat_non_stream",
                    path,
                    payload["model"],
                    e,
                    f"non-stream exception: {e}",
                )

    @task(get_task_weight("responses", default=0))
    def responses_non_stream(self):
        payload, params = split_request_options(self.build_responses_payload())
        path = CONFIG["paths"]["responses"]
        start_ts = time.time()
        start_ms = now_ms()

        with self.client.post(
            path,
            params=params,
            data=json.dumps(payload, ensure_ascii=False),
            headers=self.common_headers,
            catch_response=True,
            name="responses_non_stream",
            timeout=CONFIG["request_timeout_seconds"],
        ) as resp:
            try:
                if resp.status_code != 200:
                    self.record_failure(resp, "responses_non_stream", path, payload["model"])
                    return

                body = try_parse_json_response(resp) or {}
                end_ts = time.time()
                ttl_ms = now_ms() - start_ms
                usage_metrics = extract_usage_metrics(body)
                output_text = extract_responses_output_text(body)

                # 提取思考内容，判断是否为思考模式
                thinking_content = extract_reasoning_content(body)
                reasoning_effort = payload.get("reasoning", {}).get("effort", "unknown")

                completion_tokens = usage_metrics["completion_tokens"] or estimate_token_count_from_text(output_text)
                total_tokens = usage_metrics["total_tokens"] or (
                    usage_metrics["prompt_tokens"] + completion_tokens
                )

                resp.success()

                # 如果检测到思考内容，使用思考模式指标
                if thinking_content is not None:
                    # 估算思考阶段时长
                    thinking_end_ts = estimate_thinking_duration(resp, start_ts, thinking_content)
                    thinking_duration_ms = int((thinking_end_ts - start_ts) * 1000)

                    # 计算输出阶段指标
                    output_duration_ms = int((end_ts - thinking_end_ts) * 1000)
                    total_end_to_end_ms = int((end_ts - start_ts) * 1000)

                    # 计算输出阶段的token速率
                    output_seconds = max((end_ts - thinking_end_ts), 0.001)  # 避免除以0
                    output_tokens_per_sec = round(safe_float_div(completion_tokens, output_seconds), 2)

                    # 计算思考时间占比
                    thinking_ratio = round(safe_float_div(thinking_duration_ms, total_end_to_end_ms), 2)

                    # 记录思考模式专用指标
                    emit_structured_log(
                        metric="responses_thinking",
                        event="success",
                        path=path,
                        model=payload["model"],
                        reasoning_effort=reasoning_effort,
                        thinking_duration_ms=thinking_duration_ms,
                        output_ttft_ms=0,  # 非流式模式，输出TTFT为0
                        output_tps=output_tokens_per_sec,
                        output_ttlt_ms=output_duration_ms,
                        total_end_to_end_ms=total_end_to_end_ms,
                        thinking_ratio=thinking_ratio,
                        prompt_tokens=usage_metrics["prompt_tokens"],
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        status=body.get("status"),
                    )
                else:
                    # 非思考模式，使用原有指标
                    emit_structured_log(
                        metric="responses_non_stream",
                        event="success",
                        path=path,
                        model=payload["model"],
                        ttlt_ms=ttl_ms,
                        prompt_tokens=usage_metrics["prompt_tokens"],
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        status=body.get("status"),
                    )
            except Exception as e:
                self.record_exception_failure(
                    resp,
                    "responses_non_stream",
                    path,
                    payload["model"],
                    e,
                    f"responses exception: {e}",
                )

    def _execute_responses_task(self, reasoning_effort: Optional[str] = None, scenario: Optional[str] = None, task_name: str = "responses_custom") -> None:
        """
        执行Responses任务的通用函数

        Args:
            reasoning_effort: 推理等级，可选值: "low", "medium", "high", None(禁用思考)
            scenario: 场景标识，如 "short_simple", "medium_moderate" 等
            task_name: 任务名称，用于日志记录
        """
        payload, params = split_request_options(self.build_responses_payload(reasoning_effort=reasoning_effort, scenario=scenario))

        # 提取场景元数据
        scenario_metadata = payload.pop("scenario_metadata", {}) or {}
        context_length = scenario_metadata.get("context_length", "unknown")
        complexity = scenario_metadata.get("complexity", "unknown")
        estimated_input_tokens = scenario_metadata.get("estimated_input_tokens", 0)
        path = CONFIG["paths"]["responses"]
        start_ts = time.time()
        start_ms = now_ms()

        # 确定实际使用的推理等级
        actual_reasoning_effort = payload.get("reasoning", {}).get("effort", "off")

        with self.client.post(
            path,
            params=params,
            data=json.dumps(payload, ensure_ascii=False),
            headers=self.common_headers,
            catch_response=True,
            name=task_name,
            timeout=CONFIG["request_timeout_seconds"],
        ) as resp:
            try:
                if resp.status_code != 200:
                    self.record_failure(resp, task_name, path, payload["model"])
                    return

                body = try_parse_json_response(resp) or {}
                end_ts = time.time()
                ttl_ms = now_ms() - start_ms
                usage_metrics = extract_usage_metrics(body)
                output_text = extract_responses_output_text(body)

                # 提取思考内容，判断是否为思考模式
                thinking_content = extract_reasoning_content(body)

                completion_tokens = usage_metrics["completion_tokens"] or estimate_token_count_from_text(output_text)
                total_tokens = usage_metrics["total_tokens"] or (
                    usage_metrics["prompt_tokens"] + completion_tokens
                )

                resp.success()

                # 如果检测到思考内容或明确指定了推理等级，使用思考模式指标
                if thinking_content is not None or actual_reasoning_effort != "off":
                    # 估算思考阶段时长
                    thinking_end_ts = estimate_thinking_duration(resp, start_ts, thinking_content)
                    thinking_duration_ms = int((thinking_end_ts - start_ts) * 1000)

                    # 计算输出阶段指标
                    output_duration_ms = int((end_ts - thinking_end_ts) * 1000)
                    total_end_to_end_ms = int((end_ts - start_ts) * 1000)

                    # 计算输出阶段的token速率
                    output_seconds = max((end_ts - thinking_end_ts), 0.001)  # 避免除以0
                    output_tokens_per_sec = round(safe_float_div(completion_tokens, output_seconds), 2)

                    # 计算思考时间占比
                    thinking_ratio = round(safe_float_div(thinking_duration_ms, total_end_to_end_ms), 2)

                    # 记录思考模式专用指标
                    emit_structured_log(
                        metric=f"responses_thinking_{actual_reasoning_effort}",
                        event="success",
                        path=path,
                        model=payload["model"],
                        reasoning_effort=actual_reasoning_effort,
                        # 场景元数据
                        context_length=context_length,
                        complexity=complexity,
                        estimated_input_tokens=estimated_input_tokens,
                        actual_input_tokens=usage_metrics["prompt_tokens"],
                        scenario=scenario or "default",
                        # 性能指标
                        thinking_duration_ms=thinking_duration_ms,
                        output_ttft_ms=0,  # 非流式模式，输出TTFT为0
                        output_tps=output_tokens_per_sec,
                        output_ttlt_ms=output_duration_ms,
                        total_end_to_end_ms=total_end_to_end_ms,
                        thinking_ratio=thinking_ratio,
                        # Token统计
                        prompt_tokens=usage_metrics["prompt_tokens"],
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        status=body.get("status"),
                    )
                else:
                    # 非思考模式，使用原有指标
                    emit_structured_log(
                        metric="responses_non_stream",
                        event="success",
                        path=path,
                        model=payload["model"],
                        ttlt_ms=ttl_ms,
                        prompt_tokens=usage_metrics["prompt_tokens"],
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        status=body.get("status"),
                    )
            except Exception as e:
                self.record_exception_failure(
                    resp,
                    task_name,
                    path,
                    payload["model"],
                    e,
                    f"{task_name} exception: {e}",
                )

    @task(get_task_weight("responses_thinking_off", default=0))
    def responses_thinking_off(self):
        """禁用思考模式的Responses API测试"""
        self._execute_responses_task(reasoning_effort="off", task_name="responses_thinking_off")

    @task(get_task_weight("responses_thinking_low", default=0))
    def responses_thinking_low(self):
        """低强度思考模式的Responses API测试"""
        self._execute_responses_task(reasoning_effort="low", task_name="responses_thinking_low")

    @task(get_task_weight("responses_thinking_medium", default=0))
    def responses_thinking_medium(self):
        """中强度思考模式的Responses API测试"""
        self._execute_responses_task(reasoning_effort="medium", task_name="responses_thinking_medium")

    @task(get_task_weight("responses_thinking_high", default=0))
    def responses_thinking_high(self):
        """高强度思考模式的Responses API测试"""
        self._execute_responses_task(reasoning_effort="high", task_name="responses_thinking_high")

    # ========== 场景化测试任务 ==========

    @task(get_task_weight("scenario_short_simple", default=0))
    def scenario_short_simple(self):
        """短上下文+简单问题场景测试"""
        self._execute_responses_task(
            reasoning_effort="low",
            scenario="short_simple",
            task_name="scenario_short_simple"
        )

    @task(get_task_weight("scenario_short_moderate", default=0))
    def scenario_short_moderate(self):
        """短上下文+中等问题场景测试"""
        self._execute_responses_task(
            reasoning_effort="medium",
            scenario="short_moderate",
            task_name="scenario_short_moderate"
        )

    @task(get_task_weight("scenario_medium_moderate", default=0))
    def scenario_medium_moderate(self):
        """中等上下文+中等问题场景测试"""
        self._execute_responses_task(
            reasoning_effort="medium",
            scenario="medium_moderate",
            task_name="scenario_medium_moderate"
        )

    @task(get_task_weight("scenario_long_complex", default=0))
    def scenario_long_complex(self):
        """长上下文+复杂问题场景测试"""
        self._execute_responses_task(
            reasoning_effort="high",
            scenario="long_complex",
            task_name="scenario_long_complex"
        )

    # ========== 流式Responses任务 ==========

    def _execute_responses_stream_task(self, reasoning_effort: Optional[str] = None, scenario: Optional[str] = None, task_name: str = "responses_stream_custom") -> None:
        """
        执行流式Responses任务的通用函数

        Args:
            reasoning_effort: 推理等级，可选值: "low", "medium", "high", None(禁用思考)
            scenario: 场景标识，如 "short_simple", "medium_moderate" 等
            task_name: 任务名称，用于日志记录
        """
        payload, params = split_request_options(self.build_responses_payload(reasoning_effort=reasoning_effort, scenario=scenario))
        # 设置流式输出
        payload["stream"] = True

        # 提取场景元数据
        scenario_metadata = payload.pop("scenario_metadata", {}) or {}
        context_length = scenario_metadata.get("context_length", "unknown")
        complexity = scenario_metadata.get("complexity", "unknown")
        estimated_input_tokens = scenario_metadata.get("estimated_input_tokens", 0)
        path = CONFIG["paths"]["responses"]
        start_ts = time.time()
        first_token_ts: Optional[float] = None
        stream_chunk_count = 0
        token_count_estimated = 0
        usage_metrics = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        finish_reason = None

        # 确定实际使用的推理等级
        actual_reasoning_effort = payload.get("reasoning", {}).get("effort", "off")

        with self.client.post(
            path,
            params=params,
            data=json.dumps(payload, ensure_ascii=False),
            headers=self.common_headers,
            stream=True,
            catch_response=True,
            name=task_name,
            timeout=CONFIG["request_timeout_seconds"],
        ) as resp:
            try:
                if resp.status_code != 200:
                    self.record_failure(resp, task_name, path, payload["model"])
                    return

                for raw_line in resp.iter_lines():
                    data = parse_stream_chunk(raw_line)
                    if not data:
                        continue
                    if data == "[DONE]":
                        break

                    stream_chunk_count += 1
                    parsed = json.loads(data)

                    chunk_usage = extract_usage_metrics(parsed)
                    if chunk_usage["total_tokens"] > 0:
                        usage_metrics = chunk_usage

                    # 提取文本内容（支持多种格式）
                    chunk_text = extract_stream_text(parsed)

                    # Responses API流式格式检查
                    if not chunk_text and parsed.get("type") in ["response.content_part.done", "response.output_item.done"]:
                        part = parsed.get("part", {})
                        if isinstance(part, dict) and part.get("type") == "output_text":
                            chunk_text = part.get("text", "")

                    # 记录第一个真实输出token的时间（思考结束时间）
                    if chunk_text and first_token_ts is None:
                        first_token_ts = time.time()

                    if chunk_text:
                        token_count_estimated += estimate_token_count_from_text(chunk_text)

                    choices = parsed.get("choices", [])
                    if choices:
                        finish_reason = choices[0].get("finish_reason") or finish_reason

                end_ts = time.time()

                # 如果检测到推理等级，使用思考模式指标
                if actual_reasoning_effort != "off":
                    # 估算思考阶段时长（思考阶段无法流式，所以first_token_ts就是思考结束时间）
                    thinking_end_ts = first_token_ts or end_ts
                    thinking_duration_ms = int((thinking_end_ts - start_ts) * 1000)

                    # 计算输出阶段指标
                    output_duration_ms = int((end_ts - thinking_end_ts) * 1000)
                    total_end_to_end_ms = int((end_ts - start_ts) * 1000)

                    # 输出TTFT：从思考结束到第一个输出token
                    output_ttft_ms = int((first_token_ts - thinking_end_ts) * 1000) if first_token_ts else 0

                    # 计算输出阶段的token速率
                    output_seconds = max((end_ts - thinking_end_ts), 0.001)  # 避免除以0
                    output_tokens_per_sec = round(safe_float_div(token_count_estimated, output_seconds), 2)

                    # 计算思考时间占比
                    thinking_ratio = round(safe_float_div(thinking_duration_ms, total_end_to_end_ms), 2)

                    completion_tokens = usage_metrics["completion_tokens"] or token_count_estimated
                    total_tokens = usage_metrics["total_tokens"] or (
                        usage_metrics["prompt_tokens"] + completion_tokens
                    )

                    resp.success()
                    emit_structured_log(
                        metric=f"responses_thinking_{actual_reasoning_effort}_stream",
                        event="success",
                        path=path,
                        model=payload["model"],
                        reasoning_effort=actual_reasoning_effort,
                        # 场景元数据
                        context_length=context_length,
                        complexity=complexity,
                        estimated_input_tokens=estimated_input_tokens,
                        actual_input_tokens=usage_metrics["prompt_tokens"],
                        scenario=scenario or "default",
                        # 思考阶段指标
                        thinking_duration_ms=thinking_duration_ms,
                        # 输出阶段指标
                        output_ttft_ms=output_ttft_ms,
                        output_tps=output_tokens_per_sec,
                        output_ttlt_ms=output_duration_ms,
                        total_end_to_end_ms=total_end_to_end_ms,
                        thinking_ratio=thinking_ratio,
                        # Token统计
                        prompt_tokens=usage_metrics["prompt_tokens"],
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        # 流式特有
                        stream_chunk_count=stream_chunk_count,
                        finish_reason=finish_reason,
                    )
                else:
                    # 非思考模式流式
                    ttft_ms = int((first_token_ts - start_ts) * 1000) if first_token_ts else None
                    ttlt_ms = int((end_ts - start_ts) * 1000)
                    gen_seconds = (end_ts - first_token_ts) if first_token_ts else 0

                    completion_tokens = usage_metrics["completion_tokens"] or token_count_estimated
                    total_tokens = usage_metrics["total_tokens"] or (
                        usage_metrics["prompt_tokens"] + completion_tokens
                    )
                    tokens_per_sec = round(safe_float_div(completion_tokens, gen_seconds), 2)

                    resp.success()
                    emit_structured_log(
                        metric="responses_stream",
                        event="success",
                        path=path,
                        model=payload["model"],
                        ttft_ms=ttft_ms,
                        ttlt_ms=ttlt_ms,
                        stream_chunk_count=stream_chunk_count,
                        prompt_tokens=usage_metrics["prompt_tokens"],
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        tokens_per_sec=tokens_per_sec,
                        finish_reason=finish_reason,
                    )
            except Exception as e:
                self.record_exception_failure(
                    resp,
                    task_name,
                    path,
                    payload["model"],
                    e,
                    f"{task_name} stream exception: {e}",
                )

    @task(get_task_weight("responses_thinking_off_stream", default=0))
    def responses_thinking_off_stream(self):
        """禁用思考模式的流式Responses API测试"""
        self._execute_responses_stream_task(reasoning_effort="off", task_name="responses_thinking_off_stream")

    @task(get_task_weight("responses_thinking_low_stream", default=0))
    def responses_thinking_low_stream(self):
        """低强度思考模式的流式Responses API测试"""
        self._execute_responses_stream_task(reasoning_effort="low", task_name="responses_thinking_low_stream")

    @task(get_task_weight("responses_thinking_medium_stream", default=0))
    def responses_thinking_medium_stream(self):
        """中强度思考模式的流式Responses API测试"""
        self._execute_responses_stream_task(reasoning_effort="medium", task_name="responses_thinking_medium_stream")

    @task(get_task_weight("responses_thinking_high_stream", default=0))
    def responses_thinking_high_stream(self):
        """高强度思考模式的流式Responses API测试"""
        self._execute_responses_stream_task(reasoning_effort="high", task_name="responses_thinking_high_stream")

    # ========== 流式场景化测试任务 ==========

    @task(get_task_weight("scenario_short_simple_stream", default=0))
    def scenario_short_simple_stream(self):
        """短上下文+简单问题场景流式测试"""
        self._execute_responses_stream_task(
            reasoning_effort="low",
            scenario="short_simple",
            task_name="scenario_short_simple_stream"
        )

    @task(get_task_weight("scenario_short_moderate_stream", default=0))
    def scenario_short_moderate_stream(self):
        """短上下文+中等问题场景流式测试"""
        self._execute_responses_stream_task(
            reasoning_effort="medium",
            scenario="short_moderate",
            task_name="scenario_short_moderate_stream"
        )

    @task(get_task_weight("scenario_medium_moderate_stream", default=0))
    def scenario_medium_moderate_stream(self):
        """中等上下文+中等问题场景流式测试"""
        self._execute_responses_stream_task(
            reasoning_effort="medium",
            scenario="medium_moderate",
            task_name="scenario_medium_moderate_stream"
        )

    @task(get_task_weight("scenario_long_complex_stream", default=0))
    def scenario_long_complex_stream(self):
        """长上下文+复杂问题场景流式测试"""
        self._execute_responses_stream_task(
            reasoning_effort="high",
            scenario="long_complex",
            task_name="scenario_long_complex_stream"
        )

    @task(get_task_weight("embeddings", default=0))
    def embeddings(self):
        payload, params = split_request_options(self.build_embeddings_payload())
        path = CONFIG["paths"]["embeddings"]
        start_ms = now_ms()

        with self.client.post(
            path,
            params=params,
            data=json.dumps(payload, ensure_ascii=False),
            headers=self.common_headers,
            catch_response=True,
            name="embeddings",
            timeout=CONFIG["request_timeout_seconds"],
        ) as resp:
            try:
                if resp.status_code != 200:
                    self.record_failure(resp, "embeddings", path, payload["model"])
                    return

                body = try_parse_json_response(resp) or {}
                ttl_ms = now_ms() - start_ms
                usage_metrics = extract_usage_metrics(body)

                resp.success()
                emit_structured_log(
                    metric="embeddings",
                    event="success",
                    path=path,
                    model=payload["model"],
                    ttlt_ms=ttl_ms,
                    prompt_tokens=usage_metrics["prompt_tokens"],
                    completion_tokens=usage_metrics["completion_tokens"],
                    total_tokens=usage_metrics["total_tokens"],
                )
            except Exception as e:
                self.record_exception_failure(
                    resp,
                    "embeddings",
                    path,
                    payload["model"],
                    e,
                    f"embeddings exception: {e}",
                )
