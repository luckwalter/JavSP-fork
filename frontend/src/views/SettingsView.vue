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
  { key: 'crawler', label: '刮削源', hint: '启用哪些站点' },
  { key: 'output', label: '输出与命名', hint: '目录结构与文件名' },
  { key: 'summarizer', label: '输出控制', hint: '不想生成的东西可以关掉' },
  { key: 'appearance', label: '外观', hint: '主题与界面亮度' },
  { key: 'translator', label: '翻译', hint: '标题与剧情的自动翻译' },
]

const currentGroup = computed(() => GROUPS.find((g) => g.key === activeGroup.value) || GROUPS[0])

// 按选源类型(对应 core.py 的 movie.data_src)声明的「推荐源」清单。
// 每个 crawler 实际支持的番号类型据此收敛, 设置页每个分区只显示该类支持的源:
//   - normal: 普通厂商番号(如 ABC-123)
//   - fc2   : FC2-xxxx 专属(fc2/fc2ppvdb) + 跨类型能搜 FC2 的(avsox/javdb/javmenu)
//   - cid   : CID 专属(fanza, 必须携带 cid 才能检索)
// fanza 不含在 normal, 因为它需要 cid 而非普通番号(与上游把 fanza 放 normal 的默认不同,
// 这里严格按能力收敛)。已勾选但不在推荐里的源仍会补显示(见 sitesForKind), 不会丢配置。
// 注: javfree 是第 22 个番号源, 普通番号库, 归 normal(此前 ALL_SITES 遗漏, 此处补齐)。
// 注: arzon / arzon_iv 已于 0.2.11 移除 —— 实测 www.arzon.jp 连接被重置(ConnectionReset)、
//     直连超时, 两个源指向同一死域名, 已彻底删除(爬虫实现 + CrawlerID 枚举 + 配置 + 本列表)。
// 注: fc2fan 已于 0.2.12 移除 —— 主站 fc2club.top 实测只返回 493 字节 'Loading...' 壳页,
//     站点已关停(代码原本就直接抛"站点不可用")。其"本地镜像"能力一并移除。
const SOURCE_KINDS = {
  normal: ['airav', 'avsox', 'avwiki', 'jav321', 'javbus', 'javdb', 'javdbapi',
           'javdatabase', 'javlib', 'javmenu', 'mgstage', 'prestige', 'dl_getchu',
           'njav', 'gyutto', 'javfree'],
  fc2:    ['fc2', 'fc2ppvdb', 'avsox', 'javdb', 'javmenu'],
  cid:    ['fanza'],
}
// 实际参与刮削的选源类型(core.py 只按 movie.data_src 取 normal/fc2/cid)。
// getchu/gyutto 虽在 selection 中但不被 core 使用, 故页面不渲染以免误导。
const CRAWLER_KINDS = ['normal', 'fc2', 'cid']
const KIND_LABEL = { normal: '普通番号源', fc2: 'FC2 番号源', cid: 'CID 番号源' }
const KIND_HINT = { fc2: 'FC2-xxxx', cid: 'CID 番号' }

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

/** sites 列表在 config 里可能是字符串数组或 [{...}] 形式, 这里只取 value */
function siteList(kind) {
  const arr = cfg.value?.crawler?.selection?.[kind]
  if (!Array.isArray(arr)) return []
  return arr.map((x) => (typeof x === 'string' ? x : x?.value)).filter(Boolean)
}

function hasSite(kind, name) {
  return siteList(kind).includes(name)
}

function toggleSite(kind, name, on) {
  const cur = cfg.value.crawler.selection[kind] || []
  const arr = cur.map((x) => (typeof x === 'string' ? x : x))
  const i = arr.findIndex((x) => (typeof x === 'string' ? x : x?.value) === name)
  if (on && i < 0) arr.push(name)
  if (!on && i >= 0) arr.splice(i, 1)
  cfg.value.crawler.selection[kind] = arr
}

