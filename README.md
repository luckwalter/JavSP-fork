![JavSP](./image/JavSP.svg)

# JavSP-fork（带 WebUI 的 AV 元数据刮削器）

**汇总多站点数据的 AV 元数据刮削器，并提供了 Web 界面**

本项目基于 [Yuukiy/JavSP](https://github.com/Yuukiy/JavSP) 派生改造。原项目是一个命令行工具，本 fork 在其成熟的爬虫与元数据能力之上，**新增了 Web 界面**：你不再需要敲命令行，打开浏览器即可扫描影片目录、刮削元数据、预览信息并整理入库（生成 Emby / Jellyfin / Kodi 所需的 NFO 与封面）。

> 原项目上游声明「WebUI 不是目标」，本 fork 正是为了把它变成带界面的功能软件而存在。

![Python](https://img.shields.io/badge/python-3.10%20~%203.12-green.svg)
![License](https://img.shields.io/github/license/luckwalter/JavSP-fork)
![Version](https://img.shields.io/badge/version-0.2.7-blue.svg)

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
  - 也可以直接点扫描框左侧的 **「浏览目录…」** 按钮逐级选择，不用手输——容器内路径与 Windows 侧
    看到的 `\\HOMENAS\Downloads` 不一致时尤其省事。该按钮只列**目录**不列文件。
  - 想把可选范围收敛到媒体目录（而不是整个文件系统），给容器加一个环境变量
    `JAVSP_BROWSE_ROOT=<路径>`。**镜像已内置 `ENV JAVSP_BROWSE_ROOT=/data`**（与
    `docker-compose.yml` 的挂载点一致），开箱即用、无需手工填；挂载到别的位置时覆盖它即可。
  - 扫描页与**设置页**的「扫描目录」都有浏览按钮；页面加载时会把允许浏览的根**自动填入**
    输入框，所以新部署镜像点开就能直接选目录。对话框放在 `el-tabs` **之外**，从任意 tab
    打开都正常（嵌在某个 `el-tab-pane` 内时，因 tab 默认懒渲染而会出现点击无反应）。
  - 若输入框里残留了上一轮的旧路径（常见于换环境后），点「浏览目录…」会**自动回退到根**
    而不是报错 —— 否则新部署环境里旧值必然越界，会导致「点开就是失败」。
- **权限 / 属主**：容器默认以 root（uid 0）运行，写回的文件属主会变成 root。Jellyfin / Emby **只读**这些 NFO / 封面通常没问题；若它们要回写元数据，可能因属主受限。可用 `docker run --user 1000:1000`（或 `docker-compose.yml` 里取消 `user:` 注释）对齐 NAS 媒体文件的 uid:gid。
- **落盘行为**：改名 / 移动 / NFO / 封面都落在扫描根（`/data`）之下；默认 `move_files: true` 会移到 `#整理完成/{actress}/...` 子目录，只想**原地改名 + 同级生成 NFO** 就在「设置」关掉「移动文件」。
- **其它**：小于 `scanner.minimum_size`（默认 232MiB）的文件不扫描；`hard_link` 默认关闭（同卷想省空间可开，跨文件系统会失败）；爬虫访问外站若 NAS 出口需代理，在「设置」填 `network.proxy_server`（这与拉镜像用的 squid 是两码事）。

### 排查「只有个别站点抓得到」

这是常见现象，**多数不是故障**。逐站点实测方法：容器内直接跑爬虫看真实结果/异常：

```bash
docker exec javsp /app/.venv/bin/python -c "
from javsp.config import Cfg
from javsp.datatype import MovieInfo
import importlib, time
for cid in Cfg().crawler.selection.normal:
    name = cid.value
    mod = importlib.import_module(f'javsp.web.{name}')
    parser = getattr(mod, 'parse_data', None) or getattr(mod, 'parse_clean_data', None)
    info = MovieInfo('ABC-123'); t0 = time.time()
    try:
        parser(info)
        print(f'{name:10} {time.time()-t0:5.1f}s OK   title={(info.title or \"\")[:26]}')
    except Exception as e:
        print(f'{name:10} {time.time()-t0:5.1f}s {type(e).__name__}: {str(e)[:80]}')
"
```

常见原因对照：

| 现象 | 含义 | 处理 |
|---|---|---|
| `MovieNotFoundError` | 该站未收录此番号 | 正常，不是故障 |
| `HTTPError 403` | 站点需登录 / 反爬 | 该站本就不可用 |
| `SSLCertVerificationError` | **代理在解密 HTTPS**（MITM），容器内没有代理的 CA 证书 | 见下方「HTTPS 代理（MITM）下如何让证书校验通过」 |
| `Fail to connect` / `ConnectionReset` | 站点对当前出口不可达 | 检查 `network.proxy_server` 是否配置正确，或该出口 IP 是否被站点封锁，必要时更换代理节点 |
| 主页正常但搜索页返回**版权限制提示页** | 站点对当前出口做**地域限制** | 更换出口 IP / 代理节点 |

#### HTTPS 代理（MITM）下如何让证书校验通过

代理若做 TLS 解密，会用自己的根证书重签目标站证书 —— 默认校验必然报
`SSLCertVerificationError`，**所有**走代理的站点会一起失效。本项目**不默认关闭校验**
（那是 v0.1.20 审查明确肯定的安全基线），而是支持挂载代理的根证书：

```yaml
services:
  javsp:
    environment:
      # ①推荐：挂载代理的根证书, 告知 requests 使用它做校验(校验仍开启)
      - JAVSP_CA_BUNDLE=/certs/myproxy-ca.crt
    volumes:
      - /path/to/proxy-ca.crt:/certs/myproxy-ca.crt:ro
      # ②仅限受控内网: 显式关闭校验(不安全, 不建议)
      # - JAVSP_TLS_VERIFY=0
```

如何取到代理的 CA：在代理机上找 `ssl_bump` 目录
（如 `/etc/squid/ssl_bump/intermediate.crt`、Surge/Clash 的 `*.cer`）。

> 若代理是**透传不解密**（如默认只做 CONNECT 转发），则无需任何配置 —— 此时签发者仍是
> 站点原厂 CA（如 `Google Trust Services`），校验天然通过。可用
> `openssl s_client -proxy <代理> -connect <站点>:443` 查看 `issuer` 是否为代理自己的 CA 来判断。

> ⚠️ 各站点统一走 `network.proxy_server` 配置的全局代理，不再有按站点的镜像/免代理地址
> （旧版 `network.proxy_free` 已移除）。站点可达性取决于全局代理出口，出口被封锁时请更换代理节点。

> 多阶段构建会自动 `npm run build` 前端并托管 `frontend/dist`。

#### ⚠️ 必须：配置持久化

`config.yml` 决定**代理、刮削站点、镜像地址、命名规则、扫描目录**。**不持久化会出两个
很迷惑的现象**：

1. 容器每次重建（换镜像 / `docker compose up -d` 重建 / 更新版本）都回到镜像内的默认值 ——
   你在 Web 界面改的设置全部丢失，却什么提示都没有；
2. 若挂载了配置到别的路径但**没告诉程序**，会出现「保存提示成功、配置却没变」——
   服务读的是 A 文件、界面写回的是 B 文件（读写不同源）。

所以 compose 里必须同时做两件事：

```yaml
    volumes:
      - /share/javsp_config:/etc/javsp     # ① 挂载配置目录
    command: ["-c", "/etc/javsp/config.yml"]  # ② 告诉程序读这个文件
```

首次部署先建目录并放一份默认配置（否则容器内该路径不存在会启动失败）：

```bash
mkdir -p /share/javsp_config
docker run --rm -v /share/javsp_config:/etc/javsp \
  -v "$(pwd)":/src -w /src javsp-fork \
  cp /app/config.yml /etc/javsp/config.yml
```

> 初始化后建议**先在 Web 界面把「网络代理」设好并保存**，确认保存提示为
> `已写入 config.yml ... 即时生效`，再开始刮削。很多「站点抓不到」就是代理没配上。

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

### ⚠️ 安全说明（部署前必读）

本项目是**单用户自用工具**，Web 界面**没有登录鉴权**——任何能访问该端口的人都可以：
扫描任意目录、**移动/重命名媒体文件**、写入 NFO/封面、改写 `config.yml`。
因此：

- **`javsp server` 默认只监听 `127.0.0.1`**（仅本机可访问）。这是安全的默认值，
  请不要在不受信任的网络里改`JAVSP_HOST=0.0.0.0`。
- **Docker 部署默认监听全网卡**（镜像内已设 `JAVSP_HOST=0.0.0.0`，因为容器外需要访问）。
  用 Docker 时请注意：
  - `ports` 建议写成 `127.0.0.1:8000:8000`（仅本机访问），而非 `8000:8000`（暴露给整个局域网）；
  - 若确需局域网访问，请确认所在网络可信，并自行在前面加一层反向代理 + 认证。
- 访问的主机名必须在 `JAVSP_ALLOWED_HOSTS` 白名单里（默认只放行 `127.0.0.1`/`localhost`；
  容器部署已默认放开）。这一项用于阻断 **DNS Rebinding** 攻击。
- 扫描页与设置页的 **「浏览目录…」** 只列目录、不列文件，且**限制在 `JAVSP_BROWSE_ROOT` 之内**
  （用 `realpath` 消解 `..` 后再校验）。越界请求会**回退到该根**而非报错——因为前端以输入框里的
  残留旧值作为起点，新部署环境里这类值必然越界，直接拒绝会让功能「点开即失败」。
  该环境变量**未设置时为 `/`（不限制）**，与手输路径的能力一致；**镜像内置为 `/data`**
  （与默认挂载点一致），建议收敛范围——否则等于把整个文件系统的目录结构开放给可访问该端口的客户端。
  > 已知平台差异：`realpath` 在 Linux 上会解析符号链接、在 Windows 上对目录联接（junction）
  > 不跟踪。两种平台都拿不到根外的目录列表，只是失败形态不同（回退 vs 400）。
- **空目录清理**：移动影片文件后，源目录变空会被删除，并**向上递归清理**残留的空分类目录
  （最多 2 层）。NAS 的缩略图缓存目录（QNAP `.@__thumb`、群晖 `@eaDir`）**不计入内容**
  —— 没有这条规则时，刮削完的源目录只剩缓存目录会被判为「非空」而永久残留。缓存目录被删
  不丢数据（NAS 下次访问时自建）。
  边界保护：绝不删除扫描根、用户主目录、当前工作目录、其他隐藏目录（`.Trash`/`.git` 等）
  及有效非空的目录；任一步出错即停止，不会强行处理。
- `GET /api/config` 返回的配置里，翻译密钥等敏感字段已做**掩码**（`***MASKED***`），
  前端保存时会自动还原，不会丢密钥。请勿把掩码值手工填到别处。
- `config.yml` 是**被 git 跟踪**的文件。填入 `api_key` 后请**不要** `git commit -a`；
  推荐改用环境变量覆盖（`JAVSP_TRANSLATOR.ENGINE.API_KEY=xxx`，**用点号 `.` 表示嵌套层级**），
  环境变量优先级高于配置文件。
  > ⚠️ **嵌套层级必须用点号，不能用双下划线 `__`**。本项目 `EnvSource(prefix='JAVSP_')`
  > 未改 confz 的 `nested_separator`，其默认值就是 `"."`。写成 `JAVSP_TRANSLATOR__ENGINE__API_KEY`
  > 或 `JAVSP_NETWORK__PROXY_SERVER` 会被**静默忽略**——不报错、不生效，读出来仍是 `None`，
  > 极易误判成「配了但没效果」。仓库内 `verify_env_nested_separator.py` 对此有断言守护。
- 输出目录会限制在扫描根目录内（配置里的绝对路径与 `../` 都会被收敛），
  但仍建议只扫描你确实打算整理的目录。
- **把JavSP 放在公网上是明确不支持的用法**，请只在可信网络（本地/家庭内网）使用。

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

### 渠道监控与熔断：死源不再白等

受限出口下站点会陆续失效（返场途中某个站关停、反爬升级、代理被墙）。
本项目的「渠道监控」页会告诉你**现在还有哪些源真的能用**，并且**自动把死源从刮削里摘掉**。

**为什么需要**：早期版本对每个启用的源都会完整跑 `retry`(默认 3) × `timeout`(默认 10s) 的重试。
死源必然失败，于是每部影片白白多等几十秒，而用户只看到「没刮到数据」，完全不知道是哪些源出了问题。

**熔断规则**（`javsp/web/health.py`）：

| 机制 | 行为 |
|---|---|
| 判定标准 | 走**真实 `parse_data`** 并要求解析出有效标题。很多死站返回 200 + 壳页，只看传输层会被误判为正常 |
| 触发阈值 | 连续失败 **2** 次才熔断（不取 1：单次失败常只是网络抖动，宁可多等几秒也不能误杀好源） |
| 熔断后 | 刮削时**直接跳过**该源，不再发起请求；被跳过的源仍会显示在结果里并标为「已跳过」 |
| 自动恢复 | 冷却期 10s → 20s → 40s …（上限 5 分钟），到点进入**半开**试探：成功则恢复，失败则重新熔断 |
| 不会误熔断的���况 | `未收录该番号`、`内容解析异常`、`结果重复` —— 这些说明**源本身是好的**，绝不熔断 |
| 检测方式 | 后台每 5 分钟自动探活（可关闭/手动触发），且**每次刮削的成败也会汇入健康档案**（零额外请求） |

「渠道监控」页每行显示：状态、是否参与刮削、耗时、**近期命中率**（真实刮削统计）、站点域名、失败原因、剩余冷却。
刮削结果里每个站点的展开表也会带上「已跳过 / 试探中 / 状态标签」，一眼看出是源坏了还是这部没收录。

> 页面上的「立即探活」用于**刚改完代理/镜像配置后立刻确认是否生效**，平时无需手动点。
> 探活与日志都**不会输出请求 URL**（可能含代理凭据/令牌）。

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

当前版本：**0.2.7**

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
