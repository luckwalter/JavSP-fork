# JavSP-fork 版本迭代与问题修复记录

> 维护者：@luckwalter ｜ 基线：派生自 Yuukiy/JavSP ｜ 目标：将原 CLI 刮削器改造为带 WEBUI 的功能软件
> 版本号以 `pyproject.toml` 的 `version` 字段为准（静态管理）。
> 本文件为 fork 独立 changelog，不与上游 `CHANGELOG.md`（记录到上游 v1.8）混淆；每次发版维护此文件。

---

---

## v0.3.12（2026-10-24）

**批量整理改为一次确认完成（对应 issue #3）：不再逐部弹「确认写入磁盘」；护栏换成处理中的「停止」按钮；新增「整理落盘: 批量一次确认」开关**

### 背景

「整理落盘」页批量整理选中项时，每部影片都会弹一次「确认写入磁盘」。这是当初**刻意**设计的护栏（避免一次性误操作大量文件、逐部可叫停），但批量场景下过于啰嗦（issue #3）。

### 改动

- **一次确认完成（默认）**：批量入口确认一次后连续处理全部选中影片，不再逐部弹框。
- **护栏换成「停止」按钮**：处理中显示「正在整理 x/N 部…」，点「停止」当前这部做完即止，剩余的不再执行。
- **新增开关「整理落盘: 批量一次确认」**：放在「刮削队列」页（与批量操作同处一屏）；存 `localStorage`（前端交互偏好，**不写入 config.yml**），与「整理落盘」页共享同一键。关闭则回到逐部确认；逐部模式的确认框里另有「本次不再逐部确认（一次处理全部）」复选框，可做单次临时覆盖（只影响本次）。
- **单部「整理」按钮始终逐次确认**（防手滑）。
- **作用域**：开关只作用于「整理落盘」页的「整理选中项」。队列页「仅刮削」不改文件、本就无确认；「刮削并整理」本就是一次确认（后端 `/api/batch` 在同一条 SSE 流里连续执行 `organize_movie`，没有逐部批准的插点，做不成逐部确认）。

### 实现

- `ConfirmDialog` 新增可选 `option` 复选框 prop，confirm 事件回传勾选状态；`App.vue` 的 `askConfirm` 带 option 时 resolve `{ option: bool }`，不带时仍 `resolve(true)`（现有调用点均为 truthy 判断，向后兼容）。
- `usePersist` 抽出通用读写函数，新增 localStorage 版 `persistLocal` / `saveLocal`（会话级 sessionStorage 不适合长期偏好）。

### 验证

- Playwright + mock API 端到端三场景全过：一次确认模式弹窗**仅 1 次**且 3 部全部处理；处理中点「停止」后剩余不再处理（organize 仅调用 1 次）；逐部模式确认框带复选框、勾选后不再逐部弹框。console / page 错误 0。
- 开关跨页一致性：队列页切换写入 `localStorage`，「整理落盘」页的确认框随之在「一次确认（无复选框）」与「逐部确认（带复选框）」间正确切换；设置页「外观」组已无该开关。

---

## v0.3.11（2026-10-24）

**修复 Windows 10 下桌面版 exe 启动后空白窗口（缺失 WebView2 Runtime / .NET Framework 过旧时，pywebview 会静默回退 IE 内核）；桌面壳新增环境预检与自动降级；前端产物语法基线下调至 es2017**

### 问题根因（对应 issue #2）

桌面版默认用 pywebview 开原生窗口加载 WebUI。pywebview 5.4 在 Windows 上通过**注册表**判定能否使用 Chromium 内核：
- 查 `Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}` 的 `pv`（Edge / WebView2 Runtime 版本）；
- 查 `HKLM\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full` 的 `Release`（.NET Framework 4.x 版本）。

任一不满足即**静默回退到 IE 内核（MSHTML）**（`webview/platforms/winforms.py` 的 `_is_chromium()` → `renderer = 'mshtml'`），既不抛异常也无任何提示。而本项目前端是 Vue3 + Vite 产物（ES2020 级语法），在 IE11 下必然无法渲染 → 用户只看到一个**空白窗口**。

Win11 系统自带 WebView2 Runtime 与 .NET Framework 4.8，因此正常；Win10 需另行安装 WebView2 Runtime（Edge 被卸载 / 长期未更新 / 企业策略禁用 Edge 的机器上不存在），故复现。

`--nogui` 同样白屏，是因为它用 `webbrowser.open` 打开**系统默认浏览器**——Win10 的默认浏览器常为 IE 或旧版 EdgeHTML，内核同样过旧。

### 修复

1. **桌面壳环境预检**（`javsp/desktop.py`）：新增 `_webview2_version()` / `_netfx_release()` / `_check_gui_env()`，开窗口前主动检测 WebView2 Runtime 与 .NET Framework（需 4.7.2+）。不满足时不再静默白屏，而是弹窗给出**明确原因与修复指引**（WebView2 官方下载地址 / 改用 `--nogui`），并**自动降级为浏览器模式**（打印访问地址，功能仍可用）。
2. **杜绝 IE 回退与可观测性**：窗口模式显式设置 `PYWEBVIEW_GUI=edgechromium`；pywebview 调试日志接入 `JavSP.log`，渲染层异常可直接查日志定位。
3. **前端产物基线下调**（`frontend/vite.config.js`）：`build.target` 由 Vite 默认（es2020 / modules，≈Chrome 87）显式设为 `es2017`，兼容 Win10 上版本较旧的 WebView2 内核（Chrome 60+）。

### 用户侧临时解法（升级前）

安装 Microsoft Edge WebView2 Runtime（https://developer.microsoft.com/microsoft-edge/webview2/），或用 Chrome / Edge 打开 `http://127.0.0.1:8000`。

### 验证

- 桌面 exe 实跑：`/api/health` 回 `{"status":"ok","version":"0.3.11"}`；日志确认新预检生效（`窗口环境检查通过: WebView2 Runtime 155.0.4283.62, .NET Release 534296`），pywebview 走 Chromium 内核（`Using WinForms / Chromium`）且页面 `loaded` 事件正常触发（非白屏）。
- NAS 部署实测全绿：`javsp-fork:0.3.11`（`31a36423979e`）重建容器（含 `-e JAVSP_TLS_VERIFY=0`）→ `/api/health` 报 `0.3.11`；挂载源校验为真实卷 `/share/CACHEDEV1_DATA/javsp_config`；前端资产自洽（容器内提取首页引用 `/assets/index-Bez_nPwM.js` 再 GET 200）；代码标记校验（方案A / P0-2 / v0.3.11 桌面预检）全部 OK；旧容器已删。
- Windows 桌面版本次仅部署本机 `C:\JavSP`（实跑 0.3.11 通过，旧目录备份 `C:\JavSP.bak20261024`）；网盘部署位置待 NAS 卷恢复后再同步。
- GitHub Release：镜像 + exe 双资产上传成功（镜像 sha256 `b55c50bb94ff0898235bbb1d30c50203cda27f08e543d16624d10dc2d86c5632`，exe sha256 `b1f27cd9b0636ff74ad543c457760ae46e60c93607b75e57f1bde5a38d133600`）。

---

## v0.3.10（2026-10-22）

**设置页「整理时移动文件」开关置顶 + 关闭时窄提示条；关闭移动后元数据文件名跟随视频原名；封面下载容错（缺封面 / 下载失败不再整部整理失败）**

### 方案A：关闭「整理时移动文件」时元数据文件名跟随视频原名

此前关闭移动、只做整理时，NFO / 封面 / 背景图仍按命名模板生成（如 `番号-标题.nfo`），与视频文件不同名，Jellyfin / Emby / Kodi 无法按本地命名约定自动匹配元数据与图片。`javsp/core.py` 的 `generate_names(movie)` 两处（循环内 early-return + 循环外 fallback）改为：`move_files=False` 时，NFO 文件名用视频原名（`原名.nfo`），封面 / 背景图用 `原名-poster.jpg` / `原名-fanart.jpg`；`move_files=True` 时仍按模板生成。

### P0-2：封面下载容错（不再因缺封面整部整理失败）

- `download_cover` 失败日志由 `error` 降级为 `debug`，避免无封面影片刷屏误报。
- `organize_movie` 封面分支重构为两路：
  - 所有源都没提供封面 URL（`covers` 与 `big_covers` 皆空）→ 标记 `skipped cover`，**不报失败**（对齐上游 #316：只做 nfo / 整理）；
  - 真有封面 URL 但下载失败 → `status` **维持 `ok`**（不再覆盖为 `cover_failed`），记 `result['cover']='failed'` + `errors` + `warning`，流程继续完成 nfo / 重命名，避免被误判成整部整理失败（文件其实已生成 nfo 并完成重命名）。

### UI：设置页「整理时移动文件」置顶 + 关闭时窄提示条

`frontend/src/views/SettingsView.vue`「输出与命名」区块重构：`整理时移动文件` 开关置顶并高亮（带引导文案）；关闭时下方三个文件命名控件（输出目录模板 / 文件名模板 / NFO 文件名）整体替换为一条很窄的虚线提示条（高度压缩到不超过 1/3 页高），避免遮挡下方的 NFO 标题格式设置；NFO 标题格式始终可用并置于窄条下方。`frontend/src/components/NamingRuleBuilder.vue` 新增 `disabled` prop（默认 false），根元素加 `.naming-builder--disabled` 置灰样式（`pointer-events:none`），供关闭移动时整体禁用。

### 依赖清理

前端移除遗留未使用的 `element-plus` 及其图标依赖（`@element-plus/icons-vue`），相关 UI 组件此前已重写为原生 / 轻量实现，`frontend/package-lock.json` 同步清理。

### 验证

- `sync_version.py --check` 门禁通过：pyproject / package.json / package-lock.json / README 四处版本一致为 0.3.10
- 改动文件换行符逐一核验（CRLF=0，未被同步脚本污染）
- 单测覆盖：P0-2 三场景（无封面源 / 真失败 / 成功）全过；方案A 8 项（关移动→原名 / 开移动→模板）全过
- 桌面 exe 与 NAS 部署热更实测由主人在 WebUI 验收通过（设置页开关置顶 + 窄提示条、关闭移动后元数据原名、缺封面不再报错）

---

## v0.3.9（2026-10-21）

**jav321 支持 CID 维度双源刮削 + 渠道探活覆盖两条路径；部署固化 TLS 全局放宽**

f216f0a 的改动此前只停留在本地工作区、从未随 v0.3.8 进入 tag，运行态也被一次镜像重建冲掉。本次正式纳入发版。

### jav321 支持 CID 维度（双维度源）

jav321 是 DMM 数据镜像站（封面 hotlink `pics.dmm.co.jp`），影片页寻址即 DMM Content ID，天然适合承担 CID 维度兜底。`javsp/web/jav321.py` 的 `parse_data` 查询词由「仅 dvdid」改为「dvdid or cid」：此前 cid-only 影片（手动指定 `cid:ipx00001`）因 `sn=None` 必然 `MovieNotFoundError`，现可正常刮削。2026-10-21 NAS 实测 `sn=ipx00001` 与 `sn=IPX-001` 返回同一影片页且字段一致。

### 渠道探活覆盖两条路径（health.py）

`javsp/web/health.py` 新增 `_samples_for` 支持多样本：jav321 双维度各测一次（dvdid `IPX-001` + cid `ipx00001`），任一不通即报，reason 标注样本与路径，避免只验 dvdid 路径时 cid 挂了页面仍显示 ok 的假绿。

### 前端刮削页双类别渲染

`frontend/src/utils/sourceMeta.js` 的 `SOURCE_KINDS.cid` 增加 jav321，刮削页该行渲染「普通 + CID」两个类别 chip，CID 维度可独立启用/停用。

### 部署固化 TLS 全局放宽

全局 TLS 校验开关 `JAVSP_TLS_VERIFY=0`（由 `javsp/web/base.py` 的 `tls_verify()` 读取，驱动 Request / request_get / request_post / is_connectable / urlretrieve 的 `verify` 参数）此前作为容器运行时环境变量生效，但重建脚本 `docker run` 漏配会丢配置。现部署脚本统一带上 `-e JAVSP_TLS_VERIFY=0`，容器重建不再回退到严格校验（NAS 出网经 squid 代理，自签/中间证书场景需要）。

### 验证

- `sync_version.py --check` 门禁通过：pyproject / package.json / package-lock.json / README 四处版本一致为 0.3.9
- 改动文件换行符逐一核验（CRLF=0，未被同步脚本污染）
- 桌面 exe 实跑验证：`dist/JavSP/JavSP.exe --nogui` 起服务，`/api/health` 回 `{"status":"ok","version":"0.3.9"}`，日志确认 Uvicorn 在跑（exe 11.5MB > 10MB，uvicorn 确认打包）
- NAS 部署实测全绿：`javsp-fork:0.3.9` 重建容器（含 `-e JAVSP_TLS_VERIFY=0` 全局放宽 TLS）→ `/api/health` 报 `0.3.9`；前端资产自洽（容器内提取首页 `/assets/index-Bys7euhJ.js` 再 GET 200）；
  jav321 CID 双维度 + 探活双路径代码标记（`movie.dvdid or movie.cid` / `_samples_for`）确认在镜像内；旧容器已删、新容器 `javsp-fork:0.3.9` 运行

---

## v0.3.8（2026-10-21）

**番号还原修复：无分隔符/带分隔符前导零归一化 + R18-XXX 整理崩溃**

源自实测反馈：`cawb00035hhb_rfdertr-v6ltx.mp4` 无法还原为 `CAWB-035`，整理失败。排查发现相关修复代码早已写在工作区却从未提交（`git log -S normalize_number` 无任何记录），release 产物里根本没有——先补提交，再审出同源缺口一并修复。

### 无分隔符番号前导零归一化（`javsp/avid.py`）

无分隔符兜底分支（`([A-Z]{2,})(\d{2,5})`）此前直接拼接原始数字：`cawb00035` 被还原为 `CAWB-00035`，与站点实际收录的 `CAWB-035` 对不上，刮削必然失败。新增 `normalize_number`：去掉前导零并至少保留 3 位（对齐 JavBoss normalizeNumber）——`00035`→`035`、`0001`→`001`、不足 3 位补零（`12`→`012`）。

### 修复 R18-XXX 影片整理崩溃（commit 4374e43 引入）

`R18-?\d{3}` 正则没有捕获组，代码却写 `match.group(1)`，所有 `R18-XXX` 影片在整理时直接抛 `IndexError: no such group`，崩掉整个流程。改用 `group(0)` 返回完整匹配。此问题由本轮回归测试意外挖出，与主 bug 无关联但同文件。

### 带分隔符番号同样归一化（消除双分支不一致）

带分隔符分支（`([A-Z]{2,10})[-_](\d{2,5})`）此前直接返回原始数字：同一影片的 `cawb00035` 与 `CAWB-00035` 两种文件名会解析出 `CAWB-035` 和 `CAWB-00035` 两个不同番号，多源交叉刮削时对不上。现该分支同样过 `normalize_number`，两个分支行为对齐。

### 验证

断言式回归 26/26：核心案例 `cawb00035hhb_rfdertr-v6ltx.mp4` / `CAWB-00035.mp4` → `CAWB-035`；R18 三种写法 → `R18-123`；3 位番号（IPX-001 / ABP-123 / T28-557 / FC2-718323 / 259LUXU-123）零影响。NAS 热部署后于容器内（服务同款 venv 解释器、复现 argv）实测 8 案例全过，确认测试通过后发版。

---

## v0.3.7（2026-10-18）

**第三轮全库审查整改：死代码清理 + `/api/movies` 500 同源缺陷修复 + 会话清理接线 + import 卫生**

依据 `review_2026-10-18.md`（全库 AST 死代码扫描 + v0.3.4~0.3.6 新增代码安全/性能审查）执行，全部验证通过后提交。

### 死代码清理（12 处 + 1 处 API 面函数）

- `javsp/web/health.py`：删 `probe_all_background`、`_is_skippable`、`allowlist_names`、`skipped_sources`、`format_report`（均零引用；自动探活走 `start_auto_probe`，端点走 `probe_all`）
- `javsp/task_store.py`：删 `is_active`、`stats`
- `javsp/web/base.py`：删调试转储函数 `dump_xpath_node`
- `javsp/web/javdb.py`：删 `get_valid_cookies` 及其连带死代码 `get_user_info`（Cookie 池实际逻辑在 `get_html_wrapper` 内联，`cookies_pool` 仍在使用故保留）
- `javsp/web/javfree.py`：删 `_pick_number`
- `javsp/file.py`：删上游移植的 `is_remote_drive`（内含 bug：`platform != 'win32'` 拿模块与字符串比较，恒返回 False）
- `javsp/core.py`：删 `preview_metadata`，README 对 core 暴露函数的描述同步更新
- `frontend/src/utils/sourceMeta.js`：删 `KIND_FULL` / `KIND_HINT`（前端构建复验无引用）

### P1 修复：`_is_organized` 裸 `os.listdir`（与 v0.3.6 scan 500 同源）

`server.py` 的 `_is_organized` 对整理落盘目录 `save_dir` 直接 `os.listdir` 且无异常保护：目录权限被收紧（或 `output_folder_pattern` 配了指向系统目录的绝对路径）时抛 `PermissionError`，把 `/api/movies` 与单部查询一起打成 500。现改为读不了即返回“未整理”，不再扩散成接口级故障。

### 会话清理接线（`prune_sessions`）

`auth.prune_sessions` 的 docstring 写明“供 lifespan 定期调用”却从未接线：过期后再无人携带的会话会无限残留在 `.sessions.json`。现在 lifespan 启动 `session-prune` daemon 线程（周期 1 小时）执行原设计意图。

### 工程卫生

- `_list_volumes` 中函数体内的 `import string` 提升到模块级（避开“函数体内动态 import”这一 PyInstaller 历史事故模式）
- 清理 20 个未使用的 import（`__main__.py` 9 个，`config.py`/`fanza.py`/`javbus.py`/`translate.py`/`server.py` 各若干）

### 验证

全库 py_compile、14 个改动模块 import 冒烟、unittest 6/6、前端 `npm run build`、`_is_organized(PermissionError)` 实测返回 False、`session-prune` 线程随 lifespan 起停实测、`sync_version --check` 通过、15 个改动文件换行符逐一核验。

---

## v0.3.6（2026-10-17）