// 某分区应渲染的源 = 推荐源 ∪ 已勾选源(去重)。
// 这样既只显示该类支持的源, 又不丢用户在 config 里已勾选但不在推荐清单中的源(仍可取消)。
function sitesForKind(kind) {
  const seen = new Set()
  const out = []
  for (const s of [...(SOURCE_KINDS[kind] || []), ...siteList(kind)]) {
    if (s && !seen.has(s)) { seen.add(s); out.push(s) }
  }
  return out
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

function siteLabel(name) {
  const hints = {
    javdbapi: 'javdb App API', javdatabase: 'javdatabase.com',
    javmenu: 'javmenu.com', njav: '无码', fanza: 'FANZA',
    airav: 'javdb 镜像',
    avsox: '前端渲染·不可用', avwiki: '非番号库·不可用',
  }
  return hints[name] || ''
}

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
            <div class="row-2">
              <div class="field">
                <label class="field-label">最小文件体积</label>
                <input v-model="cfg.scanner.minimum_size" class="input" placeholder="232MiB" />
                <p class="field-hint">带单位的字符串, 如 232MiB / 1GiB</p>
              </div>
              <div class="field">
                <label class="field-label">番号识别忽略规则</label>
                <input v-model="cfg.scanner.ignored_id_pattern" class="input" />
              </div>
            </div>
            <label class="switch-row">
              <input v-model="cfg.scanner.skip_nfo_dir" type="checkbox" />
              <span>跳过已整理(nfo)目录</span>
            </label>
            <label class="switch-row">
              <input v-model="cfg.summarizer.path.hard_link" type="checkbox" />
              <span>硬链接整理(同一卷内省空间, 跨文件系统会失败)</span>
            </label>
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
                <label class="field-label">单次请求超时</label>
                <input v-model="cfg.network.timeout" class="input" placeholder="PT10S" />
                <p class="field-hint">ISO8601 时长, 如 PT10S = 10 秒</p>
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

          <!-- 刮削源: 遍历 normal/fc2/cid 三类, 每类独立勾选区(根治只暴露 normal 的坑) -->
          <template v-else-if="currentGroup.key === 'crawler'">
            <div class="field" v-for="kind in CRAWLER_KINDS" :key="kind">
              <label class="field-label">{{ KIND_LABEL[kind] }}</label>
              <p class="field-hint" style="margin-bottom: var(--space-2);">
                <template v-if="kind === 'normal'">勾选即参与刮削。已熔断的渠道会自动跳过, 可在「渠道监控」查看原因</template>
                <template v-else>仅对 {{ KIND_HINT[kind] }} 类番号生效(其余类型番号不受影响)</template>
              </p>
              <div class="site-grid">
                <label v-for="s in sitesForKind(kind)" :key="kind + '-' + s" class="site-chip">
                  <input type="checkbox" :checked="hasSite(kind, s)"
                         @change="toggleSite(kind, s, $event.target.checked)" />
                  <span class="site-name">{{ s }}</span>
                  <span v-if="siteLabel(s)" class="site-hint">{{ siteLabel(s) }}</span>
                </label>
              </div>
            </div>
            <div class="row-2">
              <div class="field">
                <label class="field-label">并发上限</label>
                <input v-model.number="cfg.crawler.max_concurrency" type="number" min="1" max="16"
                       class="input" />
              </div>
              <div class="field">
                <label class="field-label">最少命中字段数</label>
                <input v-model.number="cfg.crawler.required_keys" type="number" min="0" class="input" />
                <p class="field-hint">低于此数量视为抓取失败</p>
              </div>
            </div>
            <label class="switch-row">
              <input v-model="cfg.crawler.use_javdb_cover" type="checkbox" />
              <span>优先使用 javdb 封面(该源有水印)</span>
            </label>
          </template>

          <!-- 输出与命名: 可视化构建器(点击/拖动拼装, 无需手打 {xxx}) -->
          <template v-else-if="currentGroup.key === 'output'">
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
            <div class="row-2">
              <div class="field">
                <NamingRuleBuilder v-model="cfg.summarizer.nfo.title_pattern"
                                   label="NFO 标题格式" single />
              </div>
              <div class="field">
                <label class="field-label">目录名最大长度</label>
                <input v-model.number="cfg.summarizer.path.length_maximum" type="number"
                       min="20" max="250" class="input" />
                <p class="field-hint">超长时自动截断; 按字节计长时中文每字算 3</p>
              </div>
            </div>
            <label class="switch-row">
              <input v-model="cfg.summarizer.path.length_by_byte" type="checkbox" />
              <span>按字节计算目录名长度(中文每字 3 字节, 兼容旧版行为)</span>
            </label>
            <label class="switch-row">
              <input v-model="cfg.summarizer.move_files" type="checkbox" />
              <span>整理时移动文件(关闭则只写元数据, 不动原文件)</span>
            </label>
          </template>

          <!-- 输出控制 -->
          <template v-else-if="currentGroup.key === 'summarizer'">
            <p class="field-hint" style="margin-bottom: var(--space-3);">
              元数据交给 Jellyfin 自行刮削时, 这些重复劳动可以关掉
            </p>
            <label class="switch-row">
              <input v-model="cfg.summarizer.cover.enabled" type="checkbox" />
              <span>封面 poster(竖版裁剪)</span>
            </label>
            <label class="switch-row">
              <input v-model="cfg.summarizer.cover.highres" type="checkbox" />
              <span>使用高清原图作为封面源</span>
            </label>
            <label class="switch-row">
              <input v-model="cfg.summarizer.fanart.enabled" type="checkbox" />
              <span>封面 fanart(横版原图)</span>
            </label>
            <label class="switch-row">
              <input v-model="cfg.summarizer.extra_fanarts.enabled" type="checkbox" />
              <span>剧照(最耗时, 关掉提升明显)</span>
            </label>
            <label class="switch-row">
              <input v-model="cfg.summarizer.nfo.enabled" type="checkbox" />
              <span>NFO 文件</span>
            </label>
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
                     :value="cfg.translator.engine && cfg.translator.api_key !== MASK ? cfg.translator.api_key : ''"
                     placeholder="留空= 不使用该引擎"
                     @input="cfg.translator.api_key = $event.target.value" />
              <p class="field-hint">
                配置文件是 git 跟踪的, 建议改用环境变量
                <code>JAVSP_TRANSLATOR.ENGINE.API_KEY</code>(点号, 双下划线会被静默忽略)
              </p>
            </div>
            <label class="switch-row">
              <input v-model="cfg.translator.fields.title" type="checkbox" />
              <span>翻译标题</span>
            </label>
            <label class="switch-row">
              <input v-model="cfg.translator.fields.plot" type="checkbox" />
              <span>翻译剧情</span>
            </label>
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

.switch-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
  font-size: var(--font-base);
  cursor: pointer;
}

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
.site-hint { color: var(--color-text-tertiary); font-size: var(--font-xs); }

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
</style>
