# ---------- 前端构建 (Vite) ----------
FROM node:20-slim AS frontend
WORKDIR /frontend
# 必须同时 COPY package.json 与 package-lock.json:
# 只拷 package.json 时 npm 会重新解析依赖, 装到 rollup 最新版(>=4.65), 而 rollup 4.65+
# 的原生模块(@rollup/rollup-linux-x64-gnu)需要 AVX2 指令, 在无 AVX2 的宿主 CPU
# (如 Xeon E3-1230 V2 / 部分 NAS)上执行即 "Illegal instruction (core dumped)"(exit 132),
# 导致 vite build 失败。带上 lock 可复现依赖版本, 配合 package.json 的 overrides
# 把 rollup 钉在 4.64.0(最后一个不依赖 AVX2 的版本)。
COPY frontend/package.json frontend/package-lock.json ./
# npm install 后校验 esbuild 原生二进制可用性(装包经代理时偶发下载损坏),
# 不可用则清缓存重装一次, 避免整轮 build 失败。
RUN npm install && \
    (node_modules/.bin/esbuild --version >/dev/null 2>&1 || \
     (echo "esbuild binary broken, reinstalling..." && \
      npm cache clean --force && rm -rf node_modules && npm install))
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
# 目录选择器的默认根。与 docker-compose.yml 里的挂载点 `/share/xxx:/data` 对齐,
# 使「浏览目录…」开箱即用(不必先手动填路径); 用户挂载到别的位置时覆盖此变量即可。
# 不设则退化为 '/' (不限制), 与原先手输路径的能力一致。
ENV JAVSP_BROWSE_ROOT=/data
EXPOSE 8000
# Web 服务入口: poetry 生成的 `server` 控制台脚本(= javsp.server:entry)
# 注意不能用 `javsp server` —— `javsp` 脚本是 CLI 入口, 不解析子命令, 会跑成扫描模式而不起 HTTP 服务
ENTRYPOINT ["/app/.venv/bin/server"]
