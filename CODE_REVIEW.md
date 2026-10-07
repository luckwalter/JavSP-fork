# JavSP-fork 代码审查报告：性能与安全风险

> **✅ 整改状态：已落地 v0.1.20**（2026-10-07）。本报告列出的问题已按优先级分四批全部处理，
> 逐项改动与验证见 [`CHANGELOG_FORK.md`](CHANGELOG_FORK.md) 的 v0.1.20 条目。
> 仍未做的三项已在 CHANGELOG「已知未做」中说明原因（SSE 断开回收、批量并发化、鉴权体系）。
> 本报告保留作为**审查基线**——后续改动若触碰以下任一项，应先回看此处的证据与建议。

> 审查日期：2026-10-07 ｜ 审查范围：`javsp/` 6802 行 Python（46 文件）+ `frontend/src` + Docker/配置层
> 方法：4 个并行只读审查agent 分维度深挖（Web 层 / 爬虫层 / 性能 / 前端+CLI+配置层），交叉核对后由主审**逐条复验高影响结论**，剔除误报。
> 审查期间**未修改任何代码**。本报告只做「取证+ 定级 + 整改建议」。

---

## 摘要

**总体判断**：项目在**功能性维度质量较高**（11 个验证脚本全绿、TASKS 回收等近期加固到位），但在**信任边界**上存在系统性问题——
Web 层是一个**零鉴权、默认监听全网卡、具备任意目录读 + 任意路径写 + 配置改写**的HTTP 服务；在 NAS 场景下等价于
**把共享目录的读写权暴露给整个局域网**。爬虫层对「不可信外部输入」的净化做得较好（落盘穿越已被测试守护），
但**有2 处高危凭据泄露**和**1 处正则注入**。

**最高优先级 6 项**（按「风险 × 改动成本」排序）：

| # | 问题 | 级别 | 修复成本 |
|---|---|---|---|
| 1 |翻译 API Key **明文进日志**（任何翻译失败即触发） | **高** | 极低 |
| 2 | `server.py` 默认绑 `0.0.0.0` + 无鉴权 + 无 `TrustedHostMiddleware` | **高** | 极低 |
| 3 | `GET /api/config` 明文返回全部密钥 | **高** | 低 |
| 4 | `lib.py` 番号未转义注入正则（ReDoS + 判定绕过） | **中高** | 极低 |
| 5 | `config.yml` 被 git 跟踪（填了密钥即泄密） | **高** | 极低 |
| 6 | `retry=0` 导致刮削**静默全失败** | **中高** | 极低 |

---

## 第一部分：已复验确证的高危问题

### 【P0-1】翻译 API Key 明文写入日志 —— 触发门槛为零

**证据**（`javsp/web/translate.py`）：
- `translate.py:106/108/115/117/129` — `err_msg = "{}: {}: {}".format(engine, ...)`，
  `engine` 是 pydantic config 对象（`config.py:184-198` 定义了 `api_key`/`app_id` 字段），
  `str.format` 会触发 `__str__` → **渲染出全部字段含密钥明文**。
- `translate.py:38` / `:48` — `logger.error('翻译标题时出错: ' + result['error'])` 把上述 `err_msg` 写入日志。

**危害**：任何一次翻译失败（网络抖动、QPS 超限、API 额度耗尽——正常运行中必然发生）都会把
`api_key` / `app_id` 明文写进日志。日志扩散到日志平台即等于凭据泄露。

**整改**：日志中改用 `engine.name` 而非 `engine` 对象；或给 config 类加 `__repr__`/`__str__` 脱敏。
**成本**：改 5 处字符串即可。

### 【P0-2】Web 层零鉴权 + 默认全网卡监听 + 无 Host 校验

**证据**：
- `javsp/server.py:389` — `host = os.getenv('JAVSP_HOST', '0.0.0.0')`（**默认全网卡**）。
- `javsp/server.py:98` — `app = FastAPI(...)`，全仓**无任何** `Depends`/`Security`/auth 中间件/`TrustedHostMiddleware`。
- `docker/Dockerfile` — `ENV JAVSP_HOST=0.0.0.0`、`EXPOSE 8000`、**无 `USER`（容器以root 运行）**。
- `docker-compose.yml:16` — `ports: - "8000:8000"`（宿主机全网卡）。
- **对比**：`javsp/desktop.py:21` 绑 `127.0.0.1` —— 桌面壳安全，`server`/Docker 部署不安全，二者**不一致且文档未说明**。
- `README.md:118` 指导用户 `javsp server` 后打开 `http://127.0.0.1:8000`，但该命令实际监听**所有网卡**。

