# JavSP-fork 版本迭代与问题修复记录

> 维护者：@luckwalter ｜ 基线：派生自 Yuukiy/JavSP ｜ 目标：将原 CLI 刮削器改造为带 WEBUI 的功能软件
> 版本号以 `pyproject.toml` 的 `version` 字段为准（静态管理）。
> 本文件为 fork 独立 changelog，不与上游 `CHANGELOG.md`（记录到上游 v1.8）混淆；每次发版维护此文件。

---

## 版本规则（官方）

- 不沿用上游 JavSP 的版本号（上游最新 v1.8），本项目从 **0.0.1** 起步。
- 三段式 `主.次.修订`，迭代规则：
  - 初始版本：`0.0.1`
  - 小变更（修补 / 微调）：第三位 +1 → `0.0.2`、`0.0.3`…
  - 大变更（功能 / 架构改动）：第二位 +1 且第三位归 1 → `0.1.1`、`0.2.1`、`0.3.1`…（跳过 `.0` 结尾）
  - 正式稳定版：`1.0.0`
- 每次发版更新 `version` 字段，并 `commit` + `push` GitHub。

---

## 迭代记录

### v0.0.1（2026-10-06）— 版本体系初始化
- 脱离上游 v1.8 版本体系，确立从 `0.0.1` 起步的独立版本规则。
- `pyproject.toml` 改为静态版本管理：移除 `poetry-dynamic-versioning`，`version` 字段直接维护。
- 关联 commit：`6494dbd`

### v0.1.1（2026-10-06）— WEBUI 骨架（大变更）
- 架构拆分：从 `javsp/__main__.py` 抽出 CLI 无关逻辑到 `javsp/core.py`（核心服务层）。
- 新增 FastAPI 后端 `javsp/server.py`，实现 `pyproject.toml` 预留的 `javsp.server:entry` 入口：
  - 路由：`/api/health` `/api/scan` `/api/movies` `/api/scrape`(SSE) `/api/organize`(SSE) `/api/config`
- 新增 PyWebView 桌面壳 `javsp/desktop.py`（桌面 exe 形态）。
- 前端 Vue3 + Element Plus + Vite：扫描 / 单部刮削 / 设置三页，消费 SSE 进度。
- Docker 多阶段构建（前端 Vite build + 后端 Poetry，跑 `javsp server`）。
- 关键决策：爬虫层 `javsp/web/*` 为纯逻辑（番号 → MovieInfo），**零改动直接复用**；改动/新增仅落在编排层与 Web 层。
- 关联 commit：`b8a54da`

### v0.1.2（2026-10-06）— 批量进度联调（小变更）
- 新增 `/api/batch` SSE 端点（逐部 + 整体进度）。
- 前端新增批量任务页（勾选 → 批量刮削 / 整理，真实消费 SSE）。
- 修复 `consumeSSE` 的 Promise 悬挂：原仅在 `result` / `error` 事件 resolve，流自然结束时永不 resolve。
- 关联 commit：`ad432c2`

### v0.1.3（2026-10-06）— 配置 Web 化（小变更）
- 设置页由裸 JSON `textarea` 改为分组表单：基础 / 爬虫多选 / 命名模板 / 翻译开关。
- 保存 `PUT` 回 `/api/config`，写入 `config.yml`。
- 关联 commit：`5d5b7d0`

### v0.1.4（2026-10-06）— 爬虫加载健壮性（小变更）
- **Fixed** `import_crawlers`：导入异常捕获由 `ModuleNotFoundError` 放宽至 `Exception`。原来某爬虫因依赖不兼容抛 `ImportError` 等会冒泡到 `lifespan`、直接拖垮 `javsp server` 启动；现在仅 `warning` 并跳过该爬虫。
- **Fixed** `parallel_crawler`：遍历爬虫前检查 `mod in sys.modules`，未导入则 `warning` + `continue`，不再 `KeyError` 拖垮整次刮削。
- 新增验证脚本 `verify_robust.py`（模拟某爬虫导入失败，确认两处均不崩、正常返回其余站点）。
- 关联 commit：`bb69fe1`