**目录选择器跨盘符/网络盘定位修复（经两次返工） + 扫描无权限目录 500 修复 + 部署收尾清理工具**

### 桌面版目录选择器（`javsp/server.py`、`frontend/src/api.js`、`frontend/src/views/LibraryView.vue`）

主人实测两个场景：exe 装 `C:\JavSP` 时「回上层只能到 C 根、选不了别的盘符、手输路径报扫描失败」；exe 装 `\\HOMENAS\Downloads\JavSP` 时「目录列得出来但点了没反应，手输 Leak 才成功」。

- **根因（同一个函数的平台含义不一致）**：`_browse_root()` 默认值是字面 `'/'`，意图表达"不限制"，但各平台实际含义完全不同——
  - Windows 本地：`realpath('/')` == **当前驱动器根**，隐式锁死 C 盘；跨驱动器 `commonpath` 抛 `ValueError` → 判越界 → 静默回退；
  - Windows UNC：`realpath('/')` == 共享根，叠加尾斜杠差异，`commonpath` 抛 `Can't mix absolute and relative paths`（ntpath 把整个 `\\host\share` 当 drive，剩下的 `\Leak` 被视作**相对路径**）→ 子目录判越界 → **静默重置回根**，这正是"点了没反应"的真相。
- **第一次修复（01799ad）留了盲区**：只改了后端，`api.js` 的 `path || '/'` 兜底把空值变成 `'/'`，而 **`'/'` 是 truthy** → 直接绕过后端 `if not path:` 缺省分支，前端仍把选择器怼到盘符根。**教训：验证后端修复必须走真实前端请求链路，只调后端函数会漏掉"上游把空值变成 truthy"这类问题。**
- **修复内容**：
  - `_browse_root()` 未设置 `JAVSP_BROWSE_ROOT` 时返回 `None` 明确表示"不限制"，不再依赖 `'/'` 的平台歧义；
  - 新增 `_norm_for_compare` / `_split_drive` / `_path_parts`：两侧同规则归一后**按路径分量逐级比较**，绕开 `commonpath` 在 UNC 上的缺陷（分量比较还天然避免 `/data` 误匹配 `/database`）；
  - 所有 Windows 特性（盘符补斜杠、正斜杠归一、UNC 长路径前缀、盘符切换入口）收进 `os.name == 'nt'` 分支，**Linux 路径语义不受影响**；
  - `api_browse` 缺省落点改为 exe 目录（源码运行为项目根），并把 `'/'` 也视作"未指定"（防旧版前端/手输 `?path=/`）；
  - 新增 `_list_volumes()`：已挂载盘全列 + 空盘位最多补 3 个（不刷屏），UNC 共享根下改列同服务器的**真实共享**；
  - 选择交互：双击选中 → **显式「选中」按钮**（双击在触屏/远程桌面不可靠），`:key` 改用完整 path；新增**路径直达输入框**（可粘 `D:\` 或 `\\主机\共享\目录` 回车进入）。
- **安全边界未放松**（主人明确要求）：Docker 镜像内置 `ENV JAVSP_BROWSE_ROOT=/data`（`docker/Dockerfile:36`），"不限制"只对桌面版生效。`/data-secret`、`/database`、`/etc`、`/etc/passwd`、`/var/run/secrets`、`..` 逃逸、跨驱动器、兄弟共享 `\\host\share2` 全部正确拒绝。**Windows 本机 21/21 + NAS 容器内真实 Linux 15/15 全通过**（含新增的 `path='/'` 兜底后仍落 `/data`）。

### 扫描无权限目录时 /api/scan 报 500（`javsp/file.py`）

主人反馈"选到 C 盘其他目录还是报错"——这次报错来自**扫描**而非浏览，是独立缺陷，只测 browse 永远发现不了。

- **根因**：`scan_movies` 为判断"带 nfo 的目录"会**额外 `os.listdir` 每个子目录**，这行是裸调用。Windows 上存在一批连管理员都读不了的目录（实测 `C:\Users\Default User`），`PermissionError` 冒泡中断整次扫描 → `/api/scan` 500。**注意 `os.walk` 本身对不可读目录是宽容的，是这行额外的 listdir 击穿的。**
- **修复**：该 `listdir` 包 `try/except OSError`（跳过并从 `dirnames` 移除，避免后续遍历再踩 + debug 日志）；`os.path.getsize` 同样防护；`os.walk` 加 `onerror` 回调补日志；原 `print` 改 `logger.debug`。
- **验证**：`scan_library('C:\Users')` 由抛异常 → 正常返回；`/api/scan` 对 `C:\Users` 200；**不存在的路径仍正确 400**（没把错误一股脑吞掉）。

### 桌面 exe 构建修复（`build/pyinstaller.spec`）

- v0.3.5 首次发布的 exe **启动即 `ModuleNotFoundError: No module named 'uvicorn'`**（本地部署时跑起来才暴露）：`desktop.py` 在**函数体内**动态 import `uvicorn` 与 `javsp.server`，PyInstaller 静态分析抓不到，而 spec 的 hiddenimports 只列了 `javsp.web`/`webview`。改用 `collect_submodules('uvicorn')` 并补 `javsp.server`。

### 新增部署收尾清理工具（`tools/cleanup.py`）

- 部署/测试收尾时把过程版本与备份在确认后一起删掉：默认 dry-run 预览清单 → `--apply` 才删（先看后删）；只删白名单（本机版备份/中转目录 + 工作区临时打包/一次性脚本）；主部署目录 guard 保护永不删。
- 支持多部署位置（本地 `C:\JavSP` 与网盘 `\\HOMENAS\Downloads\JavSP`），从各部署位置的父目录推导扫描范围。

### 已知行为（非缺陷）

- 本机物理只有 C 盘时，D: 等盘符在选择器里置灰、点击提示"未插入/未挂载"，需用路径直达输入框选网盘目录。
- `subst` 造的别名盘经 `realpath` 会解析成真实目标，别名盘**无法绕过** `JAVSP_BROWSE_ROOT` 边界（已实测：指向根外判越界、指向根内放行）；目录联接（junction）指向根外同样被拒绝。

---

## v0.3.5（2026-10-17）

**刮削源页重设计（明确总开关 + 分类归并 + 源地址显示）+ 全局任务进度常驻 + 设置页去重 + 第二批审查修复**

### 刮削源页（ChannelsView）全面重设计

- **加回明确的行内「启用/停用」总开关**：重设计后把开关做成「点灭类别 chip」的隐晦操作，缺少明确总开关；现每行源加一个滑动开关（与设置页同视觉语言）。总开关关 → 从 normal/fc2/cid 三类清单移除 + 整行淡化（但仍完整可见，区别于「未启用」筛选）。
- **启用口径锚定「能力集」**：`draftEnabled` 改为「只在源真实支持的维度（SOURCE_KINDS 来自 config.py 的 CrawlerSelect 五维）被勾选才算启用」，消除「开启却不参与刮削」的自相矛盾；`offCount` 只统计有能力集的源（dl_getchu/gyutto 等不在任何维度的源不渲染开关与类别 chip，仅作只读展示，不计入启用/未启用统计）。
- **分类归并到单一数据源**（新增 `frontend/src/utils/sourceMeta.js`）：源清单/分组/预设/站点说明集中维护，供刮削源页与设置页共用，避免两处各写一份漂移。按身份分四组——普通番号库 / FC2 专题 / 厂商官方 / CID 专题；去掉「不参与刮削」矛盾措辞，统一为中性身份描述（如 `getchu 官方下载渠道`）。
- **加回源地址显示**：源名下方显示探活实测域名 `domain_actual`（来自 `/api/channels` 的 `domain`/`domain_actual`/`domain_mismatch`，由 `health.py` 的 `_decorate` 注入），`domain_mismatch` 为真时红字「与实测不符」+ 悬浮提示「可能改过 base_url / 走了镜像」，便于手动验证。
- 视图筛选改为 all / abnormal（有异常）/ off（未启用）三态；类别 chip 改为真 `<button>`（roving tabindex 键盘可达），点亮 = 入该类清单；窄屏行折叠为卡片。预设（推荐方案 / 仅核心源）一键设定三类清单，进「未保存」态由用户点「保存并应用」落盘。

### 全局任务进度（新增 useTask 组合式）

- 新增 `frontend/src/composables/useTask.js`：模块级 `reactive` 单例，批量刮削/整理进度经 `taskStart / taskProgress / taskFinish` 上报到 App，底部状态栏常驻显示，切页面也不丢进度（QueueView / OrganizeView 接入）。start/finish 成对，中断或异常 `finally` 也清理，避免状态栏卡在假进度。
- App 新增**离开守卫**：子页面可注册守卫，未保存选源草稿切页先问是否放弃；登出时清本标签页 `javsp:*` 会话级 UI 状态（影片库/队列/日志等），避免换账号后复现上一位用户数据。

### 设置页去重（SettingsView 大改）

- 把「选源能力」从设置页抽离到刮削源页 + `sourceMeta.js` 单一数据源，设置页只保留刮取行为的**高级参数**（并发/命中字段/javdb 封面策略等），消除两处维护同一份源清单导致的漂移风险。

### 导航与文案

- NavRail「渠道监控」→「刮削源」，hint 改「选源与状态」。
- `api.js` 错误提示中文化（scan failed→扫描失败、health check failed→健康检查失败 等）；`getAuthStatus` 后端不可达（如 502 代理 HTML 页）不再抛 SyntaxError，统一转可读错误。
- LookupView：抓取过程状态映射为中文短词 + 语义色（命中=绿 / 未收录=灰 / 被拦截=红 / 网络错误=黄，去掉 `javsp.web.` 前缀噪声）；整理前破坏性操作加二次确认（与整理页同文案模板）；结果区显示「来源合并自 N 个渠道」。
- `usePersist` 增加类型一致性校验，旧版本残留/别的页面写过同键导致类型不符时回退默认值，避免模板 `v-for` 抛异常白屏。

### 第二批审查修复（安全 / 并发 / 配置）

- **javdb.py 安全加固**：移除写死的 `verify=False`，改走全局 TLS 策略（`JAVSP_CA_BUNDLE`/`JAVSP_TLS_VERIFY`）；javdb 重定向登录页会携带浏览器解密得到的登录 Cookie，关闭校验等于把会话凭据暴露给同链路 MITM。实测 javdb.com 证书链完整，无需放宽。
- **auth.py 会话落盘并发安全**：原实现并发登录/滑动续期时两个线程写同一 `path+'.tmp'` → 内容交错损坏 → `os.replace` 后磁盘会话表变半截 JSON，重启静默丢失全部会话；另有线程字典迭代期间被改 → `RuntimeError(dictionary changed size during iteration)`。现改为**锁内浅拷贝快照 + `tempfile.mkstemp` 唯一临时名**，写失败 `finally` 清理垃圾临时文件。
- **config_io.py 配置保真**：`_needs_quote` 增加「`-` 开头的块序列指示符」引用保护，避免 `key: - abc` 写回 YAML 后被解析成列表（值被静默篡改）。
- `base.py` 注释修正（内网自签证书站点示例）。

### 验证

- 后端 `py_compile` 通过；前端本地 playwright 全流程实测通过（17 个开关、关→变灰+保存栏、点 chip 恢复、未启用过滤、源地址显示）；`sync_version.py --check` 门禁 EXIT=0；所有改动文件工作区保持纯 LF（无 CRLF 污染）。

## v0.3.4（2026-10-17）

**全仓库审查修复（性能/安全 4 项）+ 版本对齐工具加固 + Logo / 超时 / 布局**

### 全仓库代码审查修复（第一批）

- **修复目录浏览根路径判断缺陷**（`javsp/server.py`）：原实现用 `startswith(root + os.sep)` 做边界比较，`root='/'` 时拼出 `'//'`，所有子目录都被判越界——源码运行 / Windows 桌面版（默认 `root='/'`）的目录选择器点任何子目录都回落根目录。改用 `os.path.commonpath` 按路径分量比较；不同驱动器（Windows `C:`/`D:`）安全返回 False。Docker 部署（`JAVSP_BROWSE_ROOT=/data`）不受此影响。
- **修复 `--nogui` 形态在无 Chrome 环境打不开浏览器**（`javsp/web/base.py`）：`open_in_chrome` 原先硬编码 Chrome 路径、以 `shell=True` 字符串拼接启动，还被模块级全局覆盖到 `webbrowser.open`——未安装 Chrome 的机器上桌面版浏览器形态直接失效。现改为存在性检查 + 回退系统默认浏览器 + 列表参数启动（同时消除 url 经 shell 解释的面）。
- **修复 SSE 读取超时后连接悬挂**（`frontend/src/api.js`）：`consumeSSE` 超时 reject 时不断开底层连接，HTTP/1.1 同域 6 连接被逐次占满后全站请求排队（与 v0.2.2「点了没反应」同机理）。现超时/出错路径必定 `abort()` 断开并安全释放 reader。
- **修复单部查询整理结果无反馈**（`frontend/src/views/LookupView.vue`）：前端等待的 `d.done`/`d.error` 字段与后端实际协议（`type:'result'` / `type:'error', msg`）错配——整理（移动/重命名文件，破坏性操作）成功与失败均静默无提示。已对齐协议，并补充「正在整理…」阶段提示。

### 版本对齐工具加固

- **修复 README「当前版本」长期静默漂移**（`sync_version.py`）：`_CURRENT_RE` 只匹配 `当前版本：**x**` 旧格式，而 README 实际写法是 `**当前版本：x**`，该处从 0.3.2 起停在旧版本，`--check` 还被「徽章匹配即放行」的逻辑洗白成 OK。现正则兼容两种格式，且徽章 /「当前版本」两个标记任一缺失或写法变化都会显式报错，不再静默放行。
- README 版本规则段补充跨环境校验（`--check-target`）与防呆行为说明。

### Logo / 超时 / 布局（v0.3.3 发版后累积）

- 左上角新增 Logo：上游 `image/JavSP.svg` 是 foreignObject 内嵌 HTML 的假 svg（Chromium 渲染空白，不可用），弃用；改用 `image/JavSP.ico` 提取 SP 橙块放大生成 `frontend/public/logo.png`，favicon 同步更新（32/48 双尺寸）。
- 修复 `network.timeout` 设置项退回 ISO8601 文本输入的回归（v0.1.11 做过按秒编辑，v0.2.3 重写设置页时丢失）：恢复按秒编辑（输入 10 = PT10S）。
- 修复宽屏下内容区整体居中导致的大片空白：第一二栏之间 242px 空白带、无第二栏的页面内容也被挤在中间；内容区改为贴左占满，1920 宽屏下两侧各剩正常 padding。

### 验证

- 后端 `py_compile` 通过；前端 `npm run build` 通过；路径边界逻辑自测 8/8（含 `/database` 前缀陷阱、不同驱动器）；`sync_version.py` 边界自测 7/7（两种格式检出 / 标记缺失报错 / 双向同步）；`--check` 门禁 EXIT=0。

## v0.3.3（2026-10-17）

**命名规则可视化重构（告别手打 {xxx}）+ 全站深色模式**

### 命名规则: 从手打占位符到点选拖拽

- **修复假变量提示 bug**：设置页原提示写着并不存在的 `{studio} {publish_date}`（正确的是 `{producer}`/`{publisher}`、`{date}`）——用户照提示填写，整理时 `str.format(**copyd)` 直接 KeyError，整部影片报错。已与代码实际变量全集（14 个，见 `datatype.py: get_info_dic`）核对重写。
- **新增可视化构建器**（`frontend/src/components/NamingRuleBuilder.vue`）：「输出与命名」的 4 个模板输入框全部替换为中文化点选式构建——
  - 变量面板：14 个变量以中文标签呈现（番号/女优/标题/制作商/发行商…），源码名 `{num}` 降级为角标；另附常用符号与「＋ 自定义文本」；
  - 目录层级树：层可新建/排序/删除，层间以 `/` 分隔；层内元素点击添加、可拖动排序与跨层搬运、悬停删除；
  - 实时预览：示例数据渲染 + 路径长度条（支持按字节计长）+ **未知占位符红色告警**（后端渲染会 KeyError 的元凶，预览中原样保留 `{xxx}`）+ 空层级告警；
  - 4 枚快速模板（默认结构/按发行商/按系列/按日期归档）+ 删除操作 5 秒内可撤销；
  - 「可视化构建 / 文本源码」分段切换，双向同步——高级用户保留源码逃生门，旧配置任意字符串均可解析回结构，无需迁移。
- 单层模式适配文件名/NFO 模板；多分片影片自动追加 `-CDx` 的说明写入界面。
- 前端新增暴露 `length_by_byte` 开关（此前仅有后端配置字段）。

### 全站深色模式（浅色/深色/跟随系统/定时）

- `tokens.css` 新增 `[data-theme="dark"]` 覆盖块：背景层级反转（层级越高越亮，与浅色相反）、语义色全部提亮（soft 底上的文字对比度 ≥4.5:1，WCAG AA）、`text-inverse` 翻转（亮蓝底配深字）、阴影加重；组件零硬编码色即可换肤。
- 新增 `useTheme` 组合式函数：四种模式——浅色 / 深色 / 跟随系统（`prefers-color-scheme`）/ 定时切换（默认 19:00-07:00，支持跨午夜，每分钟检查）；localStorage 跨会话持久化；挂载前应用防刷新闪烁；切换瞬间禁用过渡。
- StatusBar 右侧新增主题按钮，点击循环切换四种模式；设置页新增「外观」分类（模式卡片 + 定时时段设置 + 当前生效主题提示）。
- `base.css` / `LoginView.vue` 全部硬编码色令牌化（notice 三色、登录页初始密码提示、阴影），深色模式下无漏网之鱼。

### 设置页三栏布局

- 由「全部分组折叠堆叠」改为「左栏分类导航（158px）+ 右栏当前组表单」，切换分类不再反复滚动找分组；窄屏（<860px）侧栏自动转为横向滚动条。

### 其他修复

- 修复 `SettingsView.vue` 一处历史文件乱码（翻译组提示"配置"二字后为乱码字节，恢复为"配置文件是 git 跟踪的"）。

### 验证

- 无头浏览器冒烟 19/19 通过：三栏与 7 分类渲染、构建器 15 格变量面板、三层目录解析、中文标签、未知占位符告警、深色防闪烁（localStorage → 首帧即深色）、StatusBar 循环切换、跟随系统、登录页深色、无 JS 运行时错误。

## v0.3.2（2026-10-16）

**修复 Windows 本机版配置落点（升级不再丢失用户配置）**

0.3.1 的 Windows 本机版把配置固化在 exe 同级的 `_internal/config.yml`，整包升级时该目录被新包整体覆盖，用户改过的配置即丢失。

- `config.get_config_source()`：frozen 且未显式 `-c` 时，默认配置路径改为 **exe 同级 `config.yml`**（便携目录），首次运行从 `_internal` 内置默认模板拷贝初始化；之后所有读写、登录会话均落在 exe 同级，整包升级不受 `_internal` 覆盖影响。
- `server._config_file_path()` 兜底分支同步为 exe 同级优先，保持一致。
- Docker / NAS 部署（非 frozen 且走 `-c` 挂载）路径不变，镜像无需重打。
- ⚠️ 迁移提示：从 0.3.1 升级时，旧版 `C:/JavSP/_internal/config.yml` 即你的用户配置；升级前请先备份该文件，解压新版后把其内容合并进新生成的 `C:/JavSP/config.yml`（新版不再读取 `_internal/config.yml`）。

## v0.3.1（2026-10-16）

**新增本机桌面 exe 交付形态（与 Docker 部署并列的第二种使用方式）**

本版本在原有 Web 服务（Docker / NAS）之外，新增一种「下载即用」的本机运行方式：把 FastAPI 后端 + 前端打包为独立 Windows 程序，解压 zip 即可运行，无需 Python 环境、无需 Docker。

### 双形态入口（javsp/desktop.py）
- **默认 GUI（C/S）**：用 pywebview 开一个原生窗口壳加载 WebUI，关闭窗口即退出；适合想要「一个程序一个窗口」体验的用户。
- **`--nogui`（B/S）**：改用系统默认浏览器打开 WebUI（服务仍在本地 127.0.0.1:8000），关闭命令行窗口即退出；适合已在用浏览器、或需要看代理/爬虫调试日志的场景。
- 启动时轮询 `/api/health` 做就绪检测（替代原先固定 sleep 2 秒），就绪后才开界面；frozen 下日志同时落 exe 同级 `JavSP.log`，便于无控制台时排查。

### 便携数据目录（exe 同级）
- 所有运行时数据（config.yml、登录会话、JavSP.log）全部落在 exe 同级目录，解压到任意位置即可用，不写系统目录、不污染用户目录。
- `lib.resource_path` 的 frozen 分支改为基于 `sys.executable` 所在目录解析，无论用户从哪个工作目录启动都能正确定位配置与图片/数据资源。
- `server._resolve_dist_dir` 与 `_config_file_path` 兜底在 frozen 下改用 `_MEIPASS` / exe 目录定位前端静态资源与配置文件（onedir / onefile 均兼容）。

### 可复现构建
- 新增 `build/pyinstaller.spec`（onedir 形态）：datas 含 `frontend/dist`、`config.yml`、`javsp/image`、`javsp/data`；hiddenimports 显式收集 `javsp.web.*`（运行时动态 import）与 `webview`。
- 新增 `tools/build_desktop.py`：一键完成「前端 build → PyInstaller 打包 → 压缩为 `JavSP-<version>-windows.zip`」。
- 分发形态：onedir 目录打 zip 直接发（不做 Inno Setup 安装包），与 Docker 镜像 tar 作为两个并列 Release 资产。

### 说明
- 本形态为 fork 自有 0.x 线演进（0.2.x 完成 WebUI 改造，0.3.x 开启本机桌面交付），不影响既有 Docker / NAS 部署路径。

## v0.2.14（2026-10-12）

**复活 javbus 渠道（站点更换年龄验证机制）+ 删除渠道 Cookie 手动配置功能**

### javbus 复活

- **根因**：javbus 于 2026-10 上旬更换年龄验证机制 —— 早期钥匙 `age=verified` 已彻底失效（实测带该 cookie 仍被 302 到 `/doc/driver-verify`，与无 cookie 完全同形），这正是 v0.2.13 将其归为"站点已失效"、以及"手动配 Cookie 也救不回来"的真因。
- **新钥匙**：实测（2026-10-12）携带 `dv=1` 即可直接放行 —— 任意番号均返回真实详情页，首页/搜索页同样需要，无需任何登录态；该值为站点内置固定值，不过期。方案与 metatube-server-fork 的修复（commit a171425, #378）一致，但**沿用旧式路径 `/{番号}`**：页面仍为繁体中文，现有解析代码零改动。
- **验证**：`parse_data` 实测 NANP-030 / SSIS-001 / IPX-001 均完整解析出标题、女优、发行日期、封面、分类、样本图；假 ok 对照 ZZZZ-999 正确返回 404 未收录（`MovieNotFoundError`），确认不是"任何号都成功"的假象。
- **探活**：删除 `SOURCE_STATUS_OVERRIDE` 里 javbus 的 source_defunct 条目（结论作废），恢复常规探活；`_SAMPLE_BY_SOURCE` 显式记录 javbus → IPX-001（默认样本恰好收录，显式记录实测依据）。
- **兜底保留**：`parse_data` 中的 Age Verification 检测保留 —— 万一日后 `dv=1` 失效，会正确识别为"未取得数据"而非 IndexError 崩溃，届时按 driver-verify 页重新找钥匙即可。

### 删除渠道 Cookie 手动配置功能

- javbus 不再需要用户粘贴浏览器 Cookie，整套手动输入功能删除：
  - 后端：`server.py` 的 `COOKIE_CAPABLE`、`/api/channels/{source}/cookie` GET/PUT 端点、`ChannelCookieRequest` 模型；`/api/channels` 响应中的 `cookie_supported` 字段。
  - 前端：`api.js` 的 `getChannelCookie` / `putChannelCookie`；`ChannelsView.vue` 的 Cookie 弹窗整套 UI、「配置 Cookie」按钮及相关文案。
  - `javbus.py`：删除 `_resolve_javbus_cookies()`（读 `Cfg().crawler.cookies['javbus']`），改为硬编码 `AGE_COOKIES = {'dv': '1'}`。
- **`config.py` 的 `crawler.cookies` 字段保留但废弃**：挂载卷里的旧 `config.yml` 可能仍带该块，删字段会让 confz 校验失败导致服务起不来（挂载卷配置不随镜像更新）。字段加废弃注释，新部署可从 config.yml 移除（本地 config.yml 已清理）。
- `health.py` 的 `_UNUSABLE_HINTS` 中 challenge_required / credential_error 两条提示不再引导"配置 Cookie"。

## v0.2.13（2026-10-11）

**修复：渠道状态归类细化 + 提交前 Code Review 发现的 6 个实质问题**

### 状态分类细化

- **区分「IP 被封」与「地区版权封锁」**：两者状态码都是 403/200，但处置方式完全不同（换代理 IP vs 换日本等地区出口）。
  - `ip_banned`：IP/ASN 被封禁。实测特征 —— fc2ppvdb 403 + `Error 1005 … has banned the autonomous system number (ASN) … (16509)`（整段机房网段被封）、njav 403 + `Sorry, you have been blocked`。
  - `geo_blocked`：地区版权封锁。实测 javdb 返回 **200** + `Due to copyright restrictions…`（不是 403），需换**地区**代理而非换 IP。
  - `challenge_required`：需过真人校验（浏览器点一下就能进）。实测 javlib 403 + **Cloudflare `cf-mitigated: challenge` 响应头** + `Just a moment...`。走该路径时可在源上配置浏览器 Cookie。
  - 判据只取**挑战页/封禁页独有**的特征：⚠️ `/cdn-cgi/challenge-platform` 是 Cloudflare **所有错误页都会加载**的脚本路径，封禁页里也有，用它做判据会把 IP 封禁误判成需校验；且封禁特征必须**优先于**挑战特征。
- **javbus 归类为站点已失效**：实测 5 个不同番号全部 `MovieNotFoundError`，且**响应字节数完全相同（21344）** —— 返回的是同一个**年龄/驾照验证页**（`<title>Age Verification JavBus`，链接指向 `/doc/driver-verify`）。已配置的 Cookie 无法通过。判据：**多个不同番号响应长度一致 ⇒ 同一个拦截页，而非"未收录"**。
- 状态文案统一去掉「(不熔断)」等实现细节，改为主动告知处置方式（换代理 / 配 Cookie / 关闭该源）。

### 刮削参与语义修正

- **「配置启用」≠「实际可用」**：原先 `active` 只看配置里勾没勾，导致被封 / 需校验 / 前端渲染 / 站点失效的源在「刮削参与」列仍显示**参与**，而它们每次必然失败。新增 `usable` 字段区分。
- **不可用源真正从刮削流程中跳过**：原先 `is_tripped()` 只看熔断器状态，**不可用的源照常发请求**，每部影片白等 `retry × timeout`。现已让二者收敛为同一判断（`usable=False ⇔ is_tripped=True`）。
- 不可用源的命中率与「失败 x/2」不再展示（必然 0%，展示只会误导为偶发失败）；未启用源的**历史命中率保留**（用户主动关掉 ≠ 源用不了）。
- 前端参与列统一为「参与 / 不参与」，并给出不参与的具体原因；状态徽章统一为红色，与「被反爬拦截」一致。

### Code Review 修复（提交前）

- **凭据泄露（严重）**：`_sanitize` 只处理了带 scheme 与协议相对两种 URL，**漏了 urllib3 真实的 path-only 形态**（单斜杠 `/search?token=SECRET`），导致 token / Cookie / 内网地址 / 本机路径可经 `reason` 字段进入 `/api/channels` 响应。已补齐并加敏感键名兜底。
- **地区封锁正则过度扩大（严重）**：词表里的「禁止访问」是 `javdb.py` 中 403/503 **所有分支的公共前缀**，会把 Cloudflare 1010（浏览器指纹封禁）等误判为地区封锁；而地区封锁属不可用且不走半开恢复，**一次误判即永久停用该源**。已改为只匹配站点原文特征。
- **展示路径篡改熔断状态（严重）**：`merge_into_scrape_sources` 在循环体内调用了带副作用的 `is_tripped()`（冷却到期会推进 half_open），渲染一次结果就会把所有熔断源提前转为半开，破坏「半开只放一个请求」的并发保护，且为 O(n²)。已改为循环外计算 + 纯查询判定。
- **出口整体故障时面板与实际行为相反（严重）**：`_guard_against_outage` 覆盖了状态却未重算派生字段，导致记录里"会参与"而界面显示"不参与"，并残留针对旧状态的错误排查建议。已补重算。
- **`probe(timeout=)` 是死参数（中等）**：函数体从未使用该参数，各爬虫在 import 时即固化全局超时，导致 `PROBE_TIMEOUT` 无效、探活时长不可控。现通过 contextvar 让其真正生效。
- **`_host_of` 解析缺陷（中等）**：对 `https://user:pass@host/` 会把用户名当主机名（可经站点列泄露），对 IPv6 地址会被截断。改用 `urlsplit().hostname`。
- **响应体扫描开销（轻微）**：`_classify_response` 改为只匹配前 8 KiB（Cloudflare 的特征均在首屏内），避免大体积错误页带来的字符集探测开销；读取失败时补充日志，避免静默降级导致「换个代理就能好」的源被永久熔断。

