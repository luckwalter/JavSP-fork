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
- **发版清单（v0.1.9 起固化）**：版本号以 `pyproject.toml` 为唯一权威来源，其余引用处用 `sync_version.py` 机械同步，避免漂移：
  1. 手动改 `pyproject.toml` 的 `version`
  2. `python sync_version.py` —— 自动同步 `frontend/package.json`、`frontend/package-lock.json`、`README.md`（版本徽章 + 「当前版本」）
  3. `python sync_version.py --check` —— 校验三处一致（不一致则退出码 1，可作发版门禁）
  4. 补 `CHANGELOG_FORK.md` 版本条目
  5. `commit` + `push` GitHub

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

### v0.1.9（2026-10-07）— 补齐 README 随版本更新 + 版本同步覆盖 README / package-lock（小变更）
- 起因：主人反馈「每次提交 changelog 时 README 并没有随版本更新」。全库排查后发现漂移**不止 README**：
  - `README.md`：版本徽章与「当前版本」均停在 **0.1.1**（而 `pyproject.toml` 已是 0.1.8）
  - `frontend/package-lock.json`：`version` 与 `packages[""].version` 也停在 **0.1.1**（此前的 `sync_version.py` 只同步了 `package.json`，漏了 lock 与 README）
- **Fixed 版本同步扩到三处**：重写 `sync_version.py`，从「只同步 package.json」扩展为同步：
  1. `frontend/package.json` → `version`
  2. `frontend/package-lock.json` → `version` 与 `packages[""].version`（两处都必须一致）
  3. `README.md` → 版本徽章 `badge/version-x.y.z-blue.svg` 与「当前版本：**x.y.z**」
  - 新增 `--check`：只校验不写入，任何一处不一致即退出码 1（可作发版门禁）。
  - 实测：同步前 `package.json=0.1.8 / package-lock=0.1.1 / README=0.1.1`，同步后三处均为 **0.1.9**，`--check` RC=0。
- **README 内容补齐**（不只是改数字）：
  - Python 徽章 `3.10` → `3.10 ~ 3.12`（对齐 `pyproject.toml` 的 `>=3.10,<3.13`）
  - 接口清单补全：`/api/health`、`/api/movies`、`/api/batch`（原只列了 scan/scrape/organize/config）
  - 功能列表补：并发限流、重试指数退避、配置 Web 化、每站点数据透传、单一版本源；并标记「不同的运行模式」为已完成（批量可选仅刮削 / 刮削并整理）
  - 前端描述补「批量任务」页
  - 新增**「发版清单」**表格：明确发版需同步的四处及同步方式（根因治理，避免再靠人记）
  - 新增**「开发」**章节：列出 `verify_*.py` 验证脚本及其覆盖范围
  - 指向 `CHANGELOG_FORK.md` 的完整迭代记录
- CHANGELOG 顶部「版本规则」补充**发版清单**（5 步），与 README 保持一致。
- 验证：`sync_version.py --check` 三处均 0.1.9 且 RC=0；`npm run build` 通过（确认 lock 重写未破坏构建）。
- 关联 commit：本提交（v0.1.9 同笔提交：`README.md` + `sync_version.py` + `frontend/package.json` + `frontend/package-lock.json` + `pyproject.toml`）

---

## v0.1.10 — 前端消费每站点贡献（sources）+ 修复贡献判据失真

- **背景**：v0.1.6 已把各站点抓取结果经 `_summarize_sources()` 透传到 SSE（`/api/scrape` 的 result、`/api/batch` 的 movie_done），但前端一直没消费，数据白传。本轮补齐展示层。
- **Added 批量页展示每站点贡献**：批量任务结果表格新增**展开行**，展开后可见该部影片各站点的明细——站点名、站点番号、站点标题、贡献字段（封面 / 分类 / 女优），无贡献的站点标注「无贡献」，另有「无码」标记。
- **Added 批量页新增「数据源」列**：以 `有效站点/总站点`（如 `3/8 站点`）直接反映本次刮削的数据来源质量，不用展开也能看出哪些片子是靠少数站点拼出来的。
- **Added 单部刮削页展示各站点贡献**：`/api/scrape` 的 result 本就带 `sources`，此前前端丢弃了；现补「各站点贡献」卡片，与批量页口径一致。
- **Fixed 贡献判据失真（本轮实测发现，见 Issue #9）**：`sources` 里的 `dvdid` 是 `MovieInfo` 构造时填入的**输入番号**，站点未收录时它**依然非空**。前端初版拿 `dvdid || has_cover || has_genre || has_actress` 判贡献，导致**所有站点永远显示「有贡献」（8/8）**，新增的「数据源」列完全失去意义。修法：后端 `_summarize_sources()` 新增权威字段 `contributed`（仅依据实际抓取到的封面 / 分类 / 女优），前端优先取该值，后端缺失时回退本地判据（同样不把 `dvdid` 算作贡献）。
- **Fixed `sync_version.py` 把 LF 写成 CRLF，污染每次发版的提交记录**（发版时顺带发现）：
  - 现象：发版跑完同步后，`README.md` / `frontend/package-lock.json` 出现**整文件重写**级别的 diff（README 174 行全变、package-lock 1426 行全变），真实改动被淹没，review 时无法看出改了什么。
  - 根因：Python 在 Windows 上以默认文本模式写文件会把 LF 一律转成 CRLF；脚本读写时未加 `newline=''`，于是仓库原本的 LF 文件被整体转成 CRLF。
  - 修复：写入前探测文件原换行符并原样保留（`_detect_newline()` + `newline=''`）；已把三个被污染的文件还原回 LF。修复后 `package-lock.json` 的 diff 从「1426 行全变」缩回**仅 2 行**（版本号两处）。
  - 顺带修正：README 徽章之间被插入空行（会导致徽章在 GitHub 上换行显示而非并排），已恢复为连续三行。