### v0.1.5（2026-10-07）— 修复 all_info 键名切片 bug（小变更）
- **Fixed** `parallel_crawler` 末尾冗余的 `all_info = {k[4:]: v for ...}` 切片。
  - 根因：该切片注释声称「删除键名中的 `web.`」，但 `all_info` 的键取自 `CrawlerID.value`（如 `'airav'` / `'javdb'`），本就不带 `web.` 前缀；`k[4:]` 会把 `'airav'→'v'`、`'javdb'→'db'`、`'javbus'→'bus'` 等错误切割。
  - 后果：`info_summary` 中 `if 'javdb' in all_info`（genre 特判）与 `all_info.get('javdb')`（封面水印处理）全部匹配不到，javdb 的 genre 汇总与封面优先级逻辑**彻底失效**。该 bug 对 CLI 与 Web 模式均生效（`parallel_crawler` 为共用逻辑）。
  - 修复：删除该切片行，保留 `CrawlerID.value` 原始键名；`info_summary` 按站点名特判恢复正常。
- 新增验证脚本 `verify_k4.py`：mock 各爬虫 `parse_data` 后真跑 `parallel_crawler`，断言返回键为 `'javdb'` 等原始形式（`'javdb' in keys = True`）；修复前返回 `'v'/'db'/'bus'` 等被切片段。
- 关联 commit：本提交（v0.1.5 同笔提交：`javsp/core.py` + `pyproject.toml` + `CHANGELOG_FORK.md`）

### v0.1.6（2026-10-07）— 刮削流程全面打磨（套餐3，小变更）
- 目标：在「功能可用」基础上做稳定性 / 反爬 / 可观测性打磨（套餐3 全面打磨：A+B+C+D+E+F+G+K）。
- **Fixed（隐藏 bug）`info_summary` 缺 `UseJavDBCover` 导入导致刮削成功路径崩溃**
  - 根因：`core.py` 的 `info_summary` 在 `use_javdb_cover` 特判中引用了 `UseJavDBCover`，但模块顶部 `from javsp.config import ...` 从未导入它。此前沙箱验证多用无效番号（早返回，走不到该分支）而一直未被触发；一旦真实番号刮削成功、执行到该分支即抛 `NameError`，整次刮削失败。
  - 修复：在 `core.py` 导入行补上 `UseJavDBCover`。本次纳入 v0.1.6。
- **A 并发限流**：`parallel_crawler` 由「无上限 `threading.Thread` 全开」改为 `ThreadPoolExecutor`，线程池大小 = 新增配置 `crawler.max_concurrency`（默认 5，可在 `config.yml` 调）。避免瞬时全开爬虫打爆出口 / 代理 / 触发站点风控。
  - `javsp/config.py` `Crawler` 新增 `max_concurrency: PositiveInt = 5`；`config.yml` 同步注释说明。
- **B 僵尸线程收敛**：原实现每个爬虫 `thread.join(timeout=...)` 仅「等不等得到」，超时的线程仍在后台跑（请求级 timeout 才兜底），存在悬挂线程。现统一用 `cf.wait(futures, timeout=overall_timeout)` + 超时未完成的 `future.cancel()` + `executor.shutdown(wait=False, cancel_futures=True)`，整体超时即强制造就、不阻塞等待；整体超时 = `retry × network.timeout + 15s` 余量。
- **C 重试指数退避**：`wrapper` 重试间隔由固定 0 改为指数退避 `min(2**cnt, 8)` 秒（1/2/4/8 封顶），降低连续重试同一站点触发风控的概率。带 `parse_data_raw` 的站点（自管重试）仍置 `retry=1` 不重退。
- **D genre 合并去重**：`info_summary` 的 genre 汇总改为「javdb 优先（标签较全）+ 其余站点补充去重」，避免原「仅取 javdb，缺失即 None」的隐患。
- **E 封面下载容错**：`download_cover` 中「当前 URL 无效 / 异常」由 `break`（放弃整轮）改为 `continue`（尝试下一个封面 URL），提高封面命中率。
- **F 全局超时统一**：`javsp/web/airav.py`（`max(20, network.timeout)`）、`javsp/web/javlib.py`（`max(5, network.timeout)`）原先硬编码 `request.timeout`，现统一尊重 `network.timeout` 配置（保留各站下限 20s / 5s）。
- **G 刮削间隔注入 Web 流程**：`parallel_crawler` 末尾新增 `sleep_after_scraping`（来自配置，>0 才 sleep），原 CLI 已有、Web 路径此前漏用，现已一致。
- **K 每站点数据透传 Web**：新增 `_summarize_sources(all_info)`（输出各站点 `dvdid/title/has_cover/has_genre/has_actress/uncensored`），挂载到 `movie.sources`；`/api/scrape` 结果、`/api/batch` 的 `movie_done`（成功 / 失败）SSE 事件均携带 `sources`，供前端展示「每站点贡献 / 失败原因」。
- 新增验证脚本 `verify_scrape_refactor.py`（纯逻辑、不联网），覆盖 T4（genre 合并）/ T5（封面 continue）/ T3（退避序列）/ T21（sources 结构）/ T1（并发限流 + 透传）：**7/7 PASS**。
- 关联 commit：本提交（v0.1.6 同笔提交：上述全部改动 + `verify_scrape_refactor.py` + `pyproject.toml`）