## v0.2.12（2026-10-11）

**清理：删除死源 fc2fan + 救回 airav（迁站 airav.io）+ 新增「前端渲染 / 站点失效」两种状态归类**

- **救回 airav（换站 + 重写解析，实测 status=ok）**：原 `airav.wiki` 已停服（HTTP 521），站点迁到 **airav.io**。但**两者架构完全不同，不能只换域名**：
  - 旧站：JSON API（`/api/video/barcode/{番号}`），详情页按番号寻址，一次返回 barcode/name/description/img_url/tags/actors/publish_date/images/factories；
  - 新站：**服务端渲染 HTML、无 JSON API**，详情页按**内部 ID** 寻址（`/video?hid=QC-BT-4732114`），番号需经搜索页 `/search_result?kw=` 定位。
  - 已重写 `parse_data`：搜索定位 → 详情页比对番号 → JSON-LD（schema.org `VideoObject`）取标题/封面，HTML 取女优（`a[contains(@href,'/actor?id=')]`）、类型标签（`/tag?tid=`）与**发行商**（`/tag?fid=`，与标签同一机制）。
  - ⚠️ **刻意不采信新站 JSON-LD 的 `duration` 与 `uploadDate`**：实测多部影片均为 `PT10M0S` 与「采集当天」，是采集站的占位值而非真实时长/发行日。采信会把「10 分钟」「今天」写进 NFO，故这两项留空交给其它源补。
  - 实测：IPZZ-989 / JUR-852 / DSOD-097 / IPX-001 均解析完整（标题、女优、发行商、类型、封面），探活由 `source_defunct` 恢复为 **正常**。
  - 已知限制：该站**不收录 FC2 番号**（`FC2-PPV-xxxxx` / 纯数字 / `FC2-` 三种写法搜索均为 0 条），FC2 影片请依赖 `fc2` / `javdb` 等源。
- **删除 fc2fan（fc2club.top）**：实测主站只返回 493 字节、`title='Loading...'` 的壳页，站点已关停（原代码在这种情况下直接抛「站点不可用」）。已清理 6 处：爬虫实现 `javsp/web/fc2fan.py`、`CrawlerID` 枚举（20 → 19）、配置字段 `crawler.fc2fan_local_path`、`DOMAIN_HINTS` 条目、前端源列表与站点标签。
  - **一并移除其「本地镜像」能力**：该源原本支持把 `fc2fan_local_path` 指向一个存放 `FC2-xxxx.html` 的本地目录来兜底解析下架影片。经确认本部署从未使用，故连同配置字段一并删除。
  - **兼容性注意**：删除枚举前必须先清理运行配置 —— 挂载卷 `config.yml` 的 `selection.fc2` 里原本含 `fc2fan`，confz 会按枚举校验，不先摘掉会直接启动失败。本次已在部署脚本里前置处理。
- **新增两种状态，避免把「站点用不了」误报成「未收录」**：
  - `client_rendered`「前端渲染·静态刮削不可用」：站点已改为纯前端渲染，静态抓取取不到数据。实测 **avsox** 任意路径（首页/搜索/详情）都返回同一个 1520 字节空壳页（`<div id="javu-site-index"></div>` + JS 注入），浏览器能看到内容全靠 JS。
  - `source_defunct`「站点已失效/非番号库」：**airav** 原域名 `www.airav.wiki` 已停服（HTTP 521），根域跳转到 `airav.io`（已非原站）；**avwiki**（`av-wiki.net`）经核实**根本不是番号库**，实测标题为「AV女優の名前が知りたい！」女优名字查询站，无影片详情页、搜索页返回 404。
  - 两者均**不熔断**（重试无意义，又不是出口/网络问题），但状态文案直指原因，主人不必再像这次一样反复排查「是不是样本号不对」「是不是代理坏了」。
  - 实现方式：`SOURCE_STATUS_OVERRIDE` 人工核实结论表 + 探活时覆盖自动分类。**不能靠异常类型自动判断** —— avsox / avwiki 抛的都是 `MovieNotFoundError`，与「该站真的没收录这部片子」完全同形。

## v0.2.11（2026-10-11）

**修复：javdb 女优全站解析失败 + fanza 整源改版失效 + fc2 发售日改版 + arzon 死源清理**

- **javdb 女优恒为空（此前无人发现）**：新版 JavDB 不再用 `<strong>♀</strong>` 标记演员性别，改为给女优的 `<a>` 加 `class="actor-female"`。原 `genders = actors_tag.xpath("strong/text()")` **恒返回空**，导致**所有影片** `actress=[]`（表现为"每部片都没女优"，而非"这片没有"）。已改为 class 优先（`a[contains(@class,'actor-female')]/text()`）、旧符号写法兜底。
- **javdb 演员性别配对越界**：原 `genders[all_actors.index(i)]` 按下标取，数量不齐时抛 `IndexError` 使整部影片解析失败（渠道监控误报"内部错误"）。一并改为 `zip` 顺序配对（不越界，且修正重名演员 `index()` 只返回首次位置导致的错配）。
- **fanza 整源结构性失效（浏览器能搜到、程序却判"未收录"）**：详情页 `digital/videoa` 已 **301 迁移到 `video.dmm.co.jp`**，且为纯前端渲染（Next.js 壳，无 title/meta/正文，payload 内也无业务数据），静态解析彻底不可用；搜索页的老选择器 `//ul[@id='list']` 已归零。改造为走**仍为服务端渲染的 `mono/dvd` 通販页**，并新增 `parse_mono_dvd_page()`（标题/品番/女优/収録時間/発売日/メーカー/レーベル/シリーズ/監督/ジャンル/封面/样本图/评分）。同时修正两处：`get_urls_of_cid` 用"精确匹配优先"会把 mono 组整组漏掉（查 `ipx00120` 时 monthly 恰好字面相等），改为**精确 + 规范化合并**再排序；`_search_and_parse` 跳过所有候选后静默返回会造成「解析成功但字段全空」的假成功，改为必须显式报错。
- **fc2 发售日改版致整部影片解析失败**：站点把发售日从 `div.items_article_Releasedate` 移到裸 `<p>販売日 : 2017/11/30</p>`，老选择器命中 0 → `[0]` 抛 `IndexError`。已改为「先按文本特征查找 → 再退回老结构」。探活样本 `FC2-1234567`（编造的号，该站根本没有，永远 `not_found`）改为真实存在的 `FC2-718323`。
- **avsox 域名拼接错误导致完全访问不到站点**：`f'{base_url}tw/search/{x}'` 而 `base_url` 结尾无斜杠，拼出畸形域名 **`avsox.clicktw`**（表现为 `ProxyError`，极易误判为"站点已死"或"出口不通"）。已改为显式 `base_url.rstrip('/') + '/' + path.lstrip('/')`。修复后确认站点已改版为纯前端渲染（返回 1520 字节空壳页），静态抓取暂不可行。
- **删除死源 arzon / arzon_iv**：实测 `www.arzon.jp` 连接被重置、直连超时，两者指向同一死域名。已彻底清理 6 处：爬虫实现、`CrawlerID` 枚举（22 → 20）、`base.py` 中的 arzon 专属 Referer 特判、`config.yml` 注释、前端源列表、渠道监控展示表。
- **渠道监控「站点」信息准确性**：修正 `DOMAIN_HINTS` 中 4 处与代码不符的标注（airav 曾错标为 `javdb.com`、实为 `www.airav.wiki`；fc2 曾错标 `fc2club.com`、实为 `adult.contents.fc2.com`；fc2fan 曾标「本地镜像」、实为写死的 `fc2club.top`；javdbapi 补注为 App API 入口）。
- **站点列改为动态显示**：探活时捕获**实际请求到的 host**（`requests` 请求层钩子）入库，面板优先显示实测地址；与静态标注不符时红色高亮并列出静态值，防将来改 `base_url` 或迁域名后展示值失真。
- **修复 host 捕获钩子的并发递归缺陷**：初版把钩子装在 thread-local 上且在调用时动态读取原始实现，后台自动探活与手动探活并发时互相把对方的包装函数装回，导致 **全部渠道被误判为 `outage` 并触发熔断**（表象极像代理失效）。改为「进程级单一钩子 + 引用计数 + 闭包捕获原始实现」，并附本地并发压测（8 线程 / 嵌套 / 异常路径）。
- **会话目录失效**：`server.py` 在 `_config_file_path` 定义之前就调用它，`NameError` 被 `try/except` 静默吞掉，会话目录回退到包目录而非挂载卷（容器重建即丢登录态）。已将定义上移到调用点之前。
- **版本一致性门禁（工程改进）**：本次容器内版本漂移到 0.2.9 才被发现——根因是部署脚本只同步 `*.py`、漏了 `pyproject.toml`。`sync_version.py` 新增 `--check-target` / `--sync-target`，把**跨环境版本**纳入可机械校验的范围，避免再靠人记。
- **废弃配置清理**：镜像（`network.proxy_free`）功能已移除，但挂载卷配置里仍残留整段镜像地址与说明注释，一并清理。

