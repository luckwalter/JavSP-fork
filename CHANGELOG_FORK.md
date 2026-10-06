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

- **【体验】配置保存后需重启服务才生效**
  `/api/config` PUT 只写入 `config.yml`、不替换运行时配置单例（`confz` 的 `Cfg` 为 frozen），故界面改完代理/超时等参数必须重启服务才生效，不利于「换一个代理马上试」。若要支持即时生效需实测配置重载路径（confz 有 `change` 上下文管理器，但持久替换单例需验证）。v0.1.11 未实施，作为套餐 C 备选。

- **【联调】批量流程：合成片源已跑通，仅剩真实联网抓取待验**
  `/api/batch` 全链路（扫描 → 批量刮削 → 整理落盘）已由 `verify_batch_e2e.py` 用**合成片源 + mock 爬虫/下载**跑通（11/11 PASS），并借此发现并修复了 Issue #7。
  剩余待验部分只剩**真实联网抓取**（沙箱无真实片源/站点访问），需在本机有真实片源时实跑确认。

---

## 提交基线说明

- `c4cfe61`：从上游克隆推上 fork 的首笔 commit（含上游截至 #503 的内容），作为本项目 git 历史起点。
- 后续 `6494dbd` 起为 JavSP-fork 自有迭代。