**危害**：在 NAS 上（本项目当前部署形态）同网段任意设备可直接：
- `POST /api/scan {"path":"/"}` 遍历容器/挂载目录；
- `POST /api/organize` **移动、重命名真实媒体文件、写入 NFO/封面**；
- `PUT /api/config` 改写服务器配置文件。
无 `TrustedHostMiddleware` 还意味着 **DNS Rebinding** 可绕过浏览器同源策略直接调这些接口。

**整改**（按性价比）：
1. `server.py:389` 默认改 `127.0.0.1`，需局域网访问时显式设`JAVSP_HOST`（Dockerfile 已有该 ENV，不受影响）；
2. 加 `TrustedHostMiddleware`（一行，堵DNS Rebinding）；
3. 暴露到局域网/公网时加基础认证；
4. `docker-compose.yml` 默认改 `127.0.0.1:8000:8000`，或保留但注释写明风险+ 必须配认证。

### 【P0-3】`GET /api/config` 明文返回全部密钥 + 无边界配置写入

**证据**：
- `javsp/server.py:306-310` — `return Cfg().model_dump(mode='json')`，**整份配置无过滤**。
- 敏感字段（`javsp/config.py`）：`BaiduTranslateEngine.app_id/api_key`(184/185)、`BingTranslateEngine.api_key`(189)、
  `ClaudeTranslateEngine.api_key`(193)、`OpenAITranslateEngine.url/api_key`(197/198)、`Network.proxy_server`(43，可含 `user:pass@`)。
- `javsp/config_reload.py:119-125` — `/api/config/runtime` 也把带凭据的代理串和各爬虫 proxies 明文返回。
- `javsp/server.py:316` — `PUT /api/config def api_config_put(updates: dict)`，`updates` **无白名单**，凡是 pydantic 字段都能改。

**危害**：
- 凭据泄露（同P0-2 的可达性）。
- 配合 PUT 可做 **SSRF**：`network.proxy_server` 或 `translator.engine.url`（OpenAI 兼容端点）改为内网地址→
  服务把请求（含 `Authorization: Bearer <api_key>`头发往攻击者指定处（凭据外泄 + 内网探测）。
- 配合 PUT 改`output_folder_pattern` 为绝对路径 → 下次 organize **写到任意路径**（`core.py:345-346` 对绝对路径原样放行）。

**整改**：`/api/config` 对 `api_key`/`app_id` 做掩码（保留后 4 位），仅鉴权后允许 reveal；
`PUT /api/config` 加字段白名单；`output_folder_pattern` 加路径校验（拒绝绝对路径与含 `..`）。

### 【P0-4】`lib.py` 番号未转义 → 正则注入（ReDoS + 判定绕过）