## v0.2.10（2026-10-11）

**修复：整理落盘状态持久化 + SSE 完成事件判定（3 处回归修复）**

- 整理结果状态持久化：`OrganizeView` 的「整理结果」列原本依赖前端内存态 `results`，刷新或重进页面即清空，已落盘的影片会误导性地显示横线 `—`。改为后端基于磁盘真实产物判定 `organized`（`server.py` 新增 `_is_organized(movie)`：检查 `save_dir` 下是否存在 `.nfo` / `poster.jpg` / `fanart.jpg` / 改名后的视频文件之一），`GET /api/movies` 与扫描接口返回均带上该字段；前端整理按钮改为优先看会话内 `results[m.guid].ok`、以 `m.organized` 兜底，点完整理立即置灰，刷新后仍保持「已落盘」。
- 扫描路由回归修复：上版加 `_is_organized` 时误把 `@app.post('/api/scan')` 装饰器贴到该函数，真正的 `api_scan` 失去路由，前端 `POST /api/scan` 只传 JSON body 无 query 参数，被 FastAPI 当成必填查询参数而返回 `422 query.movie: Field required`（表现为「扫描失败」）。已把装饰器归还 `api_scan`，并在容器内实测扫描链路：`SCAN_BADPATH 400` / `SCAN_DATA 200 count 2038`。
- 整理按钮立即置灰（真正根因）：`organizeOne` 回调此前用 `if (d.done)` 判定完成，但后端 organize SSE 完成事件发的是 `{type:'result'}`、错误是 `{type:'error'}`，**从不发 `done` 字段** → 该分支永远不成立，`results` 从不写入 `ok` → 点完「整理选中项」按钮不置灰、须手动刷新。改为 `d.type === 'result'` 写成功、`d.type === 'error'` 写失败。
- 队列统计修复（同源）：`QueueView` 用 `if (d.done)` 判 batch 完成，后端发的是 `all_done`，导致「成功/失败」计数恒为 0。改为 `d.type === 'all_done'` 写统计。

## v0.2.9（2026-10-10）

**修复：设置页「刮削间隔」保存报 Duration 校验错误**

- 现象：保存配置提示 `1 validation error for Cfg crawler.sleep_after_scraping Assertion failed ... input_value=1`。
- 根因：`sleep_after_scraping` 字段类型是 `Duration`（`pydantic_extra_types.pendulum_dt.Duration`，
  ISO8601 时长，实际只接受 `PT1S` 这类格式），而前端用 `v-model.number` + `type="number"` 渲染。
  浏览器对 `<input type=number>` 无法显示字符串 `PT1S` 而清空，用户改填数字 `1` 保存时提交 int `1`，
  后端 Duration 校验器拒绝裸 int，遂报 `assertion_error`。
  （对比：上游作者在 `network.timeout` 已用文本输入 + ISO8601 提示正确处理，唯独该字段漏改。）
- 修复：`SettingsView.vue` 将该字段改为「秒数」受控输入——显示时 `isoToSeconds()` 把 `PT1S` 解析成 `1`，
  保存时 `secondsToIso()` 把秒数转回 `PT{n}S` 提交，后端 Duration 正常接受；`0` 表示不等待。
  同时补充 `isoToSeconds`/`secondsToIso` 两个工具函数。

## v0.2.8（2026-10-10）

**改进：WEBUI 两处收敛（监控页 cookie 按钮 + 设置页刮削源分区）**

- 监控页「配置 Cookie」按钮：由禁用显示（`:disabled`）改为按能力显示（`v-if`）。仅对真正支持
  cookie 配置的渠道渲染按钮（当前仅 javbus，由后端 `COOKIE_CAPABLE` 集合驱动，见 `server.py`）；
  后续扩展支持渠道只需后端扩该集合，前端自动开放，无需再改。
- 设置页刮削源分区：由「所有分区列全量源」改为按 `data_src` 类型收敛（新增 `SOURCE_KINDS`）。
  普通番号源 / FC2 番号源 / CID 番号源各自只显示该类支持的源
  （FC2 专属 `fc2`/`fc2fan`/`fc2ppvdb` + 跨类型 `avsox`/`javdb`/`javmenu`；CID 仅 `fanza`）。
  同时补回此前遗漏的第 22 源 `javfree`。分区渲染 = 推荐源 ∪ 用户已勾选源（去重），
  既只显示该类支持的源，又不会丢失 config 中已勾选但不在推荐清单的源（仍可取消）。

## v0.2.7（2026-10-10）

**修复：WEBUI 刮削源设置补全 FC2 / CID 类型，并默认关闭 fc2ppvdb / avsox（FC2 类）**

起因：v0.2.6 时用户在「系统设置 → 刮削源」页面关闭 avsox / fc2ppvdb，但渠道监控里两者仍显示为
active、且 FC2 番号刮削仍会调用它们。排查发现页面只渲染了 `selection.normal` 一个勾选区，
`selection.fc2` / `selection.cid` 在 UI 中根本没有编辑入口；而 `core.py` 按 `movie.data_src`
（normal / fc2 / cid）选源，FC2 番号实际走 `selection.fc2`，故页面关不掉、监控里仍是 active。

改动：
- `frontend/src/views/SettingsView.vue`：刮削源分组由写死 `normal` 改为遍历
  `['normal','fc2','cid']` 三类，各自独立勾选区（标签：普通番号源 / FC2 番号源 / CID 番号源），
  复用已有的 `hasSite(kind,s)` / `toggleSite(kind,s,on)`；后端 `PUT /api/config` 早已支持任意
  kind 整体写回，仅前端缺渲染。
- `config.yml`（运行实例）：`selection.fc2` 由 `[fc2, avsox, javdb, javmenu, fc2ppvdb]` 改为
  `[fc2, javdb, javmenu]`，彻底关闭 FC2 类的 avsox / fc2ppvdb（avsox 已废、fc2ppvdb 需住宅代理，
  两者对 FC2 番号基本必失败）。

验证：
- 运行实例重启后，`/api/channels` 返回 avsox / fc2ppvdb 的 `active=false`、`origin=unknown`；
  FC2 番号刮削不再调用这两源（core 严格按 selection 选源）。
- 前端三类勾选区渲染正常，可在页面完整管理所有选源类型。

## v0.2.6（2026-10-10）

**新增 javfree 番号补充源（第 22 个刮削通道）**

起因：跨项目研究 `metatube-server-fork` 的 37 个 provider 后发现，两边真正重叠的番号库只有 8 个，
而 `javfree`（javfree.me，番号聚合检索站）是 JavSP 当前缺失的补充源。其实现为纯 Python 爬虫、
零新依赖（lxml 本就是正式依赖），可作为候选移植。经实测 javfree.me 在 NAS 的 squid 出口下可达、
且解析结构清晰，故移植为本项目第 22 个通道。

改动：
- `javsp/web/javfree.py`（新增）：实现 `parse_data(movie)`，按番号搜索结果页精确匹配（忽略横线
  差异）后进入详情页，解析 title（剥离 `[IPX-001]` 前缀）、cover（取正文首图
  `cf.javfree.me/HLIC/{id}.jpg`，已验证 200 image/webp）、actress（过滤非 ASCII 噪声标签）、
  preview_pics（正文图片集）；找不到番号匹配或详情页缺失时抛 `MovieNotFoundError`，不覆盖
  `movie.dvdid`。
- `config.py`：`CrawlerID` 枚举加 `javfree = 'javfree'`。
- `health.py`：`DOMAIN_HINTS` 加 `'javfree': 'javfree.me'`。
- `config.yml`：`crawler.selection.normal` 列表末尾加 `- javfree`（作为番号补充源兜底）。

验证：
- 本地经 squid（日本出口）实测：搜索页 / 详情页均 200；title 剥离正确（`女子校生便所交際…妃月るい`）、
  actors 取 `['妃月るい']`、cover 取真实首图、preview_pics 14 张。
- NAS 容器内 `docker cp` 部署后，渠道监控页出现 javfree 行；经 squid 出口切换至稳定节点后探测转绿，
  实刮 `IPX-001` 经服务同款代理配置取数成功。

## v0.2.5（2026-10-09）

**渠道 Cookie 配置 UI（页面内粘贴 JSON，随 config.yml 持久化，热重载生效）**

起因：此前 javbus 的浏览器 cookie 靠外挂文件 `/etc/javsp/javbus_cookies.json`（需手动 sftp
进挂载卷 + docker restart），既不内聚也不便维护。改为在「渠道监控」页面为每个支持 cookie 的
渠道提供「配置 Cookie」按钮，弹窗内粘贴 Cookie-Editor 导出的 JSON 即可，配置与其它持久化
配置同存 config.yml，保存后立即生效，无需重启。

改动：
- `config.py`：`Crawler` 新增 `cookies: Dict[str, list]`（channel_id → Cookie-Editor 精简数组）。
- `javbus.py`：删除外挂文件逻辑，运行时从 `Cfg().crawler.cookies.get('javbus')` 读取
  （数组转 {name: value} 给 requests；未配置回退默认 age=verified），热重载即生效。
- `server.py`：新增集合 `COOKIE_CAPABLE = {'javbus'}`（未来加源只改此处）；`/api/channels`
  每行附 `cookie_supported` 标记；新增 `GET/PUT /api/channels/{source}/cookie`，保存复用
  既有「保注释写回 + 热重载」机制（只替换该渠道字段，不影响其它渠道）。
- 前端 `ChannelsView.vue`：渠道列加「配置Cookie」按钮，`!cookie_supported` 置灰禁用；弹窗内
  textarea 粘贴 JSON + Cookie-Editor 操作指引 + 保存/清空，保存后提示已生效。

验证：
- 前端 `vite build` 通过；容器内 import 无错；`/api/channels` 返回 `cookie_supported`
  （javbus=true，其余 false）；PUT 写回 config.yml 并热重载生效，GET 回显一致。

## v0.2.4（2026-10-09）

**移除 `network.proxy_free` 功能（按站点镜像/免代理地址）**

起因：该机制依赖为每个站点手工维护「镜像域名」，而 `javbus.life` / `javdb38.com` /
`avsox.click` / `www.javlibrary.net` 这批镜像实际已全部失效或退化为死壳页。现象即上轮
报告的 `javbus` 渠道 `MovieNotFoundError` —— 爬虫被强制路由到死镜像 `javbus.life`（480 字节
空壳），而主站 `www.javbus.com` 经同一 squid 出口本可正常访问（49KB 真页，可被解析）。

改动：
- 删除 `javsp/config.py` 的 `Network.proxy_free` 字段，及 `javsp/web/proxyfree.py` 模块；
- `javbus` / `javdb` 的 `base_url` 回退到 `permanent_url`（主站），`avsox` 固定主站
  `https://avsox.click`，`javlib` 的 `init_network_cfg` 去掉镜像探测、只用主站；
- 四源统一走全局 `network.proxy_server` 代理，不再有按站点的镜像逻辑；
- 清理 `config.yml` / `tools/config_migration.py` / `README.md` / `verify_config_io.py` 的相关引用；
- `Network` 模型加 `model_config = ConfigDict(extra='ignore')` 保底：旧配置若残留
  `proxy_free` 段，忽略而非整体校验失败（不影响其它功能）；保存配置时该字段自动消失；
- 顺带修复 `javbus.py` 的 302 处理：之前在 302 时取 `resp.history[0]`（空的 302 响应本身）
  解析导致 `Document is empty`；改为直接用跟随重定向后的 `resp` 解析。
- 补一道护栏：实测 NAS squid 出口下 `www.javbus.com/IPX-001` 会被 302 到年龄验证页
  （`/doc/driver-verify`，标题 `Age Verification JavBus`），该页有 `.container` 外壳但无影片
  `h3` 标题。若只按原逻辑继续解析会 `container.xpath("h3/text()")[0]` 越界抛 `IndexError`
  中断整次刮削；现改为「有容器外壳却缺 `h3` 标题即判 `MovieNotFoundError`」，报错回到准确、
  可控的「该源未取得数据」（真实影片页必有 `h3` 标题，此判断不误杀正常刮削）。
  （该 302 bug 此前因走死镜像 `javbus.life` 不触发 302 分支而一直未被暴露。）

**验证**
- `verify_config_io.py` 的 T3/T8/T9 改为真实嵌套字段（crawler.selection / network 标量），
  全绿；容器内 `import javsp.web.javbus` 等无报错。
- NAS 实测：旧 `config.yml` 含 `proxy_free` 段仍可加载（extra='ignore'）；`javbus` 走主站 +
  squid 代理，IPX-001 撞上 JavBus 年龄验证墙（302 → driver-verify 空壳页），现已干净报
  `MovieNotFoundError` 而非崩溃；健康检查 `200` / 版本 `0.2.4`。



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

- **发版清单（v0.2.11 起增补：跨环境版本门禁）**：踩坑实录——本次容器内 `pyproject.toml` 长期停在 0.2.9，
  而仓库已是 0.2.11，界面右下角显示旧版本才被发现。**根因：部署脚本只 `docker cp` 了 `*.py`，
  漏掉 `pyproject.toml`**，而 `javsp/version.py` 的单一版本源正是它（界面版本号来自 `/api/health`，运行时读取）。
  故增补：
  1. 部署到容器/目标环境时，**部署清单必须包含 `pyproject.toml`**（不只是 `*.py`、前端 `dist/`）
  2. 部署后校验：`python sync_version.py --check-target <目标环境路径>`（不一致时退出码 1）
  3. 需要就地修复时：`python sync_version.py --sync-target <目标环境路径>`（自动备份 `.bak.syncver` 后只改 version 行）
  4. 改完前端后必须重建 `dist/` 并同步进容器，否则运行的是旧界面（比对容器内 `index.html` 引用的
     `index-*.js` 文件名与本地构建产物是否一致，可一眼看出漏没漏同步）

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

## v0.1.16 — 修复 Docker/NAS 部署阻断：让 `docker build` + 容器真能起服务

在 NAS（QNAP Container Station）上实测部署时，连续暴露三个「代码本身能跑、但一部署就废」的问题。
它们都属于**长期潜伏、从未被本地开发环境触发**的类型——本地 `pip install -e .` 恰好绕过了每一个。

### 1. `packaging` 是运行时依赖，却从未在 `pyproject.toml` 声明
- **现象**：容器启动即崩 `ModuleNotFoundError: No module named 'packaging'`，反复重启。
- **根因**：`javsp/func.py` 有 `from packaging import version`（用于版本号解析），但 `packaging`
  只作为 dev 组 `cx-freeze` 的**传递依赖**存在于 lock 里。`poetry install --only main` 把 dev 组
  整个排除 → 干净 venv 里就没有它。本地之所以一直没暴露，是因为 pip / poetry 自身就携带 `packaging`，
  恰好掩盖了「没声明」这件事。
- **修复**：在 `[tool.poetry.dependencies]` 主依赖显式声明 `packaging = "*"`。

### 2. Dockerfile 入口点用了 CLI 脚本，容器实际在跑扫描模式
- **现象**：容器 `Up` 但 8000 端口永不监听、日志一片空白。
- **根因**：`ENTRYPOINT ["/app/.venv/bin/javsp"]` + `CMD ["server"]`。但 `javsp` 是 **CLI** 入口
  （`javsp.__main__:entry`），**根本不解析子命令**——`server` 这个参数被当作没看见，容器实际在执行
  完整 CLI 流程（`Cfg()` → `check_update` 联网 → 扫描目录），跟 Web 服务毫无关系。
- **修复**：改用 poetry 为 `[tool.poetry.scripts]` 中 `server = "javsp.server:entry"` 生成的
  控制台脚本：`ENTRYPOINT ["/app/.venv/bin/server"]`（同时移除多余的 `CMD`）。
  > 顺带厘清：poetry 会为每个 scripts 项生成独立入口（`javsp`→CLI、`server`→Web），
  > 跑 Web 服务必须用 `server`，写 `javsp server` 是无效的。

### 3. README 的 `docker build` 缺 `-f`，按文档构建必然失败
- **现象**：`docker build -t javsp-fork .` 报 `open Dockerfile: no such file`。
- **根因**：Dockerfile 在 `docker/` 子目录，仓库根没有。
- **修复**：README 命令改为 `docker build -f docker/Dockerfile -t javsp-fork .`。

### 附带：`poetry.lock` 与 `pyproject.toml` 失同步
- `poetry.lock` 自 v0.1.6 起未再更新，`content-hash` 与 `pyproject.toml` 已不匹配，干净环境执行
  `poetry install` 会报 `pyproject.toml changed significantly`。
