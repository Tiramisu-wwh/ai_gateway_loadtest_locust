# AI网关独立并发测试方案

## 1. 文档目的

本文档定义 AI 网关自身并发处理能力的测试方案，用于：

- 评估网关最大并发连接数
- 验证网关转发性能与资源消耗
- 测试网关保护机制（限流、熔断、降级）
- 优化网关配置参数（连接池、超时、队列）

## 2. 测试原理

### 2.1 测试思路

使用**快速响应的 Mock 后端服务**替代真实模型服务，消除后端瓶颈，纯粹测试网关转发能力：

```
传统端到端测试：
测试工具 -> AI网关 -> 后端模型服务（秒级响应，可能是瓶颈）
                    无法区分网关/后端瓶颈

网关独立测试：
测试工具 -> AI网关 -> Mock后端服务（<200ms，非瓶颈）
                    纯粹测试网关性能
```

### 2.2 Mock 后端要求

**包含**：
- 网关最大并发连接数测试
- 网关转发性能与资源消耗
- 网关限流、熔断、降级机制验证
- 网关配置参数（连接池、超时、队列）调优验证

**不包含**：
- 后端模型推理性能（由端到端测试覆盖）
- 模型准确率、幻觉率等业务指标
- 安全性测试、渗透测试

### 1.3 测试价值

1. **容量评估**：明确网关自身能承载的最大并发连接数
2. **瓶颈定位**：判断性能问题是在网关还是后端模型
3. **配置优化**：验证连接池大小、超时时间等参数配置是否合理
4. **机制验证**：确保限流、熔断等保护机制在预期场景下生效

## 2. 测试原理

### 2.1 测试思路

使用 **快速响应的 Mock 后端服务** 替代真实模型服务，消除后端瓶颈，纯粹测试网关转发能力：

```
传统端到端测试：
Locust -> AI网关 -> 后端模型服务（慢，可能是瓶颈）
         ↑ 测试点

网关独立测试：
Locust -> AI网关 -> Mock后端服务（快，非瓶颈）
         ↑ 测试点
```

**响应特征**：

- 固定延迟（如 50ms、100ms、200ms）
- 高并发能力（非瓶颈）
- 可控失败率（用于测试熔断）
- 模拟慢响应（用于测试超时）

**实现方式**：

1. WireMock（功能完整）
2. simple-http-server（快速验证）
3. nginx + lua（生产级）

### 2.3 测试前提条件

- Mock 后端响应时间（< 200ms）远小于网关处理时间
- Mock 后端并发能力 > 预期网关并发上限
- 网络延迟稳定且可控
- 网关配置（连接池、超时）已确认

## 3. 测试场景设计

### 3.1 场景一：网关基线并发测试

**目的**：获取网关在低并发下的基线性能指标

**配置**：
```json
{
  "users": [1, 5, 10],
  "spawn_rate": 1,
  "run_time": "5m",
  "mock_latency": "50ms"
}
```

**关注指标**：
- 平均响应时间（应该接近 Mock 延迟 + 网关固定开销）
- P95/P99 响应时间
- 网关 CPU/内存使用率基线
- 连接数、队列深度基线

### 3.2 场景二：网关阶梯升压测试

**目的**：找到网关性能开始劣化的拐点

**配置**：
```json
{
  "users": [10, 50, 100, 200, 500, 1000, 2000],
  "spawn_rate": "等于 users",
  "run_time": "3m",
  "mock_latency": "100ms"
}
```

**阶梯终止条件**：
- 错误率 > 1%
- P95 响应时间 > Mock 延迟的 10 倍
- 网关 CPU/内存达到上限
- 出现大量连接超时、拒绝连接

**输出**：网关推荐并发上限

### 3.3 场景三：网关极限并发测试

**目的**：找到网关的绝对承载边界

**配置**：
```json
{
  "users": [1000, 2000, 5000, 10000],
  "spawn_rate": "快速爬升",
  "run_time": "1m",
  "mock_latency": "50ms"
}
```

**观察重点**：
- 网关崩溃/重启的并发点
- 最大成功响应 QPS
- 内存溢出、文件描述符耗尽等系统错误

**注意**：此场景可能影响网关稳定性，应在测试环境执行

### 3.4 场景四：网关长时间稳定性测试