### v0.1.7（2026-10-07）— 统一版本源，消除前后端版本漂移（小变更）
- 背景：版本号此前散落多处且互不一致 —— `pyproject.toml` 已是 `0.1.6`，但 `frontend/package.json` 停在 `0.1.1`、`App.vue` 硬编码 `0.1.3`、后端 `/api/health` 又读到已安装元数据的旧值（实测仍 `0.1.3`）。三处漂移。
- **根因**：后端原用 `importlib.metadata.version('javsp')` 取版本。editable install 的元数据是**安装时快照**，改了 `pyproject.toml` 后 `meta.version()` 仍返回旧值 —— 实测同一环境下 `pyproject=0.1.7` 而 `metadata=0.1.3`。
- **Fixed 建立单一版本源**：新增 `javsp/version.py`，`get_version()` 按优先级取值 —— ①仓库根 `pyproject.toml`（源码/editable 场景，权威）→ ②`importlib.metadata`（正式打包安装、无 pyproject 时）→ ③`0.0.0` 兜底。
  - 不用 `tomllib`：项目支持 Python 3.10，而 `tomllib` 自 3.11 才入标准库；此处只需取一行 `version`，正则实现保持零依赖。
  - `javsp/server.py` 改为 `from javsp.version import get_version`，`__version__ = get_version()`；`FastAPI(title=..., version=__version__)` 与 `/api/health` 随之同步。
- **Fixed 前端不再自带版本号**：`App.vue` 移除硬编码 `ref('0.1.3')`，改为 `onMounted` 时调新增的 `api.getHealth()` 从 `/api/health` 拉取；后端不可达时留空不显示（`v-if="version"`），不影响页面功能。`api.js` 新增 `getHealth()`。
- **新增 `sync_version.py`**：把 `pyproject.toml` 的 version 同步进 `frontend/package.json`（`--check` 只校验不写入，发版前可用）。`frontend/package.json` 由 `0.1.1` → `0.1.7`。
  - 说明：前端页面显示的版本**不来自** `package.json`（而是运行时问后端），故 package.json 同步属工程卫生，不同步也不影响运行时正确性。
- 验证：`get_version()` = `0.1.7`（而 `metadata` = `0.1.3`，证明取源已修正）；`/api/health` → `{"status":"ok","version":"0.1.7"}`，OpenAPI `app.version` = `0.1.7`；`npm run build` 通过（1588 modules，仅既有 chunk 体积提示）。
- 关联 commit：本提交（v0.1.7 同笔提交：`javsp/version.py` + `javsp/server.py` + `frontend/src/App.vue` + `frontend/src/api.js` + `frontend/package.json` + `sync_version.py` + `pyproject.toml`）

