# ---------- 前端构建 (Vite) ----------
FROM node:20-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---------- Python 依赖构建 (Poetry) ----------
FROM python:3.12-slim AS builder
ENV PATH=/root/.local/bin:$PATH
RUN pip install pipx && pipx ensurepath && pipx install poetry
WORKDIR /app
COPY . .
RUN poetry config virtualenvs.in-project true && \
    poetry lock && \
    poetry install --only main && \
    rm -rf /app/.git

# ---------- 运行 ----------
FROM python:3.12-slim AS runner
WORKDIR /app
# 前端构建产物(由后端 javsp/server.py 直接托管)
COPY --from=frontend /frontend/dist /app/frontend/dist
# Python 环境与代码
COPY --from=builder /app /app
ENV JAVSP_HOST=0.0.0.0
ENV JAVSP_PORT=8000
# TrustedHost 放行: 容器外访问时 Host 头是宿主机的 IP/主机名, 而容器内无法预知,
# 故默认放开所有 Host(容器网络本身已隔离)。裸机部署(server 脚本)默认只监听 127.0.0.1,
# 且默认只放行回环地址, 需跨机访问时请显式设 JAVSP_ALLOWED_HOSTS。
ENV JAVSP_ALLOWED_HOSTS=*
EXPOSE 8000
# Web 服务入口: poetry 生成的 `server` 控制台脚本(= javsp.server:entry)
# 注意不能用 `javsp server` —— `javsp` 脚本是 CLI 入口, 不解析子命令, 会跑成扫描模式而不起 HTTP 服务
ENTRYPOINT ["/app/.venv/bin/server"]
