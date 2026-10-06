![JavSP](./image/JavSP.svg)

# JavSP-fork（带 WebUI 的 AV 元数据刮削器）

**汇总多站点数据的 AV 元数据刮削器，并提供了 Web 界面**

本项目基于 [Yuukiy/JavSP](https://github.com/Yuukiy/JavSP) 派生改造。原项目是一个命令行工具，本 fork 在其成熟的爬虫与元数据能力之上，**新增了 Web 界面**：你不再需要敲命令行，打开浏览器即可扫描影片目录、刮削元数据、预览信息并整理入库（生成 Emby / Jellyfin / Kodi 所需的 NFO 与封面）。

> 原项目上游声明「WebUI 不是目标」，本 fork 正是为了把它变成带界面的功能软件而存在。

![Python 3.10](https://img.shields.io/badge/python-3.10-green.svg)
![License](https://img.shields.io/github/license/luckwalter/JavSP-fork)
![Version](https://img.shields.io/badge/version-0.1.1-blue.svg)

## 功能特点

- [x] 自动识别影片番号
- [x] 支持处理影片分片
- [x] 汇总多个站点的数据生成 NFO 数据文件
- [x] 多线程并行抓取
- [x] 下载高清封面
- [x] 基于 AI 人体分析裁剪素人等非常规封面的海报
- [x] 翻译标题和剧情简介
- [x] **Web 界面**：目录扫描、单部刮削、批量任务、元数据预览、一键整理
- [x] **多形态交付**：Web 服务（Docker / NAS）+ 桌面程序（PyWebView 打包 exe）
- [ ] 匹配本地字幕
- [ ] 不同的运行模式（抓取数据 + 整理，仅抓取数据）

## 架构

```
┌─────────────┐     HTTP / SSE     ┌──────────────────┐   复用   ┌────────────────┐
│  Web 前端    │ ───────────────▶  │  FastAPI 后端     │ ──────▶ │  爬虫层         │
│ Vue3+Element │ ◀───────────────  │  javsp/server.py  │         │  javsp/web/*   │
│  (frontend/) │   进度/结果 JSON   │  (javsp.server:entry)│      │  番号→MovieInfo │
└─────────────┘                    └──────────────────┘         └────────────────┘
                                          │
                                   ┌──────┴──────┐
                              PyWebView 桌面壳   Docker 多阶段构建
                              (javsp/desktop.py) (docker/Dockerfile)
```

- **爬虫层 `javsp/web/*`**：原项目纯逻辑（番号 → `MovieInfo`），零改动直接复用，是后端最稳的底座。
- **核心层 `javsp/core.py`**：从 CLI 抽出的编排逻辑，带进度回调，对外暴露 `scrape_movie()` / `organize_movie()` / `preview_metadata()` / `scan_library()`，CLI 与 Web 共用。
- **后端 `javsp/server.py`**：实现 `pyproject.toml` 预留的 `javsp.server:entry` 入口，提供 `/api/scan`、`/api/scrape`（SSE 进度）、`/api/organize`、`/api/config` 等接口。
- **前端 `frontend/`**：Vue3 + Vite + Element Plus，含扫描、单部刮削、设置等页面。

## 快速开始

### 方式一：Docker / NAS（推荐，最契合媒体栈）

```bash
docker build -t javsp-fork .
docker run -d -p 8000:8000 -v /你的/媒体库:/data javsp-fork
# 浏览器打开 http://<宿主机>:8000
```

> 多阶段构建会自动 `npm run build` 前端并托管 `frontend/dist`。

### 方式二：本地开发运行（Web 服务）

```bash
# 1. 安装 Python 依赖
pip install -e .

# 2. 构建前端
cd frontend && npm install && npm run build && cd ..

# 3. 启动服务
javsp server
# 或 python -m javsp server
# 浏览器打开 http://127.0.0.1:8000
```

### 方式三：桌面程序（PyWebView 打包 exe）

```bash
pip install pywebview
python -m javsp.desktop     # 启动内嵌浏览器窗口，自动拉起本地服务
# 用 PyInstaller 可打包为独立 exe
```

### 原命令行方式（仍可用）

```bash
javsp -h                    # 查看原 CLI 参数（逻辑已抽到 core，行为不变）
```

## 使用

1. **扫描**：Web 界面「扫描」页填入影片目录，列出待处理影片。
2. **刮削**：单部输入番号即可刮削并预览（封面 / 女优 / genre）；批量可发起任务并实时看进度（SSE）。
3. **整理**：预览确认后一键生成 NFO、下载封面并按规则重命名整理到媒体库目录。
4. **配置**：「设置」页可调整刮削源、代理、命名规则等（`config.yml`）。

更详细的刮削源与命名规则见原项目 [JavSP Wiki](https://github.com/Yuukiy/JavSP/wiki)。

## 版本规则

本项目**不沿用上游版本号**，从 `0.0.1` 起步：

- 小变更（修补 / 微调）：第三位 +1 → `0.0.2`、`0.0.3`…
- 大变更（功能 / 架构改动）：第二位 +1 且第三位归 1 → `0.1.1`、`0.2.1`…（跳过 `.0` 结尾）
- 正式稳定版：`1.0.0`

当前版本：**0.1.1**（WEBUI 骨架：核心层解耦 + FastAPI 后端 + Vue 前端 + 桌面壳 + Docker 多阶段）。

## 与原项目的关系

- 派生自 [Yuukiy/JavSP](https://github.com/Yuukiy/JavSP)，保留其全部爬虫与元数据能力。
- 新增 `javsp/core.py`、`javsp/server.py`、`javsp/desktop.py`、`frontend/` 等 Web 层代码；`upstream` 仍指向原仓库，便于后续同步上游更新。
- 配置格式沿用原 `config.yml`，CLI 入口 `javsp` 行为不变。

## 问题反馈

使用中遇到 Bug，欢迎在本仓库 [Issue 区反馈](https://github.com/luckwalter/JavSP-fork/issues)。

## 许可

本项目的所有权利与许可受 GPL-3.0 License 与 [Anti 996 License](https://github.com/996icu/996.ICU/blob/master/LICENSE_CN) 共同限制。此外，如果你使用此项目，表明你还额外接受以下条款：

- 本软件仅供学习 Python 和技术交流使用
- 请勿在微博、微信等墙内的公共社交平台上宣传此项目
- 用户在使用本软件时，请遵守当地法律法规
- 禁止将本软件用于商业用途
