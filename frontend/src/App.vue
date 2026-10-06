<template>
  <el-container style="height: 100vh">
    <el-header style="display: flex; align-items: center; font-size: 20px; font-weight: bold; border-bottom: 1px solid #eee">
      JavSP WebUI
      <span v-if="version" style="margin-left: 8px; font-size: 12px; color: #888">v{{ version }}</span>
    </el-header>
    <el-main>
      <el-tabs v-model="active">
        <!-- 扫描目录 -->
        <el-tab-pane label="扫描目录" name="scan">
          <el-input v-model="scanPath" placeholder="输入影片目录绝对路径，如 D:/Movies" style="max-width: 600px">
            <template #append><el-button type="primary" @click="doScan">扫描</el-button></template>
          </el-input>
          <el-alert v-if="scanMsg" :title="scanMsg" type="info" style="margin-top: 10px; max-width: 600px" />

          <div v-if="movies.length" style="margin-top: 14px">
            <el-button :disabled="!selectedGuids.length || batch.running" type="primary" @click="doBatch(false)">
              批量刮削（{{ selectedGuids.length }}）
            </el-button>
            <el-button :disabled="!selectedGuids.length || batch.running" type="success" @click="doBatch(true)">
              批量刮削并整理（{{ selectedGuids.length }}）
            </el-button>
            <el-alert v-if="batch.running || batch.log.length" style="margin-top: 12px" :closable="false">
              <template #title>
                批量任务：第 {{ batch.index }}/{{ batch.total }} 部 · {{ batch.current }}
                <span v-if="batch.done"> → 成功 {{ batch.done.success }} / 失败 {{ batch.done.fail }}</span>
              </template>
              <div>
                <el-tag v-for="c in batch.crawlers" :key="c.crawler" :type="tagType(c.status)" style="margin: 2px">
                  {{ c.crawler }}: {{ c.status }}
                </el-tag>
              </div>
              <el-table v-if="batch.log.length" :data="batch.log" style="margin-top: 10px" max-height="40vh">
                <el-table-column type="expand">
                  <template #default="{ row }">
                    <div style="padding: 4px 10px">
                      <div v-if="!sourceRows(row.sources).length" style="font-size: 12px; color: #999">
                        无站点数据（该部未成功抓取或全部站点无贡献）
                      </div>
                      <el-table v-else :data="sourceRows(row.sources)" size="small" border>
                        <el-table-column prop="site" label="站点" width="90" />
                        <el-table-column prop="dvdid" label="番号" width="110" />
                        <el-table-column prop="title" label="站点标题" min-width="160" />
                        <el-table-column label="贡献字段" width="210">
                          <template #default="{ row: s }">
                            <el-tag v-if="s.has_cover" size="small" type="success" style="margin: 2px">封面</el-tag>
                            <el-tag v-if="s.has_genre" size="small" type="warning" style="margin: 2px">分类</el-tag>
                            <el-tag v-if="s.has_actress" size="small" style="margin: 2px">女优</el-tag>
                            <el-tag v-if="!s.contributed" size="small" type="info" style="margin: 2px">无贡献</el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="无码" width="70">
                          <template #default="{ row: s }">{{ s.uncensored ? '是' : '-' }}</template>
                        </el-table-column>
                      </el-table>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column prop="index" label="#" width="60" />
                <el-table-column prop="avid" label="番号" width="160" />
                <el-table-column label="结果" width="100">
                  <template #default="{ row }">
                    <el-tag :type="row.ok ? 'success' : 'danger'">{{ row.ok ? '成功' : '失败' }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="数据源" width="110">
                  <template #default="{ row }">
                    <el-tag size="small" :type="row.ok ? 'success' : 'info'">{{ sourceSummary(row.sources) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="title" label="标题" min-width="200" />
              </el-table>
            </el-alert>
          </div>

          <el-table v-if="movies.length" :data="movies" style="margin-top: 14px" max-height="55vh"
                    @selection-change="onSelect" ref="movieTable">
            <el-table-column type="selection" width="48" />
            <el-table-column prop="dvdid" label="番号" width="140" />
            <el-table-column prop="cid" label="CID" width="140" />
            <el-table-column prop="data_src" label="类型" width="100" />
            <el-table-column label="文件" min-width="220">
              <template #default="{ row }"><div v-for="f in row.files" :key="f" style="font-size: 12px">{{ f }}</div></template>
            </el-table-column>
            <el-table-column label="已刮削" width="90">
              <template #default="{ row }"><el-tag :type="row.scraped ? 'success' : 'info'">{{ row.scraped ? '是' : '否' }}</el-tag></template>
            </el-table-column>
            <el-table-column label="操作" width="200">
              <template #default="{ row }">
                <el-button size="small" @click="scrapeTask(row)">刮削</el-button>
                <el-button size="small" type="success" :disabled="!row.scraped" @click="organizeTask(row)">整理</el-button>
              </template>
            </el-table-column>
          </el-table>
          <pre v-if="taskLog" style="margin-top: 12px; white-space: pre-wrap; font-size: 12px; color: #666">{{ taskLog }}</pre>
        </el-tab-pane>

        <!-- 单部刮削 -->
        <el-tab-pane label="单部刮削" name="scrape">
          <el-input v-model="avid" placeholder="输入番号，如 IPX-177 或 cid:sqte00300" style="max-width: 500px">
            <template #append><el-button type="primary" @click="doScrape">刮削预览</el-button></template>
          </el-input>
          <div v-if="scrapeProgress.length" style="margin-top: 12px">
            <el-tag v-for="p in scrapeProgress" :key="p.crawler + p.status" style="margin: 2px" :type="tagType(p.status)">
              {{ p.crawler }}: {{ p.status }}
            </el-tag>
          </div>
          <el-card v-if="scrapeInfo" style="margin-top: 14px; max-width: 860px">
            <template #header>元数据预览</template>
            <div style="display: flex; gap: 16px">
              <img v-if="scrapeInfo.cover" :src="scrapeInfo.cover" style="width: 160px; height: auto; border: 1px solid #eee" />
              <el-descriptions :column="1" border>
                <el-descriptions-item label="番号">{{ scrapeInfo.dvdid || scrapeInfo.cid }}</el-descriptions-item>
                <el-descriptions-item label="标题">{{ scrapeInfo.title }}</el-descriptions-item>
                <el-descriptions-item label="女优">
                  <el-tag v-for="a in scrapeInfo.actress || []" :key="a" style="margin: 2px">{{ a }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="分类">
                  <el-tag v-for="g in scrapeInfo.genre || []" :key="g" type="warning" style="margin: 2px">{{ g }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="发行">{{ scrapeInfo.publish_date }}</el-descriptions-item>
                <el-descriptions-item label="时长">{{ scrapeInfo.duration }}</el-descriptions-item>
                <el-descriptions-item label="制作商">{{ scrapeInfo.producer }}</el-descriptions-item>
                <el-descriptions-item label="评分">{{ scrapeInfo.score }}</el-descriptions-item>
              </el-descriptions>
            </div>
          </el-card>
          <el-card v-if="sourceRows(scrapeSources).length" style="margin-top: 14px; max-width: 860px">
            <template #header>
              各站点贡献（{{ sourceSummary(scrapeSources) }}）
            </template>
            <el-table :data="sourceRows(scrapeSources)" size="small" border>
              <el-table-column prop="site" label="站点" width="90" />
              <el-table-column prop="dvdid" label="番号" width="110" />
              <el-table-column prop="title" label="站点标题" min-width="180" />
              <el-table-column label="贡献字段" width="210">
                <template #default="{ row: s }">
                  <el-tag v-if="s.has_cover" size="small" type="success" style="margin: 2px">封面</el-tag>
                  <el-tag v-if="s.has_genre" size="small" type="warning" style="margin: 2px">分类</el-tag>
                  <el-tag v-if="s.has_actress" size="small" style="margin: 2px">女优</el-tag>
                  <el-tag v-if="!s.contributed" size="small" type="info" style="margin: 2px">无贡献</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="无码" width="70">
                <template #default="{ row: s }">{{ s.uncensored ? '是' : '-' }}</template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-tab-pane>

        <!-- 设置（Web 化表单） -->
        <el-tab-pane label="设置" name="settings">
          <el-button @click="loadConfig">加载配置</el-button>
          <el-alert v-if="configMsg" :title="configMsg" type="success" style="margin-top: 10px; max-width: 600px" />
          <el-form v-if="configObj" label-width="170px" style="margin-top: 16px; max-width: 820px">
            <el-divider>基础</el-divider>
            <el-form-item label="扫描目录"><el-input v-model="configObj.scanner.input_directory" placeholder="留空=当前目录" /></el-form-item>
            <el-form-item label="网络代理"><el-input v-model="configObj.network.proxy_server" placeholder="如 http://127.0.0.1:7890，留空禁用" /></el-form-item>
            <el-form-item label="失败重试次数">
              <el-input-number v-model="configObj.network.retry" :min="0" :max="10" />
            </el-form-item>
            <el-form-item label="单次请求超时(秒)">
              <el-input-number v-model="timeoutSec" :min="1" :max="120" />
            </el-form-item>
            <el-form-item label="整理并移动文件"><el-switch v-model="configObj.summarizer.move_files" /></el-form-item>

            <el-divider>站点免代理地址</el-divider>
            <el-alert type="info" :closable="false" style="margin-bottom: 12px"
                      title="部分站点直连不通时需要填写镜像地址。留空表示「不改动」，保存时不会覆盖已填地址。" />
            <el-form-item v-for="s in proxyFreeSites" :key="s" :label="s">
              <el-input v-model="configObj.network.proxy_free[s]" placeholder="如 https://avsox.click" />
            </el-form-item>

            <el-divider>爬虫选择</el-divider>
            <el-form-item label="普通番号源">
              <el-select v-model="configObj.crawler.selection.normal" multiple filterable style="width: 100%">
                <el-option v-for="s in crawlerSites" :key="s" :label="s" :value="s" />
              </el-select>
            </el-form-item>
            <el-form-item label="FC2 源">
              <el-select v-model="configObj.crawler.selection.fc2" multiple filterable style="width: 100%">
                <el-option v-for="s in crawlerSites" :key="s" :label="s" :value="s" />
              </el-select>
            </el-form-item>
            <el-form-item label="CID 源">
              <el-select v-model="configObj.crawler.selection.cid" multiple filterable style="width: 100%">
                <el-option v-for="s in crawlerSites" :key="s" :label="s" :value="s" />
              </el-select>
            </el-form-item>

            <el-divider>命名规则</el-divider>
            <el-form-item label="输出目录模板"><el-input v-model="configObj.summarizer.path.output_folder_pattern" /></el-form-item>
            <el-form-item label="文件名模板"><el-input v-model="configObj.summarizer.path.basename_pattern" /></el-form-item>
            <el-form-item label="NFO 标题模板"><el-input v-model="configObj.summarizer.nfo.title_pattern" /></el-form-item>
            <el-form-item label="NFO 文件名模板"><el-input v-model="configObj.summarizer.nfo.basename_pattern" /></el-form-item>

            <el-divider>翻译</el-divider>
            <el-form-item label="翻译引擎">
              <el-select v-model="configObj.translator.engine.name" style="width: 240px">
                <el-option label="无" value="none" />
                <el-option label="Google" value="google" />
                <el-option label="百度" value="baidu" />
                <el-option label="Bing" value="bing" />
                <el-option label="Claude" value="claude" />
                <el-option label="OpenAI" value="openai" />
              </el-select>
            </el-form-item>
            <el-form-item label="翻译标题"><el-switch v-model="configObj.translator.fields.title" /></el-form-item>
            <el-form-item label="翻译剧情"><el-switch v-model="configObj.translator.fields.plot" /></el-form-item>

            <el-button type="primary" @click="saveConfig">保存并写回 config.yml</el-button>
          </el-form>
          <el-alert v-else title="点击「加载配置」从服务端读取 config.yml" type="info" style="margin-top: 10px" />
        </el-tab-pane>
      </el-tabs>
    </el-main>
  </el-container>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import * as api from './api.js'

// 版本号不再前端硬编码: 单一版本源是后端 pyproject.toml, 启动时从 /api/health 拉取
const version = ref('')
onMounted(async () => {
  try {
    const h = await api.getHealth()
    version.value = h.version || ''
  } catch (_) {
    /* 后端不可达时留空不显示, 不影响页面功能 */
  }
})
const active = ref('scan')
const scanPath = ref('')
const movies = ref([])
const selectedGuids = ref([])
const scanMsg = ref('')
const taskLog = ref('')
const avid = ref('')
const scrapeProgress = ref([])
const scrapeInfo = ref(null)
const scrapeSources = ref(null)
const configObj = ref(null)
const configMsg = ref('')
// 超时在配置里是 ISO 8601 时长（如 PT10S），界面按「秒」编辑，保存时再转回
const timeoutSec = ref(10)
// 需要填写免代理地址的站点（对应 config.yml 的 network.proxy_free）
const proxyFreeSites = ['avsox', 'javbus', 'javdb', 'javlib']

// "PT10S" / "PT1M30S" / 纯数字 → 秒；无法识别时返回 null（保留界面原值不动）
function parseDurationToSec(v) {
  if (v === null || v === undefined || v === '') return null
  if (typeof v === 'number') return v
  const m = /^PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?$/.exec(String(v))
  if (!m) return null
  return parseFloat(m[1] || 0) * 3600 + parseFloat(m[2] || 0) * 60 + parseFloat(m[3] || 0)
}

// 可选爬虫源(对应 javsp.config.CrawlerID 枚举)
const crawlerSites = [
  'airav', 'avsox', 'avwiki', 'dl_getchu', 'fanza', 'fc2', 'fc2fan', 'fc2ppvdb',
  'gyutto', 'jav321', 'javbus', 'javdb', 'javlib', 'javmenu', 'mgstage',
  'njav', 'prestige', 'arzon', 'arzon_iv',
]

const batch = ref({ running: false, index: 0, total: 0, current: '', crawlers: [], log: [], done: null })

function onSelect(rows) {
  selectedGuids.value = rows.map((r) => r.guid)
}

async function doScan() {
  scanMsg.value = '扫描中...'
  try {
    const r = await api.scan(scanPath.value)
    movies.value = r.movies || []
    scanMsg.value = `共扫描到 ${r.count} 部影片`
  } catch (e) {
    ElMessage.error('扫描失败: ' + e.message)
    scanMsg.value = ''
  }
}

async function scrapeTask(row) {
  scrapeProgress.value = []
  scrapeInfo.value = null
  scrapeSources.value = null
  await api.scrapeStream({ guid: row.guid }, (d) => {
    if (d.type === 'progress') scrapeProgress.value.push({ crawler: d.crawler, status: d.status })
    if (d.type === 'result') {
      scrapeInfo.value = d.info
      scrapeSources.value = d.sources || null
      row.scraped = true
      ElMessage.success('刮削完成')
    }
    if (d.type === 'error') ElMessage.error(d.msg)
  })
}

async function organizeTask(row) {
  taskLog.value = '整理中...\n'
  await api.organizeStream(row.guid, (d) => {
    if (d.type === 'stage') taskLog.value += `[${d.stage}]\n`
    if (d.type === 'result') {
      taskLog.value += '完成: ' + JSON.stringify(d.result, null, 2)
      ElMessage.success('整理完成')
    }
    if (d.type === 'error') {
      taskLog.value += '错误: ' + d.msg
      ElMessage.error(d.msg)
    }
  })
}

async function doScrape() {
  scrapeProgress.value = []
  scrapeInfo.value = null
  scrapeSources.value = null
  await api.scrapeStream({ avid: avid.value }, (d) => {
    if (d.type === 'progress') scrapeProgress.value.push({ crawler: d.crawler, status: d.status })
    if (d.type === 'result') {
      scrapeInfo.value = d.info
      scrapeSources.value = d.sources || null
    }
    if (d.type === 'error') ElMessage.error(d.msg)
  })
}

async function doBatch(organize) {
  if (!selectedGuids.value.length) {
    ElMessage.warning('请先勾选影片')
    return
  }
  batch.value = { running: true, index: 0, total: selectedGuids.value.length, current: '', crawlers: [], log: [], done: null }
  try {
    await api.batchStream({ guids: selectedGuids.value, organize }, (d) => {
      if (d.type === 'movie_start') {
        batch.value.index = d.index
        batch.value.total = d.total
        batch.value.current = d.avid
        batch.value.crawlers = []
      } else if (d.type === 'progress') {
        const i = batch.value.crawlers.findIndex((c) => c.crawler === d.crawler)
        if (i >= 0) batch.value.crawlers[i].status = d.status
        else batch.value.crawlers.push({ crawler: d.crawler, status: d.status })
      } else if (d.type === 'movie_done') {
        batch.value.log.push({ index: d.index, avid: d.avid, ok: d.ok, title: d.title, sources: d.sources })
      } else if (d.type === 'all_done') {
        batch.value.running = false
        batch.value.done = { success: d.success, fail: d.fail, total: d.total }
        ElMessage.success(`批量完成：成功 ${d.success} / 失败 ${d.fail}`)
      }
    })
  } catch (e) {
    batch.value.running = false
    ElMessage.error('批量任务中断: ' + e.message)
  }
}

// 后端 sources 结构: { 站点名: { dvdid, title, has_cover, has_genre, has_actress, uncensored, contributed } }
// 转成表格行。contributed 优先取后端权威值；后端缺失时回退到本地判据。
// 注意: dvdid 是「输入番号」而非抓取成果(站点未收录时它依然非空), 绝不能拿它判断贡献。
function sourceRows(sources) {
  if (!sources || typeof sources !== 'object') return []
  return Object.keys(sources).map((site) => {
    const s = sources[site] || {}
    const hasCover = !!s.has_cover
    const hasGenre = !!s.has_genre
    const hasActress = !!s.has_actress
    return {
      site,
      dvdid: s.dvdid || '-',
      title: s.title || '-',
      has_cover: hasCover,
      has_genre: hasGenre,
      has_actress: hasActress,
      uncensored: !!s.uncensored,
      contributed: typeof s.contributed === 'boolean'
        ? s.contributed
        : !!(hasCover || hasGenre || hasActress),
    }
  })
}

// 汇总成「有效站点/总站点」短文本, 用于列表列
function sourceSummary(sources) {
  const rows = sourceRows(sources)
  if (!rows.length) return '无数据'
  const ok = rows.filter((r) => r.contributed).length
  return `${ok}/${rows.length} 站点`
}

function tagType(s) {
  return (
    {
      success: 'success',
      not_found: 'info',
      blocked: 'danger',
      error: 'danger',
      duplicate: 'warning',
      start: '',
    }[s] || ''
  )
}

async function loadConfig() {
  try {
    const c = await api.getConfig()
    // 翻译引擎归一化为 {name}，避免 Web 表单改动时残留其他引擎的必填字段
    if (c.translator && c.translator.engine) {
      c.translator.engine = { name: c.translator.engine.name || 'none' }
    }
    // 网络段可能缺字段（旧配置/精简配置），补齐后再绑定，避免 v-model 到 undefined
    c.network = c.network || {}
    if (c.network.retry === undefined) c.network.retry = 3
    if (c.network.proxy_server === undefined) c.network.proxy_server = null
    c.network.proxy_free = c.network.proxy_free || {}
    proxyFreeSites.forEach((s) => {
      if (c.network.proxy_free[s] === undefined) c.network.proxy_free[s] = ''
    })
    const sec = parseDurationToSec(c.network.timeout)
    if (sec !== null) timeoutSec.value = sec
    configObj.value = c
  } catch (e) {
    ElMessage.error('加载失败: ' + e.message)
  }
}

async function saveConfig() {
  try {
    const payload = JSON.parse(JSON.stringify(configObj.value))
    // 秒 → ISO 8601 时长
    payload.network.timeout = `PT${Math.round(timeoutSec.value)}S`
    // 免代理地址留空视为「不改动」，丢掉该键避免把空串提交给 URL 校验
    proxyFreeSites.forEach((s) => {
      const v = (payload.network.proxy_free || {})[s]
      if (v === undefined || String(v).trim() === '') delete payload.network.proxy_free[s]
    })
    const r = await api.putConfig(payload)
    configMsg.value = r.note || '已保存'
    if (r.reloaded) {
      const n = (r.refreshed || []).length
      ElMessage.success(`已写回 config.yml 并即时生效（刷新 ${n} 个爬虫出口），无需重启`)
    } else if (r.status === 'unchanged') {
      ElMessage.info('没有检测到字段变化，未写入 config.yml')
    } else {
      // 文件已写入但热重载未成功，如实告知，避免用户以为已经生效
      ElMessage.warning('已写入 config.yml，但热重载未成功，请重启服务后生效')
    }
  } catch (e) {
    ElMessage.error('保存失败: ' + e.message)
  }
}
</script>