- 验证：新增 `verify_sources_e2e.py`（**22/22 PASS**），覆盖后端 SSE 透传结构、失败部判据、以及前端转换函数。
  - 前端函数用例的做法值得一提：**从 `App.vue` 源码里正则提取 `sourceRows`/`sourceSummary` 求值后执行**，测的是真实源码而非副本，避免「测试与实现漂移」。
- 回归：`verify_scrape_refactor.py` 7/7、`verify_batch_e2e.py` 11/11 全绿；`npm run build` 通过。
- README 同步：功能列表补「每站点贡献」说明，开发章节补 `verify_sources_e2e.py`；三处版本引用均同步至 0.1.10。
- 关联 commit：本提交（v0.1.10 同笔提交：`javsp/core.py` + `frontend/src/App.vue` + `verify_sources_e2e.py` + `README.md` + `frontend/package.json` + `frontend/package-lock.json` + `pyproject.toml`）

---

## v0.1.11 — UI 补齐网络配置项 + 修复配置保存丢注释与静默失效

- 起因：主人提问「UI 里是否有设置代理的地方」。排查发现设置页**只有 `network.proxy_server` 一项**，而 `config.yml` 的网络段共四项，其余三项（`proxy_free` 免代理地址、`retry` 重试次数、`timeout` 超时）只能手改配置文件；同时发现保存逻辑存在严重隐患。
- **Added UI 补齐三项网络配置**（设置页）：
  - 失败重试次数 → `network.retry`（数字输入，0~10）
  - 单次请求超时（秒）→ `network.timeout`，界面按秒编辑、保存时转成 ISO 8601 时长（如 `PT20S`）
  - 各站点免代理地址 → `network.proxy_free` 的 avsox / javbus / javdb / javlib 四个输入框；**留空表示「不改动」**，不会把空串提交给 URL 校验
  - 兜底：加载配置时补齐缺失的网络字段，避免表单绑定到 `undefined`
- **Fixed 保存配置会丢掉 config.yml 全部中文注释（Issue #10，严重）**：`/api/config` PUT 原用 `yaml.safe_dump` 整体重写文件，实测**注释 100 行 → 0 行、总行数 200 → 136**。改为 `javsp/config_io.py` 的**只替换变更字段**方案：按缩进栈解析出「字段路径 → 行号」，仅改写变动行，注释/空行/未改动字段原样保留。列表统一写为单行 flow 风格（原多行 block 列表会整块替换）。
- **Fixed 配置保存静默失效（改造期引入，端到端验证捕获）**：`_deep_update` 原为**原地修改**传入的 base，导致 `merged = _deep_update(current, ...)` 后 `current` 自身也被改写，随后的 `diff_leaves(current, merged)` 恒为空 → 写入 0 个字段、接口却返回成功。已改为纯函数式（返回新 dict 不改 base）。端到端用例断言 `changed>=4` 可防回归。
- 验证：新增 `verify_config_io.py`（**32/32 PASS**），覆盖标量/整数/嵌套/单行列表/多行 block 列表写入、注释零丢失、无变更不写、CRLF 换行符保持、**真实 PUT 端到端（用例会临时改写仓库 config.yml 用完即按字节还原）**、以及前端时长解析函数（从 App.vue 源码提取求值）。
- 回归：`verify_sources_e2e.py` 22/22、`verify_batch_e2e.py` 11/11、`npm run build` 通过。
- 关联 commit：本提交（v0.1.11 同笔提交：`javsp/config_io.py` + `javsp/server.py` + `frontend/src/App.vue` + `verify_config_io.py` + `README.md` + `frontend/package.json` + `frontend/package-lock.json` + `pyproject.toml`）

---

## v0.1.12 — 配置保存后即时生效（热重载，免重启）

此前在界面改完配置，接口会返回「已写入 config.yml，重启服务后生效」。代理恰恰是最需要「换一个马上试」的参数，重启才能生效体验很差。

