#!/bin/bash
# Locust 启动脚本 - 自动配置正确的 web 访问地址

locust -f locustfile.py \
  --web-host localhost \
  --web-port 8089 \
  "$@"