**目的**：验证网关在推荐并发上限下的长时间运行稳定性

**配置**：
```json
{
  "users": "场景二推荐的并发上限的 80%",
  "spawn_rate": "平稳启动",
  "run_time": "30m~60m",
  "mock_latency": "100ms"
}
```

**观察重点**：
- 内存泄漏（内存使用是否持续上升）
- 连接泄漏（连接数是否持续上升）
- 响应时间抖动（P95 是否随时间劣化）
- 错误率变化

### 3.5 场景五：网关保护机制验证测试

#### 5.1 限流机制测试

**目的**：验证网关限流是否在预期阈值生效

**测试步骤**：
1. 配置网关限流：如 100 req/s
2. 发送请求：150 req/s（持续 1 分钟）
3. 验证：确认收到 429 状态码的请求约 50%

**通过标准**：
- 限流阈值附近的请求通过率符合预期
- 429 错误响应时间 < 50ms（快速失败）
- 低于限流阈值的请求不受影响

#### 5.2 熔断机制测试

**目的**：验证后端故障时熔断是否快速生效

**测试步骤**：
1. 配置 Mock 后端返回 50% 错误率
2. 持续发送请求（QPS > 熔断阈值）
3. 观察熔断器是否打开
4. 停止错误流量，验证熔断器恢复

**通过标准**：
- 错误率达到阈值后，熔断器在预期时间内打开
- 熔断期间请求快速失败（非超时）
- 后端恢复后，熔断器按配置恢复

#### 5.3 超时机制测试

**目的**：验证网关超时配置是否生效

**测试步骤**：
1. 配置网关超时：如 10s
2. 配置 Mock 后端延迟：20s
3. 发送请求

**通过标准**：
- 请求在 10s 左右返回超时错误（非 20s）
- 超时后网关连接正确释放（无连接泄漏）

#### 5.4 连接池测试

**目的**：验证连接池配置是否合理

**测试步骤**：
1. 配置网关连接池：如最大 100 连接
2. 并发发送 200 个请求
3. 观察请求排队情况

**通过标准**：
- 超过连接池大小的请求正确排队（非直接拒绝）
- 连接复用率 > 80%
- 无连接泄漏（测试结束后连接数回落）

## 4. 测试环境准备

### 4.1 Mock 后端部署

#### 方案 A：使用 simple-http-server（快速验证）

```bash
# 安装
cargo install simple-http-server

# 启动固定延迟服务
simple-http-server --port 8080 --delay 100
```

#### 方案 B：使用 WireMock（功能完整）

```bash
# 下载
wget https://repo1.maven.org/maven2/org/wiremock/wiremock-standalone/3.5.2/wiremock-standalone-3.5.2.jar

# 启动
java -jar wiremock-standalone-3.5.2.jar --port 8080
```

配置 stub（固定延迟 100ms）：
```json
{
  "request": {
    "method": "POST",
    "urlPathPattern": "/v1/chat/completions"
  },
  "response": {
    "fixedDelayMilliseconds": 100,
    "status": 200,
    "jsonBody": {
      "id": "mock-123",
      "choices": [{
        "message": {"role": "assistant", "content": "Mock response"}
      }],
      "usage": {"total_tokens": 100}
    }
  }
}
```

#### 方案 C：使用 nginx + lua（生产级）

```nginx
location /v1/chat/completions {
    access_by_lua '
        local delay = ngx.var.arg_delay or 100
        ngx.sleep(delay / 1000)
    ';

    content_by_lua_block {
        ngx.say('{"id":"mock-123","choices":[{"message":{"role":"assistant","content":"response"}}]}')
    }
}
```

#### 方案 D：使用 Python Flask（推荐，灵活可控）

创建 `mock_server.py`：

```python
from flask import Flask, request, jsonify
import time

app = Flask(__name__)

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    """模拟 Chat Completions API，固定延迟100ms"""
    time.sleep(0.1)  # 模拟100ms延迟

    return jsonify({
        "id": "mock-" + str(int(time.time())),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "mock-model",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "This is a mock response for gateway performance testing."
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30
        }
    })

@app.route('/v1/models', methods=['GET'])
def list_models():
    """模拟模型列表API"""
    return jsonify({
        "object": "list",
        "data": [{
            "id": "mock-model",
            "object": "model",
            "created": int(time.time()),
            "owned_by": "mock-org"
        }]
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
```

