# AI 网关压测落地包（Locust 版）

## 目录说明

### 核心文件
- `locustfile.py`：主压测脚本，支持 `chat/completions`、`responses`、`embeddings`
- `config.example.json`：压测配置模板（API 地址、模型名称、流量权重等）
- `config.json`：本地实际压测配置文件（由模板复制后填写，不入库）
- `run.sh`：启动脚本（自动配置 web 访问地址）
- `generate_report.py`：将结构化 JSONL 日志聚合为 Markdown 报告
- `requirements.txt`：依赖清单

### 配置文件
- `templates/`：请求模板文件
  - `chat_stream.json`：流式请求模板
  - `chat_non_stream.json`：非流式请求模板
  - `responses.json`：Responses API 请求模板
  - `responses_thinking_*.json`：思考模式请求模板（off/low/medium/high）
  - `scenario_*.json`：场景化测试模板
  - `embeddings.json`：embedding 请求模板
- `config.*.json`：配置模板文件
  - `config.basic.json`：基础测试配置
  - `config.thinking_modes.json`：思考模式对比配置
  - `config.scenarios.json`：场景化测试配置
  - `config.comprehensive.json`：全面测试配置
  - `config.quick.json`：快速验证配置
  - `config.streaming.json`：流式接口测试配置

### 输出目录
- `logs/`：测试日志目录（按 run_id 分目录存储）
- `reports/`：生成的测试报告目录

### 文档目录
- `doc/`：项目文档
  - `AI 网关性能测试标准与指标说明文档.md`：性能测试标准和指标定义
  - `AI 网关压测落地方案.md`：完整的压测实施方案和验收标准

### 测试文件
- `tests/`：单元测试和验证脚本

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 修改配置

先复制配置模板：

```bash
cp config.example.json config.json
```

再编辑 `config.json`：

**必填字段：**
- `base_url`：API 服务基础地址（如 `http://10.22.31.68:8009`）
- `api_key`：API 认证密钥
- `chat_model`：对话模型名称（如 `Qwen3-8B`）
- `responses_model`：Responses API 模型名称
- `embedding_model`：嵌入模型名称

**可选字段：**
- `responses_reasoning_effort`：推理强度（low/medium/high）
- `responses_text_verbosity`：文本冗长度（low/medium/high）
- `responses_extra_query`：额外查询参数
- `wait_time_min/max`：用户请求间隔时间（秒）
- `request_timeout_seconds`：请求超时时间
- `traffic_ratio`：不同接口的流量权重配置

**重要说明：**
- `base_url` 只包含协议+域名+端口，不包含 `/v1` 路径
- `paths` 配置中已包含完整的 API 路径（如 `/v1/chat/completions`）
- 配置错误会导致 404 错误，请确保 URL 拼接正确

### 3. 启动压测

**推荐方式（使用启动脚本）：**

```bash
./run.sh
```

启动脚本会自动配置正确的 web 访问地址，启动后直接回车即可打开控制台。

**手动启动方式：**

```bash
locust -f locustfile.py --web-host localhost --web-port 8089
```

**运行模式说明：**

Locust 支持两种运行模式：

**1. Web 界面模式（默认）**
- 适合调试和观察实时指标
- 需要手动点击 "Start" 开始，"Stop" 结束
- 会一直运行直到手动停止
- 可以实时调整并发用户数

**2. 自动运行模式（推荐用于正式压测）**
- 无需人工干预，自动执行并结束
- 设置明确的运行时间和并发参数
- 适合自动化测试和持续集成

**命令行参数详解：**

```bash
# Web 界面模式（手动控制）
./run.sh --users 100 --spawn-rate 10
# 启动后在浏览器中手动点击 Start/Stop

# 自动运行模式（推荐）
./run.sh --headless --users 100 --spawn-rate 10 --run-time 60s
# --headless: 无界面模式
# --users: 并发用户数
# --spawn-rate: 每秒启动用户数
# --run-time: 运行时长（s/m/h）

# 临时覆盖配置文件中的 host
./run.sh --host http://your-api-host:8009
```

**启动后访问：**

```text
http://localhost:8089
```

注意：启动时会显示 "Starting web interface at http://localhost:8089"，此时直接按回车键，浏览器会自动打开控制台页面。