### v0.1.8（2026-10-07）— 修复 Web 整理输出目录锚定 + 批量流程端到端验证（小变更）
- 背景：推进 backlog「`/api/batch` 完整流程待真实片源联调」。沙箱无真实片源，改用**合成片源（233MiB×2）+ mock 爬虫/下载**把全链路跑通，结果顺带挖出一个真 bug。
- **Fixed（真 bug）Web 模式下整理会把影片搬进「服务进程工作目录」而非影片目录**
  - 根因：输出目录模板 `output_folder_pattern` 默认是**相对路径**（`#整理完成/{actress}/[{num}] {title}`），`generate_names` 直接 `normpath` 后即作为 `save_dir`。CLI 靠 `javsp/__main__.py:226` 的 `os.chdir(root)` 让它相对「扫描根目录」解析；而 `core.py` 从 CLI 抽取时明确**去掉了 `os.chdir` 副作用**（见 core.py 头部注释），Web 服务又不 chdir —— 于是相对路径落到**服务进程的 CWD**。
  - 实证（探针 `probe_save_dir.py`）：影片在 temp 目录，而 `save_dir` 绝对化后 = `C:\...\JavSP\#整理完成\...`（仓库根）；「落在影片目录内」= **False**，「落在进程 CWD 内」= **True**。
  - 影响：Web UI 点「批量刮削并整理」会把影片搬进服务启动目录（如仓库根），而非扫描目录 —— 文件位置错乱且污染仓库。CLI 不受影响。
  - 修复：新增 `_root_save_dir()` —— 相对输出目录锚定到 `movie.scan_root`（`/api/scan` 时记录的扫描根绝对路径，已 `abspath` 规范化）；绝对路径模板则原样保留。`generate_names` 两处（正常/截短兜底）均改为经此函数。
  - 兼容性：CLI 未设置 `scan_root`，`getattr(movie,'scan_root',None)` 为 None 时保持原行为（相对 CWD），CLI 语义不变。
- `/api/scan` 新增记录 `m.scan_root = os.path.abspath(root)`。
- 新增验证脚本 `verify_batch_e2e.py`（合成片源，屏蔽真实网络），**11/11 PASS**，覆盖：
  - T1 `/api/scan` 识别番号并分配 guid（2 部，番号 `ABP-123`/`SSIS-456` 均正确）
  - T2 `save_dir` 锚定到扫描根目录（**本 bug 的回归守卫**）
  - T3 `/api/batch` SSE 事件序列完整（`movie_start`×2 / `progress` / `movie_done`×2 / `all_done`）
  - T4 成功 1 / 失败 1，**单部失败不中断整批**（`SSIS-456` 模拟站点未收录）
  - T5 整理产物落盘：`movie.nfo` 位于 `<扫描根>/#整理完成/...` 且含标题
  - T6 服务进程 CWD 未被污染出 `#整理完成`
  - 说明：合成文件需 ≥ `scanner.minimum_size`（232MiB）才会被扫描识别，故各 233MiB；跑完自动清理临时目录。
- 关联 commit：本提交（v0.1.8 同笔提交：`javsp/core.py` + `javsp/server.py` + `verify_batch_e2e.py` + `frontend/package.json` + `pyproject.toml`）

---

## 问题排查与修复（Issue 记录）

### 已修复

1. **【健壮性】单爬虫缺失导致 `parallel_crawler` KeyError 崩溃**
   - 根因：`parallel_crawler` 用 `getattr(sys.modules[mod], 'parse_data')` 硬取模块；若 `import_crawlers` 因依赖缺失静默跳过该爬虫，`sys.modules` 无此模块 → `KeyError`。
   - 修复：遍历前判 `sys.modules`，缺失即跳过该站点（v0.1.4）。

2. **【健壮性】`import_crawlers` 导入失败中断服务启动**
   - 根因：仅捕获 `ModuleNotFoundError`；依赖不兼容抛 `ImportError` 等会冒泡到 `lifespan` 拖垮 `javsp server` 启动。
   - 修复：捕获范围放宽至 `Exception`，失败时 `warning` + 跳过（v0.1.4）。

3. **【误报澄清】FastAPI `TestClient` 下 `import_crawlers` 不生效导致 KeyError**
   - 结论：是 `TestClient` 的 `lifespan` 未执行 `import_crawlers` 的**测试框架假阳性**；真实 `uvicorn` 部署下爬虫正常导入并运行，非部署 bug。验证方式见下方「启动炸弹排查」。

4. **【功能 bug】`parallel_crawler` 末尾 `k[4:]` 键名切片破坏 `info_summary` 站点特判**
   - 根因：`all_info` 的键为 `CrawlerID.value`（不带 `web.` 前缀，如 `'airav'` / `'javdb'`），`k[4:]` 误切前缀导致 `'javdb'→'db'`。
   - 影响：`info_summary` 中针对 `javdb` 的 genre 汇总、封面水印优先级处理（`use_javdb_cover`）全部失效（CLI / Web 共用逻辑，故两种模式都受影响）。
   - 修复：删除该切片行，恢复 `CrawlerID.value` 原始键名（v0.1.5）。