启动 Mock 服务：

```bash
# 安装依赖
pip install flask

# 启动服务
python3 mock_server.py

# 验证服务
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mock-model","messages":[{"role":"user","content":"test"}]}'
```

### 4.1.1 针对 NewAPI 网关的 Mock 配置

如果你的网关使用的是 **NewAPI**（https://docs.newapi.pro），按以下步骤配置：

#### 步骤 1：启动 Mock 服务

使用上面的方案 D（Python Flask）启动 Mock 服务，确保服务可访问：

```bash
# 检查服务是否正常
curl http://localhost:8080/v1/models
```

#### 步骤 2：在 NewAPI 中配置 Mock 渠道

1. **登录 NewAPI 管理界面**
2. **进入渠道管理**：渠道 → 添加渠道
3. **配置渠道信息**：

| 配置项 | 值 | 说明 |
|-------|---|------|
| 渠道名称 | `Mock-Test-Channel` | 标识这是测试渠道 |
| 渠道类型 | OpenAI | 或相应的类型 |
| Base URL | `http://your-mock-server:8080` | Mock 服务地址 |
| API Key | `sk-mock-test-key` | Mock服务不需要验证，随意填写 |
| 模型映射 | `mock-model` | 或其他模型名称 |

4. **启用渠道**：确保渠道状态为"启用"

#### 步骤 3：在 NewAPI 中配置测试令牌

1. **进入令牌管理**：令牌 → 添加令牌
2. **配置令牌信息**：

| 配置项 | 值 | 说明 |
|-------|---|------|
| 令牌名称 | `Gateway-Test-Token` | 测试用令牌 |
| 访问令牌 | `sk-gateway-test-123` | 测试时使用的 Token |
| 模型权限 | `mock-model` 或 `*` | 允许访问的模型 |
| 渠道 | `Mock-Test-Channel` | 选择刚才创建的渠道 |
| 额度 | `1000000` | 设置足够大的测试额度 |

#### 步骤 4：验证配置

```bash
# 通过 NewAPI 请求 Mock 后端
curl http://your-newapi-domain.com/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-test-123" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mock-model",
    "messages": [{"role": "user", "content": "test"}]
  }'
```

如果返回 Mock 响应，说明配置成功！

#### 步骤 5：配置测试文件

创建或修改 Locust 测试配置 `config.gateway_test.json`：

```json
{
  "base_url": "http://your-newapi-domain.com",
  "api_key": "sk-gateway-test-123",

  "paths": {
    "chat_completions": "/v1/chat/completions"
  },

  "chat_model": "mock-model",

  "traffic_ratio": {
    "chat_stream": 1
  }
}
```

#### 步骤 6：执行测试

```bash
# 执行网关独立并发测试
./run.sh --headless --users 1000 --spawn-rate 100 --run-time 180s

# 生成报告
python3 generate_report.py
```

### 4.1.2 快速启动脚本

创建 `start_gateway_test.sh`：

```bash
#!/bin/bash

echo "=== 启动网关独立并发测试 ==="

# 1. 启动 Mock 服务
echo "1. 启动 Mock 服务..."
python3 mock_server.py &
MOCK_PID=$!
echo "Mock 服务 PID: $MOCK_PID"

# 等待 Mock 服务启动
sleep 3

# 2. 验证 Mock 服务
echo "2. 验证 Mock 服务..."
curl -s http://localhost:8080/v1/models | jq .

# 3. 验证 NewAPI 到 Mock 的连通性
echo "3. 验证 NewAPI 配置..."
curl -s http://your-newapi-domain.com/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-test-123" \
  -H "Content-Type: application/json" \
  -d '{"model":"mock-model","messages":[{"role":"user","content":"test"}]}' | jq .

# 4. 执行测试
echo "4. 执行网关并发测试..."
./run.sh --headless --users 1000 --spawn-rate 100 --run-time 180s

# 5. 生成报告
echo "5. 生成测试报告..."
python3 generate_report.py

# 6. 清理
echo "6. 清理 Mock 服务..."
kill $MOCK_PID

echo "=== 测试完成 ==="
```

### 4.1.3 注意事项