### 4. 压测场景示例

根据不同的测试目标，推荐以下压测场景：

**场景 1：基线测试（小规模验证）**
```bash
# 10 用户跑 5 分钟，验证系统基本功能
./run.sh --headless --users 10 --spawn-rate 1 --run-time 300s
```

**场景 2：压力测试（阶梯增压）**
```bash
# 分阶段逐步增加并发
./run.sh --headless --users 50 --spawn-rate 5 --run-time 300s
./run.sh --headless --users 100 --spawn-rate 10 --run-time 300s
./run.sh --headless --users 200 --spawn-rate 20 --run-time 300s
./run.sh --headless --users 500 --spawn-rate 50 --run-time 300s
```

**场景 3：稳定性测试（长时间运行）**
```bash
# 固定并发跑 1 小时，观察系统稳定性
./run.sh --headless --users 100 --spawn-rate 10 --run-time 3600s
```

**场景 4：极限测试（寻找系统瓶颈）**
```bash
# 高并发短时间，找到系统极限
./run.sh --headless --users 1000 --spawn-rate 100 --run-time 120s
```

**场景 5：混合流量测试（模拟真实场景）**
```bash
# 使用配置文件中的 traffic_ratio 权重分配
./run.sh --headless --users 200 --spawn-rate 20 --run-time 600s
# 默认权重：chat_stream:7, chat_non_stream:2, responses:2, embeddings:1
```

### 5. 推荐压测流程

**标准压测流程：**

1. **准备阶段**：验证配置和连通性
   ```bash
   # 1-5 用户快速验证
   ./run.sh --headless --users 3 --spawn-rate 1 --run-time 60s
   ```

2. **基线测试**：建立性能基准
   ```bash
   # 小并发建立基准数据
   ./run.sh --headless --users 10 --spawn-rate 1 --run-time 300s
   ```

3. **压力测试**：阶梯增压，评估系统承载能力
   ```bash
   # 逐步增加并发，观察性能变化
   ./run.sh --headless --users 50 --spawn-rate 5 --run-time 300s
   ./run.sh --headless --users 100 --spawn-rate 10 --run-time 300s
   ./run.sh --headless --users 200 --spawn-rate 20 --run-time 300s
   ```

4. **稳定性测试**：长时间运行验证稳定性
   ```bash
   # 固定并发跑 60 分钟以上
   ./run.sh --headless --users 100 --spawn-rate 10 --run-time 3600s
   ```

5. **极限测试**：找到系统瓶颈点
   ```bash
   # 高并发找到系统极限
   ./run.sh --headless --users 1000 --spawn-rate 100 --run-time 120s
   ```

6. **报告生成**：汇总测试结果
   ```bash
   # 生成结构化测试报告
   python3 generate_report.py
   ```

**注意事项：**
- 每个阶段结束后检查日志，确认无异常后再进入下一阶段
- 建议从小并发开始，逐步增加，避免一开始就压垮系统
- 记录每个阶段的关键指标，便于对比分析

### 6. 参数设置指南

**并发用户数（--users）选择：**
- **验证测试**：1-10 用户
- **正常负载**：预计日活用户的 1-5%
- **峰值负载**：预计日活用户的 10-20%
- **压力测试**：峰值负载的 2-3 倍

**启动速率（--spawn-rate）设置：**
- **温和启动**：每秒 1-5 用户（避免瞬间冲击）
- **正常启动**：每秒 10-20 用户
- **快速启动**：每秒 50+ 用户（压力测试场景）

**运行时长（--run-time）建议：**
- **快速验证**：60 秒
- **基础测试**：5 分钟（300 秒）
- **稳定测试**：30-60 分钟（1800-3600 秒）
- **长期稳定**：数小时到数天

**计算公式：**
```bash
# 预计总请求数 = 用户数 × (运行时间 / 平均响应时间)
# 例如：100 用户 × (300 秒 / 2 秒) = 15,000 请求
```

## 指标说明

脚本已内置以下结构化指标输出：
- `ttft_ms`：首 Token 时间
- `ttlt_ms`：最后 Token 时间
- `tokens_per_sec`：生成阶段 token 速率
- `stream_chunk_count`：流式块数
- `prompt_tokens / completion_tokens / total_tokens`：优先读取标准 `usage`
- `error.type / error.code / error.message`：失败时的结构化错误分类

