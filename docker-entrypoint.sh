#!/usr/bin/env bash
set -euo pipefail

# 数据目录（config.json、数据库、日志、图片等都落在这里）
DATA_DIR="${CHATGPT2API_DATA_DIR:-/app/data}"
CONFIG_FILE="${CHATGPT2API_CONFIG_FILE:-${DATA_DIR}/config.json}"

# 容器以 root 启动：确保挂载卷可写，再降权到非 root 用户 chatgpt2api 运行。
# 若已经是非 root（例如 compose 里显式指定了 user:），则直接执行。
if [ "$(id -u)" = "0" ]; then
    mkdir -p "${DATA_DIR}"

    # 首次启动时数据目录属主可能是 root（bind mount 由 docker 以 root 创建），
    # 递归修正一次；后续启动 top 目录已是目标用户则跳过，避免大目录反复 chown。
    if [ "$(stat -c '%U' "${DATA_DIR}" 2>/dev/null || echo unknown)" != "chatgpt2api" ]; then
        chown -R chatgpt2api:chatgpt2api "${DATA_DIR}" 2>/dev/null || true
    fi

    # config.json 可能由其它 root 容器（如 warp 的 init-config）写入，
    # 每次启动都单独修正它的属主，保证 app 能写回配置。
    if [ -e "${CONFIG_FILE}" ]; then
        chown chatgpt2api:chatgpt2api "${CONFIG_FILE}" 2>/dev/null || true
    fi

    exec gosu chatgpt2api "$@"
fi

exec "$@"