**证据**：`javsp/lib.py:64-65`
```python
pattern_str = re.sub(r'[_-]', '[_-]*', avid) + r'(UC|U|C)\b'
match = re.search(pattern_str, base, flags=re.I)
```
`avid`（番号，来自文件名或 `/api/scrape` 的 `avid` 参数）被**原样注入正则模式**。
`re.sub` 只把 `-`/`_` 换成 `[_-]*`，**其余正则元字符（`*` `+` `(` `)` `.` `[` `\`）全部保留为活跃语法**。
同文件 `javsp/lib.py:12` 已有 `re_escape` 函数**但未使用**（典型的「工具写好了忘了用」）。

**危害**：
1. **ReDoS**：构造含 `a*a*a*...` 的文件名/番号 → 叠加 `[_-]*` 生成嵌套量词 → 指数级回溯，CPU 挂起；
2. **判定绕过**：把 `(UC|U|C)\b` 置于 alternation 之下，操纵无码/字幕标记判定，影响输出正确性。

**整改**：`pattern_str = re_escape(avid) + r'(UC|U|C)\b'`（一行，复用已有函数）。

### 【P0-5】`config.yml` 被 git 跟踪

**证据**：`git ls-files config.yml` 确认被跟踪；`.gitignore` 无 config 规则。
`config.yml:165-192` 是翻译引擎配置区，用户填入真实 `api_key` 后 `git commit -a` 即把密钥提交进历史。

**整改**：`.gitignore` 加入 `config.yml`，改用 `config.local.yml`；或提供 `config.example.yml` 模板。

### 【P0-6】`retry=0` 导致刮削静默全失败

**证据**：`javsp/core.py:119` `for cnt in range(retry):` —— `retry=0` 时循环体**一次都不执行**，
`setattr(info, 'success', True)`（`core.py:124`）永不执行 → 后续按 `success` 过滤时**所有站点结果被丢弃**，
`all_info` 为空，刮削**静默全失败**，无任何错误提示。`javsp/config.py:44` `retry: NonNegativeInt = 3` 允许 0，
前端 `App.vue` 的 `:min="0"` 也允许用户设0。

**整改**：`retry: int = Field(3, ge=1, le=10)`（下限必须 1）。

---

## 第二部分：性能问题（已复验并定量）

### 【P1-1】`overall_timeout` 计算错误 → 第二波爬虫被误杀

**证据**：`javsp/core.py:171-172`
```python
per_crawler_worst = Cfg().network.retry * Cfg().network.timeout.total_seconds()   # 3×10=30
overall_timeout = per_crawler_worst + 15                                          # 45s
```
注释假设「并发下整体最坏≈单爬虫」——但 `config.yml:45` 实际配了 **8 个抓取器**，`max_concurrency=5`（`config.yml:66`），
8 个任务进 5 线程池= **两波**。单爬虫最坏 = 3×10s + 退避(1+2)s = 33s；两波最坏 = 33+33 = **66s > 45s**。
`core.py:193-196` `cf.wait(timeout=45)` 到点即 `cancel()` 未完成的任务 → **第二波爬虫刚开始 12s 就被强杀**，
而它们本可正常完成。

**整改**：`overall_timeout` 改为 `ceil(站点数/线程数) × per_crawler_worst`，或直接依赖请求级 timeout + 取消机制。

### 【P1-2】批量完全串行 + 剧照强制 sleep（最大吞吐杀手）

**证据**：
- `javsp/server.py:259` — `/api/batch` 是单线程 for 循环，影片间零并发。
- `javsp/core.py:696-703` — 剧照下载循环里**每张图后无条件 `time.sleep(scrap_interval)`**
  （`config.yml:158` `PT1.5S`，且 `extrafanarts.enabled` 默认开）。javdb 常有 10-20 张剧照 →
  **每部 15-30s 纯睡眠**；且 `core.py:703` 的 sleep 在循环体内，最后一张后**多睡一次**。

**定量**：1000 部批量仅剧照 sleep ≈ 4-8 小时；加上封面（`highres:true`，8-10 MiB/部）与 `sleep_after_scraping:PT1S`，
整批约 6-12 小时。

**整改**：剧照循环改并发；去掉尾部多余 sleep；`/api/batch` 改影片级并发。
**收益**：单这一项可消掉总耗时绝大部分（性能整改中收益最大）。

### 【P1-3】`TaskStore` 每次写入 O(n) TTL 扫描 + O(n log n) 排序 → 总体 O(n²)

**证据**：`javsp/task_store.py:67-71` 每次 `__setitem__` 都调 `_evict_locked()`；
`task_store.py:135-151` 做两件O(n) 事——全量扫 `_ts` 找过期项 +超限时 `sorted(self._ts.items())` 排全部。

**实测基准**（用项目源码）：500 条 0.029s → 1000 条 0.073s → 2000 条 0.314s → 4000 条 2.251s（每条从 57µs 涨到563µs，超线性）。

**影响**：`/api/scan`（`server.py:137`）循环逐条 `TASKS[guid]=m`；5000 部 NAS 库扫描光这一项约 15-30s，
且扫描期间持 RLock 阻塞其他 API。注：这是 v0.1.17 引入的 TTL 策略的**副作用**（修内存泄漏时新引入的性能回退）。

**整改**：TTL 用 `heapq` 按时间戳有序弹出；容量淘汰仅超限时做一次排序而非每次写入。

### 【P1-4】`get_pic_size` / `valid_pic` 为拿尺寸/校验而解码整图

**证据**：`javsp/image.py:49-52`
```python
def get_pic_size(pic_path):
    pic = ImageOps.exif_transpose(Image.open(pic_path))   # 内部 image.load() + image.copy()
    return pic.size