- **修复**：重生 `poetry.lock`（保持 `lock-version=2.0`）。同时给 Dockerfile 的 `poetry install`
  前补 `poetry lock &&`，使后续任何人从零构建都能自恢复。
- 注意：重生 lock 会把依赖树解析到各包的最新兼容版本（本次 diff 较大），已由回归验证兜底。

### 改动的文件
- `pyproject.toml`：主依赖补 `packaging = "*"`；`version` 0.1.15 → 0.1.16
- `poetry.lock`：重生（`lock-version` 保持 2.0）
- `docker/Dockerfile`：`ENTRYPOINT` 改为 `/app/.venv/bin/server`；`poetry install` 前补 `poetry lock &&`
- `README.md`：`docker build` 补 `-f docker/Dockerfile`；新增「选择 NAS 上的 share 目录进行刮削/改名」小节
- `frontend/package.json` / `frontend/package-lock.json` / `README.md`：版本号经 `sync_version.py` 同步至 0.1.16

### 验证
- NAS 实测部署通过：`http://<NAS_IP>:8000` 可访问，`/` 返回 WebUI、`/api/health` 返回
  `{"status":"ok","version":"0.1.15→0.1.16"}`，`/api/config` 200，`/data` 正确挂载 NAS share 目录。
- 全量回归：既有验证脚本（`verify_output_toggles` / `verify_nfo_jellyfin` / `verify_cropper` /
  `verify_config_io` / `verify_config_reload` / `verify_batch_e2e` / `verify_sources_e2e` /
  `verify_scrape_refactor`）全部通过。

---

## v0.1.17 — TASKS 内存缓存回收：长跑不再无界增长

`TASKS`（`guid -> Movie` 的运行内存缓存）原先是一个**普通 dict，全项目没有任何删除路径**：
写入只有 `api_scan` / `api_scrape` 两处，`api_organize` / `api_batch` 只读。
于是每扫描一个新目录、每多刮削一部影片就多一份 `Movie`（含 `MovieInfo`、封面/剧照路径等）
常驻内存，NAS 上连续运行数天或反复扫描不同媒体库时会无界增长，只能靠重启容器释放
——这也解释了为什么「容器重启后 TASKS 被清空」一直被当成已知现象。

### 做法：dict 兼容的 `TaskStore`

新增 `javsp/task_store.py`，用 `TaskStore` 顶替 `server.py` 里的 `TASKS`。
它**刻意做成 dict 兼容**（实现 `__setitem__` / `__getitem__` / `get` / `__contains__` /
`values` / `keys` / `items` / `__len__` / `__iter__`），因此 server.py 里既有的
`TASKS[guid] = m`、`TASKS.get(guid)`、`g in TASKS`、`for m in TASKS.values()`
**一行都没改**——引入一套新仓储抽象会让所有调用点跟着改，回归面反而更大。

三重回收策略：

1. **TTL 过期**（默认 6 小时）：超过 TTL 未访问的条目在下次写入时顺带清理，避免隔夜残留长期占内存。
   用 `time.monotonic()`，不受系统时间跳变（NTP 校时、时区切换）影响；命中`get`/读取也会刷新时间戳，
   让 TTL 表达「最近一次活跃」而非「创建时间」，避免用户持续操作某影片时被误回收。
2. **容量上限**（默认 2000 条）：超限时按「最旧优先」淘汰，防止单次超大目录扫描（几千部影片）
   一次性把内存打满。
3. **活跃任务保护**：`organize` / `batch` 执行期间用 `mark_active()` 把涉及的条目登记为活跃，
   回收时跳过——避免任务做到一半源对象被删导致落盘失败。**必须用 `with`**，异常路径也要解除标记，
   否则条目会被永久保护、永不回收（验证脚本专门覆盖了这条）。

清理**只在写入时触发**：内存增长必然来自写入，因此在写入点维护即可，无需引入后台定时线程；
若长时间没有新写入，内存占用本身是稳定的，不清理也无害。另提供 `evict_expired()` 显式 API
与 `stats()` 状态快照（`count` / `active` / `ttl_seconds` / `max_tasks`）便于观测与测试。

参数可用环境变量按部署规模调整：`JAVSP_TASK_TTL`（秒）、`JAVSP_TASK_MAX`（条）。

### 改动的文件
- 新增 `javsp/task_store.py`
- `javsp/server.py`：`TASKS = TaskStore()`；`organize` / `batch` 的后台线程用 `mark_active()` 包裹；顺带清理已成未用导入的 `Dict`
- 新增 `verify_task_store.py`（**47/47 PASS**）、`smoke_tasks.py`（真 uvicorn 端到端冒烟）

### 验证
- `verify_task_store.py` 47/47：dict 兼容性逐条模拟、TTL 过期（含「写入顺带触发」）、访问刷新时间戳、
  容量上限与最旧优先淘汰、活跃保护（**含异常路径解除**）、TTL 跳过活跃、边界（`ttl<=0`/`max<=0`/空 store）、
  8 线程并发写入无异常、`stats()` 字段；外加 server.py **源码扫描**防漂移（确实用 `TaskStore`、
  不再用普通 dict 初始化、两处 `mark_active` 都在、未用 `Dict` 已移除）。
- `smoke_tasks.py`：真实起 uvicorn 打 `/api/health`（返回 0.1.17）、`/api/scan`、`/api/movies`，
  确认换成 `TaskStore` 后集成层面无问题。
- 全量回归：既有 9 个验证脚本全部通过（`verify_batch_e2e` 覆盖批量 SSE 全流程，证明换掉TASKS
  类型未破坏批量链路）。

### 已知未做（SSE 断开回收）
三个 SSE 生成器仍是 `while True: item = q.get()`（无超时）。客户端断开后，生成器会卡在
`q.get()`、后台 daemon 线程仍跑完整刮削并持续往队列堆积（无人消费）。彻底修复需同时做三件事：
生成器异步化 + 端点注入 `Request` 以调用 `is_disconnected()` + 增加心跳事件（心跳会插入 SSE
事件序列，影响前端与 `verify_batch_e2e` 的序列断言）。因风险高于本次改动、且现有脚本无法模拟
「客户端中途断开」，留待独立一轮设计并补上断流测试后再实施。

---

## v0.1.18 — 把「落盘路径穿越防护」固化成回归测试

影片元数据（番号 / 女优名 / 标题 / 简介）全部来自**外部站点爬虫**，属不可信输入。
它们会进入输出目录模板（`output_folder_pattern`，形如 `#整理完成/{actress}/...`）与文件名模板。
若未经净化，落盘时可能通过 `../` 逃出扫描根目录，在 NAS 上意味着可覆盖共享目录里的任意文件——
这是本项目最值得盯的安全面。

### 核查结论：防护已存在，本版本改为「固化」而非「补漏洞」

读代码发现 `javsp/file.py: replace_illegal_chars` 已做两层处理：
1. 路径分隔符 `/` `\` 全部替换为形近全角字符（`／` `＼`）；
2. `os.pardir in name`（`'..' in name`）时把连续英文点替换为 `…`。

随后用真实PoC 端到端验证（而非停留在读代码）：构造带恶意外部数据的 Movie跑
`generate_names` + `organize_movie`，断言所有落盘路径都在扫描根内。四种载荷
（`../`、Windows 反斜杠、`ACCT/../../../../ESCAPED`、`x/../../ESCAPED`）**均未逃逸**，
扫描根之外也未产生任何逃逸目录。**结论：这是既有防护有效，不是漏洞。**

所以本次交付的不是「加防御」，而是**把这个安全属性固化成 63 项回归断言**——
防止将来有人为支持某个含 `/` 的正常标题而放开替换逻辑时，无声削弱这条防线
（这类改动极易发生：看起来只是「让合法标题通过」，实际同时放开了穿越）。

### 覆盖内容（`verify_path_traversal.py`，**63/63 PASS**）
- **A1/A2/A3纯函数层**：15 种恶意载荷（纯 `..`、多层上跳、反斜杠、`....//` 绕过、
  夹带、分号结尾、URL 编码、绝对路径、Windows 盘符、NUL 字节…）逐条断言
  「分隔符已全角化」且「净化后不含 `..`」；
- **B 端到端层**：真实跑 `generate_names` + `organize_movie`（封面 / NFO 用桩函数避免联网），
  断言 `save_dir` / `nfo_file` / `fanart_file` / `poster_file` 以及 `result` 里回传的路径
  全部在扫描根内（用 `realpath` 消解符号链接后比较），并断言扫描根之外**未真的创建逃逸目录**；
- **C 语义回归守卫**：`_root_save_dir` 三种语义——无 `scan_root` 时保持相对（CLI 依赖
  `os.chdir` 解析，语义不变）、有 `scan_root` 时锚定、绝对路径模板原样保留（用户显式指定不该被改）。

### 改动的文件
- 新增 `verify_path_traversal.py`

### 验证
- `verify_path_traversal.py` 63/63；全量 11 个验证脚本通过。

### 说明
断言刻意避免「恒真式」写法（本项目此前已两次踩坑：`or True` 让断言形同虚设）：
路径比较统一走 `_in_root()` 真实 `realpath` 比对，而不是断言「路径里不含 `..`」
这种只看字符串、可能被编码或符号链接绕过的弱判据。

---

## v0.1.19 — 依赖完整性扫描：找出并补齐两个「靠传递依赖活着」的隐藏地雷

`packaging` 事件（v0.1.16）暴露了一类问题的**通用形态**：某包代码里直接 `import`、
但 `pyproject.toml` 未声明，靠上游包的传递依赖侥幸存在。本地 `pip install -e .` 会自动
解析传递依赖所以永远没事，**只有干净部署环境（`poetry install --only main` / Docker 镜像）
才会炸**。修掉 `packaging` 只能算处理了「一个实例」，所以本次做的是**把这类问题一次找全**。

### 工具：`verify_dependency_completeness.py`

用 `ast` 解析 `javsp/` 下**全部** `.py`（含 `web/` 下 30+ 爬虫模块）提取顶层 import 名，
剔除标准库（`sys.stdlib_module_names`）与项目内模块，再与 `pyproject.toml` 的 main 组比对，
报出「import 了但未声明」的包。

比 grep 精确：能识别 `from X import`、别名、条件导入，也不会把注释/字符串里的名字误判为import。
难点是**包名 ≠ import 名**（`pillow`→`PIL`、`pycryptodome`→`Crypto`、`python-multipart`→`multipart`、
`pywebview`→`webview`、`pretty-errors`→`pretty_errors`），故维护映射表，未命中的按原名/下划线化兜底。

### 扫描结果：找出 2 个真漏，1 个误报

**真漏（已补声明）**：
- **`pydantic`** —— `config.py`（`ByteSize` / `Field` / `NonNegativeInt` / `PositiveInt`）、
  `server.py`（`BaseModel`）、`__main__.py`（`ValidationError`）都直接 import，
  原先仅靠 `confz` 的传递依赖（lock 里 `confz → pydantic >=1.9,<3`）。
- **`pydantic-core`** —— `config.py` 与 `web/translate.py` 直接 `from pydantic_core import Url`，
  原先靠 `pydantic` v2 自带的编译核心。

两者与 `packaging` 完全同类：一旦上游 `confz` / `pydantic` 不再传递即崩，且崩溃点
（`import` 语句）离真正的原因（依赖未声明）很远，排查成本高。已显式声明；
`pydantic-core` 版本约束交给 `pydantic` 自身对齐，避免独立锁版本造成冲突。

**误报（修正工具，不改代码）**：`webview` 被报为未声明，实则 `pywebview` 已声明——
是映射表漏了 `pywebview → webview`。这说明**扫描器输出必须人工核实**：直接照单全收会
把工具自身的缺陷当成代码缺陷去「修」。

### 改动的文件
- `pyproject.toml`：主依赖新增 `pydantic = "^2.9.0"`、`pydantic-core = "^2.23.0"`
- `poetry.lock`：随之重生（`lock-version` 保持 2.0，换行符已还原为 LF）
- 新增 `verify_dependency_completeness.py`（**扫描退出码 0 = 无漏网**）

### 验证
- `verify_dependency_completeness.py` 现在退出码 0：19 个外部顶层 import 全部已被 main 组覆盖
  （或属允许的平台限定项 `win32crypt`）。
- 全量 11 个验证脚本通过。

### 工具的已知边界
本脚本只做**静态声明完整性**检查，不能证明「干净环境真能装上并跑起来」——后者由
`poetry install --only main` 在干净 venv 的实跑、以及 Docker 镜像构建时的真实 import 链来验证。
两者互补：静态扫描防患未然，实跑兜底。

---

## v0.1.20 — 系统性代码审查整改：安全基线 + 正确性 + 性能

依据 [`CODE_REVIEW.md`](CODE_REVIEW.md) 的四维并行审查（Web 层 / 爬虫层 / 性能 / 前端+CLI+配置），
逐条复验后落地。审查报告的结论均由主审核对源码/实测，剔除了前提有误的项
（`shutil.move` 同卷即 `os.rename` 并不慢、`Image.open` 未 close 不构成泄漏、`hard_link:false`
恰好最省空间）。

### 一、安全基线（6 项，均为高危且改动极小）

1. **翻译 API Key 明文进日志**：`translate.py` 用 `format(engine, …)` 拼错误信息，
   pydantic config 对象的 `__str__` 会渲染出全部字段（含 `api_key`），随后被
   `logger.error` 写入日志——**任何一次翻译失败（网络抖动/额度耗尽）即触发，门槛为零**。
   改为打印 `engine.name`（8 处）。

2. **默认绑 `0.0.0.0` + 零鉴权 + 无 Host 校验**：`server.py` 默认监听改为 `127.0.0.1`
   （需局域网访问时显式设 `JAVSP_HOST`；Dockerfile 已有该 ENV，容器行为不变）；
   新增 `TrustedHostMiddleware` 阻断 **DNS Rebinding**（经`JAVSP_ALLOWED_HOSTS` 放开，
   容器部署默认放开，裸机默认只允许回环地址）。
   `desktop.py` 本就绑 `127.0.0.1`，此前与 `server` 不一致且文档未说明。

3. **`GET /api/config` 明文返回全部密钥**：新增 `config_io.mask_secrets` /
   `unmask_secrets`，GET 返回掩码（`***MASKED***`），PUT 时把掩码还原为真实值——
   **既不泄露，又不会因前端回传掩码而丢密钥**。`/api/config/runtime` 的代理地址同步抹掉
   userinfo 中的密码。

4. **`config.yml` 被 git 跟踪**：仓库内该文件的凭据字段本就全为注释（无真实密钥），
   但用户填写后 `git commit -a` 即会提交。已在文件内加显著警示并给出
   **环境变量覆盖**写法（`JAVSP_TRANSLATOR__ENGINE__API_KEY=xxx`）。

5. **番号正则注入**：`lib.py` 的 `detect_special_attr` 把 `avid` 原样拼进正则模式，
   其中的 `*` `+` `(` 等元字符成为活跃语法（可致 ReDoS 与判定绕过）。
   改为复用同文件**已存在却未被使用**的 `re_escape`；`-`/`_` 的 `[_-]*` 放宽语义保持不变。

6. **`retry=0` 导致刮削静默全失败**：`core.py` 的 `for cnt in range(retry)` 在 `retry=0` 时
   一次都不执行 → `success` 标记永不置位 → 全部站点结果被丢弃且**无任何错误提示**。
   改为 `Field(3, ge=1, le=10)`；`max_concurrency` 同样加上界 `le=32`。

### 二、正确性

7. **`output_folder_pattern` 任意路径写**：`_root_save_dir` 原对绝对路径原样放行、
   相对路径不做越界收敛，配合无鉴权 PUT 可写到扫描根之外。现用 `realpath` 收敛，
   越界时 warning 并落回扫描根内（`verify_path_traversal.py` 同步补 3 条守卫，66/66）。

8. **配置回滚非原子写**：`config_reload` 回滚用 `open(path,'wb')`（先截断为 0），
   与本模块 `_atomic_write` 的设计前提自相矛盾；崩溃时会留下**长度 0 的 config.yml**。
   改用 `mkstemp` + `os.replace`（含 `fsync`）。

9. **配置写入 TOCTOU**：并发 PUT 时「取快照+合并+算diff」在锁外，后写者会用旧快照
   覆盖先写者的改动。新增 `config_transaction` 上下文（复用同一把 RLock），
   把「读→合并→校验→写入」整体纳入临界区。

10. **换行注入**：`config_io._needs_quote` 字符类不含 `\n\r\t`，值里的裸换行会被原样写入
    并在解析时折叠成一行（**值被静默篡改**）。已补字符类，并对含换行的值改用双引号转义。

11. **CLI 单部失败整批崩**：`__main__.py` 外层 `try` 的 `except` 被注释掉，只剩 `finally`，
    而 `check_step` 失败时 `raise` → **任意一部影片任一步失败即终止整个 CLI 进程**。
    已恢复 `except`（记录失败并继续，与 Web 侧逐部捕获 + 计数对齐）。
    剧照目录 `os.mkdir` 改 `os.makedirs(..., exist_ok=True)`（原先会 `FileExistsError`）。

12. **进度条溢出**：`move_files` 那次 `check_step` 未计入 `total_step`，默认配置
    （`move_files: true`）下进度条走到 `8/7`。已补累加；
    `verify_output_toggles.py` 从「逐项列举」改为**覆盖全部顶层步骤的守卫**（70 → 78 项）。

13. **前端保存丢弃 API Key**：`App.vue` 把 `engine` 裁成只留 `{name}`，保存后
    `api_key` 消失导致 `model_validate` 失败（表现为「配了翻译密钥后点保存就报错」）。
    改为保留 `api_key`/`app_id`/`url`/`model`。

### 三、性能

14. **`get_pic_size` / `valid_pic` 为拿尺寸而解码整图**：`ImageOps.exif_transpose` 内部会
    `load()` + `copy()`，只为返回 `.size` 两个整数就要解码并复制一遍
    （4000×6000 峰值约 137MB，每部影片每张封面各一次）。改为只读文件头 +
    按 EXIF Orientation 判断是否交换宽高（保持返回语义不变）。
    实测 `get_pic_size` 峰值降 92%、耗时 354ms → 190ms（`valid_pic` 354→190ms）。

