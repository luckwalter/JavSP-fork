# JavSP-fork 整改目标清单

> **📌 本清单已被 2026-10-07 的系统性代码审查取代。**
> 现已完成 4 维度并行审查（Web 层 / 爬虫层 / 性能 / 前端+CLI+配置），完整取证、定级与整改路线见
> **[`CODE_REVIEW.md`](CODE_REVIEW.md)**。下方 P0/P1 部分（部署阻断、内存泄漏、路径穿越、依赖完整性）
> 已分别在 v0.1.16~v0.1.19 落地；P2/P3 的「待审查」候选已在审查报告中定级为 P0-1~P1-16 / P2-1~P2-5。
>
> **当前最高优先级（摘自 CODE_REVIEW.md，均为改动极小的高危项）**：
> 翻译 API Key 明文进日志 · 默认绑 `0.0.0.0` 无鉴权 · `/api/config` 明文返回密钥 ·
> `lib.py` 番号正则注入 · `config.yml` 被 git 跟踪 · `retry=0` 静默全失败。

---

<details>
<summary>（历史）初版整改清单 —— P0/P1 部分，保留备查</summary>

> **依据说明**：本清单基于 ①NAS 部署排查的实据发现 ②快速代码扫描坐实的风险点 ③项目长期积累的系统性教训整理。
> 优先级：**P0** 阻断正常部署/构建 → **P1** 长跑资源/性能风险 → **P2** 安全（暴露场景） → **P3** 持续工程纪律。

---

## P0 — 阻断部署/构建（先解）

| # | 问题 | 证据 / 现状 | 整改目标 | 验收 |
|---|---|---|---|---|
| 1 | **poetry.lock 与 pyproject.toml 失同步** | 仓库 `poetry.lock` 停在 10-06 版本；本次给主依赖补了 `packaging = "*"`，lock 未重生 → 干净环境 `poetry install`（不带 `poetry lock &&`）会报「pyproject.toml changed significantly」 | 跑 `poetry lock` 重新生成并提交，使 `poetry install` 单独可用 | 干净 venv `poetry install --only main` 成功；`poetry check` 退出码 0 |
| 2 | **Dockerfile 入口点 / packaging 声明 / README build 命令（本地已改未提交）** | 部署排查实证：原 `ENTRYPOINT javsp` + `CMD server` 跑成扫描模式（8000 不监听）；已改 `ENTRYPOINT ["/app/.venv/bin/server"]`、补 `poetry lock &&`、README 补 `-f docker/Dockerfile` | 提交并按发版五步推送（拟定 v0.1.16） | `docker build -f docker/Dockerfile -t javsp-fork .` 成功；`docker run` 后 8000 监听、`/api/health` 200 |

---

## P1 — 资源 / 性能（长跑风险）

| # | 问题 | 证据 / 现状 | 整改目标 | 验收 |
|---|---|---|---|---|
| 3 | **TASKS 内存缓存无上限、无清理（内存泄漏）** | `javsp/server.py:35` `TASKS: Dict[str, Movie] = {}`；scan(134)/batch(185) 仅写入，全仓无 `del`/`pop`/`clear`，仅容器重启清空（MEMORY 教训⑥亦印证）→ NAS 长跑越积越多 | 任务完成/失败后清理对应 `guid` 条目；或加 TTL 上限 + LRU 淘汰；批量结束统一回收 | 批量跑 N 部后 `len(TASKS)` 不无限增长；新增单测覆盖清理路径 |
| 4 | **SSE 连接未显式清理** | `/api/batch` 的 `StreamingResponse` 生成器在客户端断开后是否及时终止后台线程需复核（v0.1.6 已收敛僵尸线程，但 SSE 信道层面未单独验证） | 复核 SSE 断开→后台任务取消链路；客户端断开后后台线程及时退出 | 复现客户端中途断开，断言后台线程在超时内退出 |

---

## P2 — 安全（暴露场景）

| # | 问题 | 证据 / 现状 | 整改目标 | 验收 |
|---|---|---|---|---|
| 5 | **WebUI 免登录 + 扫描目录无白名单** | `server.py:126` scan 端点仅 `os.path.isdir` 校验，无路径白名单；WebUI 无鉴权 | 内网部署风险可接受；**若计划公网暴露**：加基础认证 + 扫描根白名单/路径约束，并在 README 明确「仅内网」 | 暴露前 checklist 通过；非白名单路径返回 403 |
| 6 | **路径穿越面（整理落盘）** | `output_folder_pattern` 含番号/女优等外部数据，落盘路径构造需复核是否会被 `../` 注入 | 复核 `_root_save_dir` / 文件移动路径规范化，确认无 `..` 逃逸 | 构造恶意番号/女优名（含 `../`）的单测，断言落盘不逃出扫描根 |