```
核对 Pillow 10.2 源码：`exif_transpose` 内部 `image.load()`（强制整图解码）+ 无 EXIF 旋转时 `image.copy()`（再复制一遍）。
只为返回 `.size` 两个整数，峰值内存约**137 MB**（4000×6000）。`image.py:13-21` `valid_pic` 同理。
`core.py:539` 对每部影片的每张封面调用一次。

**定量**：500 张累计多耗 50-125s，峰值内存 137 MB/次。

**整改**：改为 `with Image.open(p) as im: size = im.size`（**零解码**）。**性价比最高的一处性能改动。**

### 【P1-5】slimeface 全分辨率图喂检测器且警告被抑制

**证据**：`javsp/cropper/slimeface_crop.py:26-29`
```python
with warnings.catch_warnings():     # 抑制了 slimeface 的「图像过大」警告
    bbox_confs = detectRGB(fanart.width, fanart.height, fanart.convert('RGB').tobytes())
```
`slimeface` 自己在 `__init__.py:13-14` 警告「>960 可能性能下降」，而本项目用 `catch_warnings()` **把这条警告抑制掉了**。
`convert('RGB')` + `tobytes()` 对 4000×6000 额外产生约 **137 MB**。

**缓解事实**：模型只加载一次（常驻 <1MB，非瓶颈）；**且 `config.yml:141` `engine: null` 默认关闭，默认不触发**。

**整改**：检测前把长边缩到 960px 内（省 90%+ 检测耗时 + 内存降到约 5MB）；不要抑制那条警告（它是有效的性能信号）。

### 已核查「理论问题但实际影响小」（**不必改**）

| 问题 | 为何影响小 |
|---|---|
| `Image.open` 未 `with`/`close()`（`core.py:40-41`, `image.py:16,51`） | 经核对 Pillow 源码**无 `__del__`**，CPython 引用计数即回收，**不构成泄漏** |
| `shutil.move`「慢」 | 同卷时即 `os.rename`（O(1) 不拷数据），`_root_save_dir` 保证同卷。**原假设有误** |
| `hard_link: false` 会重复占空间 | **前提相反**：默认 false 走 move（源文件消失），**不重复占空间** |
| `get_id`/`getsize` 无缓存 | 实测约 30µs/文件 → 50 万文件 15s，占全流程 <1% |
| `Cfg()` 每次调用开销 | confz 2.1.0 走单例缓存，实测 0.538µs |
| `file.py:41` 目录项读 2 次 | O(n)非 O(n²)，NAS 上5000 目录约 2.5-10s |
| 批量 1000 部TASKS 内存 | 实测 Movie 约 5.5KB/部 → 1000 部仅约 8MB，完全可接受 |

---

## 第三部分：配置与 CLI 层问题

### 【P1-6】资源字段无上限（可被配置接口放大成资源耗尽）

**证据**（`javsp/config.py`）：`retry`(44, `NonNegativeInt`)、`max_concurrency`(118, `PositiveInt`)、
`timeout`(45, `Duration`)、`sleep_after_scraping`(115)、`scrap_interval`(149) **均无上界**。
配合 `server.py:316` 无鉴权的 `PUT /api/config`：
- `retry: 100000` → `core.py:171` `overall_timeout` 变天文数字 → `cf.wait` 永久挂起；
- `max_concurrency: 10**7` → 创建海量线程 → 资源耗尽；
- `retry: 0` → 静默全失败（见 P0-6）。

**整改**：全部改 `Field(..., ge=…, le=…)`；`retry` 下限必须为 1。

### 【P1-7】`output_folder_pattern` 无任何校验 → 任意路径写

**证据**：`javsp/config.py:129` 裸 `str`；`javsp/core.py:345-346` `if os.path.isabs(pattern_dir): return pattern_dir`
（**绝对路径原样放行**）；`core.py:439` `os.path.normpath` 会规整 `..` 上跳，`_root_save_dir`（`core.py:335-350`）只拼接**不做越界收敛**。
配合无鉴权 PUT + organize = 任意路径写+ 移动文件。

**整改**：加 validator 拒绝绝对路径与含 `..`；`_root_save_dir` 落地前用 `realpath` 校验在扫描根内（**收敛**而非拒绝）。

### 【P1-8】`config_reload` 回滚用非原子写 → `config.yml` 可归零

**证据**：`javsp/config_reload.py:186` 回滚走 `_write_bytes` → `config_reload.py:153-156` `open(path,'wb')`（**非原子，先截断为 0**）。
与本模块 `_atomic_write`（`config_io.py:212-231`）的设计前提自相矛盾。回滚窗口内并发 `Cfg()` 读到空配置；
若中途进程崩溃，磁盘留下**长度 0 的 config.yml**，下次启动直接失败（**持久化损坏，需人工修**）。

**整改**：回滚改用 `config_io._atomic_write`。

### 【P1-9】`config_io` 换行注入 → 配置值静默损坏

**证据**：`javsp/config_io.py:61` 的需引号字符类**不含 `\n`/`\r`/`\t`** → 值含裸换行时走`config_io.py:77 return s`（不加引号）
原样写入 → YAML 折叠为多行，**值被静默篡改**。不能注入新键（字符类含 `:` 会触发加引号），但可损坏值/结构（会回滚）。

**整改**：`_needs_quote` 字符类补 `\n\r\t`。

### 【P1-10】配置写入 TOCTOU（并发 PUT 丢失更新）

**证据**：`javsp/config_reload.py:37` 有 `_lock`，但仅在 `apply_config_changes` 内持有（:169）。
端点侧快照+合并在锁外：`server.py:330-331` `current = Cfg().model_dump()` / `merged = _deep_update(...)`。
并发两个 PUT → 第二个完整执行，第一个基于旧快照算出的 `changes` 会**覆盖**第二个的改动。

**整改**：把「读快照+合并+算diff」纳入同一把锁。

### 【P1-11】CLI 的 `except` 被注释掉 → 单部失败整批崩（与 Web 漂移）

**证据**：`javsp/__main__.py:211-213`
```python
# except Exception as e:
#     logger.debug(e, exc_info=True)
#     logger.error(f'整理失败: {e}')
finally:
    inner_bar.close()
