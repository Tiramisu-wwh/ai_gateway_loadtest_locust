# AI 网关压测落地方案

## 1. 压测目标

本次压测不仅关注 QPS 上限，还需验证以下目标：

1. 网关是否稳定可用
2. 用户是否感知响应迅速
3. 系统在目标并发下是否能够长期稳定运行
4. 上游模型异常时，网关是否具备容错能力

## 2. 压测对象拆分

建议将 AI 网关能力拆分为以下 4 类分别压测，避免混测导致结果失真。

### 2.1 核心推理转发链路

典型接口：

- `/v1/chat/completions`
- `/v1/completions`
- `/v1/responses`

这是最核心链路。

### 2.2 流式输出链路

重点关注：

- 首 Token 时间（TTFT）
- 流式中断率
- 整体完成时间（TTLT）

### 2.3 非流式链路

重点关注：

- 总响应时间
- 错误率
- 吞吐能力

### 2.4 网关管理能力

例如：

- 鉴权
- 配额校验
- 模型路由
- 限流
- fallback

该类能力应单独压测，避免被模型推理时延掩盖。

## 3. 压测前必须确认的基线信息

正式压测前，需要补齐以下信息，否则测试结果缺乏解释力。

### 3.1 测试环境信息

- AI 网关部署节点数
- 单节点 CPU / 内存 / 带宽
- 网关实例副本数
- 上游模型类型与部署位置
- 是否走本地模型 / 云模型 / 混合路由
- 是否开启缓存、限流、重试、fallback

### 3.2 请求画像

至少需要明确：

- 平均输入 Token
- 平均输出 Token
- 流式占比
- 各模型调用占比
- 高峰并发预估
- 峰值 QPS 预估

缺少真实数据时，可先采用业务假设，例如：

- 70% 普通问答：输入 800 Tokens，输出 400 Tokens
- 20% 长文本总结：输入 6000 Tokens，输出 1200 Tokens
- 10% embeddings / rerank / 管理请求

## 4. 建议的压测指标

### 4.1 通用指标

- 请求总数
- 成功率
- 错误率
- 超时率
- 平均响应时间
- P95 / P99 响应时间
- QPS / RPS
- 并发数

### 4.2 AI 专项指标

- TTFT：首 Token 时间
- TTLT：最后 Token 完成时间
- Tokens/s：输出速率
- Token Throughput：每秒处理 Token 总量
- 流式中断率
- fallback 成功率
- 重试成功率

### 4.3 系统资源指标

- CPU
- 内存
- 网络带宽
- 连接池使用率
- 线程池活跃数
- JVM 堆 / GC（如果网关基于 Java）
- 容器重启次数
- Pod CPU throttle（如果运行于 K8s）

## 5. 压测场景设计

建议至少覆盖以下 6 类场景。

### 5.1 基线测试

目的：测单请求纯能力基线。

配置建议：

- 并发 1~5
- 持续 5~10 分钟
- 流式与非流式分别执行

观察项：

- TTFT 基线
- TTLT 基线
- 输出速度基线
- 单请求成功率

### 5.2 阶梯升压测试

目的：找到系统性能拐点。

配置建议：

- 并发按 `10 -> 20 -> 50 -> 100 -> 200 -> 300` 逐级提升
- 每级持续 10 分钟
- 每级间隔 2~3 分钟

观察项：

- 从哪一级开始 TTFT 明显抖动
- 从哪一级开始错误率上升
- 从哪一级开始 CPU / 线程池 / 连接池打满
- 从哪一级开始上游模型 429 / 5xx 增多

输出结论：

- 最大稳定并发
- 性能拐点

### 5.3 稳定性测试

目的：验证长时间运行不发生明显劣化。

配置建议：

- 目标并发取预估线上峰值并发的 70%~100%
- 持续 1 小时起步，建议 2 小时

观察项：

- P95 TTFT 是否持续升高
- 错误率是否持续爬升
- 内存是否持续上涨
- 连接是否泄漏
- 网关是否出现线程堆积

验收建议：

- 性能波动不超过 20%
- 无持续性错误积累
- 无实例异常重启

### 5.4 峰值突刺测试

目的：验证突发流量承载和保护能力。

配置建议：

- 从 50 并发瞬间拉升到 300 或 500
- 保持 3~5 分钟
- 再回落到正常值

观察项：

- 是否触发限流
- 是否出现请求堆积
- 是否发生雪崩
- 回落后是否可以恢复

重点不在于“全成功”，而在于：

- 是否可控失败
- 是否自动恢复
- 核心链路是否保住

### 5.5 混合流量测试

目的：贴近真实业务场景。

建议比例：

- 60% 流式 chat
- 20% 非流式长文本生成
- 10% embedding
- 10% 管理接口

观察项：

- 不同接口之间是否互相影响
- 管理接口是否被推理流量拖慢
- 小请求是否被大请求拖垮

### 5.6 异常容错测试

目的：验证网关容错价值。

建议模拟：

- 上游模型 429
- 上游模型 5xx
- 上游响应超时
- 某个模型实例不可用
- DNS / 网络抖动
- fallback 模型响应较慢

观察项：

- fallback 是否生效
- 重试是否合理
- 是否发生重试风暴
- 核心业务成功率是否可接受

## 6. 压测分档建议

### 6.1 按请求大小分档

