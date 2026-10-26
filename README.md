![JavSP](./image/JavSP.svg)

> 语言 / Language: **[English](README.en.md)** | [简体中文](README.md)

# JavSP-fork（带 WebUI 的 AV 元数据刮削器）

**汇总多站点数据的 AV 元数据刮削器，并提供 Web 界面**

> **本项目是 [Yuukiy/JavSP](https://github.com/Yuukiy/JavSP) 的派生分支。** 原项目是一个非常优秀的命令行刮削器，但自 **2025 年 2 月起已停止更新**（大量 issue 与 PR 无人处理，多个站点改版后失效无人修复）。本 fork 完整保留它的爬虫与元数据能力，并补上了社区呼声最高的**图形界面**，同时持续跟进站点适配 —— 详细差异见**[与原项目的关系](#与原项目的关系)**。

本项目基于 [Yuukiy/JavSP](https://github.com/Yuukiy/JavSP) 派生改造。原项目是一个命令行工具，本 fork 在其成熟的爬虫与元数据能力之上，**新增了 Web 界面与桌面程序**：打开浏览器（或桌面窗口）即可扫描影片目录、刮削元数据、预览信息并整理入库（生成 Emby / Jellyfin / Kodi 所需的 NFO 与封面）。

代码**完全开源**（GPL-3.0），Docker 镜像与 Windows exe 均提供本地构建脚本，可自行审阅和打包。

![Python](https://img.shields.io/badge/python-3.10%20~%203.12-green.svg)
![License](https://img.shields.io/github/license/luckwalter/JavSP-fork)
![Version](https://img.shields.io/badge/version-0.4.7-blue.svg)

> **📌 v0.4.0 起支持软件更新检查**：启动时后台检测新版本并提示（网络不可达时静默跳过，不拖慢启动）；桌面版可一键下载并自动完成升级（程序自动重启，配置与会话完整保留），Docker 部署会给出对应的镜像更新命令。设置页「软件更新」可手动检查与开关。

## 功能特点

- 自动识别影片番号，支持处理影片分片
- 汇总多个站点的数据生成 NFO，多线程并行抓取，下载高清封面
- **标题与剧情简介翻译**：支持百度 / 必应 / Google / Claude / OpenAI 兼容（含本地 Ollama）五类引擎，目标语种可选 7 种
- **译文可审阅再落盘**：刮削完成后在「结果审阅」页逐部对照原文与译文，满意后再整理；原文已是目标语种时自动跳过翻译，NFO 全文用字统一（简繁不混用）
- **Web 界面**：目录扫描、单部查询、批量任务（可选附带整理）、元数据预览、一键整理
- **每站点数据透传**：结果可看到各站点分别贡献了哪些字段，便于判断数据质量与排查单站点失效
- **审阅页渠道封面缩略图**：每个有贡献的渠道前并排显示该站自己的封面小图，一眼比对「哪个源给的图更好看」；图片本地缓存（缩放到 120px 宽，单张仅几 KB），可在「软件更新」页手动清除或按天数自动清理
- **渠道健康监控与自动熔断**：死源自动从刮削中摘掉，不再白等
- **配置 Web 化**：「设置」页用分组表单直接编辑并写回 `config.yml`，保存后即时生效、保留中文注释
- **输出项可单独关闭**：封面 poster / fanart / 剧照 / NFO 各有开关（交给 Jellyfin 自行刮削时可全关，整理更快）
- **封面 AI 裁剪可选**：本地 `slimeface` 人脸检测，且能确认它到底有没有生效（用上了 / 回退了 / 为什么回退都会如实回报）
- **命名规则可视化构建器**：点选变量即时预览命名结果，支持深色模式
- **内置软件更新**：启动时后台检查新版本，桌面版可一键下载并完成升级（保留配置与会话、自动重启），Docker 部署给出对应的镜像更新命令
- **多形态交付**：Web 服务（Docker / NAS）+ Windows 桌面程序（exe，默认 GUI 窗口壳 / `--nogui` 浏览器）
- **可选单用户登录**：设置 `JAVSP_AUTH_PASSWORD` 即启用，未设置则免登录（默认）

内置 **19 个刮削源**（`javsp/web/*`，设置页可勾选启用）：airav、avsox、avwiki、dl_getchu、fanza、fc2、fc2ppvdb、gyutto、jav321、javbus、javdatabase、javdb、javdbapi、javfree、javlib、javmenu、mgstage、njav、prestige。其中 `javdbapi` / `javdatabase` / `javmenu` / `javfree` / `gyutto` / `njav` 为本 fork 移植增强。

## 架构

```
              ┌──────────────────────────────────────────────┐
              │           前端资产 frontend/dist               │
              │      Vue3 + Vite · 自建设计系统(无 UI 框架)     │
              └───────────────────────┬──────────────────────┘
                                      │ 两种形态共同加载
        ┌─────────────────────────────┴─────────────────────────────┐
        │                                                           │
┌───────┴────────┐                                    ┌─────────────┴───────┐
│ ① Docker / NAS  │                                    │ ② Windows 桌面 exe   │
│ docker/Dockerfile│                                   │ PyInstaller onedir   │
│ 多阶段构建镜像   │                                    │ JavSP.exe 桌面壳     │
│ 容器内 uvicorn   │                                    │ 默认 pywebview 窗口  │
└───────┬────────┘                                    └─────────────┬───────┘
        │              同一 FastAPI 后端(server.py:entry)            │
        └──────────────────────────┬─────────────────────────────────┘
                                   ▼
        ┌──────────────────────────────────────────────┐
        │  FastAPI 后端 javsp/server.py                 │◀── HTTP/SSE ──▶ 浏览器
        │  鉴权 auth.py · 配置 Web 化写回与热重载        │
        └──────────────────────┬───────────────────────┘
                               │ 调用
        ┌──────────────────────┴───────────────────────┐
        │  核心编排 javsp/core.py                       │ CLI / Web / 桌面共用
        │  scrape_movie() / organize_movie() / ...     │
        └──────────────────────┬───────────────────────┘
                               │
        ┌──────────────────────┴───────────────────────┐
        │  爬虫层 javsp/web/*                           │ 19 源 + health.py 熔断
        │  番号 → MovieInfo                             │
        └──────────────────────────────────────────────┘

Release 双资产：javsp-fork-<ver>-image.tar.gz（docker load 即用）
              ＋ JavSP-<ver>-windows.zip（解压双击即用）
```

- **爬虫层 `javsp/web/*`**：番号 → `MovieInfo`，上游纯逻辑底座 + fork 移植增强，是最稳的根基。
- **核心层 `javsp/core.py`**：从 CLI 抽出的编排逻辑，带进度回调，对外暴露 `scrape_movie()` / `organize_movie()` / `scan_library()`，CLI 与 Web 共用。
- **后端 `javsp/server.py`**：FastAPI + SSE，主要接口：
  | 接口 | 说明 |
  |---|---|
  | `GET /api/health` | 健康检查，同时返回服务端版本号 |
  | `GET /api/channels` | 列出所有刮削源及其健康状态（是否熔断、命中率等） |
  | `POST /api/channels/check` | 手动触发指定渠道探活 |
  | `GET /api/browse` | 「浏览目录…」列出子目录（受 `JAVSP_BROWSE_ROOT` 限制） |
  | `GET /api/auth/status` · `POST /api/auth/login` · `POST /api/auth/logout` | 可选单用户登录（未设密码时全部放行） |
  | `POST /api/scan` | 扫描影片目录，返回影片列表并分配 `guid` |
  | `GET /api/movies` | 列出当前内存中的影片任务 |
  | `POST /api/scrape` | 单部刮削，SSE 推送各爬虫进度 |
  | `POST /api/organize` | 单部整理（NFO + 封面 + 重命名），SSE 推送进度 |
  | `POST /api/batch` | **批量**刮削（可选附带整理），SSE 推送逐部 + 整体进度 |
  | `GET` / `PUT /api/config` | 读取 / 写回 `config.yml`（写回后即时生效，无需重启） |
  | `GET /api/config/runtime` | 查看运行时实际生效的配置（含各爬虫出口的代理 / 超时） |
  | `GET /api/update/check` | 检查是否有新版本（后台静默轮询，失败不影响使用） |
  | `POST /api/update/download` | 下载更新包（**含 sha256 校验**，不符即删包中止） |
  | `POST /api/update/apply` | 启动升级助手并退出主进程，由助手完成替换与重启 |
  | `GET /api/thumb?url=…` | 取某张封面的缩略图（命中缓存直返，未命中则下载原图缩放落盘） |
  | `GET /api/cache/thumbs` | 缩略图缓存统计（张数 / 占用 / 目录 / 自动清理设置） |
  | `DELETE /api/cache/thumbs` | 手动清空缩略图缓存 |
- **前端 `frontend/`**：Vue3 + Vite，自建设计系统（0.2.x 起移除 Element Plus，手写组件 + 设计令牌 CSS），共 7 个页面：媒体库（扫描）、单部查询、刮削队列、结果审阅、批量整理、刮削源（渠道监控）、设置；含命名规则构建器、登录页、深色模式与更新提示条。
- **渠道健康 `javsp/web/health.py`**：后台探活 + 熔断/半开恢复，详见下文。
- **鉴权 `javsp/auth.py`**：可选单用户登录，会话落盘、登录锁定。

## 快速开始

### 方式一：Docker / NAS（推荐，最契合媒体栈）

```bash
# Dockerfile 在 docker/ 子目录, 必须带 -f
docker build -f docker/Dockerfile -t javsp-fork .
docker run -d -p 8000:8000 -v /你的/媒体库:/data javsp-fork
# 浏览器打开 http://<宿主机>:8000
```

- **刮削 NAS 的 share 目录**：应用层对扫描目录无限制，把 NAS 真机 share 用 bind mount 挂进容器（建议统一 `/data`），前端填**容器内挂载路径**即可直接刮削、改名、生成 NFO / 封面，无需改代码。扫描框左侧有「浏览目录…」按钮可逐级选择（只列目录），镜像已内置 `JAVSP_BROWSE_ROOT=/data` 收敛可浏览范围。
- **⚠️ 配置持久化（必须）**：`config.yml` 决定代理、刮削源、命名规则、扫描目录。不持久化会在容器重建/升级时**静默丢失**全部设置。compose 里须同时挂载配置目录并告知程序：
  ```yaml
  volumes:
    - /share/javsp_config:/etc/javsp        # ① 挂载配置目录
  command: ["-c", "/etc/javsp/config.yml"]   # ② 告诉程序读这个文件
  ```
  首次部署先建目录并放入默认配置（`docker run --rm ... cp /app/config.yml /etc/javsp/config.yml`）。初始化后建议先在「设置」配好网络代理并确认保存提示「已写入 config.yml … 即时生效」再开始刮削。
- **网络代理**：各站点统一走 `network.proxy_server` 全局代理（`proxy_free` 按站免代理地址已移除），出口被封锁时请更换代理节点。若代理做 TLS 解密（MITM），默认校验会报 `SSLCertVerificationError`，本项目**不默认关闭校验**，而是支持挂载代理根证书：设 `JAVSP_CA_BUNDLE=/certs/proxy-ca.crt` 并挂载该证书（校验仍开启）。
- **排查「只有个别站点抓得到」**：多数不是故障。容器内直接跑爬虫看真实异常：`MovieNotFoundError`=该站未收录此番号（正常）；`403`=需登录/反爬；`SSLCertVerificationError`=MITM 代理缺 CA；`Fail to connect`=出口不可达或 IP 被墙。
- 容器默认 root 运行，写回文件属主为 root（Jellyfin/Emby 只读通常无碍，需回写时用 `--user 1000:1000` 对齐）。小于 `scanner.minimum_size`（默认 232MiB）的文件不扫描。

### 方式二：本地开发运行（Web 服务）

```bash
pip install -e .                                  # 安装依赖
cd frontend && npm install && npm run build && cd ..   # 构建前端
javsp server                                      # 或 python -m javsp server
# 浏览器打开 http://127.0.0.1:8000
```

### 方式三：桌面程序（Windows 本机 exe，开箱即用）

下载 Release 里的 `JavSP-<version>-windows.zip`，解压到任意目录，**双击 `JavSP.exe`** 即可：默认开一个原生窗口加载 WebUI（pywebview）；想用浏览器打开则加 `--nogui`。所有配置/会话都落在 **exe 同级目录**（便携，不写系统盘）；自 0.3.2 起配置文件为 exe 同级 `config.yml`，整包升级不会覆盖你的配置。

> **应用内升级**：启动后设置页「软件更新」→「立即升级」即可下载并完成升级，程序自动重启，`config.yml` / 会话 / 日志完整保留，下载包带 **sha256 校验**。
> ⚠️ **v0.4.5 之前的所有版本都带有一个升级缺陷**（升级助手的工作目录落在待替换目录内部，导致目录改名失败），从这些版本点「立即升级」会失败——请先下载 `JavSP-0.4.5-windows.zip` **手动覆盖**一次，之后应用内升级功能即恢复正常。

> **Windows 10 必看**：默认 GUI 壳依赖系统 **Microsoft Edge WebView2 运行时**。Win11 系统自带；**Win10 并不保证自带**（Edge 被卸载 / 长期未更新 / 企业策略禁用 Edge 的机器上就没有）。缺失时旧版本会退回 IE 内核渲染，而本项目前端是 Vue3 现代产物 → 表现为**双击后空白窗口**。
>
> 解决办法任选其一：
> 1. 安装 WebView2 Runtime（约 130MB，一次性）：https://developer.microsoft.com/microsoft-edge/webview2/ ——装完重新双击即可；
> 2. 或走浏览器模式：`JavSP.exe --nogui` 让服务在后台运行，再用 **Chrome / Edge** 打开 `http://127.0.0.1:8000`（功能与窗口模式完全一致）。
>
> 自 **v0.3.11** 起，程序会在开窗前自检 WebView2 与 .NET Framework，缺失时**弹窗明确告知原因并自动切换到浏览器模式**，不再出现「双击没反应 / 白屏」；渲染层日志同时写入 exe 同级 `JavSP.log`，排查问题可直接查看。

自行构建（需在已装项目依赖的 Python 3.10~3.12 环境中运行）：

```bash
python tools/build_desktop.py          # 一键：前端 build → PyInstaller 打包 → 生成 zip
# 仅验证桌面端逻辑（不打包）：
python -m javsp.desktop                # 默认 GUI 窗口
python -m javsp.desktop --nogui        # 用浏览器打开 WebUI
```

### 原命令行方式（仍可用）

```bash
javsp -h        # 原 CLI 参数（逻辑已抽到 core，行为不变）
```

## 使用

1. **扫描**：「扫描」页填入影片目录，列出待处理影片。
2. **刮削**：单部输入番号即可刮削并预览；批量可发起任务并实时看进度（SSE）。结果会展示**每站点贡献**，便于判断数据质量与排查单站点失效。
3. **整理**：预览确认后一键生成 NFO、下载封面并按规则重命名整理到媒体库目录（默认移到 `#整理完成/{actress}/...`，可关）。
4. **配置**：「设置」页调整刮削源、代理、重试/超时、命名规则、输出开关等。保存采用**只替换变更字段**写入，中文注释与排版不丢失；保存后**即时生效、无需重启**（重载运行时配置并刷新各爬虫网络出口），坏配置会自动回滚。

更详细的刮削源说明可参考原项目 [JavSP Wiki](https://github.com/Yuukiy/JavSP/wiki)；但命名规则的**变量请以本项目「设置」页的构建器为准**（写法与上游不同，见[迁移注意](#从原项目迁移过来请注意)）。

### 输出控制：不想生成的东西可以关掉

元数据交给 Jellyfin / Emby 自行刮削时，再去下载高清封面、裁剪 poster、抓剧照、写 NFO 都是重复劳动。「设置」页可逐项关闭：

| 开关 | 控制什么 | 关掉之后 |
|---|---|---|
| `cover.enabled` | 竖版封面 poster | 不做裁剪也不生成 poster |
| `fanart.enabled` | 横版原图 fanart | 仍会下载封面用于裁剪 poster，生成完即删除原图 |
| `extra_fanarts.enabled` | 剧照 | 完全不抓（最耗时的一项） |
| `nfo.enabled` | NFO 文件 | 完全不写 NFO |

poster 与 fanart 同源，两项都关时连封面下载都不会发生，批量整理明显变快。

### 审阅页的渠道封面缩略图：并排比一比哪个源给的图更好

「结果审阅」页里，每个**有贡献**的渠道前面会显示一张该站自己的封面小图，一眼就能看出哪个源给的图更清晰、构图更好——不必等到整理落盘后才发现选错了封面。

- **本地缓存**：原图下载后缩到 120px 宽存为 jpg（单张仅几 KB），同一张图只下载一次；缩略图是懒加载的，页面滚动到哪才拉哪。
- **缓存位置**：容器形态放在 `/data/.javsp_cache/thumbs`（点开头的隐藏目录，媒体服务器默认跳过，不会污染媒体库索引）；桌面版放在 exe 同级 `cache/thumbs`。都可在「设置 → 软件更新」页的「封面缓存」区显式指定。
- **清理**：同一区域提供缓存统计（张数 / 占用 / 目录）、「立即清除全部缓存」按钮，以及「自动清理过期缓存」开关与保留天数（默认 7 天，启动时清理超期文件）。**清理只涉及缩略图缓存，绝不会删除已整理到媒体库的封面文件。**
- **取不到也不影响**：某个源取图失败只会显示占位块，刮削与审阅流程照常进行。

### 渠道监控与熔断：死源不再白等

受限出口下站点会陆续失效。本项目「渠道监控」页告诉你**现在还有哪些源真的能用**，并**自动把死源从刮削里摘掉**。

| 机制 | 行为 |
|---|---|
| 判定标准 | 走真实 `parse_data` 并要求解析出有效标题（很多死站返回 200 + 壳页，只看传输层会误判） |
| 触发阈值 | 连续失败 2 次才熔断（单次失败常只是网络抖动，不误杀好源） |
| 熔断后 | 刮削直接跳过该源，不再发起请求；被跳过的源仍显示在结果里并标为「已跳过」 |
| 自动恢复 | 冷却 10s → 20s → 40s …（上限 5 分钟），到点半开试探：成功则恢复，失败则重新熔断 |
| 不熔断的情况 | 未收录该番号、内容解析异常、结果重复——说明源本身是好的 |
| 检测方式 | 后台每 5 分钟自动探活（可关/手动），且每次刮削成败也会汇入健康档案（零额外请求） |

页面每行显示：状态、是否参与刮削、耗时、近期命中率、站点域名、失败原因、剩余冷却。CLI 与 Web 共用同一份判据。

### 与 Jellyfin / Kodi 对接 NFO

整理后每部影片落在**独立文件夹**下（`output_folder_pattern` 默认为 `#整理完成/{actress}/[{num}] {title}`），NFO 默认名为 `movie.nfo`，正好落在 Jellyfin 的查找规则内（非混合文件夹中的电影会读取该目录下的 `movie.nfo`）。

> **容易忽略的前提**：Jellyfin 需在媒体库**启用本地元数据读取**才会认 NFO（「管理媒体库」把 **Nfo** 加入「本地元数据读取顺序」）。缺失时不报错，只是整份 NFO 被无视。

写入 NFO 时会把评分规范化到 0~10（Jellyfin 解析 `<rating>` 只做一次浮点转换、不做范围校验，越界值会原样显示）。

### 翻译引擎：标题与剧情简介的自动翻译

整理时可将日文标题与剧情简介翻译为中文（写入 NFO 与文件名），在「系统设置 → 翻译」中配置。

**可用引擎**：百度翻译 / 必应翻译 / Google 翻译 / Claude / **OpenAI 兼容**（端点、模型名、密钥自填，可直连本地 [Ollama](https://ollama.com/) / LM Studio 等任意 OpenAI 兼容服务）。除 OpenAI 兼容外的引擎只需填密钥即可使用；走代理的引擎可在同一页开启「使用通用代理」。

**功能范围**：只翻译**影片标题与剧情简介**，不涉及电影字幕生成或语音转写——那是另一套能力（未来或作为独立插件/项目提供）。

> **不需要翻译？** 本节内容可整段跳过：翻译默认关闭（`engine: null`），不影响刮削与整理主流程，需要时再随时开启。

**翻译的确认环节**：配置翻译后，在「刮削队列」批量刮削（不勾选「并整理」）完成后，「结果审阅」页会在每部影片下方展示标题与简介的**原文 → 译文对照**，并默认勾选「落盘时使用此翻译」——确认译文满意后再整理落盘；对某部不满意可单独关闭开关，该部将保留原文写入 NFO。若直接选择「刮削并整理」，则按默认翻译直接落盘，不经审阅。

**目标语种**：设置页可选简体中文 / 繁体中文 / 英语 / 日语 / 法语 / 德语 / 西班牙语，译文写入 NFO 与文件名时会严格保持所选语种，不会简繁混用或夹带其它文字。

**NFO 全文中文字体一致**：除标题/简介译文外，影片分类（genre）等字段来自各刮削站点的原始标签（部分站点给繁体、部分给日文），落盘时会按目标语种自动统一用字——选简体则繁体标签一并转简体（如 `單體作品` → `单体作品`、`DMM獨家` → `DMM独家`），选繁体则反向转换；目标语种为非中文时不转换。映射表中标记为"不写入"的标签（如发行商专属标签）不受影响。

**同语言不翻译**：若原文已是目标语种（如源站标题直接是中文、目标选简体中文），则跳过翻译直接采用原文，不再调用翻译引擎——既省时间（本地模型一段剧情要数秒），也避免译文对原文做无谓的同义改写。原文为繁体、目标为简体时同样跳过翻译，由上述用字统一机制保证 NFO 一致。

**设计决策：只做自定义接口，不在项目内集成任何大模型**。把某个模型/推理框架内置进本项目会让软件迅速臃肿（模型文件、依赖、GPU/CPU 适配都是无底洞），且用户的硬件与网络条件千差万别。因此本项目只提供**标准 OpenAI 兼容接口**：端点地址、模型名、密钥都由用户自行填写，本地部署（Ollama / LM Studio）或云端源均可，部署形式完全自选。该能力继承自上游 Yuukiy/JavSP（上游仅有 OpenAI 引擎、无自定义入口），因 [issue #5](https://github.com/luckwalter/JavSP-fork/issues/5)「翻译引擎能加Ollama吗」的反馈而拓展完善。

**本地部署（推荐：免费 / 无审查 / 无限额）**：本机或局域网跑起 [Ollama](https://ollama.com/) 后，端点预设选「本地 Ollama」或「局域网 Ollama」即可自动填好地址与推荐模型（翻译场景可选 `qwen3:14b` / `glm-4-9b` / `translategemma:12b`，硬件有限选 `qwen3:8b`；7B 以下质量约等于自动字幕翻译，不建议）。⚠️ **成人内容场景下云端 API 普遍不可用**：OpenAI / Anthropic / Google 的服务条款禁止成人内容，国内平台（SiliconFlow / 魔搭 / 智谱 / Kimi）的模型均过安全对齐会拒答——本地模型是唯一稳妥路径。

**本地部署 Ollama 简要步骤**（以作者局域网部署为范例，Windows / Linux / macOS 通用）：

1. **安装 Ollama**：到 [ollama.com](https://ollama.com/) 下载安装；Linux 也可一行脚本 `curl -fsSL https://ollama.com/install.sh | sh`
2. **放行局域网访问**（换机器访问时必需）：设置环境变量 `OLLAMA_HOST=0.0.0.0` 并重启 Ollama（Windows 在「系统属性 → 高级 → 环境变量」中添加后重启 Ollama 即可），同时确认该机器防火墙放行 11434 端口
3. **拉取模型**：`ollama pull translategemma:4b`（约 3.3GB，Google 翻译专用微调，55 种语言互译；通用对话模型可选 `qwen3:14b` / `glm-4:9b`，硬件紧张选 `qwen3:8b`）
4. **在 JavSP 中配置**：「系统设置 → 翻译」→ 引擎选「OpenAI 兼容（含本地 Ollama）」→ 端点预设选「局域网 Ollama」→ 接口地址改为 `http://<跑 Ollama 的机器 IP>:11434/v1/chat/completions` → 模型名填 `translategemma:4b` →「使用通用代理」保持关闭 → 目标语种选「简体中文」→ 保存（即时生效）
5. **验证**：「刮削队列」选几部刮削（先不要勾「并整理」）→ 打开「结果审阅」查看每部的译文对照 → 满意后「去整理落盘」

**本地实测案例**（v0.3.14 开发期间作者局域网环境）：一台 Windows 机器跑 Ollama + `translategemma:4b`——Google 基于 Gemma 3 的翻译专用微调模型（约 3.3GB，Q4_K_M，支持 55 种语言互译），JavSP 端点填 `http://192.168.1.100:11434/v1/chat/completions`、模型名 `translategemma:4b`、关闭「使用通用代理」（本地直连）。实测日→中的标题与剧情翻译语义准确、行文通顺，**4B 量级即可满足元数据场景**，CPU 上一段剧情约数秒；追求更高文采可换 `translategemma:12b/27b`。⚠️ Ollama 默认只监听 `127.0.0.1`，局域网访问需设环境变量 `OLLAMA_HOST=0.0.0.0` 并重启 Ollama（同时确认机器防火墙放行 11434 端口）。

**配置示例（本地 Ollama，可直接抄进 config.yml）**：

```yaml
translator:
  fields:
    title: true      # 翻译标题
    plot: true       # 翻译剧情简介
  engine:
    name: openai
    url: 'http://127.0.0.1:11434/v1/chat/completions'   # 本机 Ollama；局域网机器换成 http://<机器IP>:11434/v1/chat/completions
    api_key: 'ollama'                                    # 本地模型不校验密钥，任意非空值即可
    model: 'translategemma:4b'                           # 也可以换 qwen3:14b / glm-4:9b 等
    use_proxy: false                                     # 本地部署不走代理
```

当然更推荐在 WebUI「系统设置 → 翻译」中配置——引擎选「OpenAI 兼容（含本地 Ollama）」、端点预设选「本地 Ollama」即自动填好地址与推荐模型，保存后立即生效：

![翻译设置](docs/images/translator-settings.png)

**云端免费源评判**（2026-10 时点整理，额度随时可能调整，以各平台控制台为准）：

| 方案 | 免费额度（约） | 国内直连 | 成人内容适配 | 备注 |
|---|---|---|---|---|
| Groq | 30 RPM / 1000 RPD / 20 万 tokens 每天 | 需代理 | ⚠️ ToS 灰色地带，模型可能拒答 | 速度极快，免卡注册 |
| OpenRouter | 20 RPM / 50 RPD（累计充值 $10 后 1000 RPD） | 需代理 | ⚠️ 取决于所选模型，免费池轮换不稳定 | 一个 key 遍览开源模型 |
| Gemini（AI Studio） | Flash 15 RPM / 1500 RPD | 需代理 | ❌ 安全过滤默认开启 | 免费档数据会被用于训练 |
| SiliconFlow 硅基流动 | 1000 RPM / 5 万 TPM | ✅ | ❌ 安全对齐会拒答 | 国内延迟最低 |
| ModelScope 魔搭 | 2000 RPD | ✅ | ❌ 同上 | 阿里官方 |
| 本地 Ollama | 无限 | ✅ | ✅ 无审查 | 占本机内存 / CPU，速度随硬件 |

非本地部署的云端源，勾选「使用通用代理」即可直接走「网络与容错」里配置的出口代理，无需重复填写。

## 安全说明（部署前必读）

- **单用户自用工具**，仅建议在可信网络（本地/家庭内网）使用，**放在公网上是明确不支持的用法**。
- **可选单用户登录**：设 `JAVSP_AUTH_PASSWORD` 即启用登录（会话落盘、带登录锁定）；**未设置则所有接口免登录（默认）**。启用后可阻止能访问该端口的人扫描目录、移动/重命名媒体文件、改写 `config.yml`。
- `javsp server` **默认只监听 `127.0.0.1`**（仅本机可访问），请不要在不受信任的网络里改 `JAVSP_HOST=0.0.0.0`。
- **Docker 部署默认监听全网卡**（容器外需要访问）。用 Docker 时 `ports` 建议写成 `127.0.0.1:8000:8000`（仅本机），而非 `8000:8000`（暴露给整个局域网）；确需局域网访问请确认网络可信并自行在前加反向代理 + 认证。
- 访问的主机名须在 `JAVSP_ALLOWED_HOSTS` 白名单内（默认只放行 `127.0.0.1`/`localhost`，容器部署已放开），用于阻断 **DNS Rebinding** 攻击。
- 「浏览目录…」只列目录、不列文件，且限制在 `JAVSP_BROWSE_ROOT` 之内（`realpath` 消解 `..` 后校验）；未设置时为 `/`，镜像内置 `/data`，建议收敛范围。
- 移动影片后**源目录变空会被删除**并向上递归清理残留空目录（最多 2 层），NAS 缩略图缓存目录不计入内容；边界保护：绝不删除扫描根、用户主目录、当前工作目录、其他隐藏目录及有效非空目录，任一步出错即停。
- `GET /api/config` 返回的配置里，翻译密钥等敏感字段已做**掩码**，前端保存时自动还原，不会丢密钥。
- `config.yml` 被 git 跟踪，填入 `api_key` 后请勿 `git commit -a`；推荐改用环境变量覆盖（如 `JAVSP_TRANSLATOR.ENGINE.API_KEY=xxx`，优先级高于配置文件）。⚠️ **嵌套层级必须用点号 `.`，不能用双下划线 `__`**（写成 `JAVSP_TRANSLATOR__ENGINE__API_KEY` 会被静默忽略）。

## 版本规则

本项目**不沿用上游版本号**，从 `0.0.1` 起步：小变更（修补/微调）第三位 +1；大变更（功能/架构）第二位 +1 且第三位归 1；正式稳定版 `1.0.0`。**当前版本：0.4.7**。

版本号以 `pyproject.toml` 为唯一权威来源，`python sync_version.py` 同步到 `frontend/package.json` / `package-lock.json` / `README.md`，`python sync_version.py --check` 可校验一致性（README 的版本徽章与「当前版本」两处标记，任一缺失或写法变化都会报错，不会静默放行）；跨环境部署（如 NAS 容器）另用 `python sync_version.py --check-target <路径>` 校验目标环境里的 `pyproject.toml`。完整的版本迭代记录见 **[CHANGELOG_FORK.md](./CHANGELOG_FORK.md)**（本 fork 独立维护，不覆盖上游 `CHANGELOG.md`）。

## 与原项目的关系

本 fork 派生自 [Yuukiy/JavSP](https://github.com/Yuukiy/JavSP)，**爬虫层与元数据逻辑全部沿用上游**（那是它最稳的根基，我们尽量少动），在其之上新增了编排 / Web / 桌面 / 健康 / 鉴权几层：`javsp/core.py`、`javsp/server.py`、`javsp/desktop.py`、`javsp/auth.py`、`javsp/web/health.py`、`frontend/`。`upstream` 仍指向原仓库，便于继续同步；配置格式沿用原 `config.yml`，CLI 入口 `javsp` 行为不变。

### 差异一览

| 能力 | 上游 Yuukiy/JavSP | 本 fork |
|---|---|---|
| 交互方式 | 命令行 | **Web 界面** + **Windows 桌面 exe**（CLI 仍原样可用） |
| 维护状态 | 2025-02 起停止更新 | 持续维护：站点改版适配、失效源复活 / 退役持续跟进 |
| 刮削源 | 13 个 | **19 个**（新增 javdbapi / javdatabase / javmenu / javfree / gyutto / njav 的移植增强） |
| 渠道健康 | 无 | `health.py` 后台探活 + 自动熔断 + 冷却半开自愈，死源不再白等 |
| 过程可见性 | CLI 输出 | SSE 实时进度、**每个站点分别贡献了哪些字段**透传、失败原因可见 |
| 配置方式 | 手改 `config.yml` | 「设置」页分组表单写回（**保留中文注释与排版**）+ 保存即时生效 + 坏配置自动回滚 |
| 输出控制 | 固定生成 | poster / fanart / 剧照 / NFO 各自独立开关 |
| 软件更新 | 无 | 启动后台检查新版本，桌面版一键下载并完成升级（sha256 校验 + 保留配置/会话 + 自动重启），Docker 给出镜像更新指引 |
| 交付形态 | pip / 源码 | Docker 镜像、NAS、**Windows 桌面 exe**、源码（Release 双资产） |
| 访问控制 | 无 | 可选单用户密码登录（会话落盘 + 登录锁定） |
| 版本记录 | — | 独立版本号 + **[CHANGELOG_FORK.md](./CHANGELOG_FORK.md)**，不覆盖上游 `CHANGELOG.md` |

### 从原项目迁移过来请注意

1. **`config.yml` 基本可直接沿用**：格式未变。但如果旧配置里含有本 fork 已移除的字段（例如 `network.proxy_free` 按站免代理地址），配置校验会**直接拒绝启动**，删掉该字段即可。
2. **⚠️ 命名规则的变量写法与上游 wiki 不同**：本 fork 使用**花括号风格 `{title}`**，上游 wiki 里的**美元风格 `$title` 在这里不适用**（会原样输出成文本）。变量全集 14 个，比上游多一个 `{genre}`（标签）；**填了不存在的占位符会在整理时直接报错**。建议在「设置」页的**命名规则构建器**中点选变量，不要手写。

更详细的刮削源说明仍可参考原项目 [JavSP Wiki](https://github.com/Yuukiy/JavSP/wiki)，但**以本文档与构建器中的变量列表为准**。

## 开发

仓库的回归测试在 `unittest/` 目录下（`python -m pytest unittest -q` 或 `python -m unittest discover -s unittest -v` 运行），其中 6 个指定用例文件是**每次发版的必跑门禁**（更新升级链路、配置健壮性、多分片移动、空目录清理重试、NFO 繁简归一化、同语言跳过翻译，共 47 项）；`tests/` 下另有针对上游 PR 移植修复的行为守卫。新增修复时应同步补用例。

`tools/` 下提供辅助脚本，其中 `tools/build_desktop.py` 可一键完成前端构建 + PyInstaller 打包 + zip 生成。

> 纯逻辑验证脚本（`verify_*.py` 等）属本地开发工具，**不入库**（见 `.gitignore`），仅在开发机上留存。

## 问题反馈

使用中遇到 Bug，欢迎在本仓库 [Issue 区反馈](https://github.com/luckwalter/JavSP-fork/issues)。

## 许可

本项目的所有权利与许可受 GPL-3.0 License 与 [Anti 996 License](https://github.com/996icu/996.ICU/blob/master/LICENSE_CN) 共同限制。此外，如果你使用此项目，表明你还额外接受以下条款：

- 本软件仅供学习 Python 和技术交流使用
- 请勿在微博、微信等墙内的公共社交平台上宣传此项目
- 用户在使用本软件时，请遵守当地法律法规
- 禁止将本软件用于商业用途