---

## P3 — 持续工程纪律

| # | 项 | 现状 | 目标 |
|---|---|---|---|
| 7 | **运行时依赖声明完整性** | `packaging` 是漏网的又一个（靠 dev 组传递依赖侥幸存活）；上游可能还有同类 | 跑一次 import vs 声明依赖静态差异扫描（如 `pip-audit` 或自写脚本），把所有运行时 import 但未在 main 声明的依赖补齐 |
| 8 | **CLI/Web 双实现同步守卫** | `__main__.py` 有独立整理流程，v0.1.15 已用源码扫描断言守住 | 持续维护该断言；任何输出/配置改动必须两边同步或抽到 `core.py` 共用 |
| 9 | **验证脚本改写仓库文件的还原纪律** | v0.1.15 踩过「基准本身是脏的」，已加 `atexit` + 基线自愈 | 新增会改写仓库真实文件的验证脚本一律先问「第 3 步崩了会留下什么」 |
| 10 | **换行符污染** | Windows 下 Python 文本模式写文件偷换 LF→CRLF，已全局加 `newline=''` | 涉及读改写的脚本统一走 `_detect_newline()` + `newline=''` |

---

## 已确认安全基线（无需整改，记录在案）

- **前端 XSS 风险低**：`frontend/src` 全仓无 `v-html` / `innerHTML` / `dangerouslySetInnerHTML`，Vue 默认文本插值自动转义。
- **爬虫请求合规**：`javsp/web/base.py` 所有 `requests.*` 均带 `timeout`、走 `read_proxy()`，未显式关 TLS 校验（默认 `verify=True`）。

---

## 待审查 / 提议

本次为快速扫描，**未覆盖**：全部端点的鉴权矩阵、SSRF（爬虫 URL 是否可被用户输入操控）、依赖 CVE（`pip-audit`）、封面图片解码内存占用、并发资源上限。建议后续执行一次系统性「性能 + 安全」代码审查，把上述 P2/P3 候选项坐实或排除。

---

## 建议执行顺序

1. **P0-1 + P0-2 一起做**：`poetry lock` 重生 → 提交（含 Dockerfile/README/pyproject 三处改动 + lock）→ 发版 v0.1.16 → 推送。
2. **P1-3**：TASKS 清理（独立小改，附单测）。
3. **P2-6**：路径穿越单测（防御性，成本低）。
4. **系统性审查**：覆盖剩余 P2/P3 候选。← **已完成，见 `CODE_REVIEW.md`**

</details>

---

## 审查后新增的 P0/P1 概览（详见 CODE_REVIEW.md）

| 优先 | 项 | 一句话 |
|---|---|---|
| P0-1 | 翻译密钥明文进日志 | `translate.py:106/108/115/117/129` 的 `format(engine,…)` 会渲染出 `api_key`，`translate.py:38/48` 写进日志 |
| P0-2 | 默认绑 `0.0.0.0` + 无鉴权 | `server.py:389`；Dockerfile 无 `USER`；compose `8000:8000` 暴露全网卡；无 `TrustedHostMiddleware` |
| P0-3 | `/api/config` 明文返密钥 | `server.py:310` 无过滤；`config_reload.py:119-125` 代理凭据也明文 |
| P0-4 | 番号正则注入 | `lib.py:64` `avid` 未转义拼进正则（`re_escape` 存在却未用）→ ReDoS + 判定绕过 |
| P0-5 | `config.yml` 被 git 跟踪 | `.gitignore` 无 config 规则，填了密钥即泄密 |
| P0-6 | `retry=0` 静默全失败 | `core.py:119` `range(0)` 不执行 → `success` 永不置位 → 结果全丢 |
| P1-1 | `overall_timeout` 误杀第二波 | `core.py:171-172` 按单爬虫算，但 8 站/5 线程是两波，45s < 真实 66s |
| P1-2 | 批量串行 + 剧照 sleep | `server.py:259` 零并发；`core.py:703` 每张睡 1.5s → 1000 部多花 4-8h |
| P1-3 | `TaskStore` O(n²) | 每次写入全量扫+排序，4000 条实测 2.25s，且持锁阻塞其他 API |
| P1-4 | `get_pic_size` 解码整图 | `image.py:49-52` 为拿 `.size` 解码+复制 137MB，500 张多耗 50-125s |
| P1-5 | slimeface 全分辨率 + 警告被抑制 | `slimeface_crop.py:26-29` 把 >960 的性能警告 `catch_warnings()` 掉了 |
| P1-6~16 | 配置边界/CLI 漂移/前端 | 见CODE_REVIEW.md 第三部分 |
