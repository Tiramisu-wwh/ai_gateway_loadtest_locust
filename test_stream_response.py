#!/usr/bin/env python3
"""测试流式响应的实际内容"""

import json
import requests
import time

# 从config.json读取配置
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

url = f"{config['base_url']}{config['paths']['responses']}"

payload = {
    "model": config["responses_model"],
    "input": [
        {"role": "user", "content": "1+1等于几？"}
    ],
    "reasoning": {"effort": "low"},
    "stream": True
}

print(f"请求URL: {url}")
print(f"请求体: {json.dumps(payload, ensure_ascii=False)}")
print("\n开始发送流式请求...\n")

start_time = time.time()
chunk_count = 0
last_3_chunks = []

response = requests.post(
    url,
    headers={
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json"
    },
    json=payload,
    stream=True,
    timeout=180
)

print(f"响应状态码: {response.status_code}")
print("\n流式响应内容:\n")

for line in response.iter_lines():
    if not line:
        continue

    chunk_count += 1
    line_str = line.decode('utf-8')

    # 解析SSE格式
    if line_str.startswith('data: '):
        data_str = line_str[6:]  # 去掉 'data: ' 前缀

        if data_str.strip() == '[DONE]':
            print(f"\n[Chunk #{chunk_count}] [DONE]")
            break

        try:
            data = json.loads(data_str)
            data_preview = json.dumps(data, ensure_ascii=False)

            # 打印前3个chunk的完整内容
            if chunk_count <= 3:
                print(f"\n[Chunk #{chunk_count}] 完整内容:")
                print(data_preview)

            # 保存最后3个chunk
            last_3_chunks.append((chunk_count, data))
            if len(last_3_chunks) > 3:
                last_3_chunks.pop(0)

        except json.JSONDecodeError as e:
            print(f"[Chunk #{chunk_count}] JSON解析错误: {e}")
            print(f"原始内容: {data_str[:200]}")

end_time = time.time()
duration = end_time - start_time

print(f"\n\n=== 最后3个chunk ===")
for chunk_num, data in last_3_chunks:
    print(f"\n[Chunk #{chunk_count}]:")
    print(json.dumps(data, ensure_ascii=False, indent=2))

print(f"\n\n=== 统计信息 ===")
print(f"总chunk数: {chunk_count}")
print(f"总耗时: {duration:.2f}秒")
print(f"平均每chunk: {duration/chunk_count*1000:.2f}ms")

# 检查最后几个chunk是否包含usage
print(f"\n=== 检查usage信息 ===")
for chunk_num, data in last_3_chunks:
    if "usage" in data:
        print(f"[Chunk #{chunk_num}] 包含usage:")
        print(json.dumps(data["usage"], indent=2))
    else:
        print(f"[Chunk #{chunk_num}] 不包含usage")
