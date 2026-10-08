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
import { ref, onMounted } from 'vue'
import * as api from '../api'

const cfg = ref(null)
const runtime = ref(null)
const msg = ref('')
const err = ref('')
const saving = ref(false)
const loading = ref(false)
const open = ref({ scan: true, network: true, crawler: true, output: false, summarizer: false, translator: false })

const GROUPS = [
  { key: 'scan', label: '扫描与识别', hint: '决定哪些文件被纳入刮削' },
  { key: 'network', label: '网络与容错', hint: '代理、超时与重试' },
  { key: 'crawler', label: '刮削源', hint: '启用哪些站点' },
  { key: 'output', label: '输出与命名', hint: '目录结构与文件名' },
  { key: 'summarizer', label: '输出控制', hint: '不想生成的东西可以关掉' },
  { key: 'translator', label: '翻译', hint: '标题与剧情的自动翻译' },
]

const ALL_SITES = [
  'airav', 'avsox', 'avwiki', 'jav321', 'javbus', 'javdb', 'javdbapi',
  'javdatabase', 'javlib', 'javmenu', 'mgstage', 'prestige',
  'dl_getchu', 'fanza', 'fc2', 'fc2fan', 'fc2ppvdb', 'gyutto', 'njav',
  'arzon', 'arzon_iv',
]

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

function siteLabel(name) {
  const hints = {
    javdbapi: 'javdb App API', javdatabase: 'javdatabase.com',
    javmenu: 'javmenu.com', njav: '无码', fanza: 'FANZA',
    fc2fan: '本地镜像', airav: 'javdb 镜像',
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

      <div v-for="g in GROUPS" :key="g.key" class="section group">
        <button class="group-head" @click="open[g.key] = !open[g.key]" :aria-expanded="!!open[g.key]">
          <span class="group-label">{{ g.label }}</span>
          <span class="group-hint">{{ g.hint }}</span>
          <svg class="group-caret" :class="{ open: open[g.key] }" width="16" height="16"
               viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
               stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>

        <div v-show="open[g.key]" class="group-body">
          <!-- 扫描 -->
          <template v-if="g.key === 'scan'">
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
          <template v-else-if="g.key === 'network'">
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
              <input v-model.number="cfg.crawler.sleep_after_scraping" type="number" min="0" class="input"
                     style="max-width: 140px;" />
              <p class="field-hint">每部之间的等待, 调低更快但更易触发反爬</p>
            </div>
          </template>

          <!-- 刮削源 -->
          <template v-else-if="g.key === 'crawler'">
            <div class="field">
              <label class="field-label">普通番号源</label>
              <p class="field-hint" style="margin-bottom: var(--space-2);">
                勾选即参与刮削。已熔断的渠道会自动跳过, 可在「渠道监控」查看原因
              </p>
              <div class="site-grid">
                <label v-for="s in ALL_SITES" :key="s" class="site-chip">
                  <input type="checkbox" :checked="hasSite('normal', s)"
                         @change="toggleSite('normal', s, $event.target.checked)" />
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

          <!-- 输出与命名 -->
          <template v-else-if="g.key === 'output'">
            <div class="field">
              <label class="field-label">输出目录模板</label>
              <input v-model="cfg.summarizer.path.output_folder_pattern" class="input" />
              <p class="field-hint">
                占位符: {num} {title} {actress} {studio} {publish_date} 等
              </p>
            </div>
            <div class="row-2">
              <div class="field">
                <label class="field-label">文件名模板</label>
                <input v-model="cfg.summarizer.path.basename_pattern" class="input" />
              </div>
              <div class="field">
                <label class="field-label">NFO 文件名</label>
                <input v-model="cfg.summarizer.nfo.basename_pattern" class="input" />
              </div>
            </div>
            <div class="row-2">
              <div class="field">
                <label class="field-label">NFO 标题格式</label>
                <input v-model="cfg.summarizer.nfo.title_pattern" class="input" />
              </div>
              <div class="field">
                <label class="field-label">目录名最大长度</label>
                <input v-model.number="cfg.summarizer.path.length_maximum" type="number"
                       min="20" max="250" class="input" />
              </div>
            </div>
            <label class="switch-row">
              <input v-model="cfg.summarizer.move_files" type="checkbox" />
              <span>整理时移动文件(关闭则只写元数据, 不动原文件)</span>
            </label>
          </template>

          <!-- 输出控制 -->
          <template v-else-if="g.key === 'summarizer'">
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

          <!-- 翻译 -->
          <template v-else-if="g.key === 'translator'">
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
                配置��件是 git 跟踪的, 建议改用环境变量
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
    </template>
  </div>
</template>

<style scoped>
.group { padding: 0; overflow: hidden; }

.group-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-4) var(--space-5);
  font-family: inherit;
  text-align: left;
  background: none;
  border: 0;
  cursor: pointer;
}

.group-head:hover { background: var(--color-bg-subtle); }
.group-head:focus-visible { outline: none; box-shadow: var(--focus-ring); }

.group-label { font-size: var(--font-md); font-weight: 600; }
.group-hint { flex: 1; font-size: var(--font-sm); color: var(--color-text-secondary); }

.group-caret {
  color: var(--color-text-tertiary);
  transition: transform var(--transition-fast);
}
.group-caret.open { transform: rotate(180deg); }

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
</style>