**Mock 服务部署位置**：
- ✅ 与 NewAPI 在同一内网环境
- ✅ 确保网络延迟稳定
- ✅ 确保 Mock 服务性能足够（不被先成为瓶颈）
- ❌ 不推荐部署在公网（网络延迟不稳定）

**验证 Mock 服务性能**：

在测试前，先验证 Mock 服务本身不是瓶颈：

```bash
# 直接压测 Mock 服务（绕过 NewAPI）
ab -n 10000 -c 100 -p request.json -T application/json \
   http://localhost:8080/v1/chat/completions
```

如果 Mock 服务能轻松处理你的测试并发（如 1000+），那就可以开始测试 NewAPI 了。

**双渠道策略**：

你可以同时配置两个渠道，方便切换：

**渠道1：生产渠道**
- 名称：`Production-OpenAI`
- Base URL：`https://api.openai.com`
- API Key：真实的生产 Key
- 状态：启用

**渠道2：测试渠道**
- 名称：`Mock-Test-Channel`
- Base URL：`http://192.168.1.100:8080`
- API Key：`sk-mock-test-key`
- 状态：启用

**两个令牌**：
- `sk-prod-token` → 使用 `Production-OpenAI` 渠道（日常业务）
- `sk-test-token` → 使用 `Mock-Test-Channel` 渠道（性能测试）

### 4.2 网关配置确认

在测试前，需要确认网关以下配置项：

| 配置项 | 当前值 | 说明 |
|-------|-------|------|
| 最大并发连接数 | ___ | 如 worker_connections |
| 连接池大小 | ___ | 如 upstream 连接池 |
| 请求超时时间 | ___ | 如 proxy_read_timeout |
| 限流阈值 | ___ | 如 leaky_bucket rate |
| 熔断配置 | ___ | 如熔断阈值、恢复时间 |

### 4.3 监控准备

**网关监控**（必须）：
- CPU 使用率、内存使用率
- 网络连接数（ESTABLISHED、TIME_WAIT）
- 网关进程资源使用（如 fd 数量）
- 网关日志（error.log、access.log）

**监控命令示例**：
```bash
# CPU/内存
docker stats <gateway_container>

# 连接数
ss -s | grep "TCP:"
netstat -an | grep :8080 | wc -l

# 文件描述符
ls -l /proc/<pid>/fd | wc -l

# 网关日志
tail -f /var/log/gateway/error.log
```

## 5. 执行步骤

### 5.1 前置检查

- [ ] Mock 后端已启动并验证联通
- [ ] 网关配置已确认并记录
- [ ] 监控工具已准备就绪
- [ ] 测试脚本已配置正确的 Mock 地址
- [ ] 已确认止损条件与责任人

### 5.2 执行顺序

按以下顺序执行测试：

```
1. Mock 后端验证
   └─ curl 测试 Mock 延迟是否符合预期

2. 网关基线测试
   └─ 低并发验证网关正常转发

3. 阶梯升压测试
   └─ 找到性能拐点，记录推荐并发上限

4. 极限测试（可选）
   └─ 找到绝对边界，可能需要重启网关

5. 稳定性测试
   └─ 在推荐上限 80% 并发下长时间运行

6. 保护机制测试
   └─ 验证限流、熔断、超时、连接池

7. 结果分析与报告
   └─ 生成性能测试报告
```

### 5.3 止损条件

测试过程中，如出现以下情况立即停止：

- 网关崩溃或重启
- 错误率持续 > 5%
- 网关 CPU/内存持续 > 90%
- 生产环境告警（如在生产环境测试）
- Mock 后端成为瓶颈（响应时间飙升）

## 6. 指标体系

### 6.1 网关性能指标

| 指标 | 说明 | 目标值 |
|------|------|--------|
| **最大并发连接数** | 网关能同时处理的最大连接数 | 待测试确定 |
| **转发 QPS 上限** | 网关每秒能转发的最大请求数 | 待测试确定 |
| **转发延迟（P50/P95/P99）** | 网关增加的处理延迟 | P95 < 100ms（Mock 50ms 时） |
| **连接建立时间** | TCP 连接建立耗时 | P95 < 50ms |
| **错误率** | 网关层面错误（502/503/504） | < 0.1% |

### 6.2 资源消耗指标