### 改动内容
- **新增 `javsp/config_reload.py`**：负责让运行时跟上磁盘上的 `config.yml`。
  - `reload_runtime_config()`：置空 `Cfg.confz_instance` 触发 confz 重新读盘；失败立即恢复旧实例（避免后续所有 `Cfg()` 全崩）。
  - `_refresh_crawler_requests()`：刷新各爬虫模块级 `request` 实例的代理与超时。
  - `apply_config_changes()`：写入 + 重载一体化，失败自动回滚文件与运行时。
  - `describe_runtime()`：输出运行时实际生效值（含各爬虫出口）。
- **刷新爬虫出口（本功能的重点）**：`javsp/web/*.py` 在 import 时就执行 `request = Request(...)`，而 `Request.__init__` 会把 `proxies` / `timeout` **当场固化**。只重载 `Cfg` 的话，爬虫仍会拿着旧代理发请求 —— 界面改了却看不到效果。现遍历已加载的 `javsp.web.*` 模块逐个刷新。
- **超时下限不被冲掉**：airav（20s）、javlib（5s）因站点特性设了下限，改为模块级常量 `_TIMEOUT_FLOOR`，刷新时读取该常量取 `max`，与初始化逻辑一致。
- **原子写**：`config_io` 新增 `_atomic_write`（同目录临时文件 + `os.replace`）。热重载会在保存后立即读盘，直接原地写存在读到「半截文件」的风险。
- **新增 `GET /api/config/runtime`**：查看运行时真正生效的配置，用于确认是否即时生效（区别于 `GET /api/config` 读的磁盘值）。
- **前端**：保存提示改为「已写回并即时生效（刷新 N 个爬虫出口），无需重启」；热重载未成功则明确提示需重启，不谎报成功。
- 验证：新增 `verify_config_reload.py`（**24/24 PASS**）。

### 生效范围说明
新配置对**后续**请求立即生效；已在进行中的抓取任务仍沿用旧配置（不会中断任务）。

- 关联 commit：本提交（v0.1.12 同笔提交：`javsp/config_reload.py` + `javsp/config_io.py` + `javsp/server.py` + `javsp/web/airav.py` + `javsp/web/javlib.py` + `frontend/src/App.vue` + `verify_config_reload.py` + `verify_config_io.py` + `README.md` + `frontend/package.json` + `frontend/package-lock.json` + `pyproject.toml`）

---

## v0.1.13 — 封面 AI 裁剪：让那句宣传站得住，并能确认它到底有没有生效

起因是逐条核对 README 里的宣传语，发现「基于 AI 人体分析裁剪素人等非常规封面的海报」与实现有出入：
实现早已从「百度人体分析」换成 `slimeface` 本地**人脸检测**（措辞过时）；配置里 `crop.engine` 默认是 `null`
（**根本没启用**）；更棘手的是失败时用**裸 `except`** 静默回退默认裁剪，日志一个字不留。三者叠加的结果：
用户以为有这功能，即便开了也无从判断到底有没有用上。

### 改动内容
- **裁剪器不再静默失败**（`javsp/cropper/interface.py`、`javsp/cropper/slimeface_crop.py`）
  - 基类新增 `last_status` 记录本次裁剪去向，子类用 `_mark_ok` / `_mark_fallback` 上报；
  - 原先一句裸 `except` 覆盖全部情况，现按四类分别捕获：依赖缺失 / 未检测到人脸 / 检测出错 / 定位裁剪失败，
    每类都带回具体原因 —— 「没检测到人脸」其实是预期内的常见情形，过去却被当成异常一并吞掉；
  - 补 `get_cropper` 兜底：将来新增引擎若漏了分支，返回默认裁剪器而不是 None
    （否则调用点报 `'NoneType' object has no attribute 'crop'`，很难联想到是裁剪器分支漏了）。
- **裁剪结果如实回传**（`javsp/core.py`）
  - `process_poster` 返回 `{engine, applied, reason}`：AI 生效记 info，回退记 **warning 并写明原因**；
  - `organize_movie` 把结果放进 `result['crop']`，Web 整理结果与界面提示都看得到；
  - 语义统一：`applied=True` 只在**真的用上 AI 引擎**时为真，未启用时为 False 且说明「使用默认居中裁剪」。
- **配置可写回、状态可观测**
  - `config_io` 支持把标量改写成 YAML flow mapping（`engine: {name: slimeface}`），以及把多行嵌套块收敛回 `null`（删除子行），
    注释与排版不受影响 —— 这是开关能从 Web 保存的前提（原先只会渲染标量，dict 会被写成 Python 字典字符串）；
  - `describe_runtime()` 新增 `cover_crop`：引擎名 / 是否启用 / **依赖可用性探测** / 生效番号规则，`GET /api/config/runtime` 可见。
- **前端**：「设置」页新增「封面裁剪」分组 —— 人脸检测裁剪开关、启用条件（番号正则，可增改）、依赖状态查询；
  整理完成后明确提示本次是否真的用上了人脸检测、没用上是因为什么，不再笼统报「整理完成」。
