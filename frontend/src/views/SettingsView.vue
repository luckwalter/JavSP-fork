<script setup>
/** 系统设置: 按语义分组折叠
 *  ⚠️ 字段名严格对齐 config.yml 的真实结构(v0.2.1 实测核对):
 *     summarizer.path.output_folder_pattern  (不是 output_dir)
 *     summarizer.path.basename_pattern       (不是 output_file)
 *     summarizer.cover.enabled / fanart.enabled / extra_fanarts.enabled / nfo.enabled
 *     scanner.minimum_size 是 "232MiB" 这类**字符串**(ByteSize), 不是数字
 *     network.timeout 是 "PT10S" 这类 ISO8601 时长, 不是秒数
 *     translator.fields.{title,plot} 而非 translate_title/translate_plot
 *  写错字段名会让 PUT /api/config 静默不生效或写坏配置 —— 故此处逐一核对过。
 */
import { ref, computed, onMounted } from 'vue'
import * as api from '../api'
import NamingRuleBuilder from '../components/NamingRuleBuilder.vue'
import { useTheme, THEME_MODES } from '../composables/useTheme'

const emit = defineEmits(['navigate'])

const cfg = ref(null)
const runtime = ref(null)
const msg = ref('')
const err = ref('')
const saving = ref(false)
const loading = ref(false)

// 三栏布局: 左栏分类导航(当前组), 右栏只渲染当前组表单 —— 比「全部折叠堆叠」
// 更像设置面板, 长表单也不再需要反复滚动找分组。
const activeGroup = ref('output')

// 外观: 主题四模式(浅色/深色/跟随系统/定时), 与 StatusBar 快捷切换共享同一单例。
const { mode: themeMode, schedule: themeSchedule, resolvedDark, setMode: setThemeMode, setSchedule: setThemeSchedule } = useTheme()

const themeModeLabel = { light: '浅色', dark: '深色', auto: '跟随系统', schedule: '定时切换' }
const themeModeDesc = {
  light: '始终使用浅色界面',
  dark: '始终使用深色界面',
  auto: '跟随操作系统深浅色偏好',
  schedule: '在下方时段内自动使用深色, 其余时间浅色',
}
const themeEffective = computed(() => (resolvedDark.value ? '深色' : '浅色'))

const GROUPS = [
  { key: 'scan', label: '扫描与识别', hint: '决定哪些文件被纳入刮削' },
  { key: 'network', label: '网络与容错', hint: '代理、超时与重试' },
  { key: 'crawler', label: '刮削源', hint: '高级刮取参数(选源在刮削源页)' },
  { key: 'output', label: '输出与命名', hint: '目录结构与文件名' },
  { key: 'summarizer', label: '输出控制', hint: '不想生成的东西可以关掉' },
  { key: 'appearance', label: '外观', hint: '主题与界面亮度' },
  { key: 'translator', label: '翻译', hint: '标题与剧情的自动翻译' },
]

const currentGroup = computed(() => GROUPS.find((g) => g.key === activeGroup.value) || GROUPS[0])

// 选源(启用哪些源/哪些番号类别)已于 2026-10-17 迁移到「刮削源」页(ChannelsView):
// 选源 chip + 实时健康状态合一, 避免两处可改同一字段而漂移。
// 源能力集/分组/预设的单一数据源在 utils/sourceMeta.js, 由刮削源页消费。
// 本页只保留刮取行为的高级参数(并发/命中字段/javdb 封面策略等)。

const MASK = '***MASKED***'