| 指标 | 说明 | 观察重点 |
|------|------|---------|
| **CPU 使用率** | 网关进程 CPU 占用 | 与并发数的关系 |
| **内存使用率** | 网关进程内存占用 | 是否存在内存泄漏 |
| **网络连接数** | ESTABLISHED 连接数 | 是否随并发线性增长 |
| **文件描述符数** | 打开的 fd 数量 | 是否接近系统限制 |
| **上下文切换率** | 进程上下文切换次数 | 过高表示调度开销大 |

### 6.3 机制验证指标

| 机制 | 验证指标 | 通过标准 |
|------|---------|---------|
| **限流** | 超过阈值的请求返回 429 比例 | 95% ~ 105% 之间 |
| **熔断** | 熔断打开时间 | < 配置阈值的 2 倍时间 |
| **超时** | 超时请求的响应时间 | 接近配置的超时时间 |
| **连接池** | 连接复用率 | > 80% |

## 7. 结果分析

### 7.1 数据收集

测试过程中需收集：

- Locust 统计数据（QPS、RT、错误率）
- 网关资源监控数据（CPU/内存/连接数）
- 网关日志（错误日志、访问日志）
- Mock 后端监控数据（确认非瓶颈）

### 7.2 分析方法

**性能拐点识别**：
```
绘制并发数 vs P95 RT 曲线
       RT
        │      ┌────── 拐点：性能急剧劣化
        │     ╱
        │    ╱
        │   ╱
        │  ╱
        │ ╱
        └──────────── 并发数
```

**推荐并发上限**：
- 取拐点并发数的 80% 作为推荐上限
- 确保在此并发下 P95 RT < 基线的 2 倍
- 确保错误率 < 0.1%

### 7.3 报告内容

测试报告应包含：

1. **测试环境**：网关配置、Mock 后端配置、监控方式
2. **测试结果**：各场景的性能数据、资源使用数据
3. **性能拐点**：识别的关键拐点与原因分析
4. **推荐配置**：
   - 推荐并发上限
   - 网关配置优化建议（连接池、超时等）
   - 监控告警阈值建议
5. **风险边界**：绝对极限、危险区域
6. **后续建议**：是否需要扩容、优化、调整配置

## 8. 常见问题

### Q1：为什么不用真实后端测试？

**A**：真实后端（模型推理）响应慢（秒级），会成为瓶颈，无法测试网关自身能力。使用快速 Mock 后端可以：

- 消除后端瓶颈
- 纯粹测试网关转发能力
- 更快完成测试（无需等待模型推理）

### Q2：测试结果如何应用到生产？

**A**：网关独立测试给出的是**网关自身的能力上限**。实际生产容量需综合考虑：

```
生产容量 = min(网关并发上限, 后端模型处理能力)
```

例如：
- 网关独立测试：最大 10000 并发
- 后端模型测试：最大 1000 并发
- **生产推荐容量**：1000 并发（受模型限制）

但网关独立测试的价值在于：
- 确认网关不是瓶颈
- 如需扩容，优先扩容模型而非网关
- 网关配置优化的基准

### Q3：如何判断 Mock 后端是否成为瓶颈？

**A**：观察以下指标：

- Mock 后端 CPU 使用率 > 80%
- Mock 后端响应时间 > 配置延迟的 2 倍
- Mock 后端出现连接超时、拒绝连接

如出现上述情况，需要：
- 升级 Mock 后端配置
- 或使用多个 Mock 后端实例（负载均衡）

### Q4：测试过程中网关崩溃了怎么办？

**A**：
1. 立即停止测试
2. 收集网关崩溃日志（core dump、error log）
3. 检查系统资源（内存、文件描述符）
4. 分析崩溃原因（配置问题、代码 bug、资源耗尽）
5. 修复后重新测试

### Q5：如何与端到端测试结果对比？

**A**：

| 指标 | 网关独立测试 | 端到端测试 | 差异分析 |
|------|------------|-----------|---------|
| P95 RT | 150ms | 5000ms | 4850ms 是后端推理时间 |
| QPS 上限 | 10000 | 1000 | 瓶颈在后端模型 |

通过对比可以：
- 定位瓶颈在网关还是后端
- 指导性能优化方向
- 合理规划扩容资源

## 9. 配置文件示例