- 附带修复：`verify_config_io.py` / `verify_sources_e2e.py` 里写死的 node 版本号改为动态探测（详见 Issue #13）。
- 验证：新增 `verify_cropper.py`（**65/65 PASS**），覆盖开关写回保注释、四类回退上报、
  AI 生效时结果与默认确有差异、整理结果透出、接口端到端开关、以及前端四个判定函数。

### 测试上的一个坑（值得记住）
验证「AI 裁剪是否真的生效」时必须用**宽图 + 主体偏左**的构图：宽图下默认裁剪固定取最右侧，
只有人脸不在右边时两者的裁剪框才会不同；拿纯色图或不带人脸的合成图来测，
任何裁剪结果都相同，会误判成「功能没生效」（本次踩过）。

- 关联 commit：本提交（v0.1.13 同笔提交：`javsp/cropper/interface.py` + `javsp/cropper/slimeface_crop.py` + `javsp/cropper/__init__.py` + `javsp/core.py` + `javsp/config_io.py` + `javsp/config_reload.py` + `frontend/src/App.vue` + `frontend/src/api.js` + `verify_cropper.py`(新) + `verify_config_io.py` + `verify_sources_e2e.py` + `README.md` + `CHANGELOG_FORK.md` + `pyproject.toml` + `frontend/package.json` + `frontend/package-lock.json`）

---

## v0.1.14 — 对照本机 Jellyfin 校准 NFO：修复评分量纲越界

起因是交叉检查「本项目生成的 NFO 是否和 Jellyfin 一致」。结论是：**结构层面本来就是一致的**，
真正不一致的是**数据层面的一处评分量纲 bug**（详见 Issue #14）。

### 如何取证的（不靠网上的 NFO 教程）
1. 本机 `C:\Program Files\Jellyfin\Server\jellyfin.dll` 读出 `ProductVersion = 10.10.7`；
2. 同目录 `MediaBrowser.XbmcMetadata.dll` 的 UTF-16 字符串表 → 取出 NFO 标签常量清单；
3. 拉 GitHub 源码 **v10.10.7** 与 **v12.2** 的 `MediaBrowser.XbmcMetadata/Parsers/BaseNfoParser.cs`
   （`FetchDataFromXmlNode` 的 switch-case）+ `MovieNfoParser.cs` + `MovieNfoSaver.cs` 做权威比对。

两份证据互相印证，也顺带确定了：**10.10.7 与 12.2 在涉及本项目的字段上语义完全一致**
（12.2 仅多一个带范围校验的 `<communityrating>`），因此本次结论对两个版本都成立。

### 交叉检查结论
| 维度 | 结论 |
|---|---|
| 标签是否被识别 | ✅ 本项目写入的 17 个标签全部在 Jellyfin 支持清单内 |
| NFO 文件名 | ✅ 默认 `movie.nfo`，且每部片独立文件夹，落在 Jellyfin 查找规则内、不会互相覆盖 |
| 日期 `premiered` | ✅ 各站点均为 `yyyy-MM-dd`，符合 Exact 解析要求 |
| 时长 `runtime` | ✅ 各站点均为分钟整数，能被 `int.TryParse` 接住 |
| 演员 `actor` | ✅ `<name>` / `<thumb>` 结构与 `GetPersonFromXmlNode` 一致 |
| 系列 `set` | ✅ `<set><name>` 与 Jellyfin `MovieNfoSaver` 自己的写法一致 |
| **评分 `rating`** | ❌ **存在越界**（见下） |

### 改动内容
- **修复评分量纲**（`javsp/web/fanza.py`）
  - FANZA 没有打分区、只有星星图时会走到另一分支，原实现把图片文件名（形如 `00`/`05`/…/`50`，
    即 **5 分制的十倍值**）**直接赋给 `score`**，既没换算也没转成约定的字符串类型；
  - 存档数据实测到 `score = 45`（int），写入 NFO 就是在 Jellyfin 界面上显示「45 分」；
  - 现已按 `/5` 换算到 10 分制并保持字符串类型，与同一文件中其它分支的 `*2` 换算保持一致；
  - 顺带修：打分区用 `r'\d+'` 提取会丢掉小数（`4.5` → 取到 `4`），改为 `r'[\d.]+'`。
- **写入 NFO 时加边界兜底**（`javsp/nfo.py`）
  - 新增 `_normalize_rating()`：把评分规范化到 Jellyfin 的 0~10 区间，越界钳制并记 warning，无法解析则跳过该字段；
  - 必要性来自取证发现：Jellyfin 解析 `<rating>` 时**只做一次浮点转换，没有范围校验**
    （12.2 才给新标签 `<communityrating>` 加了 0~10 校验），所以错误不会被服务端纠正，只会在界面上原样显示；
  - 加 warning 而非静默钳制，是为了保留线索回查上游爬虫 —— 这与本项目「失败要可观测」的一贯原则一致。