async function load() {
  loading.value = true
  err.value = ''
  try {
    cfg.value = await api.getConfig()
    runtime.value = await api.getConfigRuntime().catch(() => null)
  } catch (e) {
    err.value = e.message
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  msg.value = ''
  err.value = ''
  try {
    const r = await api.putConfig(cfg.value)
    msg.value = `已保存并立即生效（改动 ${r.changed ?? 0} 个字段）`
  } catch (e) {
    err.value = e.message
  } finally {
    saving.value = false
  }
}

// Duration 字段后端用 ISO8601 字符串(如 PT1S=1秒); 前端以“秒”展示与编辑, 保存时转回 ISO。
// 直接 v-model.number 会因 PT1S 非数字被浏览器清空, 用户改填数字保存时后端 Duration 校验失败。
function isoToSeconds(v) {
  if (v == null) return 0
  if (typeof v === 'number') return v
  const m = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/.exec(String(v))
  if (!m) return 0
  return (+(m[1] || 0)) * 86400 + (+(m[2] || 0)) * 3600 + (+(m[3] || 0)) * 60 + (+(m[4] || 0))
}

function secondsToIso(n) {
  const s = Math.max(0, Math.floor(Number(n) || 0))
  return 'PT' + s + 'S'
}

// ignored_id_pattern 后端是 List[str](config.py:20), 多行文本每行一条正则。
// 早期直接 v-model 到 <input>: 数组被 Vue 转成逗号串显示, 用户编辑后存回字符串,
// PUT 时 confz 校验 List[str] 直接 422 —— 保存必失败。computed get/set 双向同步。
const ignoredIdText = computed({
  get: () => (cfg.value?.scanner?.ignored_id_pattern || []).join('\n'),
  set: (v) => {
    if (cfg.value?.scanner) {
      cfg.value.scanner.ignored_id_pattern = String(v).split('\n').map((s) => s.trim()).filter(Boolean)
    }
  },
})

// 「刮削间隔等待」开关: sleep_after_scraping 为 0 = 不等待; 打开时回落 PT1S。
// 秒数微调仍在「网络与容错」组, 这里只做总闸(与设计稿的开关语义一致)。
const scrapeSleepOn = computed({
  get: () => isoToSeconds(cfg.value?.crawler?.sleep_after_scraping) > 0,
  set: (v) => {
    if (cfg.value?.crawler) cfg.value.crawler.sleep_after_scraping = v ? 'PT1S' : 'PT0S'
  },
})

// required_keys 后端是 list[MovieInfoField](config.py:90), 语义是「至少要到哪些字段
// 才算抓取成功」。早期页面按「数量」做了数字输入 —— 数组被转成逗号串显示, 用户改完
// 保存写回数字, confz 校验 list[MovieInfoField] 直接 422(且「字段清单」被误做成「个数」)。
const REQUIRED_KEY_OPTS = [
  'cover', 'title', 'actress', 'genre', 'plot', 'duration',
  'producer', 'publisher', 'director', 'publish_date', 'serial', 'uncensored', 'score',
]
function requiredKeyOpts() {
  // 推荐字段 ∪ 已配置字段: 旧配置/未来版本出现新枚举值时仍显示, 不会丢
  const seen = new Set()
  const out = []
  for (const k of [...REQUIRED_KEY_OPTS, ...(cfg.value?.crawler?.required_keys || [])]) {
    if (k && !seen.has(k)) { seen.add(k); out.push(k) }
  }
  return out
}
function hasRequiredKey(k) {
  return (cfg.value?.crawler?.required_keys || []).includes(k)
}
function toggleRequiredKey(k, on) {
  const arr = [...(cfg.value?.crawler?.required_keys || [])]
  const i = arr.indexOf(k)
  if (on && i < 0) arr.push(k)
  if (!on && i >= 0) arr.splice(i, 1)
  cfg.value.crawler.required_keys = arr
}

// use_javdb_cover 是三值枚举 UseJavDBCover(config.py:116), 早期绑成 checkbox:
// GET 的 "fallback" 真值字符串显示为勾选, 一动保存就写 true/false -> 422 存不进去。
const JAVDB_COVER_OPTS = [
  { value: 'fallback', label: '优先其他站', desc: '能从别的站点拿到封面就不用 javdb 的, 避开水印' },
  { value: 'yes', label: '总是使用', desc: '任何情况下都优先使用 javdb 封面' },
  { value: 'no', label: '从不用', desc: '不使用 javdb 封面(其他站都没有时将没有封面)' },
]

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1 class="page-title">系统设置</h1>
      <p class="page-desc">改动写入配置文件并<b>立即生效</b>, 无需重启</p>
    </div>

    <div v-if="loading" class="section"><div class="empty">加载配置中…</div></div>
    <div v-else-if="err && !cfg" class="section">
      <div class="notice notice--danger">{{ err }}</div>
      <button class="btn" style="margin-top: var(--space-3);" @click="load">重试</button>
    </div>

    <template v-else-if="cfg">
      <div class="settings-layout">
        <!-- 左栏: 分类导航(页内级, 158px) -->
        <nav class="settings-nav" aria-label="设置分类">
          <button v-for="g in GROUPS" :key="g.key" type="button" class="nav-item"
                  :class="{ 'nav-item--on': g.key === activeGroup }"
                  :aria-current="g.key === activeGroup ? 'page' : undefined"
                  @click="activeGroup = g.key">
            <span class="nav-label">{{ g.label }}</span>
            <span class="nav-hint">{{ g.hint }}</span>
          </button>
        </nav>

        <!-- 右栏: 当前组表单 -->
        <div class="settings-main">
          <div class="toolbar">
            <button class="btn btn--primary" :disabled="saving" @click="save">
              <span v-if="saving" class="spinner" aria-hidden="true"></span>
              {{ saving ? '保存中' : '保存并生效' }}
            </button>
            <button class="btn" @click="load">放弃修改</button>
            <span v-if="runtime?.crop_engine" class="pill pill--muted">
              封面引擎: {{ runtime.crop_engine }}
            </span>
          </div>

          <div v-if="msg" class="notice notice--info" style="margin-bottom: var(--space-4);">{{ msg }}</div>
          <div v-if="err" class="notice notice--danger" style="margin-bottom: var(--space-4);">{{ err }}</div>

          <div class="section group">
            <div class="group-head group-head--static">
              <span class="group-label">{{ currentGroup.label }}</span>
              <span class="group-hint">{{ currentGroup.hint }}</span>
            </div>
            <div class="group-body">
          <!-- 扫描 -->
          <template v-if="currentGroup.key === 'scan'">
            <div class="field">
              <label class="field-label">最小文件体积</label>
              <input v-model="cfg.scanner.minimum_size" class="input" style="max-width: 200px;" placeholder="232MiB" />
              <p class="field-hint">带单位的字符串, 如 232MiB / 1GiB; 小于此体积的文件跳过</p>
            </div>
            <div class="field">
              <label class="field-label">番号识别忽略规则</label>
              <textarea v-model="ignoredIdText" class="input input--mono" rows="4"
                        spellcheck="false" placeholder="每行一条正则, 如 ^HD-"></textarea>
              <p class="field-hint">
                命中的文件不识别番号(如采样片 <code>^HD-</code>); 每行一条, 大多数情况保持默认即可
              </p>
            </div>
            <div class="switch-list">
              <label class="switch-row">
                <input v-model="cfg.scanner.skip_nfo_dir" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">跳过已整理(nfo)目录</span>
                  <span class="switch-desc">目录内已有 nfo 时不再重复刮削——长期运行不重复劳动的关键</span>
                </span>
              </label>
              <label class="switch-row">
                <input v-model="cfg.summarizer.path.hard_link" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">硬链接整理</span>
                  <span class="switch-desc">同一卷内省空间, 跨文件系统会失败</span>
                </span>
              </label>
              <label class="switch-row">
                <input v-model="scrapeSleepOn" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">刮削间隔等待</span>
                  <span class="switch-desc">每部之间的等待秒数, 调低更快但更易触发反爬; 秒数在「网络与容错」细调</span>
                </span>
              </label>
            </div>
          </template>

          <!-- 网络 -->
          <template v-else-if="currentGroup.key === 'network'">
            <div class="field">
              <label class="field-label">出口代理</label>
              <input v-model="cfg.network.proxy_server" class="input" style="max-width: 360px;"
                     placeholder="留空= 直连" />
              <p class="field-hint">
                NAS 出口需代理才能访问刮削站点时在此填写, 如 http://&lt;代理主机&gt;:3128
              </p>
            </div>
            <div class="row-2">
              <div class="field">
                <label class="field-label">失败重试次数</label>
                <input v-model.number="cfg.network.retry" type="number" min="1" max="10" class="input" />
              </div>
              <div class="field">
                <label class="field-label">单次请求超时(秒)</label>
                <input :value="isoToSeconds(cfg.network.timeout)" type="number" min="1" max="120" class="input"
                       @input="cfg.network.timeout = secondsToIso($event.target.value)" />
                <p class="field-hint">单位秒, 如 10 = PT10S; 站点响应慢可适当调大(保存时自动转回 ISO8601 时长)</p>
              </div>
            </div>
              <div class="field">
                <label class="field-label">刮削间隔(秒)</label>
                <input :value="isoToSeconds(cfg.crawler.sleep_after_scraping)" type="number" min="0" class="input"
                       style="max-width: 140px;"
                       @input="cfg.crawler.sleep_after_scraping = secondsToIso($event.target.value)" />
                <p class="field-hint">每部之间的等待(单位秒), 调低更快但更易触发反爬; 0 表示不等待</p>
              </div>
          </template>

          <!-- 选源已迁移到「刮削源」页: 选源 chip + 实时状态合一, 避免两处可改同一字段而漂移 -->
          <template v-else-if="currentGroup.key === 'crawler'">
            <div class="notice notice--info migrate-card">
              <div style="flex: 1; min-width: 0;">
                <p style="font-weight: 500; margin-bottom: 4px;">选源已迁移到「刮削源」页</p>
                <p style="font-size: var(--font-sm); color: var(--color-text-secondary);">
                  在那里按类别(普通 / FC2 / CID)点亮或停用每个源, 同时查看实时健康状态与熔断原因。
                  本页只保留刮取行为的高级参数。
                </p>
              </div>
              <button class="btn btn--sm btn--primary" @click="emit('navigate', 'channels')">前往刮削源</button>
            </div>
            <div class="row-2">
              <div class="field">
                <label class="field-label">并发上限</label>
                <input v-model.number="cfg.crawler.max_concurrency" type="number" min="1" max="16"
                       class="input" />
                <p class="field-hint">各爬虫线程池大小, 调大可提速但更易触发站点风控</p>
              </div>
              <div class="field">
                <label class="field-label">javdb 封面策略</label>
                <div class="radio-row" role="radiogroup" aria-label="javdb 封面策略">
                  <label v-for="opt in JAVDB_COVER_OPTS" :key="opt.value" class="radio-chip"
                         :class="{ 'radio-chip--on': cfg.crawler.use_javdb_cover === opt.value }">
                    <input type="radio" :value="opt.value" v-model="cfg.crawler.use_javdb_cover" />
                    <span>{{ opt.label }}</span>
                  </label>
                </div>
                <p class="field-hint">{{ (JAVDB_COVER_OPTS.find(o => o.value === cfg.crawler.use_javdb_cover) || JAVDB_COVER_OPTS[0]).desc }}</p>
              </div>
            </div>
            <div class="field">
              <label class="field-label">至少命中字段</label>
              <p class="field-hint" style="margin-bottom: var(--space-2);">
                一部影片至少要刮到这些字段才算成功, 缺项太多的渠道结果会被丢弃
              </p>
              <div class="site-grid">
                <label v-for="k in requiredKeyOpts()" :key="k" class="site-chip">
                  <input type="checkbox" :checked="hasRequiredKey(k)"
                         @change="toggleRequiredKey(k, $event.target.checked)" />
                  <span class="site-name">{{ k }}</span>
                </label>
              </div>
            </div>
            <div class="switch-list">
              <label class="switch-row">
                <input v-model="cfg.crawler.hardworking" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">努力爬取</span>
                  <span class="switch-desc">爬取更准确更丰富的信息, 会略微增加部分站点的耗时</span>
                </span>
              </label>
              <label class="switch-row">
                <input v-model="cfg.crawler.respect_site_avid" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">以站点番号为准</span>
                  <span class="switch-desc">使用网页番号作为最终番号, 启用时会对番号大小写等更正</span>
                </span>
              </label>
              <label class="switch-row">
                <input v-model="cfg.crawler.normalize_actress_name" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">统一女优艺名</span>
                  <span class="switch-desc">尝试将女优的多个艺名统一成一个</span>
                </span>
              </label>
            </div>
          </template>

          <!-- 输出与命名: 可视化构建器(点击/拖动拼装, 无需手打 {xxx}) -->
          <template v-else-if="currentGroup.key === 'output'">
            <!-- 核心开关置顶: 整理时移动文件(突出) + 按字节(依赖移动, 关闭时禁用) -->
            <p class="field-hint" style="margin-bottom: var(--space-3);">
              整理的核心开关: 决定要不要动你的原文件。关闭后仅生成元数据(NFO/封面), 文件与目录保持原样; 下方文件命名/目录相关配置不生效, 元数据文件名将使用视频原名(原名.nfo / 原名-poster.jpg / 原名-fanart.jpg)以便 Jellyfin/Emby/Kodi 直接识别
            </p>
            <div class="switch-list">
              <label class="switch-row switch-row--primary">
                <input v-model="cfg.summarizer.move_files" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">整理时移动文件</span>
                  <span class="switch-desc">关闭则只写元数据, 不动原文件</span>
                </span>
              </label>
              <label class="switch-row">
                <input v-model="cfg.summarizer.path.length_by_byte" type="checkbox"
                       :disabled="!cfg.summarizer.move_files" />
                <span class="switch-text" :class="{ 'is-muted': !cfg.summarizer.move_files }">
                  <span class="switch-title">按字节计算目录名长度</span>
                  <span class="switch-desc">中文每字算 3 字节, 兼容旧版行为; 关闭则按字符数计</span>
                </span>
              </label>
            </div>

            <!-- 移动开启: 完整命名配置可用 -->
            <template v-if="cfg.summarizer.move_files">
              <div class="field">
                <NamingRuleBuilder
                  v-model="cfg.summarizer.path.output_folder_pattern"
                  label="输出目录模板"
                  :length-max="Number(cfg.summarizer.path.length_maximum) || 0"
                  :by-byte="!!cfg.summarizer.path.length_by_byte" />
                <p class="field-hint" style="margin-top: var(--space-2);">
                  层与层之间以 <code>/</code> 分隔形成多级目录; 多分片影片的番号会自动追加 <code>-CD1/-CD2</code> 后缀
                </p>
              </div>
              <div class="row-2">
                <div class="field">
                  <NamingRuleBuilder v-model="cfg.summarizer.path.basename_pattern"
                                     label="文件名模板" single />
                </div>
                <div class="field">
                  <NamingRuleBuilder v-model="cfg.summarizer.nfo.basename_pattern"
                                     label="NFO 文件名" single />
                </div>
              </div>
              <div class="field">
                <label class="field-label">目录名最大长度</label>
                <input v-model.number="cfg.summarizer.path.length_maximum" type="number"
                       min="20" max="250" class="input" />
                <p class="field-hint">超长时自动截断; 按字节计长时中文每字算 3</p>
              </div>
            </template>

            <!-- 移动关闭: 压缩为一条窄提示, 不再渲染禁用控件, 避免大块灰区挡住下方设置 -->
            <div v-else class="disabled-note">
              <span class="disabled-note__icon">⊘</span>
              <span>
                整理时移动文件已关闭, 以下文件命名配置不生效:
                <b>输出目录模板</b> / <b>文件名模板</b> / <b>NFO 文件名</b>(固定为视频原名 <code>原名.nfo</code>) / <b>目录名最大长度</b>;
                元数据文件名统一使用视频原名(原名.nfo / 原名-poster.jpg / 原名-fanart.jpg), 便于 Jellyfin/Emby/Kodi 直接识别
              </span>
            </div>

            <!-- NFO 标题格式始终可用(仅影响 nfo 内部标题文本, 与文件改名无关) -->
            <div class="field">
              <NamingRuleBuilder v-model="cfg.summarizer.nfo.title_pattern"
                                 label="NFO 标题格式" single />
              <p class="field-hint">关闭『整理时移动文件』后仍生效(只影响 nfo 内部标题文本)</p>
            </div>
          </template>

          <!-- 输出控制 -->
          <template v-else-if="currentGroup.key === 'summarizer'">
            <p class="field-hint" style="margin-bottom: var(--space-3);">
              元数据交给 Jellyfin 自行刮削时, 这些重复劳动可以关掉, 整理速度明显提升
            </p>
            <div class="switch-list">
              <label class="switch-row">
                <input v-model="cfg.summarizer.cover.enabled" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">封面 poster</span>
                  <span class="switch-desc">竖版裁剪, Jellyfin 主界面显示用; 与 fanart 同源, 两者都关则完全不下载封面</span>
                </span>
              </label>
              <label class="switch-row">
                <input v-model="cfg.summarizer.cover.highres" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">使用高清原图作为封面源</span>
                  <span class="switch-desc">高清封面约 8-10 MiB, 网络条件不佳时会拖慢整理</span>
                </span>
              </label>
              <label class="switch-row">
                <input v-model="cfg.summarizer.fanart.enabled" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">封面 fanart</span>
                  <span class="switch-desc">横版原图, Jellyfin 详情页背景; 只想要 poster 时可关</span>
                </span>
              </label>
              <label class="switch-row">
                <input v-model="cfg.summarizer.extra_fanarts.enabled" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">
                    剧照 extrafanart
                    <span class="cost cost--slow">最耗时 · 关掉提升明显</span>
                  </span>
                  <span class="switch-desc">每部数张至数十张, NAS 弱机建议关闭</span>
                </span>
              </label>
              <label class="switch-row">
                <input v-model="cfg.summarizer.nfo.enabled" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">NFO 文件</span>
                  <span class="switch-desc">影片元数据, 媒体库刮削的依据; 关掉后 Jellyfin 自行识别</span>
                </span>
              </label>
            </div>
          </template>

          <!-- 外观: 主题四模式(与 StatusBar 快捷切换共享同一状态) -->
          <template v-else-if="currentGroup.key === 'appearance'">
            <div class="theme-modes" role="radiogroup" aria-label="主题模式">
              <button v-for="m in THEME_MODES" :key="m" type="button" class="theme-card"
                      :class="{ 'theme-card--on': themeMode === m }"
                      role="radio" :aria-checked="themeMode === m"
                      @click="setThemeMode(m)">
                <span class="theme-card-name">{{ themeModeLabel[m] }}</span>
                <span class="theme-card-desc">{{ themeModeDesc[m] }}</span>
              </button>
            </div>
            <div v-if="themeMode === 'schedule'" class="schedule-row">
              <div class="field">
                <label class="field-label">深色开始</label>
                <input type="time" class="input" :value="themeSchedule.start"
                       @change="setThemeSchedule({ start: $event.target.value, end: themeSchedule.end })" />
              </div>
              <div class="field">
                <label class="field-label">深色结束</label>
                <input type="time" class="input" :value="themeSchedule.end"
                       @change="setThemeSchedule({ start: themeSchedule.start, end: $event.target.value })" />
              </div>
              <p class="field-hint schedule-tip">
                支持跨午夜(如 19:00 → 次日 07:00); 每分钟检查一次, 切换即时生效
              </p>
            </div>
            <p class="field-hint">
              当前生效: <b>{{ themeEffective }}</b> 主题 · 状态栏主题按钮可快速循环切换四种模式
            </p>
          </template>

          <!-- 翻译 -->
          <template v-else-if="currentGroup.key === 'translator'">
            <div class="field">
              <label class="field-label">翻译引擎</label>
              <select v-model="cfg.translator.engine" class="input" style="max-width: 240px;">
                <option :value="null">不翻译</option>
                <option value="google">Google</option>
                <option value="baidu">百度</option>
                <option value="bing">Bing</option>
                <option value="claude">Claude</option>
                <option value="openai">OpenAI</option>
              </select>
            </div>
            <div class="field">
              <label class="field-label">API 密钥</label>
              <input type="password" class="input" style="max-width: 360px;"
                     :disabled="!cfg.translator.engine"
                     :value="cfg.translator.engine && cfg.translator.api_key !== MASK ? cfg.translator.api_key : ''"
                     :placeholder="cfg.translator.engine ? '留空= 不使用该引擎' : '请先选择翻译引擎'"
                     @input="cfg.translator.api_key = $event.target.value" />
              <p class="field-hint">
                <template v-if="!cfg.translator.engine">选好翻译引擎后才需要填写密钥</template>
                <template v-else>
                  配置文件是 git 跟踪的, 建议改用环境变量
                  <code>JAVSP_TRANSLATOR.ENGINE.API_KEY</code>(点号, 双下划线会被静默忽略)
                </template>
              </p>
            </div>
            <div class="switch-list">
              <label class="switch-row">
                <input v-model="cfg.translator.fields.title" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">翻译标题</span>
                  <span class="switch-desc">NFO 与文件名中的日文标题转为中文</span>
                </span>
              </label>
              <label class="switch-row">
                <input v-model="cfg.translator.fields.plot" type="checkbox" />
                <span class="switch-text">
                  <span class="switch-title">翻译剧情</span>
                  <span class="switch-desc">简介文本翻译, 耗时随引擎而定</span>
                </span>
              </label>
            </div>
          </template>
        </div>
      </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* ---------- 三栏: 左栏分类导航 + 右栏当前组表单 ---------- */
.settings-layout {
  display: grid;
  grid-template-columns: 158px minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.settings-nav {
  position: sticky;
  top: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: var(--space-2) var(--space-3);
  font-family: inherit;
  text-align: left;
  background: transparent;
  border: 0;
  border-left: 3px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--transition-fast);
}
.nav-item:hover { background: var(--color-bg-subtle); }
.nav-item:focus-visible { outline: none; box-shadow: var(--focus-ring); }
.nav-item--on { background: var(--color-primary-soft); border-left-color: var(--color-primary); }
.nav-label { font-size: var(--font-base); font-weight: 500; color: var(--color-text-primary); }
.nav-item--on .nav-label { color: var(--color-primary); }
.nav-hint { font-size: var(--font-xs); color: var(--color-text-tertiary); }

.group { padding: 0; overflow: hidden; }

/* 三栏下组头是静态标题(不再点击折叠) */
.group-head { display: flex; align-items: center; gap: var(--space-3); width: 100%; padding: var(--space-4) var(--space-5); }
.group-head--static { cursor: default; }
.group-label { font-size: var(--font-md); font-weight: 600; }
.group-hint { flex: 1; font-size: var(--font-sm); color: var(--color-text-secondary); }

.group-body { padding: 0 var(--space-5) var(--space-5); }

/* ---------- 开关行: 滑动开关 + 标题 + 「关了会怎样」说明 ---------- */
.switch-list {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  padding: var(--space-1) var(--space-3);
}

.switch-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  font-size: var(--font-base);
  cursor: pointer;
}
.switch-list .switch-row + .switch-row { border-top: 1px solid var(--color-border-light); }