```
`except` 被注释，只剩 `finally`。而 `check_step`（`__main__.py:93-98`）失败时 `raise`。
→ **任意一部影片任一步失败（封面 404、目标已存在、NFO 权限）都会让整个 CLI 进程抛异常终止**，后面影片不再处理，无失败汇总。
Web 侧则逐部捕获+ `fail` 计数（`server.py:289`）。

**对比其他漂移**：CLI 在封面/翻译/裁剪/NFO/移动失败时都`raise`（`__main__.py:149/135/161/197-201`），Web 侧全部 catch 并继续。
另 `__main__.py:175` `os.mkdir(extrafanartdir)` 会 `FileExistsError`，而 Web `core.py:695`用 `exist_ok=True`。

**整改**：恢复 `except` 并记录失败继续下一部（与 Web 对齐）；剧照目录改 `os.makedirs(..., exist_ok=True)`。

### 【P1-12】`move_files` 步骤未计入 `total_step` → 进度条溢出（默认配置下必然）

**证据**：`__main__.py:101-113` 的 `total_step` 累加了poster/nfo/剧照/封面/翻译，**唯独漏了 `move_files`**；
但 `__main__.py:199-202` 移动文件后有 `check_step(True)`（第 9 次调用）。默认配置（`move_files: true`）下
`total_step=3+4=7`，实际 `check_step` **8 次** → 进度条走到 `8/7`（约 114%）。

**且现有守卫漏检**：`verify_output_toggles.py:239-244` 只检查 `total_step=3` 与 poster/nfo 两处增量，**未检查 `move_files`**
→ v0.1.15 的「CLI 不能漂移」守卫在此失效。

**整改**：`__main__.py:113` 后补 `if Cfg().summarizer.move_files: total_step += 1`；
守卫脚本加「所有 `check_step` 分支都有对应 `total_step += 1`」的静态断言。

### 【P1-13】前端保存配置会静默丢弃 API Key（功能 bug）

**证据**：`frontend/src/App.vue:514-516`
```js
if (c.translator && c.translator.engine) {
  c.translator.engine = { name: c.translator.engine.name || 'none' }
}
```
把 `engine` 裁成只含 `name`，随后 `App.vue:564` PUT 回后端→ `engine.api_key` 消失→
`server.py:332` `Cfg.model_validate` 因`api_key` 缺失抛 `ValidationError`（`config.py:185/189/193/198` 均无默认值）→ **保存失败**。
即：若原本配了翻译密钥，界面上点一次保存就会失败。

**整改**：仅在 `name` 变更时重置 `engine`，保留原 `api_key`/`app_id`/`url`/`model`。

### 【P1-14】翻译请求全部无 timeout + Google 无限重试

**证据**：`javsp/web/translate.py:154/171/184/212/244` — 全部 `requests.post/get` **无 `timeout` 参数**
（对比 `web/base.py` 全都有 timeout）→ 翻译卡住会让 `core.py:199` 的线程池留下**无法回收的孤儿线程**（Python 无法强杀线程）。
`translate.py:185-190` Google 翻译 `while r.status_code == 429:` **无最大重试次数**，等待单调递增至数小时/数天。

**整改**：全部补 `timeout`；Google 加最大重试次数。

### 【P1-15】`download()` 无大小上限 + 非 http 即本地复制

**证据**：`javsp/web/base.py:239-245`
```python
if not url.startswith('http'):
    shutil.copyfile(url, output_path)      # 任意本地文件复制原语
