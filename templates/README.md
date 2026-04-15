# Templates 目录说明

本目录包含AI网关负载测试的所有请求模板文件。

## 文件命名规则

- **非流式模板**：直接命名，如 `chat_non_stream.json`
- **流式模板**：添加 `_stream` 后缀，如 `chat_stream.json`

## 模板分类

### 1. Chat Completions API（传统对话接口）

| 文件 | 类型 | 说明 | 使用场景 |
|------|------|------|---------|
| `chat_stream.json` | 流式 | SSE流式输出，逐token返回 | 实时对话、用户交互 |
| `chat_non_stream.json` | 非流式 | 一次性返回完整结果 | 后台任务、批量处理 |

**关键差异**：
```json
// 流式
{"stream": true, "stream_options": {"include_usage": true}}

// 非流式
{"stream": false}  // 或不传此参数
```

### 2. Responses API - 思考推理模型（基础版本）

| 文件 | 类型 | 推理等级 | 说明 |
|------|------|----------|------|
| `responses.json` | 非流式 | - | 常规Responses API |
| `responses_thinking_off.json` | 非流式 | OFF | 禁用思考模式 |
| `responses_thinking_low.json` | 非流式 | LOW | 轻度思考 |
| `responses_thinking_medium.json` | 非流式 | MEDIUM | 中度思考 |
| `responses_thinking_high.json` | 非流式 | HIGH | 深度思考 |

### 3. Responses API - 思考推理模型（流式版本）

| 文件 | 类型 | 推理等级 | 说明 |
|------|------|----------|------|
| `responses_thinking_off_stream.json` | 流式 | OFF | 禁用思考，流式输出 |
| `responses_thinking_low_stream.json` | 流式 | LOW | 轻度思考，流式输出 |
| `responses_thinking_medium_stream.json` | 流式 | MEDIUM | 中度思考，流式输出 |
| `responses_thinking_high_stream.json` | 流式 | HIGH | 深度思考，流式输出 |

**重要说明**：
- 思考阶段无法流式（模型需要完整思考）
- 输出阶段可以流式（逐token返回）
- 流式模板的性能指标会区分：
  - `thinking_duration_ms`：思考时长（无法流式）
  - `output_ttft_ms`：输出首token时间（可流式）
  - `output_tps`：输出token速率

### 4. 场景化测试模板（基础版本）

| 文件 | 上下文 | 复杂度 | 推理等级 | 输入Token |
|------|--------|--------|----------|-----------|
| `scenario_short_simple.json` | 短 | 简单 | LOW | ~50 |
| `scenario_short_moderate.json` | 短 | 中等 | MEDIUM | ~80 |
| `scenario_medium_moderate.json` | 中等 | 中等 | MEDIUM | ~300 |
| `scenario_long_complex.json` | 长 | 复杂 | HIGH | ~1200 |

### 5. 场景化测试模板（流式版本）

| 文件 | 上下文 | 复杂度 | 推理等级 | 输入Token |
|------|--------|--------|----------|-----------|
| `scenario_short_simple_stream.json` | 短 | 简单 | LOW | ~50 |
| `scenario_short_moderate_stream.json` | 短 | 中等 | MEDIUM | ~80 |
| `scenario_medium_moderate_stream.json` | 中等 | 中等 | MEDIUM | ~300 |
| `scenario_long_complex_stream.json` | 长 | 复杂 | HIGH | ~1200 |

### 6. Embeddings API

| 文件 | 说明 |
|------|------|
| `embeddings.json` | 向量化接口模板 |

## 模板结构说明

### 基础模板结构
```json
{
  "model": "your-model-name",
  "input": [
    {"role": "system", "content": "系统提示词"},
    {"role": "user", "content": "用户问题"}
  ],
  "temperature": 0.3
}
```

### 思考模式模板结构
```json
{
  "model": "your-model-name",
  "input": [...],
  "reasoning": {
    "effort": "medium"  // low/medium/high
  },
  "text": {
    "verbosity": "low"  // low/medium/high
  }
}
```

### 场景化模板结构
```json
{
  "model": "your-model-name",
  "input": [...],
  "reasoning": {"effort": "medium"},
  "text": {"verbosity": "low"},
  "scenario_metadata": {
    "context_length": "medium",      // short/medium/long
    "complexity": "moderate",         // simple/moderate/complex
    "estimated_input_tokens": 300,    // 预估token数
    "expected_reasoning_time": "10-25s"  // 预期思考时间
  }
}
```

