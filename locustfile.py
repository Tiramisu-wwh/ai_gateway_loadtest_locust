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
    return int(traffic_ratio.get(task_name, default))


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
    usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
    if not isinstance(usage, dict):
        usage = {}

    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = usage.get("total_tokens")
    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": int(total_tokens or 0),
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

    def build_responses_payload(self) -> Dict[str, Any]:
        payload = deep_copy(RESPONSES_TEMPLATE)
        payload["model"] = CONFIG.get("responses_model") or CONFIG["chat_model"]
        payload["reasoning"] = {
            "effort": CONFIG.get("responses_reasoning_effort", "medium")
        }
        payload["text"] = {
            "verbosity": CONFIG.get("responses_text_verbosity", "low")
        }

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

    @task(get_task_weight("chat_stream"))
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
                resp.failure(f"stream exception: {e}")

    @task(get_task_weight("chat_non_stream"))
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
                resp.failure(f"non-stream exception: {e}")

    @task(get_task_weight("responses"))
    def responses_non_stream(self):
        payload, params = split_request_options(self.build_responses_payload())
        path = CONFIG["paths"]["responses"]
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
                ttl_ms = now_ms() - start_ms
                usage_metrics = extract_usage_metrics(body)
                output_text = extract_responses_output_text(body)
                completion_tokens = usage_metrics["completion_tokens"] or estimate_token_count_from_text(output_text)
                total_tokens = usage_metrics["total_tokens"] or (
                    usage_metrics["prompt_tokens"] + completion_tokens
                )

                resp.success()
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
                resp.failure(f"responses exception: {e}")

    @task(get_task_weight("embeddings"))
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
                resp.failure(f"embeddings exception: {e}")
