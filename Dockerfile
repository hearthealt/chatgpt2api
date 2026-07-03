ARG TARGETARCH

FROM node:22-alpine AS web-build

WORKDIR /app/web-vue

COPY web-vue/package.json web-vue/package-lock.json ./
RUN npm ci

COPY VERSION /app/VERSION
COPY CHANGELOG.md /app/CHANGELOG.md
COPY web-vue ./
RUN npm run build


FROM python:3.13-slim AS app

ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    TZ=Asia/Shanghai \
    CHATGPT2API_THREAD_TOKENS=80 \
    CHATGPT2API_CONFIG_FILE=/app/data/config.json

WORKDIR /app

# 安装系统依赖
# - git: Git 存储后端需要
# - curl: 健康检查需要
# - gosu: entrypoint 修正卷属主后降权到非 root 用户
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    gosu \
    openssl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户
RUN groupadd -r chatgpt2api && useradd -r -g chatgpt2api -u 1000 chatgpt2api \
    && mkdir -p /app/data

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY main.py ./
COPY config.example.yaml ./
COPY VERSION ./
COPY api ./api
COPY services ./services
COPY utils ./utils
COPY scripts ./scripts
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY --from=web-build /app/web-vue/dist ./web_dist

RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && chown -R chatgpt2api:chatgpt2api /app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f "http://localhost:8080/health?format=json" || exit 1

# 以 root 启动，entrypoint 修正 /app/data 属主后用 gosu 降权到 chatgpt2api
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["/app/.venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--access-log"]