说明：
- 若你们网关的流式协议不是标准 SSE，需要调整 `parse_stream_chunk` 方法
- 若返回体里没有标准 `usage` 字段，token 统计会退化为基于文本内容的近似估算
- `responses` 请求默认按 OpenAI 风格的 `input + reasoning + text + extra_query` 结构发送
- 每次启动压测会在 `log_dir/<run_id>/` 下生成独立日志目录

### 日志目录结构

压测启动后，默认会在 `logs/` 下生成一轮独立目录，例如：

```text
logs/20260414-173000/
├── metrics.jsonl
├── errors.jsonl
└── run_metadata.json
```

说明：

- `metrics.jsonl`：成功请求及结构化指标
- `errors.jsonl`：失败请求及结构化错误信息
- `run_metadata.json`：本轮压测的基础配置快照

## 报告生成

压测完成后，可将最新一轮日志自动汇总为 Markdown 报告：

```bash
python3 generate_report.py
```

默认输出到：

```text
reports/<run_id>_report.md
```

生成的报告会自动包含：

- 关键指标汇总
- 达标判断（PASS / FAIL / PARTIAL）
- HTTP 状态码分布
- `error.type` / `error.code` 错误分类
- 风险摘要与建议

如果要指定某一轮日志目录：

```bash
python3 generate_report.py --run-dir 20260414-173000
```

如果要指定输出文件：

```bash
python3 generate_report.py --run-dir 20260414-173000 --output reports/custom_report.md
```

## 建议

- 流式接口优先用 Locust
- 管理接口和非流式接口也可继续保留 JMeter 做补充
- 对接 OpenAI 风格大模型时，建议同时覆盖 `chat/completions` 与 `responses`
- 生产环境前至少完成：基线、阶梯升压、稳定性、异常容错 4 类测试

## 常见问题

### 1. 404 错误

**现象：** 所有请求都返回 404 错误

**原因：** URL 配置错误，路径重复拼接

**解决方法：**
- 检查 `config.json` 中的 `base_url` 不应包含 `/v1` 路径
- 正确配置：`"base_url": "http://10.22.31.68:8009"`
- 错误配置：`"base_url": "http://10.22.31.68:8009/v1"`

**验证方法：**
```
base_url + path = http://10.22.31.68:8009 + /v1/chat/completions
              = http://10.22.31.68:8009/v1/chat/completions ✅
```

### 2. 什么时候会结束？

**问题：** 启动后会一直请求吗？什么时候会结束？

**回答：** Locust 有两种运行模式，结束条件不同：

**Web 界面模式（默认）：**
- 启动后不会自动开始，需要点击 "Start" 按钮
- 会一直运行，直到手动点击 "Stop"
- 适合调试和观察实时指标
- 何时结束：手动停止或关闭程序

**自动运行模式（--headless）：**
- 启动后自动开始运行
- 设置了 `--run-time` 参数后会自动结束
- 适合自动化测试和正式压测
- 何时结束：
  - 达到指定的运行时间
  - 手动停止（Ctrl+C）

**示例：**
```bash
# 运行 60 秒后自动结束
./run.sh --headless --users 100 --spawn-rate 10 --run-time 60s

# 无时间限制，手动结束（Ctrl+C）
./run.sh --headless --users 100 --spawn-rate 10
```

### 3. 0.0.0.0 无法访问

**现象：** 启动后显示 `Starting web interface at http://0.0.0.0:8089`，但浏览器无法访问

**原因：** 0.0.0.0 是服务器监听地址，不是客户端访问地址

**解决方法：**
- 使用 `./run.sh` 启动脚本，会自动配置为 localhost
- 或手动访问 `http://localhost:8089` / `http://127.0.0.1:8089`

### 4. 模型名称配置

**现象：** 返回模型不存在错误

**解决方法：**
- 检查 `config.json` 中的模型名称是否正确
- 模型名称不应包含 `/models/` 前缀（除非 API 确实需要）
- 常见格式：`Qwen3-8B`、`gpt-4` 等

### 5. 认证失败

**现象：** 返回 401 或 403 错误

**解决方法：**
- 检查 `config.json` 中的 `api_key` 是否正确
- 确认 API 密钥格式和权限