| 档位 | 输入 Token | 输出 Token |
| --- | --- | --- |
| 小请求 | < 2k | < 500 |
| 中请求 | 2k ~ 8k | 500 ~ 1500 |
| 大请求 | > 8k | > 1500 |

### 6.2 按接口分档

- 流式 chat
- 非流式 chat
- embedding
- rerank
- 管理接口

### 6.3 按模型分档

- 轻量模型
- 通用大模型
- 推理型模型
- 多模态模型

## 7. 验收口径

建议将以下标准作为第一轮验收基线。

### 7.1 流式推理接口

- 成功率 >= 99.9%
- 错误率 <= 0.1%
- 超时率 <= 0.5%
- P95 TTFT <= 2s
- P99 TTFT <= 3s
- 平均输出速率 >= 15 tokens/s

### 7.2 非流式接口

- 成功率 >= 99.9%
- P95 总响应时间按 Token 档位定义
- 峰值并发下无明显错误抬升

### 7.3 稳定性

- 连续运行 1 小时以上
- P95 性能波动 <= 20%
- 无明显资源泄漏

### 7.4 容错

- 单模型故障时核心业务成功率 >= 99%
- fallback / 降级生效
- 不出现级联雪崩

## 8. 工具落地建议

### 8.1 优先推荐 Locust

原因：

- 易于构造动态请求
- 易于统计 TTFT / TTLT
- Python 写法适合 AI 接口
- 更适合流式 SSE 场景

### 8.2 JMeter 适用场景

- HTTP 基础压测
- 非流式接口
- 团队已有 JMeter 使用经验
- 快速验证 QPS / 并发能力

### 8.3 建议组合

- 流式 chat：Locust
- 非流式 / 管理接口：JMeter 或 Locust 均可
- 长期建议统一为 `Locust + Prometheus + Grafana`

## 9. Locust 落地脚本骨架

```python
from locust import HttpUser, task, between
import time
import json


class AIGatewayUser(HttpUser):
    wait_time = between(1, 3)

    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer your-token"
    }

    @task(7)
    def chat_stream(self):
        payload = {
            "model": "your-model",
            "stream": True,
            "messages": [
                {"role": "user", "content": "请总结一下AI网关性能测试的核心目标"}
            ],
            "temperature": 0.7
        }

        start = time.time()
        first_token_time = None
        token_count = 0

        with self.client.post(
            "/v1/chat/completions",
            data=json.dumps(payload),
            headers=self.headers,
            stream=True,
            catch_response=True,
            name="chat_stream"
        ) as resp:
            try:
                if resp.status_code != 200:
                    resp.failure(f"HTTP {resp.status_code}")
                    return

                for line in resp.iter_lines():
                    if not line:
                        continue

                    now = time.time()
                    if first_token_time is None:
                        first_token_time = now

                    token_count += 1

                end = time.time()

                ttft = (first_token_time - start) if first_token_time else None
                ttlt = end - start
                tps = token_count / (end - first_token_time) if first_token_time and end > first_token_time else 0

                resp.success()
                print(f"TTFT={ttft}, TTLT={ttlt}, TPS={tps}, tokens={token_count}")

            except Exception as e:
                resp.failure(str(e))

    @task(2)
    def chat_non_stream(self):
        payload = {
            "model": "your-model",
            "stream": False,
            "messages": [
                {"role": "user", "content": "请写一段100字左右的总结"}
            ]
        }

        with self.client.post(
            "/v1/chat/completions",
            data=json.dumps(payload),
            headers=self.headers,
            catch_response=True,
            name="chat_non_stream"
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(1)
    def embeddings(self):
        payload = {
            "model": "your-embedding-model",
            "input": "AI网关压测示例文本"
        }

        with self.client.post(
            "/v1/embeddings",
            data=json.dumps(payload),
            headers=self.headers,
            catch_response=True,
            name="embeddings"
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")
```

## 10. JMeter 落地建议

如果团队以 JMeter 为主，可按以下范围划分。

### 10.1 JMeter 适合压测的接口

- `/v1/chat/completions` 非流式
- `/v1/embeddings`
- `/v1/rerank`
- 管理接口

### 10.2 JMeter 不适合精细统计的指标

- 流式 TTFT
- Token 输出速率
- SSE 完整链路细粒度分析

### 10.3 JMeter 线程组建议

- 线程数：10 / 20 / 50 / 100 / 200 阶梯
- Ramp-Up：30~120 秒
- Duration：10~60 分钟
- 断言：HTTP 状态码、响应字段、超时阈值
- 聚合报告：平均值、P95、P99、错误率、吞吐量

## 11. 压测执行顺序

建议按以下顺序执行：

1. 接口可用性验证
2. 单接口基线测试
3. 单接口阶梯升压
4. 单接口稳定性测试
5. 混合流量测试
6. 异常容错测试
7. 复测与优化验证

## 12. 压测报告模板结构

每轮测试建议固定输出以下内容。

### 12.1 测试背景

- 测试目标
- 环境说明
- 接口范围
- 数据规模

### 12.2 测试配置

- 并发数
- 持续时间
- 请求比例
- 模型配置
- 压测工具

### 12.3 测试结果

- 成功率
- 错误率
- 超时率
- P95 / P99
- TTFT
- TTLT
- Tokens/s
- 最大稳定并发

### 12.4 资源监控

- CPU
- 内存
- 网络
- 线程池 / 连接池
- JVM / GC

### 12.5 问题结论

- 性能瓶颈点
- 风险点
- 优化建议
- 是否达标