/* 滑动开关: appearance 清零后自绘, 与设计稿一致(不用浏览器原生勾选框) */
.switch-row input[type="checkbox"] {
  appearance: none;
  -webkit-appearance: none;
  position: relative;
  flex-shrink: 0;
  width: 36px;
  height: 20px;
  margin: 2px 0 0;
  border-radius: 999px;
  background: var(--color-bg-subtle);
  border: 1.5px solid var(--color-border-medium);
  cursor: pointer;
  transition: background var(--transition-fast), border-color var(--transition-fast);
}
.switch-row input[type="checkbox"]::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--color-text-tertiary);
  transition: transform var(--transition-fast), background var(--transition-fast);
}
.switch-row input[type="checkbox"]:checked {
  background: var(--color-primary);
  border-color: var(--color-primary);
}
.switch-row input[type="checkbox"]:checked::after {
  transform: translateX(16px);
  background: #fff;
}
.switch-row input[type="checkbox"]:focus-visible { box-shadow: var(--focus-ring); }

.switch-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.switch-title { font-size: var(--font-base); color: var(--color-text-primary); }
.switch-desc { font-size: var(--font-sm); line-height: 1.6; color: var(--color-text-tertiary); }

/* 耗时徽标: 只在配置文档里才知道的信息, 搬到开关旁(设计稿行为分析) */
.cost {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  margin-left: 6px;
  font-size: var(--font-xs);
  font-weight: 500;
  border-radius: var(--radius-sm);
  vertical-align: 1px;
}
.cost--slow { color: var(--color-warning); background: var(--color-warning-soft); }

