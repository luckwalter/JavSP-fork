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
EXPOSE 8000
ENTRYPOINT ["/app/.venv/bin/javsp"]
CMD ["server"]