- 新增 `verify_nfo_jellyfin.py`（**31/31 PASS**）：把取证到的 Jellyfin 标签清单固化成断言，
  覆盖标签识别、`set`/`actor`/`uniqueid` 结构、日期与时长格式、评分边界、端到端越界兜底、
  以及用源码扫描防止「直接赋 int 原始值」这类写法复活。
- README 新增「与 Jellyfin / Kodi 对接 NFO」小节。

### 两个值得记住的教训
1. **别凭印象判断站点的评分量纲**：一度以为 javlib 也是 5 分制需要换算，核对存档数据后发现它抓下来就是
   `8.20` / `8.70` 这类 10 分制数值，改了反而会错。凡是「要不要换算」，一律回到真实数据求证。
2. **二进制字符串取证要警惕漏判**：首轮扫描 `MediaBrowser.XbmcMetadata.dll` 的字符串表时没匹配到
   `uniqueid`，差点误判成「Jellyfin 不支持该标签」；改用 UTF-16LE **精确字节匹配**后确认存在。
   结论要在二进制层面下最终判断前，务必再用另一种取证方式交叉验证（本次是靠源码比对挽回的）。

> Jellyfin 侧还有一处需要用户手动设置：本机 `system.xml` 中 Movie 的 `LocalMetadataReaderOrder` 为空，
> 意味着**即使 NFO 格式完全正确，Jellyfin 也不会去读它**。需在「管理媒体库」中把 Nfo 加入本地元数据读取顺序。

- 关联 commit：本提交（v0.1.14 同笔提交：`javsp/web/fanza.py` + `javsp/nfo.py` + `verify_nfo_jellyfin.py`(新) + `README.md` + `CHANGELOG_FORK.md` + `pyproject.toml` + `frontend/package.json` + `frontend/package-lock.json`）

---

## v0.1.15 — 输出项可分别关闭：不再为 Jellyfin 会自己做的事买单

既然影片元数据最终交给 Jellyfin / Emby 自行刮削，本项目再去下载高清封面（单张 8-10 MiB）、
裁剪 poster、抓剧照、写 NFO 就是纯重复劳动。新增四项输出开关，可在「设置」页直接控制，
也可写 `config.yml` 的 `summarizer` 段。

### 开关与行为
| 开关 | 位置 | 关闭后的行为 |
|---|---|---|
| `cover.enabled` | `summarizer.cover` | 不裁剪也不生成 poster |
| `fanart.enabled` | `summarizer.fanart` | 仍下载封面用于裁剪 poster，生成完即删除原图，不占空间 |
| `extra_fanarts.enabled` | `summarizer.extra_fanarts` | 完全不抓剧照（原本就有此开关） |
| `nfo.enabled` | `summarizer.nfo` | 完全不写 NFO |

poster 与 fanart **同源**（poster 由下载的原图裁剪而来），因此只有「任意一项开着」才会下载封面，
**两项都关时连下载都不发生**——这是真正省时间的地方。

三个容易忽略的点，本次都做了处理：
1. **被跳过的输出不能再对外声称生成了**：`result` 里相应的 `poster_file` / `fanart_file` / `nfo_file`
   置 `None`，否则 Web 界面拿着不存在的路径，看起来像成功实际没生成。
2. **跳过不算失败**：原先封面下载失败会置 `status='cover_failed'`，关闭封面时不能走这条分支。
3. **跳过要能看见**：结果里新增 `skipped` 列表，界面上提示「按『输出控制』设置跳过了：…」。

### CLI 不能漂移
`javsp/__main__.py` 有一份**独立的整理流程**（不走 `core.organize_movie`）——Web 侧就是从它抽出来的，
但抽取后它自己没改成复用。这类「两份逻辑」历史上吃过亏，所以本次在 `javsp/core.py` 加了唯一判据
`output_enabled(kind)`，Web 与 CLI 都调它，并用源码扫描断言 CLI 确实在用（而不是各写各的）。
顺带两处修正：
- CLI 的 `total_step` 原本写死 6，必须与实际 `check_step` 次数一致，否则关闭输出后进度条走不满 → 改为按开关动态累加；
- CLI 里打印「剧照大小」时误用了封面图的 `pic_path`（显示的是海报的尺寸），且关闭封面时该变量未定义会 `NameError` → 改为用目标路径。

### 向后兼容
新增字段在 `javsp/config.py` 中均带默认值 `= True`，**旧 `config.yml` 不写这些键也能正常加载**
（`verify_output_toggles.py` 用 `Cfg.model_validate` 显式验证过缺键的配置仍被接受且按启用处理）。