/* 三选一(枚举值): 用 chip 化 radio, 比原生 select 更直观 */
.radio-row { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.radio-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-3);
  font-size: var(--font-sm);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.radio-chip:hover { background: var(--color-bg-subtle); }
.radio-chip--on { border-color: var(--color-primary); background: var(--color-primary-soft); color: var(--color-primary); }
.radio-chip input { width: auto; margin: 0; accent-color: var(--color-primary); }

.input--mono { font-family: var(--font-mono); font-size: var(--font-sm); resize: vertical; }

/* 选源迁移引导卡: 左文右按钮 */
.migrate-card { display: flex; align-items: center; gap: var(--space-4); }
.migrate-card p { margin: 0; }

.site-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: var(--space-1);
}

.site-chip {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-2);
  font-size: var(--font-sm);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.site-chip:hover { background: var(--color-bg-subtle); }
.site-chip input { width: auto; flex-shrink: 0; }
.site-name { font-family: var(--font-mono); }

.row-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: var(--space-4); }

code {
  padding: 1px 4px;
  font-family: var(--font-mono);
  font-size: var(--font-xs);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
}

/* ---------- 外观: 主题模式卡片 ---------- */
.theme-modes {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.theme-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3);
  font-family: inherit;
  text-align: left;
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-medium);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: border-color var(--transition-fast);
}
.theme-card:hover { border-color: var(--color-text-tertiary); }
.theme-card:focus-visible { outline: none; box-shadow: var(--focus-ring); }
.theme-card--on { border-color: var(--color-primary); background: var(--color-primary-soft); }
.theme-card-name { font-size: var(--font-base); font-weight: 500; color: var(--color-text-primary); }
.theme-card--on .theme-card-name { color: var(--color-primary); }
.theme-card-desc { font-size: var(--font-xs); color: var(--color-text-secondary); }