### 9.1 网关专用配置文件

创建 `config.gateway_stress.json`：

```json
{
  "description": "网关独立并发性能测试配置，使用快速响应的Mock后端",

  "base_url": "http://your-gateway.com",
  "api_key": "sk-test",
  "request_timeout_seconds": 10,

  "paths": {
    "chat_completions": "/v1/chat/completions"
  },

  "mock_backend": {
    "enabled": true,
    "latency_ms": 100,
    "error_rate": 0,
    "description": "Mock后端配置，可选：50/100/200/500ms"
  },

  "traffic_ratio": {
    "gateway_mock_fast": 5,
    "gateway_mock_medium": 3,
    "gateway_with_limit": 2
  },

  "gateway_limits": {
    "rate_limit": {
      "enabled": true,
      "threshold": 100,
      "burst": 150,
      "description": "网关限流阈值（req/s），用于验证限流机制"
    },
    "connection_pool": {
      "max_connections": 100,
      "description": "网关连接池大小，用于验证连接池管理"
    },
    "timeout": {
      "read_timeout": 10,
      "connect_timeout": 5,
      "description": "网关超时配置（秒）"
    }
  },

  "test_scenarios": {
    "baseline": {
      "users": [1, 5, 10],
      "spawn_rate": 1,
      "run_time": "5m",
      "description": "基线测试"
    },
    "step_up": {
      "users": [10, 50, 100, 200, 500, 1000],
      "spawn_rate": "等于users",
      "run_time": "3m",
      "stop_on_error_rate": 0.01,
      "description": "阶梯升压测试"
    },
    "limit": {
      "users": [1000, 2000, 5000],
      "spawn_rate": "快速爬升",
      "run_time": "1m",
      "description": "极限测试"
    },
    "stability": {
      "users": 200,
      "spawn_rate": 10,
      "run_time": "30m",
      "description": "稳定性测试（使用step_up推荐上限的80%）"
    }
  }
}
```

### 9.2 执行脚本示例

创建 `scripts/run_gateway_test.sh`：

```bash
#!/bin/bash

# 网关独立并发性能测试执行脚本

CONFIG_FILE="config.gateway_stress.json"
MOCK_BACKEND_URL="http://mock-server:8080"

echo "=== 网关独立并发性能测试 ==="

# 1. 检查 Mock 后端
echo "1. 检查 Mock 后端..."
if ! curl -s -f "$MOCK_BACKEND_URL/health" > /dev/null; then
    echo "错误：Mock 后端未启动，请先启动 Mock 服务"
    exit 1
fi
echo "✓ Mock 后端正常"

# 2. 检查网关配置
echo "2. 检查网关配置..."
GATEWAY_URL=$(jq -r '.base_url' "$CONFIG_FILE")
if ! curl -s -f "$GATEWAY_URL/health" > /dev/null; then
    echo "错误：网关不可访问"
    exit 1
fi
echo "✓ 网关正常"

# 3. 执行基线测试
echo "3. 执行基线测试..."
./run.sh --headless --users 10 --spawn-rate 1 --run-time 300s

# 4. 执行阶梯升压测试
echo "4. 执行阶梯升压测试..."
for users in 10 50 100 200 500 1000; do
    echo "测试并发数：$users"
    ./run.sh --headless --users $users --spawn-rate $users --run-time 180s
    
    # 检查错误率，超过 1% 则停止
    error_rate=$(python3 scripts/check_error_rate.py)
    if (( $(echo "$error_rate > 1" | bc -l) )); then
        echo "错误率超过 1%，停止升压"
        break
    fi
done

# 5. 生成报告
echo "5. 生成报告..."
python3 generate_gateway_report.py

echo "=== 测试完成 ==="
```

## 10. 参考资料

- [AI网关整体性能测试标准.md](./AI网关整体性能测试标准.md)：端到端性能测试标准
- [AI网关性能测试执行方案.md](./AI网关性能测试执行方案.md)：整体测试执行方案
- [配置文件使用指南.md](./配置文件使用指南.md)：配置文件详细说明
- Locust 官方文档：https://docs.locust.io/
- WireMock 官方文档：http://wiremock.org/

---

**文档版本**：v1.0
**最后更新**：2026-04-16
**维护者**：AI 网关测试团队
