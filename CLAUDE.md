# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

AI网关负载测试工具，基于 Locust 框架，针对思考推理模型的性能测试进行了专门优化。核心挑战在于准确区分思考阶段和输出阶段的性能指标，这对于思考推理模型（如 OpenAI o1 系列）的性能评估至关重要。

## 关键架构决策

### 模板驱动的请求系统
- `templates/` 目录包含所有请求模板，按接口类型和功能分类
- 模板文件在运行时加载（`load_json()`），避免硬编码请求结构
- 支持 Chat Completions API、Responses API、Embeddings API
- 流式/非流式、思考模式/传统模式通过模板参数区分

### 任务权重系统
- 使用 `@task(get_task_weight("task_name", default=0))` 装饰器控制任务执行
- `config.json` 中的 `traffic_ratio` 字段定义各任务的权重
- 权重为 0 的任务不会执行，权重为相对值（不是百分比）
- 关键修复：`get_task_weight()` 函数正确处理 weight=0 的情况，避免未配置任务默认执行

### 思考模式性能测量的核心挑战
思考模型的性能测量难点在于区分思考阶段和输出阶段：

**关键发现**：
- 思考阶段：模型内部推理，无法流式输出，消耗 `reasoning_tokens`
- 输出阶段：推理完成后生成答案，可以流式输出，消耗 `completion_tokens`
- 首字符返回时间 = 思考结束时间，而非传统意义上的 TTFT

**解决方案**：
1. **时间测量**：记录第一个输出 chunk 的时间戳作为思考结束时间
   ```python
   if chunk_text and first_token_ts is None:
       first_token_ts = time.time()  # 思考结束时间
   thinking_duration_ms = int((first_token_ts - start_ts) * 1000)
   ```

2. **Token 统计**：从 API 响应的 `usage` 字段提取 `reasoning_tokens`
   ```python
   reasoning_tokens = usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)
   ```

3. **API 格式兼容**：支持 Responses API 的嵌套格式
   ```python
   # Responses API: response.usage.input_tokens
   # Chat API: usage.prompt_tokens
   ```

### 响应解析的复杂性
- **流式响应**：SSE 格式，支持 `data: {...}` 和 `data: [DONE]`
- **不同 API 格式**：Chat Completions (`choices.delta.content`) vs Responses (`response.content_part.done`)
- **Usage 提取**：支持根级别 `usage` 和嵌套 `response.usage`，支持 `input_tokens`/`prompt_tokens` 别名

## 常用命令

### 基础测试流程
```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置模板
cp config.basic.json config.json
# 编辑 config.json，设置 base_url、api_key、model 名称

# 3. 快速验证（3用户，60秒）
./run.sh --headless --users 3 --spawn-rate 1 --run-time 60s

# 4. 标准压测（100用户，5分钟）
./run.sh --headless --users 100 --spawn-rate 10 --run-time 300s

# 5. 生成报告
python3 generate_report.py
```

### 不同测试场景
```bash
# 思考模式对比测试
cp config.thinking_modes.json config.json
./run.sh --headless --users 10 --spawn-rate 1 --run-time 120s

# 场景化测试
cp config.scenarios.json config.json
./run.sh --headless --users 20 --spawn-rate 2 --run-time 180s

# 全面测试
cp config.comprehensive.json config.json
./run.sh --headless --users 100 --spawn-rate 10 --run-time 600s
```

### 调试和验证
```bash
# 验证流式响应格式
python3 test_stream_response.py

# 检查日志数据
python3 -c "
import json
from pathlib import Path
metrics = [json.loads(line) for line in open('logs/最新目录/metrics.jsonl')]
print(f'总请求数: {len(metrics)}')
print(f'思考模式请求: {len([m for m in metrics if \"thinking\" in m.get(\"metric\", \"\")])}')
"

# 重新生成指定测试报告
python3 generate_report.py --run-dir logs/20260415-154926
```

## 配置系统设计