/* ---------- 关闭移动文件时的窄提示条(替代大块灰色禁用区) ---------- */
.disabled-note {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  margin: var(--space-2) 0;
  border: 1px dashed var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-bg-subtle);
  color: var(--color-text-tertiary);
  font-size: var(--font-sm);
  line-height: 1.5;
}
.disabled-note__icon {
  flex: none;
  font-size: 14px;
  line-height: 1.5;
  color: var(--color-text-tertiary);
}
.disabled-note code {
  font-family: monospace;
  font-size: .92em;
  padding: 0 4px;
  border-radius: 3px;
  background: var(--color-bg-hover);
  color: var(--color-text-secondary);
}
.disabled-note b { color: var(--color-text-secondary); font-weight: 600; }

.schedule-row {
  display: grid;
  grid-template-columns: 132px 132px minmax(0, 1fr);
  gap: var(--space-4);
  align-items: end;
  margin-bottom: var(--space-4);
}
.schedule-tip { padding-bottom: var(--space-2); }

/* ---------- 窄屏: 左栏变横向滚动条 ---------- */
@media (max-width: 860px) {
  .settings-layout { grid-template-columns: 1fr; }
  .settings-nav {
    position: static;
    flex-direction: row;
    overflow-x: auto;
    padding-bottom: var(--space-2);
  }
  .nav-item { flex-shrink: 0; border-left: 0; border-bottom: 3px solid transparent; }
  .nav-item--on { border-bottom-color: var(--color-primary); }
  .theme-modes { grid-template-columns: 1fr; }
  .schedule-row { grid-template-columns: 1fr 1fr; }
  .schedule-tip { grid-column: 1 / -1; }
}
/* 关闭『整理时移动文件』时, 依赖移动的字段整体置灰 + 禁交互 */
.is-disabled { opacity: 0.5; pointer-events: none; }
.is-disabled .field-label,
.is-disabled .field-hint { color: var(--color-text-muted); }
.is-muted { opacity: 0.5; }
.switch-row--primary {
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  background: var(--color-primary-soft);
}
</style>