15. **`overall_timeout` 误杀第二波爬虫**：原按「单爬虫最坏」计算，但 8 个站点 / 5 线程
    是**两波**，真实上界 66s 而阈值只有 45s → 第二波刚开始 12s 就被 `cf.wait` 取消，
    每部影片白丢 3 个站点结果。改为按波数 + 退避开销估算。

16. **`TaskStore` 写入 O(n²)**（v0.1.17 引入 TTL 策略时的副作用）：每次写入都全量扫描
    `_ts` + 超限时对全部条目排序，实测 4000 条写入 2.25s，且持锁阻塞其它 API。
    改为**时间闸门**（`_next_ttl_check`：在「现存最旧条目的到期时刻」之前直接跳过 TTL 检查，
    每次写入约O(1)），`evict_expired` 仍是全量扫描以保证显式调用语义正确。
    实测 4000 条 2.25s → 0.82s（**降 2.75 倍**），增长曲线由超线性转为近似线性。

### 四、健壮性

17. **`download()` 任意文件读取原语**：原逻辑是「非 http 开头就当本地路径 `shutil.copyfile`」，
    而 url 来自爬虫解析的 `cover`（远端站点 HTML 可控）→ 站点返回 `/etc/passwd` 即被拷走。
    改为只允许 `http/https`，本地镜像需求改用显式 `file://` 前缀；
    同时加**下载体积上限**（默认 64MiB，防异常响应写满磁盘）。

18. **翻译请求无 timeout + Google 无限重试**：`translate.py` 全部请求补 `timeout=20`
    （此前卡住会留下 Python 无法强杀的孤儿线程）；Google 的 `while 429` 加最大重试 5 次
    （原为无限循环，等待单调递增到数小时）；顺带给 Google URL 做 `quote()` 编码
    （`texts` 是远端可控的标题/简介，未编码可注入额外查询参数）。

19. **`_scraper_monitor` HEAD 失败回退成 POST**：回退逻辑只区分 get 与「其他」，
    导致 `javdb` 用 HEAD 校验封面失败时变成 POST（对接受 POST 的服务器等于以登录凭据
    发起写操作）。改为由调用方显式传入对应的 fallback。

20. **前端健壮性**：`consumeSSE` 去掉 `new Promise(async …)` 反模式、加空闲超时与
    `AbortController`、正常读完也 resolve；错误体解析统一 `readDetail`/`normalizeDetail`
    （此前 `await r.json()` 无 catch，反代 502 的 HTML 错误页会让真实原因变成 `SyntaxError`；
    422 的 `detail` 是数组，`new Error([...])`会变 `[object Object]`）；
    批量页 `batch.running` 改在 `finally` **无条件复位**（此前若 SSE 正常结束却没收到
    `all_done`，按钮会永久禁用，只能刷新页面）。

### 五、文档

21. **README 新增「安全说明」章节**：明确服务无鉴权、默认只监听本机、Docker 的端口绑定
    注意事项、`JAVSP_ALLOWED_HOSTS`、密钥掩码行为、`config.yml` 不要提交、**不支持公网暴露**。
    `docker-compose.yml` 的端口映射改为 `127.0.0.1:8000:8000`（安全默认）。

### 验证
- 全量 **12 个验证脚本**通过：output_toggles 78（新增 8 项守卫）/ config_io 32 /
  config_reload 24 / task_store 47 / path_traversal 66 / cropper 65 / nfo_jellyfin 31 /
  sources 22 / batch 11 / scrape 7 / k4 / 依赖完整性扫描 EXIT=0。
- 端到端冒烟 `smoke_tasks.py` PASS（真起 uvicorn，health 200、scan/movies 全链路通）。
- 前端 `npm run build` 通过（1588 modules）。
- 新增依赖声明 `starlette`（`TrustedHostMiddleware` 直接使用），
  被依赖完整性扫描当场抓出并补齐——**扫描器对新引入的 import 立即生效**。

### 已知未做
- **SSE 断开后后台线程与副作用仍不回收**（客户端断开≠取消操作，文件照旧被整理）。
  需异步化生成器 + 注入 `Request` + 心跳事件（心跳会插入 SSE 序列影响断言），
  风险高于本批改动，留待独立一轮。
- **批量完全串行 + 剧照强制 sleep**（`PT1.5S`/张）：1000 部多花 4-8 小时纯睡眠。
  改动涉及并发化落盘，回归面较大，建议单独一轮。
- **未做鉴权**：本项目定位仍是单用户自用工具，本次是把「默认暴露面」收敛到安全默认，
  真正多用户需要另行设计认证体系。

---

## v0.1.21 — 紧急修复：保存配置时整份回传被拒（满屏校验错误）

**这是 v0.1.20 引入的回归**，在 NAS 上手工测试时立刻暴露：Web 界面改配置点「保存」→ 满屏 `Input should be a valid string` 错误。

### 现象与根因

-现象：`PUT /api/config` 返回 **400 + 59 条 `string_type` 校验错误**，
  如 `scanner.ignored_id_pattern.0 Input should be a valid string [input_value=0, input_type=int]`。
  只改单个字段（如 `network.timeout`）却能成功 —— 说明问题出在**整份回传**这条路径上。
- 定位过程：先确认后端 `GET /api/config` 返回的 JSON **类型完全正确**（全是字符串），
  排除后端产出问题；再本地逐步打印类型，定位到 `unmask_secrets` 这一步
  **`ignored_id_pattern` 从 `['str','str',...]` 变成了 `[int,int,...]`**。
- 根因：`config_io.unmask_secrets` 的列表分支写成
  ```python
  return [unmask_secrets(i, reference[i] if isinstance(reference, list) and i < len(reference) else None)
          for i in range(len(submitted))]
  ```
  这里的 `i` 是**整数下标**（来自 `range`），被当作「提交项的值」递归传出，
  于是每个标量元素都被替换成了整数。`ignored_id_pattern`、`filename_extensions`
  这类**纯字符串数组**首当其冲被整段污染成 int，校验必然全红。
- 影响：前端「保存」按钮走的就是「取配置 → 改若干字段 → 整份 PUT」，
  所以**每次保存都会失败**，与用户改了什么无关。

### 修复

改为按位置对齐遍历（并只递归、不替换标量）：

```python
out = []
for idx, item in enumerate(submitted):
    ref_v = reference[idx] if isinstance(reference, list) and idx < len(reference) else None
    out.append(unmask_secrets(item, ref_v))
return out
```

### 验证

- 新增 `verify_config_io.py` 的 **T11 脱敏/还原往返完整性**（10 项断言）：
  覆盖嵌套 api_key 掩码与还原、**字符串数组往返后类型/内容必须不变**、
  混合列表（数字+字符串）往返不变、以及用**真实配置跑一遍「整份往返 + 模型校验」**
  ——即前端保存路径本身。此前该用例稳定失败（59 错误），修复后通过。
- 回归：`verify_config_io` **42/42**、`config_reload` 24、`output_toggles` 78、
  `task_store` 47、`path_traversal` 66 全部通过。
- NAS 实测：原样保存 → **200 `unchanged`**（修复前 400）；改 `retry` 3→4 → **200 `applied`**，
  6 个爬虫出口即时刷新。

### 教训

**纯逻辑的新函数也必须覆盖「整份数据往返」这种真实路径**——本次单测全部通过
（当时只测了 dict 形态的密钥还原），但一进真实流程就因「列表分支写错」而崩。
新增「脱敏/还原」这类**成对变换**函数时，验证脚本必须包含：①字符串数组 ②数字数组
③混合数组 ④嵌套 dict in list ⑤**用真实配置整体往返并校验**。

---

## v0.1.22 — 扫描页新增图形化目录选择器

此前扫描目录只能手输绝对路径；NAS 环境下容器内路径（如 `/data`）与Windows 侧看到的
`\\HOMENAS\Downloads` 不一致，容易填错。现提供「浏览目录…」按钮，可逐级下钻选择。

### 后端：新增 `GET /api/browse`

- **只列子目录，不列文件** —— 避免把文件名等无关信息一并暴露。
- 结果按目录名自然序排序，并返回 `parent`（上级路径，便于「上一层」；已在允许根内时为 `None`，
  不给越界上溯的入口）。
- 单个条目不可读（权限等）时跳过而非让整个列举失败。

### 安全边界：限制在 `JAVSP_BROWSE_ROOT` 之内

本服务无鉴权。若允许任意路径浏览，等于把**整个文件系统的目录结构**暴露给任何能访问该端口的客户端
（v0.1.20 审查已标记「`/api/scan` 无白名单」为中风险面，浏览会把它从「能读媒体目录」
扩大到「能枚举任意目录」）。因此：

- 根路径之外的请求返回 **403**；`..` 上跳同样被拒（`realpath` 消解符号链接后再比对，
  可防借软链绕过）。
- `os.scandir` + `follow_symlinks=False`：目录项中的符号链接不参与列举。
- 根由环境变量 `JAVSP_BROWSE_ROOT` 指定，**未设置时为 `/`（不限制）**——与原先手输路径的能力一致，
  本项目定位仍是单用户自用工具。若要收敛，容器部署时设 `JAVSP_BROWSE_ROOT=/data` 即可。

### 前端

- 扫描输入框前加「浏览目录…」按钮，打开对话框后可：点击目录名下钻、「上一层」返回、
  直接输入路径回车跳转、每行「选择」直接选定、「选择当前目录」选定所在层级。
- 选定后**只回填输入框、不自动扫描**——避免误触直接对根目录发起扫描。
- 以输入框已有内容为起点；为空时从允许浏览的根开始（不在前端硬编码 `/`）。

### 一个实现踩坑

`api_browse` 最初把缺省 `path` 硬编码为 `'/'`。测试立刻暴露：当 `JAVSP_BROWSE_ROOT` 被设成
别的目录（如容器里的 `/data`）时，`/` 落在允许根之外 → **点开对话框直接 403**。
改为缺省取「允许浏览的根」本身，前后端都不再硬编码 `/`。

### 验证

- 新增 `verify_browse.py`（**25/25 PASS**）：只列目录不列文件、自然序排序、`parent` 语义与根处为 `None`、
  **越界 403**（含 `..` 上跳）、不存在目录 400、缺省/空路径按根处理、符号链接不跟随、单条目异常容错，
  以及前端接线扫描（按钮/对话框/两个函数/提示文案）。
- 全量 12 个验证脚本通过；前端 `npm run build` 通过。

## v0.1.23 修复手工测试反馈的 4 个问题（空目录残留 / 浏览默认根 / 任务窗体残留 / 设置页无浏览）

### 1. 刮削完成后残留空目录

- 原实现只在 `datatype.rename_files` 末尾做一次 `os.rmdir(dir)`，**仅删最内层**。实际目录
  结构是 `<根>/<分类>/<影片>/xxx.mp4`，影片目录删掉后 **`<分类>` 空壳仍留在原地**。
- 改为 `_cleanup_empty_dirs` 向上递归清理。这是**不可逆操作**，故边界保护优先于功能：
  - `scan_root`（Web 端运行时挂载）为硬边界，越出即停；
  - 递归层数上限 2（`_CLEANUP_MAX_DEPTH`）——CLI 无 `scan_root`，靠它兜底防止一路删到
    用户目录树顶端；
  - 黑名单（统一用 `realpath` 口径，避免 `abspath` 与 `realpath` 混用使黑名单失效）：
    文件系统根、用户主目录、当前工作目录；
  - 隐藏目录（`.` 开头，如 `.Trash`/`@eaDir`）及其祖先一律不动；
  - 只删「确实为空」者，任一步 `OSError` 即停止而非硬删。
- 踩坑记录：首版实现里我在黑名单中加了 `os.path.dirname(start_dir)`，恰好把**待删的
  父目录**拉黑，导致递归完全失效（验证脚本立刻抓到 3 项失败）。层数上限也踩了一次：
  检查写在循环**开头**（`depth > MAX`）会多删一层，须写在删除**之前**才符合「最多删 N 个」语义。

### 2. 目录选择器需手工填默认根（且旧路径直接失败）

- 根因：`JAVSP_BROWSE_ROOT` 只在**运行时**通过 `-e` 传入，`Dockerfile` 里没有 → **新部署镜像
  默认就是 `/`**，与 `docker-compose.yml` 的挂载点 `/data` 不一致；且前端以输入框里的
  **残留旧值**作为起点（如上一轮填的容器真机路径 `/share/xxx`），这类值在新环境必然越界，
  点开浏览就是 `403 该路径不在允许浏览的范围内`。
- 三处改动：
  - `docker/Dockerfile` 新增 `ENV JAVSP_BROWSE_ROOT=/data`，与默认挂载点一致 → **开箱即用，
    不必手工填默认目录**；挂载到别处时覆盖该变量即可。
  - `api_browse` 越界时**回退到允许根**而非报错：回退后对话框可正常打开，用户再逐级点选。
    明确不做「静默列根外内容」——回退只是改变起点，白名单边界本身未放松。
  - 前端启动时调 `/api/browse('')` 取回根，**自动预填**扫描目录输入框，并在对话框里展示
    「根目录」；`doScan` 不受影响。
- 平台差异实测并记录：`os.path.realpath` 在 **Linux 上解析符号链接、在 Windows 上对目录
  联接（junction）不跟踪**。两种平台都拿不到根外目录列表，只是失败形态不同（回退 vs 400）。
  README 已注明，不再把「realpath 可防软链绕过」说成跨平台保证。

### 3. 批量/刮削结果窗体重新选目录后不消失

- `el-alert` 的 `v-if="batch.running || batch.log.length"`：只要 log 非空就**永久显示**，
  而 `doScan` 从不清空 batch 状态 → 换目录重扫后旧任务窗体仍挂在页面上。
- `doScan` 成功后重置 `batch` 与 `selectedGuids`：重新扫描即换了一批影片，旧 log 中的 guid
  已不在当前列表里，留着不仅占屏还会误导（旧任务的成败与新列表无关）。
- 单部刮削无独立 loading 状态（按钮靠 `scrapeResult` 判断），不受此问题影响。

### 4. 设置页「扫描目录」没有浏览按钮

- 把目录选择器抽成**可指定目标字段的复用组件**：`openBrowser(target)` +
  `browseTarget`，`chooseDir` 按 target 分流到 `scanPath` 或 `configObj.scanner.input_directory`，
  对话框标题随之变化。
- 前端接线扫描新增 9 项断言（含 Dockerfile 默认根、启动预填、设置页复用、doScan 重置），
  防止将来重构时静默丢掉其中一项。

### 验证

- 新增 `verify_empty_dir_cleanup.py` **30/30**：递归向上清理 / 非空目录绝不能删 / `scan_root`
  边界 / 无 `scan_root`（CLI）路径 / 隐藏目录不参与 / 多文件 CD1-CD2 / 家目录与 CWD 保护 /
  层数上限。边界断言多于功能断言——误删用户目录树不可逆。
- `verify_browse.py` **40/40**（原 25 项）：T4 语义由「403」改为「回退到根」并断言未泄漏根外
  内容；T7 符号链接断言改为跨平台不变式（原先写成 `if got == [...] else True` 的**恒真式**，
  等于没断言）；T9 接线扫描补 9 项。
- 全量 **14 个验证脚本通过**（output_toggles 78 / path_traversal 66 / config_io 42 /
  task_store 47 / browse 40 / cropper 65 / nfo_jellyfin 31 / empty_dir_cleanup 30 /
  config_reload 24 / sources 22 / batch 11 / scrape 7 / k4 / 依赖完整性 EXIT=0）；
  `npm run build` 通过。

## v0.1.24 修二次刮削暴露的 3 个问题（配置页浏览按钮 / NAS 缩略图缓存致空目录残留 / 番号正则顺序 bug）

### 1. 设置页「浏览目录」按钮点击无反应

- 根因：`el-dialog` 写在**「扫描目录」tab-pane 内部**。`el-tab-pane` 默认懒渲染，对话框
  随其宿主 tab 一起渲染/销毁，**从「设置」tab 调用同一组件**时出现定位与层级异常。
- 修法：把对话框**移到 `el-tabs` 之外**（`el-main` 内、tabs 后），并显式 `append-to-body`。
  另修 `chooseDir` 与 `openBrowser` 对 `configObj.value.scanner` 的空值访问——旧/精简配置
  可能整段缺失 `scanner`，直接 `v-model`/`赋值` 会抛 `TypeError`，表现同样是「点了没反应」。

### 2. 刮削后源目录仍残留（v0.1.23 的修复盲区）

- 现象：v0.1.23 已实现递归清理，但 NAS 上刮削后 `<分类>/<番号> #未知女优/` 仍原样留存。
- 实测定位（NAS 上逐目录查证）：那些目录里**只剩 `.@__thumb/`** —— QNAP 在任何被访问过的
  目录里生成的**缩略图缓存**，内含 `s100/s800/s2000/default` 前缀的缩略图副本（25 KB 级，
  影片本体 3.9 GB 已在 `#整理完成`）。`os.listdir` 因此判为「非空」，清理逻辑直接 return。
- 修法：新增 `_THUMB_CACHE_DIRS`（`.@__thumb` / `@eaThumb` / `@eaDir` / `.@__thumb_v2`）
  与 `_is_effectively_empty()`：
  - 判定「是否为空」时**忽略**这些缓存目录；
  - 递归过程中若某级**本身就是**缓存目录，直接 `shutil.rmtree` 后继续向上——
    不能只靠「隐藏目录一律不动」那条规则，否则 `.@__thumb` 被跳过，其父目录因残留该项
    而永远清不掉（这正是 v0.1.23 在 NAS 上的实际表现）。
  - 删缓存删不掉时只记 debug 日志、继续删目录，**不让「缓存清理失败」阻断主流程**
    （用户数据安全优先于目录整洁）。`.Trash` / `.git` 等其他隐藏目录仍受保护。

### 3. 「只有一个站点生效」——一个真 bug + 一批配置/网络事实

**真 bug（v0.1.20 引入）**：`javsp/lib.py` 的番号正则构造顺序错误，容器日志实证：

```
/app/javsp/lib.py:71: FutureWarning: Possible nested set at position 4/5/6
```

v0.1.20 为修「番号正则注入」把 `re.sub(r'[_-]','[_-]*',avid)` 改成
`re_escape(avid).replace('-','[_-]*').replace('_','[_-]*')`，并在注释里写下了一个
**错误推断**：「re_escape 的转义表不含 `-` 与 `_`, 故这一步不会破坏已转义的部分」。
实际 `re_escape` **会转义 `[` `]` `*`**，于是第二次 `.replace('_', ...)` 命中了第一次
刚生成的 `[_-]*` 里的 `_` 与 `[`，产出嵌套字符类 `[[_-]*-]*`。

