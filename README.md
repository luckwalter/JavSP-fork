![JavSP](./image/JavSP.svg)

# JavSP-fork（带 WebUI 的 AV 元数据刮削器）

**汇总多站点数据的 AV 元数据刮削器，并提供了 Web 界面**

本项目基于 [Yuukiy/JavSP](https://github.com/Yuukiy/JavSP) 派生改造。原项目是一个命令行工具，本 fork 在其成熟的爬虫与元数据能力之上，**新增了 Web 界面**：你不再需要敲命令行，打开浏览器即可扫描影片目录、刮削元数据、预览信息并整理入库（生成 Emby / Jellyfin / Kodi 所需的 NFO 与封面）。

> 原项目上游声明「WebUI 不是目标」，本 fork 正是为了把它变成带界面的功能软件而存在。

![Python](https://img.shields.io/badge/python-3.10%20~%203.12-green.svg)
![License](https://img.shields.io/github/license/luckwalter/JavSP-fork)
![Version](https://img.shields.io/badge/version-0.1.16-blue.svg)

## 功能特点

- [x] 自动识别影片番号
- [x] 支持处理影片分片
- [x] 汇总多个站点的数据生成 NFO 数据文件
- [x] 多线程并行抓取
- [x] 下载高清封面
- [ ] 基于人脸检测裁剪构图异常的封面海报（**可选能力，默认关闭**，需在「设置」页开启）
- [x] 翻译标题和剧情简介
- [x] **Web 界面**：目录扫描、单部刮削、批量任务、元数据预览、一键整理
- [x] **多形态交付**：Web 服务（Docker / NAS）+ 桌面程序（PyWebView 打包 exe）
- [ ] 匹配本地字幕
- [x] 不同的运行模式（抓取数据 + 整理，仅抓取数据）：批量任务可选「仅刮削」或「刮削并整理」

其它已具备的能力：

- **并发限流**：刮削并发数可配（`crawler.max_concurrency`，默认 5），避免瞬时全开打爆出口 / 代理
- **重试指数退避**：失败重试间隔 1/2/4/8s 封顶，降低连续重试触发站点风控的概率
- **配置 Web 化**：「设置」页用分组表单直接编辑并写回 `config.yml`
- **每站点数据透传**：结果可看到各站点分别贡献了哪些字段（`sources`）
- **单一版本源**：版本号以 `pyproject.toml` 为准，界面显示的版本由后端 `/api/health` 提供
- **输出项可单独关闭**：封面 poster / fanart / 剧照 / NFO 各有开关
  （元数据交给 Jellyfin 自行刮削时可以全关，省掉下载与裁剪，整理更快）
- **封面裁剪可选 AI**：关键是**能确认它到底有没有生效**

  > 这项能力派生自上游。上游早年用的是「百度人体分析」接口，后来换成
  > 本地 `slimeface` **人脸检测**，但宣传语没跟着改；加上 `config.yml` 里
  > `crop.engine` 默认是 `null`（不启用），以及失败时会**静默**回退到默认裁剪，
  > 于是「开了 AI 裁剪」和「真的用了 AI 裁剪」在过去根本无法区分。现已修复：
  > - 「设置」页可直接开关，写回后即时生效（依赖缺失会在界面标明）；
  > - 仅对无码 / FC2 / 匹配番号规则的封面启用（`on_id_pattern` 可编辑）；
  > - 每次裁剪都会如实回报：是用上了人脸检测，还是回退了、为什么回退
  >   （依赖缺失 / 没检测到人脸 / 检测出错），整理结果与日志里都能看到。

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
- **后端 `javsp/server.py`**：实现 `pyproject.toml` 预留的 `javsp.server:entry` 入口，接口如下：
  | 接口                        | 说明                               |
  | ------------------------- | -------------------------------- |
  | `GET /api/health`         | 健康检查，同时返回服务端版本号                  |
  | `POST /api/scan`          | 扫描影片目录，返回影片列表并分配 `guid`          |
  | `GET /api/movies`         | 列出当前内存中的影片任务                     |
  | `POST /api/scrape`        | 单部刮削，SSE 推送各爬虫进度                 |
  | `POST /api/organize`      | 单部整理（NFO + 封面 + 重命名），SSE 推送进度    |
  | `POST /api/batch`         | **批量**刮削（可选附带整理），SSE 推送逐部 + 整体进度 |
  | `GET` / `PUT /api/config` | 读取 / 写回 `config.yml`（写回后即时生效，无需重启）     |
  | `GET /api/config/runtime` | 查看**运行时**实际生效的配置（含各爬虫出口的代理 / 超时），用于确认是否已生效 |
- **前端 `frontend/`**：Vue3 + Vite + Element Plus，含扫描、单部刮削、**批量任务**、设置等页面。

## 快速开始

### 方式一：Docker / NAS（推荐，最契合媒体栈）

```bash
# Dockerfile 在 docker/ 子目录, 必须带 -f
docker build -f docker/Dockerfile -t javsp-fork .
docker run -d -p 8000:8000 -v /你的/媒体库:/data javsp-fork
# 浏览器打开 http://<宿主机>:8000
```

#### 选择 NAS 上的 share 目录进行刮削 / 改名

应用层对扫描目录没有任何限制：Web 界面「扫描」页填的是任意绝对路径，后端只校验目录是否存在。因此只需把 NAS 的 share 目录用 bind mount 挂进容器，前端填**容器内的挂载路径**，即可直接刮削、重命名、生成 NFO / 封面——**无需修改任何代码或配置**。

- **挂卷**：把 NAS 真机 share 映射到容器路径（建议统一用 `/data`）。例如 QNAP 的 `/share/Multimedia/JAV`、群晖的 `/volume1/视频/JAV`：
  ```bash
  docker run -d --name javsp -p 8000:8000 \
    -v /share/Multimedia/JAV:/data javsp-fork
  ```
  嫌手敲命令麻烦可用仓库里的 `docker-compose.yml`（`docker compose up -d`），挂载写法一致。
- **前端填路径**：扫描框填容器内的 `/data`，**不要填 NAS 真机路径**（如 `/share/Multimedia/JAV`，否则报 400「目录不存在」）。bind mount 双向即时，容器内写回即落盘到 NAS。
- **权限 / 属主**：容器默认以 root（uid 0）运行，写回的文件属主会变成 root。Jellyfin / Emby **只读**这些 NFO / 封面通常没问题；若它们要回写元数据，可能因属主受限。可用 `docker run --user 1000:1000`（或 `docker-compose.yml` 里取消 `user:` 注释）对齐 NAS 媒体文件的 uid:gid。
- **落盘行为**：改名 / 移动 / NFO / 封面都落在扫描根（`/data`）之下；默认 `move_files: true` 会移到 `#整理完成/{actress}/...` 子目录，只想**原地改名 + 同级生成 NFO** 就在「设置」关掉「移动文件」。
- **其它**：小于 `scanner.minimum_size`（默认 232MiB）的文件不扫描；`hard_link` 默认关闭（同卷想省空间可开，跨文件系统会失败）；爬虫访问外站若 NAS 出口需代理，在「设置」填 `network.proxy_server`（这与拉镜像用的 squid 是两码事）。

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
2. **刮削**：单部输入番号即可刮削并预览（封面 / 女优 / genre）；批量可发起任务并实时看进度（SSE）。刮削结果会展示**每站点贡献**（哪个站点提供了封面 / 分类 / 女优），便于判断数据质量与排查单站点失效。
3. **整理**：预览确认后一键生成 NFO、下载封面并按规则重命名整理到媒体库目录。若开启了 AI 封面裁剪，结果会如实标明它是否真的用上了人脸检测（没用上会写明回退原因）。
4. **配置**：「设置」页可调整刮削源、代理、各站点免代理地址、重试次数、超时、命名规则等（`config.yml`）。
   - 保存采用**只替换变更字段**的方式写入，`config.yml` 里的中文注释与排版不会丢失。
   - 保存后**即时生效，无需重启**：会重载运行时配置，并刷新各爬虫的网络出口（代理 / 超时）。
     已在进行中的抓取任务不受影响，新请求即用新配置。
   - 若新配置无法加载，会自动回滚文件与运行时配置，不会让服务带着坏配置运行。

更详细的刮削源与命名规则见原项目 [JavSP Wiki](https://github.com/Yuukiy/JavSP/wiki)。

### 输出控制：不想生成的东西可以关掉

元数据最终交给 Jellyfin / Emby 自行刮削时，本项目再去下载高清封面（单张 8-10 MiB）、裁剪 poster、抓剧照、写 NFO 都是重复劳动。
「设置」页的**输出控制**分组可以把每一项单独关掉（写回 `config.yml` 的 `summarizer` 段）：

| 开关 | 控制什么 | 关掉之后 |
|---|---|---|
| `cover.enabled` | 竖版封面 poster | 不做裁剪也不生成 poster |
| `fanart.enabled` | 横版原图 fanart | 仍会下载封面用于裁剪 poster，生成完即删除原图 |
| `extra_fanarts.enabled` | 剧照 | 完全不抓（这一项最耗时） |
| `nfo.enabled` | NFO 文件 | 完全不写 NFO |

poster 与 fanart 同源：只要有任意一项开着才会下载封面，**两项都关时连下载都不会发生**，批量整理能明显变快。
被跳过的项目会记录在整理结果的 `skipped` 里，界面上也会提示，不会让你以为生成成功了却找不到文件。

> CLI 与 Web 共用同一份判据（`javsp/core.py` 的 `output_enabled`），命令行模式同样生效。

### 与 Jellyfin / Kodi 对接 NFO

整理后每部影片落在**独立文件夹**下（`output_folder_pattern` 默认为 `#整理完成/{actress}/[{num}] {title}`），
NFO 默认名为 `movie.nfo`，正好落在 Jellyfin 的查找规则内 —— 非混合文件夹中的电影会读取该目录下的 `movie.nfo`。

NFO 里用到的全部标签都对照 Jellyfin 的 NFO 解析器核对过（对其 **10.10.7** 本机安装的程序集取证，
并与 **12.2** 的 `BaseNfoParser` 源码复核比对），两边字段语义一致。

> **一个容易忽略的前提**：Jellyfin 需要在媒体库里**启用本地元数据读取**才会认 NFO。
> 在「管理媒体库」中把 **Nfo** 加入「本地元数据读取顺序」。缺失时不会报错，只是整份 NFO 被无视。

评分字段有个坑值得记一笔：Jellyfin 解析 `<rating>` 时**只做一次浮点转换、不做范围校验**
（12.2 才给新标签 `<communityrating>` 加了 0~10 校验）。爬虫一旦漏做量纲换算，界面会原样显示越界值。
本项目在写入 NFO 时会把评分规范化到 0~10，越界则钳制并记 warning 便于回查上游爬虫。

## 版本规则

本项目**不沿用上游版本号**，从 `0.0.1` 起步：

- 小变更（修补 / 微调）：第三位 +1 → `0.0.2`、`0.0.3`…
- 大变更（功能 / 架构改动）：第二位 +1 且第三位归 1 → `0.1.1`、`0.2.1`…（跳过 `.0` 结尾）
- 正式稳定版：`1.0.0`

当前版本：**0.1.16**

> 完整的版本迭代记录与问题修复见 **[CHANGELOG\_FORK.md](./CHANGELOG_FORK.md)**（本 fork 独立维护，不覆盖上游 `CHANGELOG.md`）。

### 发版清单

版本号以 `pyproject.toml` 为准，**发版时以下四处需一起更新**（漏改就会出现版本漂移）：

| 文件                           | 位置                               | 同步方式                          |
| ---------------------------- | -------------------------------- | ----------------------------- |
| `pyproject.toml`             | `version` 字段                     | 手动改（唯一权威来源）                   |
| `frontend/package.json`      | `version`                        | `python sync_version.py` 自动同步 |
| `frontend/package-lock.json` | `version`、`packages[""].version` | 同上                            |
| `README.md`                  | 版本徽章、`当前版本：**x.y.z**`            | 同上                            |
| `CHANGELOG_FORK.md`          | 新增版本条目                           | 手动补                           |

```bash
python sync_version.py          # 写入同步（package.json / package-lock.json / README.md）
python sync_version.py --check  # 只校验，任何一处不一致则以退出码 1 报错
```

## 与原项目的关系

- 派生自 [Yuukiy/JavSP](https://github.com/Yuukiy/JavSP)，保留其全部爬虫与元数据能力。
- 新增 `javsp/core.py`、`javsp/server.py`、`javsp/desktop.py`、`frontend/` 等 Web 层代码；`upstream` 仍指向原仓库，便于后续同步上游更新。
- 配置格式沿用原 `config.yml`，CLI 入口 `javsp` 行为不变。

## 开发

仓库根目录附带若干**纯逻辑验证脚本**（不联网，可直接运行），改动后跑一遍可快速自查：

| 脚本                          | 覆盖内容                                       |
| --------------------------- | ------------------------------------------ |
| `verify_scrape_refactor.py` | 刮削打磨项：并发限流 / 重试退避 / genre 合并 / 封面容错 / 站点透传 |
| `verify_batch_e2e.py`       | 批量端到端：合成片源 + mock 爬虫，覆盖扫描 → 批量刮削 → 整理落盘    |
| `verify_sources_e2e.py`     | 每站点贡献：SSE 透传结构 + 前端转换函数（直接从 App.vue 源码提取求值，防测试与实现漂移） |
| `verify_config_io.py`       | 配置写回：保注释（标量 / 整数 / 嵌套 / 单行列表 / 多行列表）+ 换行符 + API 端到端        |
| `verify_config_reload.py`   | 配置热重载：运行时跟随 + 爬虫出口刷新 + 超时下限保留 + 非法配置自动回滚                 |
| `verify_cropper.py`         | 封面 AI 裁剪：开关写回(保注释) / 人脸检测生效与四类回退上报 / 整理结果透出 / 接口与前端判定     |
| `verify_output_toggles.py`  | 输出开关：封面 poster/fanart 四种组合的实际落盘 / 剧照 / NFO 开关 / 旧配置向后兼容 / CLI 与 Web 共用判据 |
| `verify_k4.py`              | 历史 bug 回归守卫（`all_info` 键名切片）               |
| `verify_robust.py`          | 历史 bug 回归守卫（爬虫加载健壮性）                       |

## 问题反馈

使用中遇到 Bug，欢迎在本仓库 [Issue 区反馈](https://github.com/luckwalter/JavSP-fork/issues)。

## 许可

本项目的所有权利与许可受 GPL-3.0 License 与 [Anti 996 License](https://github.com/996icu/996.ICU/blob/master/LICENSE_CN) 共同限制。此外，如果你使用此项目，表明你还额外接受以下条款：

- 本软件仅供学习 Python 和技术交流使用
- 请勿在微博、微信等墙内的公共社交平台上宣传此项目
- 用户在使用本软件时，请遵守当地法律法规
- 禁止将本软件用于商业用途