```
`url` 来自爬虫的 `cover`/`big_covers`（**远端站点 HTML 可控**）→ 返回 `/etc/passwd` 等会被拷到 fanart 路径。
另 `base.py:216-233` 下载**不校验 Content-Length、无字节上限、无剩余空间检查**→ 恶意响应可写满磁盘。

**缓解事实**：`stream=True` + 1024 字节分块，**不会整图入内存**（不存在超大图撑爆内存）。
严重性取决于是否能控制站点响应（`proxy_free` 镜像域名可被配置改写，见 P0-3）。

**整改**：`download` 校验 scheme 只允许 http/https；加最大字节数上限。

### 【P1-16】`_scraper_monitor` HEAD 失败回退成 POST

**证据**：`javsp/web/base.py:60-71` 回退只区分 `get` 与"其他"→ **HEAD 请求失败时会回退成 POST**。
`base.py:58` `__head` 绑定同一 wrapper。调用点 `javdb.py:209` `request.head(movie.cover)`（校验封面存在性）
会变成 POST 语义→ 对接受 POST 的服务器**以登录凭据发起写操作**。

**整改**：回退分支按被包装的函数分别处理。

---

## 第四部分：前端问题（均非 XSS）

### 已核查**无问题**的项（重要 reassuring 结论）

- **XSS**：全前端**无** `v-html`/`innerHTML`/`dangerouslyUseHTMLString`；刮削数据全走 Vue 文本插值自动转义。
  `el-alert :title` 是文本节点渲染（element-plus 用 `createTextVNode`）。**无 XSS**。
- **前端内存泄漏**：全前端**无** `setInterval`/`addEventListener`/`watch`/`EventSource`/`onUnmounted`——无需清理；增长型集合每轮重置。
- **CSRF**：所有写接口用 `fetch` + `Content-Type: application/json` → 触发预检，服务端无 CORS 中间件 → **跨站请求被浏览器阻断**。
  （但 DNS Rebinding 是真实高危路径，见 P0-2。）

### 【P2-1】`batch.running` 可能永久卡死（状态机缺陷）

**证据**：`frontend/src/App.vue:433-462` `doBatch` 只在「收到 `all_done`」（:452）或「抛异常」（:458）时复位 `batch.running=false`。
若 SSE 流**正常结束却没收到 `all_done`**（后端生成器中断/代理截断）→ 两个复位分支都不执行→
按钮 `:disabled="… || batch.running"`（`App.vue:17,20`）**永久禁用**，只能刷新页面。

**整改**：`try/catch/finally`，`finally` 里无条件复位 + 用 `all_done` 标志判断真完成。

### 【P2-2】SSE 客户端无重连/超时/取消

**证据**：`frontend/src/api.js:62-101` `consumeSSE` — 无重连（断连即 `resolve()`）、无超时（`reader.read()` 可无限挂起）、
无 `AbortController`（无法中断）、`reader` 未 `cancel()`/`releaseLock()`。另 `api.js:63` `new Promise(async …)` 是async executor 反模式。

**整改**：加超时与 `AbortController`；组件卸载时取消。

### 【P2-3】`api.js` 错误解析无 `.catch` 兜底 + 422 detail 是数组

**证据**：`frontend/src/api.js:10/28/35/45` `await r.json()` 无 catch（对比 `api.js:71` 有）。后端返回非 JSON（反代 502）时抛 `SyntaxError`替换真实错误；
422 时 FastAPI detail 是**数组** → `new Error([...])` 变 `[object Object]`。

**整改**：统一封装 `readDetail(r)`，`.catch` 兜底 + `Array.isArray` 归一化。

### 【P2-4】`el-table` 未虚拟化

**证据**：`App.vue:33/76` 用 `max-height` 但 `:data` 是完整数组（非虚拟滚动，只限容器高不限行数）。批量 2000 部→2000 行×7 列全量 DOM 卡顿。
**整改**：改 `el-table-v2` 或分页。

### 【P2-5】`config.yml` 中 `retry:0` 等边界值前端未设防

`App.vue:160` `:min="0"` 允许 `retry=0`（见 P0-6）；`output_folder_pattern` 输入框（`App.vue:192`）无校验。

---

## 整改路线建议

### 第一优先（安全基线，改动极小，建议尽快做）

1. **翻译密钥脱敏日志**（P0-1）——5 处字符串，改 `engine` → `engine.name`。
2. **默认绑 `127.0.0.1` + 加 `TrustedHostMiddleware`**（P0-2）——2 行。
3. **`config.yml` 从 git 移除**（P0-5）——`.gitignore` 加一行。
4. **`lib.py` 正则转义**（P0-4）——1 行，复用已有 `re_escape`。
5. **`/api/config` 密钥掩码**（P0-3）——加一层序列化脱敏。

### 第二优先（正确性）

6. **`retry` 下限改 1**（P0-6/P1-6）+ 其他资源字段加上界。
7. **`_root_save_dir` 落地前 realpath 收敛**（P1-7）。
8. **配置回滚改原子写**（P1-8）+ TOCTOU 纳入锁（P1-10）+ 换行注入补字符类（P1-9）。
9. **恢复 CLI 的 `except`**（P1-11）+ `move_files` 计入进度条（P1-12）+ 守卫补断言。
10. **前端保存保留 api_key**（P1-13）。

### 第三优先（性能收益，按性价比）

11. **`get_pic_size`/`valid_pic` 零解码改写**（P1-4）——**改动最小、收益最确定**，单这一项可省 500 张图 50-125s + 数百MB峰值。
12. **`overall_timeout` 修误杀**（P1-1）——小改动，避免丢站点结果。
13. **剧照循环并发 + 去尾部 sleep**（P1-2）——收益最大（消掉 4-8 小时纯睡眠）。
14. **`TaskStore` TTL 用 heapq**（P1-3）——消除扫描 15-30s 与 API 阻塞。

### 第四优先（纵深防御/健壮性）

15. `download()` 限 scheme + 大小上限（P1-15）；`translate.py` 补 timeout + 限重试（P1-14）。
16. SSE 生成器超时 + 取消（P1-4 in earlier list / §2.4 agent）；前端 finally 复位（P2-1）+ 超时/取消（P2-2）。
17. `output_folder_pattern` 模板占位符校验；`on_id_pattern` 正则预编译校验。
18. `desktop.py` 固定端口 + `sleep(2)` 竞态改健康探测。

### 建议补充的回归守护（沿用项目既有风格）

- 新增 `verify_resource_bounds.py`：断言配置字段的 `ge/le` 生效（`retry=0` 被拒等）。
- 新增 `verify_regex_safety.py`：断言 `avid` 含元字符时不改变正则语义、不产生 ReDoS。
- 强化 `verify_output_toggles.py` 的 CLI 守卫：所有 `check_step` 分支都有 `total_step` 累加。
- 新增配置脱敏断言：`/api/config` 响应中不得出现明文 `api_key`。

---

## 审查方法学备注（供后续复用）

本次审查刻意遵守的几条原则（源自项目既有教训）：
- **不凭报告下结论**：4 个 agent 的findings 全部由主审复验高影响项，剔除了「`shutil.move` 慢」「`Image.open` 泄漏」
  等前提有误的结论（前者同卷即 `os.rename`；后者经核对 Pillow 无 `__del__`，CPython 引用计数即回收）。
- **区分「真瓶颈」与「理论问题」**：性能项全部给出定量估计，并把 7 项「实测影响<1%」明确标为不必改，避免无意义优化。
- **验证触发条件**：如 `retry=0` 静默失败需追踪到 `core.py:124` 的 `success` 标记链才确认，而非只看类型定义。