正确做法：**先按分隔符切分 → 逐段 `re_escape` → 用未转义的 `[_-]*` 拼接**
（`'[_-]*'.join(re_escape(seg) for seg in re.split(r'[-_]', avid))`）。分隔符是自己加的、
不参与转义，两种顺序问题都不受影响。已实测两种错误顺序各自的后果（见教训）。

**配置/网络事实（非代码故障）**：NAS 上逐站点实测（`MFCS-082`）：

| 站点 | 结果 | 原因 |
|---|---|---|
| jav321 | ✅ | 唯一收录该番号的站点 |
| airav | 403 | 站点需登录/付费 |
| avsox / javdb / mgstage / prestige | `MovieNotFoundError` | 未收录，**属正常** |
| javbus | `ParserError: Document is empty` | Cloudflare 空响应 |
| javlib | 双路皆断 | 镜像站 `y78k.com` 连接被重置；官方站走代理时 `SSLCertVerificationError` |

关键发现：`network.proxy_free` 的语义是**「该站的镜像 / 免代理地址」**（如 `javdb368.com`），
**不是**「让这个站绕过代理」。配合 MITM 型代理（squid 解密 HTTPS）且容器内无其根证书时，
访问镜像站会因证书校验失败而全盘失败。README 已补「排查只有个别站点抓得到」一节
（含容器内逐站点实测命令与现象对照表），并澄清该字段语义。

### 验证

- 新增 `verify_special_attr.py` **49/49**：从**源码**提取 `pattern_str` 构造表达式求值
  （避免测试与实现漂移），断言无 nested set 警告、`-/_` 放宽语义保留、语义与修复前逐例
  一致、恶意 avid（8 种）仍无法注入、源码级顺序守卫。
- `verify_empty_dir_cleanup.py` **43/43**（原 30 项 + 13 项 NAS 场景）：`.@__thumb` /
  `@eaDir` 目录被清、**有效非空目录不被误删**（构造未被移动的额外子目录）、
  `.Trash-1000` 不被当作缓存删、`scan_root` 保留。
- `verify_browse.py` 40/40；**全量 15 个验证脚本通过**；`npm run build` 通过。

### 踩坑记录

1. 修 `.@__thumb` 时先写了「判定为空时忽略缓存」，但漏了「递归到缓存目录自身该怎么办」——
   它是隐藏目录，会被既有的「隐藏目录一律不动」规则跳过，父目录仍清不掉。测试抓到。
2. 写「有效非空目录不被误删」用例时，第一版把影片本体放在**会被移动的** `sub/` 里，
   于是目录理应被清、断言却要求保留——是测试数据构造错误，不是实现问题。改为放一个
   **不会被移动**的额外子目录。
3. 验证脚本里写了 `check('...', os.path.isdir(x) or not os.path.exists(x))` 这种
   **恒真断言**（永真），发现后改为真实判定。

## v0.1.28 废站优雅降级 —— javbus/javlib 壳页不再 IndexError 崩溃

### 现象与根因

v0.1.27 部署后持续实测多源抓取，结合 squid 代理临时故障恢复后的完整复测，澄清了「多站点能否恢复」的真相：

1. **三个镜像站在当前 NAS 出口（squid 机房 IP）下已全部变质**：`javlibrary.net` 偶发返回壳页/首页、`javbus.life` 已是 `noindex` 空壳、`javdb38.com` 变成垃圾站（`HeadlineLogic News Portal`），`javdb.com` 主站则被 Cloudflare 拦截（`403 Just a moment...`，cloudscraper 无法过新版挑战）。
2. **根因是镜像站本身废了，不是「反爬墙挡一下」**：纯 HTTP 跟 JWT/cookie 挑战、甚至上无头浏览器都救不了——浏览器也走同一个 squid 出口、面对的仍是废站。多源恢复在当前网络环境（机房 IP + 变质镜像）下不可行。
3. **唯一稳定可取数的源是 jav321**（多组真实番号实测均返回真实标题 + 封面）。

### 改动

- `javsp/web/javbus.py`：`parse_data` 解析前检测 `//div[@class='container']`，壳页/墙页/垃圾站直接抛 `MovieNotFoundError`（被 `core` 的 `except MovieNotFoundError` 容错干净跳过，不再因 `xpath(...) [0]` 越界抛 `IndexError` 重试刷错）。
- `javsp/web/javlib.py`：① 解析前检测 `/html/body/div/div[@id='rightcolumn']`，不存在抛 `MovieNotFoundError`；② 搜索结果遍历中 `tag.xpath("div[@class='id']/text()")` 改安全取法（缺值时 `continue`），避免 `[0]` 越界。
- 注：`javdb.py` 对废站已返回 `MovieNotFoundError`（本身优雅）；三废站（javlib/javbus/javdb）现均干净跳过，`jav321` 单源稳定运行。

### 验证

- 本地语法校验通过；部署 `javsp-fork:0.1.28` 后容器内多源 `parse_data` 实测（真实番号 IPX-001）：
  - `jav321`：OK，返回真实标题（如「女子校生便所交際…」）+ 封面，唯一稳定可取数据源。
  - `javlib` / `javbus` / `javdb`：均抛 `MovieNotFoundError`（壳页/墙页/垃圾站被解析前结构检测拦截），被 `core` 的 `except MovieNotFoundError` 容错干净跳过，**不再 `xpath [0]` 越界抛 `IndexError` 重试刷错**。
- 注：三废站在当前 NAS 出口（squid 机房 IP + 变质镜像）下均无法取数，属网络环境限制；代码层面已保证批量刮削不被废站拖崩，`jav321` 单源稳定。
- 关联 commit：`8db45ec`

## v0.1.27 多站点抓取修复 —— 翻转「有代理走主站」逻辑 + javdb 单站放宽 TLS

### 现象与根因

v0.1.26 修好「代理没配上」后，实测发现 javbus/javdb/javlib 三个站仍抓不到，但 jav321 稳定可用。
逐站点代码级实测（带 `-c` 真实配置）澄清了之前「mgstage/prestige 要日本 IP」等**误判**：

1. **javbus / javdb 逻辑反了**：原代码 `if proxy_server is not None: base_url = 主站` —— 一配代理就走
   `javbus.com` / `javdb.com` 主站（被 Cloudflare driver-verify / SSL 拦），而**可用镜像反而被闲置**。
   应改为「有代理走镜像、无代理走主站」。
2. **javdb38 镜像证书链不完整**：源站只下发叶证（Let's Encrypt YE1）、缺中间证书，certifi 严格校验必
   报 `CERTIFICATE_VERIFY_FAILED`（certifi 已最新仍 FAIL，证实非信任库旧）。属源站自身问题，非代理 MITM，
   故在 `javdb.py` 内对该 `Request` 实例显式 `verify=False` 单站放宽，不全局关闭校验。
3. **javlib 镜像 `javlibrary.org` 失效**：实测 `javlibrary.net` 证书默认即 200，仅镜像地址过期。

### 改动

- `javsp/web/base.py`：`Request.__init__` 增加 `verify=None` 形参（默认仍走 `tls_verify()` 全局策略），
  允许单站显式放宽校验。
- `javsp/web/javdb.py`：① `request = Request(use_scraper=True, verify=False)`；② `base_url` 翻转
  （有代理→`proxy_free[javdb]`，无代理→主站）。
- `javsp/web/javbus.py`：`base_url` 翻转（有代理→`proxy_free[javbus]`，无代理→主站）。
- `config.yml`：`proxy_free` 默认值更新为实测可用镜像 —— `javbus: javbus.email`、`javdb: javdb38.com`、
  `javlib: javlibrary.net`。
- 部署脚本同步把 NAS 持久化配置里的 `proxy_free` 三处也改为上述镜像（持久化配置不会被模板覆盖）。

### 验证

- 部署后容器内代码级解析实测 `ABP-123`/`SSIS-456`/`MIDE-800`：jav321 稳定；javbus/javdb/javlib 走镜像后
  应可命中（javbus 若仍撞 Cloudflare 则暂放弃，不影响 javdb/javlib/jav321 的多站目标）。

**补丁注**：v0.1.27 首提交（8ff0741）因写操作竞态导致 `javdb.py`/`javbus.py` 被误清空（空文件已推送），
已从 `HEAD~1` 恢复并重新应用上述全部改动；当前两文件非空、语法通过、换行符全 LF，已重新部署验证。
- 全 16 个回归脚本仍应全绿（本次仅动爬虫逻辑，不影响既有能力）。

## v0.1.26 配置读写不同源 —— 「设置保存了却没生效」「站点只有 1 个能抓到」的共同根因

### 现象与根因

主人反馈两个问题，实测发现**同一个根因**：

1. 「设置里的扫描目录不生效」——配置存了，扫描页不自动用它（v0.1.25 已修预填优先级）；
2. 「只有 1 个站点生效」——实测发现**运行时 `proxy_server` 是 `null`**，而 NAS 直连境外
   被阻断；唯一成功的 jav321 恰好是**不依赖代理也能直连**的站点。换言之「只有一个站点能抓」
   根本不是站点问题，**是代理没配上**。

而代理「配了又丢」的原因有两条：

1. **配置不持久化**：`config.yml` 在镜像内 `/app/config.yml`，容器每次重建都回到默认值。
   我历次部署都重建容器 ⇒ **主人之前在 Web 界面改的代理、扫描目录被我覆盖掉了**，且无任何提示。
2. **读写不同源（代码 bug）**：`server.py` 的 `PUT /api/config` **硬编码**写回
   `<包目录>/config.yml`。而 confz 支持 `-c/--config` 指定其它文件 —— 挂载配置到
   `/etc/javsp/config.yml` 后，**服务读的是它、界面写的是包目录那个**。实测表现极具迷惑性：
   `PUT` 返回 `{"status":"applied","reloaded":true,"changed":2}`，紧接着
   `GET` 读到的仍是旧值（写 A 读 B）。

### 改动

- **`javsp/server.py` 新增 `_config_file_path()`**：从 confz 的 `CONFIG_SOURCES` 取第一个
  `FileSource` 的路径 —— 即 `Cfg()` **真实读取**的文件，写回路径跟随之，不再硬编码。
- `docker-compose.yml` 补**配置持久化**（`/share/javsp_config:/etc/javsp`）+ `command: -c`，
  并注明两者缺一不可（只挂载不指定路径 = 写回落到镜像内文件 = 不生效）。
- README 新增「⚠️ 必须：配置持久化」小节，说明两个迷惑现象与初始化步骤，并提醒
  **先确认代理保存成功再开始刮削**（很多「站点抓不到」就是代理没配上）。

### 验证

- `verify_config_reload.py` **24/24 → 31/31**：新增「写回路径与读取路径同源」7 项 ——
  源码级守卫（定义了 `_config_file_path`、从 confz 取路径、写回已改用、不再硬编码）
  + 行为级（解析结果指向存在的文件、**与 confz 实际 FileSource 路径一致**）。
- **全量 16 个验证脚本通过**；`npm run build` 通过。

### 教训

「保存成功提示 + 配置不变」这类现象，先查**读的路径和写的路径是不是同一个文件**，
不要先怀疑前端或缓存。这类 bug 在本地开发时完全看不出来（读写都指向包目录那个文件）。

---

## v0.1.25 设置里的扫描目录自动回填 + 站点抓取失败的正确诊断

### 1. `scanner.input_directory` 存了却不生效

- 现象：在「设置」里选好扫描目录并保存，但扫描页输入框仍是旧值，等于配置没起作用。
- 修法两处：
  1. **页面加载时按优先级预填**：`scanner.input_directory`（用户保存的默认扫描位置）
     > 允许浏览的根。原先只用后者，所以配置值形同虚设。
  2. **保存后立即同步**：设置页保存成功即把 `scanner.input_directory` 写回扫描页输入框，
     免得保存完还要切 tab 重新选一次（且切过去看到的仍是旧值）。
- 不静默改写配置值：配置里可能是 CLI 用的相对路径或宿主机路径，与 Web 端容器内路径未必
  一致；后端 `/api/scan` 有 `isdir` 校验，填了但扫不了时用户能看到明确报错。

### 2. 「只有个别站点抓得到」的根因（实测推翻了两处想当然的判断）

**先说被实测推翻的**：
- 最初以为 NAS 出口直连被墙 → 实测 `ConnectionReset`，但**经代理是通的**；
- 最初以为是 MITM 证书校验失败 → 实测**容器内严格校验反而是通的**
  （`javdb.com`/`javbus.com`/`jav321.com`/`prestige-av.com` 全 200）；
  且经代理握手 `issuer=Google Trust Services WE1`，说明 **squid 对这些域名是透传不解密**。

**真正的两个原因**：
1. **配置里的镜像地址已失效**：`javdb368.com` / `seedmm.help` / `y78k.com` 四个全部
   `is_connectable=False`，自动获取新地址也全部失败。实测可用：`avsox.click`、
   `javdb.com`、`javbus.com`、`javlibrary.org`。已更新 `config.yml` 默认值。
   自动获取机制本身是活的（实测 javlib 成功拿到 `https://202608.urldance.com`），
   只是它同样走那套请求层。
2. **javdb 主站对中国出口返回「版权限制」提示页**：
   `https://javdb.com/search?q=MIDE-800` 返回
   `Due to copyright restrictions, access to this site is...`（仅 1278 字节），
   于是解析成「未找到影片」——**连 `MIDE-800` 这种常见番号都报未收录**，可确认是地域
   限制而非地址失效，`/cn/` 路径还额外 404（站点结构已变）。**属环境问题，需换镜像或换出口**。

**代码侧补的能力（不默认降低安全性）**：新增 `javsp/web/base.py: tls_verify()`。
代理若做 TLS 解密(MITM)，默认校验会让**所有**走代理的站点一起失效。本项目**不默认关闭
校验**（那是 v0.1.20 审查明确肯定的安全基线），改为支持注入：
- `JAVSP_CA_BUNDLE=/certs/proxy-ca.crt` —— 指定代理根证书，校验**仍开启**；
- `JAVSP_TLS_VERIFY=0` —— 显式关闭（不推荐，仅受控内网）。

实现要点：本项目有 40+ 个请求点（cloudscraper ×3 + requests get/post/head + 模块级函数 +
`urlretrieve`），**逐处加 `verify=` 易漏**，故在 `Request.__init__` 里用
`functools.partial` **一次性绑定**到三个方法，模块级 4 处调用单独注入。

**`is_connectable` 漏传 `proxies` —— 「地址失效自动获取新地址」长期失效的根因**（本轮
实测才发现）。该函数是 `proxyfree.get_proxy_free_url` 的**唯一探测入口**，原先不传
proxies ⇒ 永远走**直连**探测，而实际抓取走代理。实测后果：NAS 上四个站点的
`is_connectable` **全部 False**，自动获取全部返回空串 —— 主人镜像地址失效后**无法自动
恢复**，只能手工填。已修。这是「探测与实际使用配置不一致」的典型：**探测必须与真实路径
同配置**，否则结论不可用。

### 验证

- 新增 `verify_tls_inject.py` **28/28**：`tls_verify()` 三种取值语义（默认校验 / 自定义 CA /
  显式关闭）、CA 文件不存在时退回默认而非抛错、**CA 优先于关闭开关**、空白值被忽略、
  运行时 `partial.keywords` 确实带 `verify`、**全项目无硬编码 `verify=False`**、
  **用 AST 精确统计**模块级 `requests.get/post` 调用点数量与 `verify`/`proxies` 覆盖
  （不靠字符串 `count`——多行写法会少算，本轮就因此误报过一次）、`is_connectable`
  必须带 `proxies=read_proxy()`。
- `verify_config_io.py` 42/42：修 T3「同级站点未被误改」——原断言硬编码了
  `seedmm` 这个会随时间失效的域名，换镜像地址后**假失败**。改为比对「改动前后同级键
  逐行相等」，不再耦合具体地址。
- **全量 16 个验证脚本通过**；`npm run build` 通过。

### 踩坑

1. **我改 `config.yml` 时把 `proxy_free` 写成空串**，而该字段类型是 `Url`（不接受空串），
   导致**整个配置校验失败**、测试直接跑不起来。前端「留空 = 不改动该键」是 PUT 语义，
   与 yml 文件里「键必须有合法 URL」是两回事——**文档没说清这点**。
2. **`verify_tls_inject.py` 第一版有环境变量泄漏**：用例里设了 `JAVSP_CA_BUNDLE` 指向临时
   文件，`finally` 只还原初始快照，导致后续「空白 CA 值」用例继承了上一个用例的路径而假失败。
   修法：每个用例前显式清空两个变量（比依赖 try/finally 更可靠）。
3. `verify_config_io.py` 早前也有一处恒真式断言，本轮顺手清掉了。

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

16. **【部署阻断】`packaging` 是运行时依赖却从未声明，容器启动即崩**
   - 现象：NAS Docker 部署后容器反复重启，`ModuleNotFoundError: No module named 'packaging'`。
   - 根因：`javsp/func.py` 用了 `from packaging import version`，但 `pyproject.toml` 未声明该依赖，
     它仅作为 dev 组 `cx-freeze` 的传递依赖存在于 lock；`poetry install --only main` 排除 dev 组后，
     干净 venv 里就没有它。本地长期未暴露是因为 pip / poetry 自身携带 `packaging`，恰好掩盖了「未声明」。
   - 隐蔽性：这是一类**只有干净环境才暴露**的 bug——本地 `pip install -e .` 会解析传递依赖而侥幸存活。
   - 修复：主依赖显式声明 `packaging = "*"`（v0.1.16）。

17. **【部署阻断】Dockerfile 入口点用 CLI 脚本，容器跑成扫描模式、永不监听端口**
   - 现象：容器状态 `Up` 但 8000 端口不监听、容器日志空白，看起来「启动了又没起来」。
   - 根因：`ENTRYPOINT ["/app/.venv/bin/javsp"]` + `CMD ["server"]`；而 `javsp` 是 CLI 入口
     （`javsp.__main__:entry`），**不解析子命令**，`server` 参数被忽略，容器实际执行完整 CLI 流程
     （读配置 → `check_update` 联网 → 扫描目录），与 Web 服务无关。
   - 踩坑记录：为快速修复曾用 `docker run --entrypoint sleep ...` 起临时容器改镜像再 `docker commit`，
     结果 **`docker commit` 会把临时容器的 ENTRYPOINT 一并固化进镜像**，导致后续 `docker run`
     起的是 sleep 而非服务。改镜像入口点必须用 `docker commit --change 'ENTRYPOINT [...]'` 显式覆盖。
   - 修复：改用 poetry 生成的 `server` 控制台脚本（`= javsp.server:entry`）作为入口点（v0.1.16）。