### 改动的文件
- `javsp/config.py`：`CoverSummarize` / `FanartSummarize` / `NFOSummarize` 各加 `enabled: bool = True`
- `javsp/core.py`：新增 `OUTPUT_TOGGLES` 与 `output_enabled()`；`organize_movie` 按开关分支，结果新增 `skipped`
- `javsp/__main__.py`：CLI 走同一判据；修 `total_step` 计数与剧照日志取路径
- `javsp/config_reload.py`：`describe_runtime` 新增 `output`（供 `/api/config/runtime` 与验证脚本断言）
- `frontend/src/App.vue`：设置页新增「输出控制」分组；整理结果提示本次跳过了哪些输出
- `config.yml`：补齐四个开关及说明注释
- 新增 `verify_output_toggles.py`（**70/70 PASS**）、`README.md`

- 关联 commit：本提交（v0.1.15 同笔提交：`javsp/config.py` + `javsp/core.py` + `javsp/__main__.py` + `javsp/config_reload.py` + `frontend/src/App.vue` + `config.yml` + `verify_output_toggles.py`(新) + `README.md` + `CHANGELOG_FORK.md` + `pyproject.toml` + `frontend/package.json` + `frontend/package-lock.json`）

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

8. **【流程/文档】README 与 package-lock 的版本号长期不随发版更新**
   - 现象：`pyproject.toml` 已 0.1.8，而 README 版本徽章 / 「当前版本」与 `frontend/package-lock.json` 仍停在 0.1.1。
   - 根因：`sync_version.py` 只同步了 `frontend/package.json`，漏掉 lock 与 README；且发版流程里没有明文规定要更新 README。
   - 修复：`sync_version.py` 扩展为同步三处（package.json / package-lock.json / README）并提供 `--check` 门禁；README 与 CHANGELOG 均写入「发版清单」固化流程（v0.1.9）。

9. **【功能 bug】`sources` 的 `dvdid` 是输入番号而非抓取成果，导致「站点贡献」判据失真**
   - 现象：为批量页新增「数据源（有效站点/总站点）」列后，实测失败部（站点未收录）依然显示 **8/8 站点**有贡献，该列完全失去意义。
   - 根因：`_summarize_sources()` 输出的 `dvdid` 来自 `MovieInfo` 构造时填入的**输入番号**——即便站点根本没收录这部片子，它也**始终非空**。前端初版判据为 `dvdid || has_cover || has_genre || has_actress`，只要 `dvdid` 非空就判为「有贡献」，于是所有站点恒为有效。
   - 实证（失败部 SSIS-456 的 sources 片段）：`{'airav': {'dvdid': 'SSIS-456', 'title': None, 'has_cover': False, 'has_genre': False, 'has_actress': False, ...}}` —— 番号非空但无任何成果字段。
   - 修复：后端 `_summarize_sources()` 新增权威字段 `contributed`（仅依据实际抓到的封面 / 分类 / 女优）；前端优先取该值，缺失时回退本地判据且**同样不把 `dvdid` 算作贡献**（v0.1.10）。
   - 教训：判断「抓取成果」时，要分清字段是**输入**还是**输出**；`dvdid` 这类输入回声字段看着像成果，实则恒定非空，极易污染判据。

10. **【数据丢失】UI 保存配置会丢掉 config.yml 的全部中文注释**
   - 现象：在设置页点一次「保存」，`config.yml` 的所有注释被清空（实测注释 100 行 → 0 行、总行数 200 → 136），文件可读性被毁灭性破坏，后续手工调整极其困难。
   - 根因：`/api/config` PUT 用 `yaml.safe_dump(merged)` 整体重写文件，而 YAML 序列化不保留注释；该文件近一半内容是注释说明。
   - 修复：新增 `javsp/config_io.py`，改为**只替换发生变化的字段行**（按缩进栈定位路径 → 行号），其余内容原样保留；列表统一写单行 flow 风格。写入时用 `newline=''` 保持原换行符（v0.1.11）。
   - 连带修复：改造过程中 `_deep_update` 原地修改入参，导致变更差异恒为空、**配置保存静默失效**（接口返回成功但 0 个字段被写入）。已改为纯函数式，并由端到端用例（`changed>=4`）守住。

11. **【功能缺失】设置页只暴露 1/4 项网络配置**
   - 现象：`config.yml` 的 `network` 段有四项（代理 / 免代理地址 / 重试 / 超时），Web 设置页只有「网络代理」一项，其余三项只能手改配置文件。
   - 修复：设置页补齐 `retry`（次数）、`timeout`（按秒编辑，内部转 ISO 8601）、`proxy_free`（四个站点的免代理地址）（v0.1.11）。