### 流式模板结构
```json
{
  "model": "your-model-name",
  "stream": true,  // 关键：启用流式
  "stream_options": {
    "include_usage": true
  },
  "input": [...]
}
```

## 如何选择模板

### 按测试目的选择

**日常性能监控**：
```
chat_stream.json (70%)
chat_non_stream.json (20%)
responses.json (10%)
```

**思考模式性能测试**：
- 非流式：`responses_thinking_*.json`
- 流式：`responses_thinking_*_stream.json`

**场景化性能分析**：
- 非流式：`scenario_*.json`
- 流式：`scenario_*_stream.json`

**完整覆盖测试**：
- 混合使用所有模板

### 按接口类型选择

**Chat Completions API**：
- 实时交互 → `chat_stream.json`
- 后台任务 → `chat_non_stream.json`

**Responses API（传统模式）**：
- 非流式 → `responses.json`
- 流式 → 使用流式思考模板并设置 `reasoning_effort: null`

**Responses API（思考模式）**：
- 不需要实时反馈 → `responses_thinking_*.json`
- 需要输出阶段流式 → `responses_thinking_*_stream.json`

**场景化测试**：
- 分析性能影响 → `scenario_*.json`（非流式）
- 验证流式性能 → `scenario_*_stream.json`（流式）

## 模板使用示例

### 示例1：基础流式测试

```python
# config.json
{
  "traffic_ratio": {
    "chat_stream": 5,
    "chat_non_stream": 2,
    "responses_stream": 2,
    "responses_thinking_medium_stream": 1
  }
}
```

### 示例2：思考模式对比测试

```python
# config.json
{
  "traffic_ratio": {
    "responses_thinking_off": 1,
    "responses_thinking_low": 1,
    "responses_thinking_medium": 1,
    "responses_thinking_high": 1
  }
}
```

### 示例3：场景化性能分析

```python
# config.json
{
  "traffic_ratio": {
    "scenario_short_simple": 2,
    "scenario_medium_moderate": 2,
    "scenario_long_complex": 1
  }
}
```

### 示例4：流式场景测试

```python
# config.json
{
  "traffic_ratio": {
    "scenario_short_simple_stream": 2,
    "scenario_medium_moderate_stream": 2,
    "scenario_long_complex_stream": 1
  }
}
```

## 创建自定义模板

### 1. 复制现有模板
```bash
cp templates/scenario_medium_moderate.json templates/scenario_custom.json
```

### 2. 修改内容
```json
{
  "model": "your-actual-model",
  "input": [
    {"role": "user", "content": "你的自定义问题"}
  ],
  "scenario_metadata": {
    "context_length": "custom",
    "complexity": "custom",
    "description": "自定义测试场景"
  }
}
```

### 3. 在配置中引用
```json
{
  "traffic_ratio": {
    "scenario_custom": 1
  }
}
```

## 性能指标差异

### 非流式接口指标
- `ttlt_ms`：总响应时间
- `total_tokens`：总token数
- `success_rate`：成功率

### 流式接口指标
- `ttft_ms`：首token时间（重要！）
- `tokens_per_sec`：token生成速率
- `ttlt_ms`：最后一个token时间
- `stream_chunk_count`：流式chunk数量

### 思考模式流式指标
- `thinking_duration_ms`：思考时长
- `output_ttft_ms`：输出首token时间
- `output_tps`：输出token速率
- `thinking_ratio`：思考时间占比

## 注意事项

1. **模型路径**：模板中的 `model` 字段需要替换为实际的模型路径
2. **思考模式**：确保模型支持思考推理功能（reasoning_effort）
3. **流式支持**：确认API支持流式输出（`stream: true`）
4. **超时设置**：思考模式需要更长的超时时间（建议180秒）
5. **场景元数据**：自定义场景时务必提供准确的 `scenario_metadata`

## 相关文档

- [配置文件使用指南.md](../配置文件使用指南.md)
- [AI 网关性能测试标准与指标说明文档.md](../doc/AI%20网关性能测试标准与指标说明文档.md)
- [README.md](../README.md)