18. **【文档/流程】README 的 `docker build` 缺 `-f`，`poetry.lock` 与 pyproject 失同步**
   - `docker build -t javsp-fork .` 报 `open Dockerfile: no such file`——Dockerfile 实际在 `docker/` 子目录，
     仓库根没有；README 命令缺 `-f docker/Dockerfile`（v0.1.16 修正）。
   - `poetry.lock` 的 `content-hash` 与 `pyproject.toml` 已不匹配，干净环境 `poetry install` 会报
     `pyproject.toml changed significantly since poetry.lock was last generated`，只能靠 Dockerfile 里
     临时加 `poetry lock &&` 绕过。已重生 lock，并给 Dockerfile 加 `poetry lock &&` 使其自恢复（v0.1.16）。
   - 相关工具坑：Windows 下 `poetry lock` 会把整个 lock 写成 CRLF（与本项目长期坚持的 LF 冲突），
     已用二进制方式还原为 LF；且 `poetry lock` 会拿项目 venv 的解释器校验 pyproject 的 python 约束，
     必须先 `env use` 到合规版本（本项目 `<3.13`，故用 3.12）。

19. **【内存泄漏】`TASKS` 内存缓存无界增长，长跑只能靠重启释放**
   - 现象：`server.py` 的 `TASKS`（`guid -> Movie`）是普通 dict，**全项目没有任何删除路径**：
     写入只发生在 `api_scan`（扫描入库）与 `api_scrape`（按 avid 刮削后入库），而
     `api_organize` / `api_batch` 只读、`api_movies` 只遍历。每扫描一个新目录、每多刮削一部影片
     就多一份 `Movie`（含 `MovieInfo`、封面/剧照路径）常驻内存。
   - 影响：NAS 上连续运行数天、或反复扫描不同媒体库时内存无界增长，只能重启容器释放；
     此前「容器重启后 TASKS 被清空」一直被当作已知现象，根因未被发现。
   - 修复：新增 `javsp/task_store.py`的 `TaskStore`（dict 兼容，实现 `__setitem__`/`get`/
     `__contains__`/`values` 等），`TASKS = TaskStore()` 顶替普通 dict，既有调用点零改动。
     三重策略：TTL 过期（默认 6h，`monotonic` 防时间跳变，访问刷新戳）+ 容量上限（默认 2000，
     最旧优先淘汰）+ 活跃保护（`mark_active()`上下文管理器，organize/batch 期间跳过回收，
     保证异常路径也解除标记）。清理只在写入时触发，不引入后台定时线程（v0.1.17）。
   - 验证：`verify_task_store.py` **47/47 PASS**（含异常路径解除活跃保护、8 线程并发、源码扫描防漂移）
     + `smoke_tasks.py` 真 uvicorn 端到端冒烟；全量 10 个验证脚本通过。

20. **【已知未做】SSE 客户端断开后生成器与后台线程均不回收**
   - 现象：`api_scrape` / `api_organize` / `api_batch` 三处SSE 生成器都是
     `while True: item = q.get()`（**无超时**）。客户端中途断开后，生成器永久卡在 `q.get()`，
     而后台 daemon 线程仍会跑完整个刮削流程并持续往队列堆积（已无人消费）。
   - 为何本次不做：彻底修复需同时做三件事 —— ①生成器异步化 ②端点注入 `Request` 才能调
     `await request.is_disconnected()`（当前端点收的是 Pydantic 模型，`req` 并非 Request，
     直接用会 `NameError`）③增加心跳事件（**心跳会插入 SSE 事件序列**，影响前端解析与
     `verify_batch_e2e` 的序列断言）。风险高于同批改动，且现有脚本**无法模拟「客户端中途断开」**
     （需真实 HTTP 断流），无测试守护下改SSE 核心路径违背「小步快跑/不堆积未验证代码」。
   - 处置：留待独立一轮，先设计并补上断流测试再实施（v0.1.17 记录）。

21. **【安全·已核查】落盘路径穿越：防护已存在，缺的是防回归的测试**
   - 威胁模型：番号 / 女优名 / 标题 / 简介均来自外部爬虫（不可信输入），会进入
     `output_folder_pattern`（`#整理完成/{actress}/...`）与文件名模板；若未净化，
     落盘可能经`../` 逃出扫描根 —— 在 NAS 上等于可覆盖共享目录任意文件。
   - 核查结论：`javsp/file.py: replace_illegal_chars` 已有两层防护——分隔符 `/` `\` 全角化、
     含 `..` 时把连续点替换为 `…`。真实 PoC 端到端验证（`../`、反斜杠、夹带、多点绕过
     四种载荷跑 `generate_names` + `organize_movie`）**均未逃逸**，扫描根外也未产生逃逸目录。
     **故非漏洞，是「防护有效但无测试守护」。**
   - 处置：新增 `verify_path_traversal.py`（**63/63 PASS**）把该属性固化为回归断言——
     15 种恶意载荷的纯函数断言 + 端到端落盘边界断言（统一用 `realpath` 比对，
     避免只看字符串的弱判据）+ `_root_save_dir` 语义回归。目的是防止将来为支持
     含 `/` 的正常标题而放宽替换逻辑时，无声削弱防线（v0.1.18）。

22. **【隐藏地雷】`pydantic` / `pydantic-core` 直接 import 但未声明，靠传递依赖存活**
   - 与 Issue 16（`packaging`）**完全同类**：代码直接 import，但 `pyproject.toml` 未声明，
     靠上游包传递依赖侥幸存在。本地 pip 会自动解析传递依赖故永远无感，
     只有干净环境（`poetry install --only main` / Docker 镜像）才会崩。
   - 扫描发现：`pydantic` —— `config.py`（`ByteSize`/`Field`/`NonNegativeInt`/`PositiveInt`）、
     `server.py`（`BaseModel`）、`__main__.py`（`ValidationError`）均直接 import，原仅靠
     `confz → pydantic>=1.9,<3` 传递；`pydantic-core` —— `config.py` 与 `web/translate.py`
     直接 `from pydantic_core import Url`，原仅靠 `pydantic` v2 自带核心。
   - 危害：一旦上游 `confz`/`pydantic` 停止传递即崩，且崩溃点（import 语句）离真正原因很远，
     排查成本高。
   - 修复：显式声明 `pydantic = "^2.9.0"`、`pydantic-core = "^2.23.0"`（版本交由 pydantic 对齐），
     重生 lock（v0.1.19）。
   - 工具：新增 `verify_dependency_completeness.py`，用 `ast` 扫描全部 `.py`（含 web/ 下 30+
     爬虫模块）比对声明，退出码 0 = 无漏网。**扫描结果必须人工核实**：本次误把 `webview`
     报为未声明（实为 `pywebview` 提供，映射表漏项），照单全收会把工具缺陷当成代码缺陷去改。

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

### v0.2.1（2026-10-08）— 渠道移植 + 渠道监控与熔断面板（大变更）

**背景**：v0.1.28 实证受限出口下 `javlibrary`/`javbus` 已废、`javdb` 网站被 Cloudflare 拦截，
仅 `jav321` 可取数。研究 JavBoss（github.com/Solr159/JavBoss）刮削层后移植可用渠道，并引入渠道健康监控。

**新增渠道（NAS 经 squid 实测可达，e2e 成功 11 部 / 非预期异常 0 处）**
- `javdbapi` — javdb 手机 App 私有 API（`jdforrepam.com`），纯 JSON 绕开 Cloudflare。
  来源：JavBoss `internal/jav/javdbapi`（MIT），文件头保留署名。
- `javdatabase` — javdatabase.com 详情页。移植自 JavBoss `internal/jav/javdatabase`。
- `javmenu` — **原实现抓取死站 `mrzyx.xyz`（自 v0.1.28 起恒空过），整体重写为 `javmenu.com`**。
  模块名与 `CrawlerID.javmenu` 保持不变，属同名替换，无需改动任何注册逻辑。
- `minnanoav` 经实测**判定不适用**：6 个 movie-by-code 候选路径全部 404、搜索页被 Cloudflare 拦截，
  且其接口本就只有女优查询，与 `parse_data(movie)` 不匹配，故不强行适配。

**新增：渠道监控与熔断（`javsp/web/health.py`，移植自 JavBoss `availability.go`）**
- WebUI 新增「渠道监控」页，覆盖**全部 21 个已注册渠道**（含未启用的），展示状态/耗时/命中率/
  失败原因/剩余冷却，并支持手动探活。
- **熔断器**：连续失败 2 次才熔断（不取 1，避免网络抖动误杀好源），冷却 10s→20s→…→上限 5 分钟，
  到点进入半开试探，成功恢复 / 失败重新熔断。实测死源刮削 **5.80s → 1.00s**。
- 仅对通道级故障（网络/DNS/TLS/超时/HTTP/被拦）熔断；**`未收录`、`内容异常`、`结果重复`
  绝不熔断** —— 这些说明源本身是好的。
- 判定走真实 `parse_data` 且要求解析出有效标题（很多死站返回 200 + 壳页，只看传输层会误判）。
- 每次刮削的成败自动汇入健康档案（零额外请求），刮削结果表同步显示「已跳过 / 试探中 / 状态」。
- API：`GET /api/channels`、`POST /api/channels/check`。

**修复**
- **熔断阈值形同虚设**：首次失败即熔断（`breaker != OPEN or ...` 短路致阈值判断被绕过）。
- **熔断无法恢复的死锁**：`success` 未纳入恢复分支，熔断中的源即使半开试探成功也永远回不来。
- 成功路径不记录 `elapsed_ms`，致前端「耗时」列对正常源恒为 0。
- 脱敏漏洞：requests 常见的无 scheme URL（`//path?token=xxx`）逃过脱敏正则，token 可能进日志。
- **设置页无法勾选新源**：`App.vue` 的 `crawlerSites` 是 `CrawlerID` 的手工副本，漏了新注册渠道；
  已补齐并新增 `verify_crawler_sites_sync.py` 守护此类漂移。
- **README 错误说明**：原写「环境变量嵌套层级用双下划线」是错的 —— confz 2.x 的
  `nested_separator` 默认是 `.`，双下划线会被**静默忽略**（不报错、不生效），已改点号并加警示。

**其他**
- `samples/`（真实第三方页面样本）加入 `.gitignore`，不入库。

**验证**：`verify_health`(104) / `verify_health_integration`(22) / `verify_channels_api`(38) /
`verify_sanitize`(11) / `verify_newsrcs_real`(39) / `verify_newsrcs_registration`(21) 等全绿；
NAS 容器内经 squid 实测确认新三源正常、javbus 被熔断并跳过、javlib 未收录未被误熔断。

---

### v0.2.3（2026-10-09）— 会话不再被重启打断、401 不再「点了没反应」（小变更）

**修复**

- **容器/进程一重启，会话就全部失效**（表现：「操作一会儿就提示未登录或会话已过期」，刷新也一样）。
  根因实测取证：会话原先只存进程内存，容器 17:09:45 重启后内存会话清空，而浏览器 Cookie 还在，
  之后每一个请求（**连只读的 GET 也算**）都被中间件判为 401 —— NAS 日志里连续 15 个 401 全部来自
  带旧 Cookie 的浏览器。
  → 会话改为**落盘**：默认写在「实际生效的配置文件」所在目录（Docker 部署即挂载出来的
  `/etc/javsp/.sessions.json`，可用 `JAVSP_SESSION_FILE` 覆盖），重启后自动恢复未过期会话。
  落盘内容只有令牌的 **sha256 哈希**与过期时间，文件权限 `0600` —— 即便文件被读走也反推不出可用
  Cookie。实测：容器 `restart` 后旧 Cookie 访问 `/api/channels`、`/api/config`、`/api/movies`、
  `PUT /api/config` 全部 200。

- **401 之后界面没有任何出口**（表现：设置页显示红字，点保存/重试都原地打转，即反馈里的
  「点击无反应」）。根因：`api.js` 里 `getConfig` / `getMovies` / `getChannels` / `browse` /
  `getConfigRuntime` 等仍是**裸 fetch**，拿到 401 只把错误文本丢给调用方，**不会广播事件**，
  App 因此完全不知道会话已失效，页面就卡在错误态不动。
  → 除 `login` / `logout` 外所有接口统一走 `request()`，401 一律广播；App 立即退回登录页并显示
  「会话已失效，请重新登录」。该提示走 `notice`（信息态）而非 `error`（错误态），不会把输入框
  标红 —— 用户只是被登出，照常登录即可。
  注：登录/注销刻意例外 —— 它们在白名单里，密码错的 401 是「凭据错误」而非「会话过期」，
  广播它会让用户看到莫名其妙的「会话已失效」。

- **滑动续期把有效期越滚越大**：续期条件用 `expires - created` 当窗口，而该差值每次续期都会变大，
  等于让有效期无限滚雪球。改为用会话自带的固定 `ttl` 续期（实测续期后剩余时间恒等于原 TTL）。

- 会话目录跟随**实际生效的配置文件**目录（`auth.set_state_dir`），并在设置后**重新读盘**一次，
  避免「从默认位置读、往指定位置写」的读写不同源（与 v0.1.26 配置那次同类坑）。

**修复（同版本第二批：保存配置报 YAML 解析失败 + 渠道参与列自相矛盾）**

- **保存设置报 `expected <block end>, but found '<scalar>'` 并自动回滚**。
  根因：用户挂载的 config.yml 里 `crawler.selection.normal` 写成**跨两行的 flow 数组**
  （YAML 语法上合法），而 `write_config_preserving_comments` 只替换首行，第二行成了孤立标量。
  → 新增 `_flow_end()`：替换 leaf 时按括号深度吞掉续行（忽略引号内的括号，避免误算）。
  修复后保存会把该值收敛成单行，旧格式自动被纠正。
- **「番号不适用」被当成内部错误计进熔断**（会误杀好源）：探活用固定样本 `IPX-001` 去问
  FC2 / GETCHU / gyutto 这类只收录特定番号段的站点，它们抛 `Invalid XX number` —— 这是
  「问错了对象」，不是站点故障。实测 fc2 / fc2ppvdb / dl_getchu / gyutto **各已失败 1 次，
  再探一轮就会被全部跳过**。
  → 新增状态 `code_mismatch`（归入"源没问题"集合，不熔断、不计数）；同时 `_sample_for()`
  按源给样本（FC2-1234567 / GETCHU-12345 / GYUTTO-12345 / fanza 用 cid ipx00001）。
- **FANZA 因空 cid 被扣上「内部错误」**：只设 dvdid 时它拿空 cid 拼详情页 URL，拿到非详情页，
  xpath 取标题抛 `IndexError` → 归为 error（可熔断）。→ `parse_data` 开头校验 cid，
  缺失时抛 `MovieNotFoundError`；探活侧为 fanza 显式设置 `movie.cid`。
- **渠道监控「刮削参与」列与状态矛盾**：状态已是「内部错误/HTTP 错误」却仍写「参与」。
  → 已累计失败但未达阈值时显示 `参与 · 失败 1/2`（并转为警示色），让人知道"再失败一次就跳过"。

**修复（同版本第三批：探活真正打进站点后暴露的真实爬虫缺陷 + 出口故障保护）**

- **gyutto 解析 `UnboundLocalError`**：`producer` / `genre` / `publish_date` 只在遍历到对应
  `<dt>` 时才赋值，页面缺项就未定义 → 被记成"内部错误"并累计熔断（探活真正打进该站才暴露，
  此前一直被"番号不适用"挡着没走到这一步）。→ 循环前给默认值，并跳过没有 `<dt>` 的行。
  修复后 gyutto 探活 **正常**（70ms）。
- **FANZA 空 cid 与无效 cid 都抛 `IndexError`**：空 cid 拼出的 URL 拿到非详情页，xpath 取标题越界；
  cid 无效时 DMM 也不返回 404 而是 200 的"无此商品"页。→ 缺 cid 抛 `MovieNotFoundError`，
  详情页无标题同样判"未收录"。探活侧为 fanza 显式设置 `movie.cid`（它按 cid 而非番号查询）。
- **出口整体故障会把所有好源一起熔断**：新增 `_guard_against_outage()` —— 单轮探活里
  ≥5 个源且 ≥70% 同时通道级失败时，判定为"出口整体故障"，全部豁免（状态 `outage`，不熔断、
  计数清零）。实测触发：容器重启后 squid 失效，15 个源同一时刻全部 TLS 失败、10 个被熔断，
  而其中 jav321 / javdbapi / javmenu 几分钟前还是"正常"。

**验证**

- `verify_health.py` 扩到 **120 项**：新增「番号不适用连报 3 次也不熔断、且不累计计数，
  而真实内部错误照常熔断」「探活样本符合各源番号格式」两组断言。
- `verify_config_io.py` 新增 **T8b**（6 项）：跨行 flow 数组替换后仍是合法 YAML、值正确、
  相邻字段未波及、续行残留已清除、注释仍在、**单行 flow 不误吞下一行**（防吞过头）。
- 新增 `verify_session_persist.py`（11 项）：用**独立子进程**模拟重启（同进程重新 import 证明不了
  跨进程存活），覆盖跨重启有效、注销后重启不复活、落盘无明文、过期会话不恢复、续期不膨胀、换目录即失效。
- `verify_api_credentials.py` 扩到 **24 项**：新增「除登录/注销外所有接口必须走 `request()`」回归断言
  （按花括号配平提取函数体，避免相邻非导出函数混入切片造成误报）。
- `verify_session_ux.py`（19 项）全绿。
- NAS 容器内实测：0.2.3 健康检查、登录、设置页保存、重启后旧 Cookie 全部通过。

---

## 提交基线说明

- `c4cfe61`：从上游克隆推上 fork 的首笔 commit（含上游截至 #503 的内容），作为本项目 git 历史起点。
- 后续 `6494dbd` 起为 JavSP-fork 自有迭代。