12. **【体验】配置保存后需重启才生效，且「只重载配置对象」并不够**
   - 现象：`PUT /api/config` 只写文件、不替换运行时单例（`confz` 的 `Cfg` 为 frozen），界面改完代理必须重启。
   - 实现热重载时发现的**隐藏坑**：`javsp/web/*.py` 在 import 时就执行 `request = Request(...)`，而 `Request.__init__` 把 `proxies` / `timeout` **当场固化**（`self.proxies = read_proxy()`、`self.timeout = Cfg()...`）。仅重载 `Cfg` 的话，6 个爬虫仍会拿着旧代理发请求 —— 界面改了却看不到效果，属于「改了一半」的假生效。
   - 修复：重载 `Cfg` 单例后，再遍历已加载的 `javsp.web.*` 模块刷新其 `request.proxies` / `request.timeout`；超时下限（airav 20s / javlib 5s）提取为模块级 `_TIMEOUT_FLOOR` 常量供刷新复用，避免被全局值冲掉（v0.1.12）。
   - 安全设计：重载失败会立即恢复旧实例（否则 `confz_instance` 一直是 None，后续任何 `Cfg()` 都会重建并抛错，等于打瘫服务）；文件同步回滚；写入改为原子替换，避免重载读到半截文件。

13. **【文档与功能不符】README 宣传的「AI 人体分析裁剪」名不副实**
   - 现象：功能列表写着「基于 AI 人体分析裁剪素人等非常规封面的海报」，核对后发现三处出入：
     ① **措辞过时** —— 上游早年确实用过「百度人体分析」接口，几经改版后已换成 `slimeface`（本地**人脸检测**），但宣传语没跟着改；
     ② **默认并未启用** —— `config.yml` 里 `crop.engine: null`（注释明示「null 表示禁用图像剪裁」），实际一直走默认居中裁剪；
     ③ **静默回退** —— `SlimefaceCropper` 用**裸 `except`** 吞掉一切异常，连「没检测到人脸」这种完全正常的情况也一并吞掉：封面照旧生成，日志一个字没有。
   - 影响：用户以为有这个能力；即便手动开启，也**无法判断到底有没有生效** —— 「开了 AI 裁剪」和「真的用了 AI 裁剪」完全不可区分。
   - 修复：
     1. 裁剪器改为分类捕获四类失败并记录 `last_status`（依赖缺失 / 未检测到人脸 / 检测出错 / 定位裁剪失败），不再用裸 except；
     2. `process_poster` 返回本次**实际采用**的裁剪方式（`{engine, applied, reason}`），按级别写日志，并由 `organize_movie` 放进整理结果 `result['crop']`；统一语义 —— `applied=True` 只在真的用上 AI 引擎时为 True；
     3. `describe_runtime()` 暴露 `cover_crop`（引擎 / 是否启用 / **依赖可用性探测** / 生效番号规则），界面可直接看出「装没装」；
     4. 前端「设置」页提供开关与番号规则编辑；整理结果明确提示未生效及原因；
     5. README 改为准确描述，勾选状态从「已完成」改为「默认关闭的可选项」。
   - 附带修复：为让开关能从 Web 写回，`config_io` 支持把标量改写成 YAML flow mapping（`engine: {name: slimeface}`）以及把多行嵌套块收敛回 `null`（删除子行），注释与排版均不受影响。
   - 另修复：`verify_config_io.py` / `verify_sources_e2e.py` 里 node 路径写死了 managed 版本号，环境升级后报 `FileNotFoundError`（看着像功能坏了，实为路径漂移）→ 改为动态探测。
   - 验证：新增 `verify_cropper.py`（**65/65 PASS**）。

14. **【数据失真】FANZA 部分影片评分超出 Jellyfin 的 0~10 区间**
   - 起因：交叉检查本项目生成的 NFO 与本机 Jellyfin 10.10.7 是否一致。
   - 现象：`javsp/web/fanza.py` 中，影片没有打分区、只有星星图时会走到另一个分支，该分支把图片文件名
     （形如 `00`/`05`/…/`50`，即 **5 分制的十倍值**）**直接赋给 `score`**，既没换算到 10 分制，
     也违背了 `MovieInfo.score`「应以字符串类型表示」的约定。存档数据实测到 `score = 45`（int）——
     写入 NFO 后 Jellyfin 界面显示「45 分」。
   - 隐蔽性：**Jellyfin 解析 `<rating>` 时只做一次 `float.TryParse`，没有范围校验**
     （直到 12.2 才给新标签 `<communityrating>` 加了 0~10 校验），因此越界值不会被纠正，只会在界面原样显示。
     文件照样生成、刮削照样「成功」，用户只会觉得某个评分数字怪，很难联想到是爬虫的量纲漏了换算。
   - 同一文件里另两条分支（`*2`、`/5`）都是对的，唯独这一条漏了 —— 属于典型的分支间不一致。
   - 修复：
     1. 源头换算 —— 星星图分支改为 `/5` 换到 10 分制并保持字符串类型；
     2. 顺带修 —— 打分区用 `r'\d+'` 提取评分会丢小数（`4.5` 被取成 `4`），改为 `r'[\d.]+'`；
     3. 兜底 —— `javsp/nfo.py` 新增 `_normalize_rating()`，写入前把评分规范化到 0~10，越界钳制并记 warning，
        无法解析则跳过该字段。用 warning 而非静默钳制，是为了留下线索回查上游爬虫。
   - 取证方式：本机 `jellyfin.dll` 读出 ProductVersion=`10.10.7`；取 `MediaBrowser.XbmcMetadata.dll`
     的 UTF-16 字符串表得到标签清单；再拉 v10.10.7 / v12.2 的 `BaseNfoParser.cs`、
     `MovieNfoParser.cs`、`MovieNfoSaver.cs` 源码做权威比对。结论：两个版本在涉及本项目的字段上语义一致。
   - **踩坑**：首轮字符串扫描没匹配到 `uniqueid`，差点误判成「Jellyfin 不支持该标签」；
     改用 UTF-16LE 精确字节匹配后确认存在。另外一度以为 javlib 也是 5 分制要换算，
     核对存档数据（8.20 / 8.70）后发现它本来就是 10 分制，改了反而会错。
   - 另发现（需用户侧处理）：本机 `system.xml` 中 Movie 的 `LocalMetadataReaderOrder` 为空 ——
     **即使 NFO 完全正确，Jellyfin 也不会读它**。需在「管理媒体库」中把 Nfo 加入本地元数据读取顺序。
   - 验证：新增 `verify_nfo_jellyfin.py`（**31/31 PASS**）。