5. **【隐藏 bug】`info_summary` 缺 `UseJavDBCover` 导入，真实刮削成功路径崩溃**
   - 根因：`core.py` 的 `info_summary` 在 `use_javdb_cover` 特判引用 `UseJavDBCover`，但模块顶部从未导入；此前验证多用无效番号（早返回，走不到该分支）而长期未被触发，一旦真实番号刮削成功即抛 `NameError`。
   - 影响：任何「成功刮削」的影片在汇总阶段崩溃、整次刮削失败（CLI / Web 均受影响）。
   - 修复：在 `core.py` 导入行补 `UseJavDBCover`（v0.1.6）。

6. **【版本漂移】版本号散落三处且互不一致（后端读到已安装元数据旧值）**
   - 现象：`pyproject.toml`=0.1.6，而 `frontend/package.json`=0.1.1、`App.vue` 硬编码 0.1.3、`/api/health` 返回 0.1.3。
   - 根因：后端用 `importlib.metadata.version('javsp')` 取版本，editable install 的元数据是**安装时快照**，改了 pyproject 不跟随（实测 `pyproject=0.1.7` 时 `metadata` 仍 `0.1.3`）。
   - 影响：界面显示的版本号长期错误，无法反映真实发版状态，排查时易误导。
   - 修复：新增 `javsp/version.py` 建立单一版本源（pyproject 优先、元数据兜底）；前端改为运行时从 `/api/health` 取；新增 `sync_version.py` 同步 package.json（v0.1.7）。

7. **【功能 bug】Web 模式下整理把影片搬进「服务进程工作目录」而非影片目录**
   - 现象：Web UI 批量整理后，影片未落在扫描目录下的 `#整理完成`，而是出现在服务启动目录（仓库根）。
   - 根因：输出目录模板是相对路径；CLI 依赖 `__main__.py` 的 `os.chdir(root)` 解析，而 `core.py` 抽取时去掉了 chdir 副作用、Web 服务又不 chdir，导致相对路径落到进程 CWD。
   - 实证：探针显示 `save_dir` 绝对化 = `<仓库根>/#整理完成/...`，「落在影片目录内」= False。
   - 修复：新增 `_root_save_dir()`，相对输出目录锚定到 `movie.scan_root`（`/api/scan` 记录）；CLI 无 `scan_root` 时保持原行为（v0.1.8）。

### 启动炸弹排查（沙箱 Python 3.12 + uvicorn 实跑验证，确认均无问题）

| 检查项 | 结论 |
|--------|------|
| `core.py` 模块级 `Image.open` 资源 | `image/` 下资源存在，`resource_path` 指向项目根，不缺 ✅ |
| `Movie.guid` 动态赋值 | `Movie` 为普通类，`guid` 已在 `__init__` 声明，不炸 ✅ |
| `config.yml` 是否存在 | 仓库自带、未被 `.gitignore` 排除，`Cfg()` 能加载 ✅ |
| SSE 实现缺 `sse-starlette` | 使用 FastAPI 原生 `StreamingResponse`（`data: {...}\n\n`），不缺包 ✅ |

- 实跑结论：`javsp server` 在沙箱（Python 3.12.10 + uvicorn）正常启动，`health` / SSE / 刮削流程均正常，爬虫全部成功导入（无无效爬虫 warning）。

---

## 已知待解决（Backlog）

- ~~**【版本一致性】前端版本号未同步**~~ → **已于 v0.1.7 解决**
  已建立单一版本源 `javsp/version.py`（实读 `pyproject.toml`，元数据兜底）；前端改为运行时从 `/api/health` 取版本号，不再硬编码；新增 `sync_version.py` 同步 `frontend/package.json`。

- **【联调】批量流程：合成片源已跑通，仅剩真实联网抓取待验**
  `/api/batch` 全链路（扫描 → 批量刮削 → 整理落盘）已由 `verify_batch_e2e.py` 用**合成片源 + mock 爬虫/下载**跑通（11/11 PASS），并借此发现并修复了 Issue #7。
  剩余待验部分只剩**真实联网抓取**（沙箱无真实片源/站点访问），需在本机有真实片源时实跑确认。

---

## 提交基线说明

- `c4cfe61`：从上游克隆推上 fork 的首笔 commit（含上游截至 #503 的内容），作为本项目 git 历史起点。
- 后续 `6494dbd` 起为 JavSP-fork 自有迭代。