### 模板配置文件
- `config.basic.json`：日常监控，基础接口测试
- `config.thinking_modes.json`：思考模式对比（off/low/medium/high）
- `config.scenarios.json`：场景化测试（上下文长度 × 复杂度）
- `config.comprehensive.json`：全面测试，包含所有场景
- `config.quick.json`：快速验证，最小配置
- `config.streaming.json`：专注流式接口性能

### 配置关键字段
```json
{
  "base_url": "https://your-gateway.com",  // 不包含 /v1 路径
  "api_key": "sk-your-key",
  "chat_model": "model-name",
  "responses_model": "model-name",
  "request_timeout_seconds": 180,  // 思考模式建议 >= 180s
  "traffic_ratio": {
    "chat_stream": 7,
    "responses_thinking_medium_stream": 2,
    "scenario_medium_moderate_stream": 1
  }
}
```

### URL 路径拼接规则
- `base_url` + `path` = 完整请求 URL
- 正确：`"base_url": "https://api.com"` + `"path": "/v1/chat/completions"`
- 错误：`"base_url": "https://api.com/v1"` + `"path": "/v1/chat/completions"` (路径重复)

## 性能指标体系

### 思考模式专用指标
- `thinking_duration_ms`：思考阶段时长（首字符时间 - 请求开始时间）
- `output_ttft_ms`：输出首 token 时间（思考后的第一个输出 token）
- `output_tps`：输出阶段 token 速率（tokens/秒）
- `thinking_ratio`：思考时间占比（思考时长 / 总时长）
- `reasoning_tokens`：思考阶段消耗的 token 数

### 传统模式指标
- `ttft_ms`：首 token 时间
- `ttlt_ms`：最后 token 时间
- `tokens_per_sec`：token 生成速率
- `prompt_tokens/completion_tokens/total_tokens`：标准 token 统计

### 性能标准调整
报告生成会根据是否包含思考模式数据自动选择标准：
- **思考模式**：P95 TTFT ≤ 15000ms，平均输出速率 ≥ 20 tokens/s
- **传统模式**：P95 TTFT ≤ 2000ms，平均 Token 速率 ≥ 15 tokens/s

## 结构化日志系统

### 日志目录结构
```
logs/<run_id>/
├── metrics.jsonl      # 成功请求的结构化指标
├── errors.jsonl       # 失败请求的错误信息
└── run_metadata.json  # 测试配置快照
```

### 指标记录函数
```python
emit_structured_log(
    metric="responses_thinking_low_stream",  # 指标名称
    event="success",                        # 事件类型
    thinking_duration_ms=8963,              # 思考时长
    output_tps=2653.8,                      # 输出速率
    reasoning_effort="low",                 # 推理等级
    # ... 其他指标
)
```

## 常见问题和解决方法

### 404 错误
**原因**：URL 路径重复拼接
**检查**：`base_url` 不应包含 `/v1`，`paths` 配置已包含完整路径

### 思考时长显示为 "- ms"
**原因**：
1. 未检测到思考内容（`extract_reasoning_content()` 返回 None）
2. 流式响应中未正确记录 `first_token_ts`
3. Usage 信息未正确提取（`response.usage` 嵌套结构）

**解决**：
- 检查 API 是否返回 `reasoning_tokens > 0`
- 验证流式响应是否包含 `response.content_part.done` 事件
- 确认 `extract_usage_metrics()` 支持 `response.usage` 嵌套格式

### 权重为 0 的任务仍在执行
**原因**：`get_task_weight()` 函数未正确处理 `weight=0`
**解决**：确保函数返回 `0 if weight == 0 else int(weight)`

### 报告指标显示为 0
**原因**：Python 模块缓存，导入旧版本
**解决**：重启 Python 进程或清除 `__pycache__`

## 关键文件说明

- `locustfile.py`：主测试脚本，包含任务定义、响应解析、指标收集
- `generate_report.py`：报告生成，处理日志数据、计算百分位数、生成 Markdown 报告
- `run.sh`：启动脚本，配置 web 访问地址
- `templates/`：请求模板文件，按接口类型和功能分类
- `config.*.json`：配置模板，针对不同测试场景预配置
- `doc/AI 网关性能测试标准与指标说明文档.md`：详细的指标定义和测试标准