15. **【无用功】整理时必须产出全部封面与 NFO，无法按需关闭**
   - 起因：元数据最终交给 Jellyfin 自行刮削时，本项目再下载高清封面（单张 8-10 MiB）、
     裁剪 poster、抓剧照、写 NFO 全是重复劳动，且没有任何开关可以省掉它们。
   - 现象：`summarizer` 下只有 `extra_fanarts.enabled` 一个开关，其余输出写在流程里无条件执行；
     想不用封面只能手工 `os.chdir` 之类的土办法，Web 界面更是完全没法控制。
   - 隐蔽性：这些步骤失败时会被容忍（封面失败置 `cover_failed` 但流程继续），用户很难区分
     「生成失败」和「本来就不想要」；而且 CLI（`javsp/__main__.py`）还有一份**独立的整理流程**，
     只改 Web 侧的话命令行用户依然全部生成 —— 属于典型的「两份逻辑」漂移风险。
   - 修复：
     1. `config.py` 为 `cover` / `fanart` / `nfo` 各加 `enabled: bool = True`（缺键默认启用，旧配置无需改动）；
     2. `core.py` 新增唯一判据 `output_enabled(kind)`，Web 与 CLI 共用；`organize_movie` 按开关分支；
     3. poster 与 fanart 同源说明：任一项开才下载，两项皆关时**连下载都不发生**（这才是真正的省时点）；
     4. 被跳过的输出在 `result['skipped']` 中记录，且相应的 `*_file` 置 `None` ——
        不能对外声称生成了不存在的文件；跳过时不计入失败状态；
     5. CLI 的 `total_step` 原本写死 6，关闭输出后进度条会走不满 → 改为按开关动态累加。
   - 另修复（CLI 既有 bug）：打印剧照大小时误用封面图的 `pic_path`（显示的其实是海报的尺寸），
     且在关闭封面输出时该变量未定义会 `NameError` → 改用目标文件路径。
   - 验证：新增 `verify_output_toggles.py`（**70/70 PASS**），覆盖四种封面组合的实际落盘、
     NFO / 剧照开关、旧配置向后兼容、CLI 与 Web 共用判据（源码扫描防止漂移）、
     开关经 `/api/config` 写回后 `describe_runtime` 跟随变化、前端纯函数从 App.vue 提取求值。

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

- ~~**【数据透传】前端未消费 v0.1.6 已透传的 `sources`（每站点贡献）**~~ → **已于 v0.1.10 解决**
  批量页新增展开行展示各站点贡献 + 「数据源（有效/总数）」列，单部刮削页新增「各站点贡献」卡片；并借此发现并修复了 Issue #9（贡献判据把输入番号 `dvdid` 误当成果）。

- ~~**【体验】配置保存后需重启服务才生效**~~ → **已于 v0.1.12 解决**
  新增 `javsp/config_reload.py`：置空 `Cfg.confz_instance` 触发重新读盘，并刷新各爬虫模块级 `request` 的代理/超时（详见下方 Issue #12）；写入改原子写，非法配置自动回滚。界面保存后即时生效，无需重启。

- **【联调】批量流程：合成片源已跑通，仅剩真实联网抓取待验**
  `/api/batch` 全链路（扫描 → 批量刮削 → 整理落盘）已由 `verify_batch_e2e.py` 用**合成片源 + mock 爬虫/下载**跑通（11/11 PASS），并借此发现并修复了 Issue #7。
  剩余待验部分只剩**真实联网抓取**（沙箱无真实片源/站点访问），需在本机有真实片源时实跑确认。

---

## 提交基线说明

- `c4cfe61`：从上游克隆推上 fork 的首笔 commit（含上游截至 #503 的内容），作为本项目 git 历史起点。
- 后续 `6494dbd` 起为 JavSP-fork 自有迭代。
