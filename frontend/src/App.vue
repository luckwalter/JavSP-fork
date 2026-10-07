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
          <div style="display: flex; gap: 8px; max-width: 600px">
            <el-button @click="openBrowser">浏览目录…</el-button>
            <el-input v-model="scanPath" placeholder="输入影片目录绝对路径，如 D:/Movies；也可点左侧按钮逐级选择" style="flex: 1">
              <template #append><el-button type="primary" @click="doScan">扫描</el-button></template>
            </el-input>
          </div>
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
            <el-form-item label="扫描目录">
              <div style="display: flex; gap: 8px; width: 100%">
                <el-button @click="openBrowser('config.input_directory')">浏览目录…</el-button>
                <el-input v-model="configObj.scanner.input_directory" placeholder="留空=当前目录（CLI 有效；Web 端请用左侧按钮选择）" style="flex: 1" />
              </div>
            </el-form-item>
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

            <el-divider>输出控制</el-divider>
            <el-alert type="info" :closable="false" style="margin-bottom: 12px"
                      title="如果元数据最终交给 Jellyfin / Emby 自行管理，这里的封面与 NFO 都是重复劳动，可以全部关掉——省下下载高清封面（单张 8-10 MiB）和裁剪的开销，整理会明显变快。注意：关闭后本项目不会再生成对应文件，媒体库里没有封面是预期结果。" />
            <el-form-item label="封面 poster">
              <el-switch v-model="configObj.summarizer.cover.enabled" />
              <span style="color: #909399; margin-left: 10px">竖版封面，由下载的原图裁剪而来</span>
            </el-form-item>
            <el-form-item label="封面 fanart">
              <el-switch v-model="configObj.summarizer.fanart.enabled" />
              <span style="color: #909399; margin-left: 10px">横版原图；关掉后仍会下载用于裁剪 poster，生成完即删除</span>
            </el-form-item>
            <el-form-item label="剧照">
              <el-switch v-model="configObj.summarizer.extra_fanarts.enabled" />
              <span style="color: #909399; margin-left: 10px">逐张下载，最耗时的一项</span>
            </el-form-item>
            <el-form-item label="NFO 文件">
              <el-switch v-model="configObj.summarizer.nfo.enabled" />
              <span style="color: #909399; margin-left: 10px">由 Jellyfin 自行刮削元数据时可关闭</span>
            </el-form-item>

            <el-divider>封面裁剪</el-divider>
            <el-alert type="info" :closable="false" style="margin-bottom: 12px"
                      title="默认关闭。开启后，无码 / FC2 / 番号匹配下方规则的封面会改用本地人脸检测来定位裁剪区，能把主体偏在一侧的封面救回来。代价：每张封面多一次检测耗时，且依赖 slimeface。" />
            <el-form-item label="人脸检测裁剪">
              <el-switch v-model="cropEnabled" />
            </el-form-item>
            <el-form-item label="依赖状态">
              <el-tag v-if="cropRuntime" size="small"
                      :type="cropAvailable ? 'success' : 'danger'">
                {{ cropAvailable ? 'slimeface 已安装' : 'slimeface 未安装' }}
              </el-tag>
              <span v-else style="color: #909399">未查询</span>
              <el-button size="small" style="margin-left: 8px" @click="loadRuntime">查询运行时</el-button>
            </el-form-item>
            <el-form-item label="启用条件(番号正则)">
              <el-select v-model="configObj.summarizer.cover.crop.on_id_pattern"
                         multiple filterable allow-create default-first-option
                         style="width: 100%" placeholder="回车新增，如 ^SIRO" />
            </el-form-item>

            <el-button type="primary" @click="saveConfig">保存并写回 config.yml</el-button>
          </el-form>
          <el-alert v-else title="点击「加载配置」从服务端读取 config.yml" type="info" style="margin-top: 10px" />
        </el-tab-pane>
      </el-tabs>

      <!-- 目录选择器: 扫描页与设置页共用。browseTarget 指明选完后写入哪个字段。
           必须放在 el-tabs 之外: el-tab-pane 默认懒渲染, 嵌在某个 tab 内时, 从另一个 tab
           打开对话框会出现定位/层级异常(表现为「点了浏览目录但没反应」)。 -->
          <el-dialog v-model="browserVisible" :title="browseTarget === 'scanPath' ? '选择扫描目录' : '选择扫描目录（配置项）'" width="640px" append-to-body>
            <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 10px">
              <el-button size="small" :disabled="!browseParent" @click="loadBrowse(browseParent)">上一层</el-button>
              <el-input size="small" v-model="browseInput" placeholder="也可直接输入路径回车" style="flex: 1"
                        @keyup.enter="loadBrowse(browseInput)" />
              <el-button size="small" type="primary" :loading="browseLoading" @click="loadBrowse(browseInput)">转到</el-button>
            </div>
            <div style="font-size: 12px; color: #888; margin-bottom: 8px">
              当前：<span style="color: #409EFF">{{ browseCurrent || '-' }}</span>
              <span style="margin-left: 10px">（仅列目录，不列文件）</span>
              <span v-if="browseRoot" style="margin-left: 10px">根目录：{{ browseRoot }}</span>
            </div>
            <el-scrollbar max-height="46vh">
              <div v-if="!browseDirs.length && !browseLoading" style="color: #999; padding: 12px 0">
                该目录下没有子目录
              </div>
              <div v-for="d in browseDirs" :key="d.path"
                   style="padding: 7px 10px; cursor: pointer; border-radius: 4px; display: flex; align-items: center"
                   :style="browseHover === d.path ? 'background:#f5f7fa' : ''"
                   @click="loadBrowse(d.path)"
                   @click.stop="chooseDir(d.path)"
                   @mouseenter="browseHover = d.path" @mouseleave="browseHover = ''">
                <span style="margin-right: 8px">📁</span>
                <span style="flex: 1">{{ d.name }}</span>
                <el-button size="small" text type="primary" @click.stop="chooseDir(d.path)">选择</el-button>
              </div>
            </el-scrollbar>
            <template #footer>
              <el-button @click="browserVisible = false">取消</el-button>
              <el-button type="primary" :disabled="!browseCurrent" @click="chooseDir(browseCurrent)">
                选择当前目录
              </el-button>
            </template>
          </el-dialog>
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
// 封面裁剪：配置项是 `crop.engine: {name} | null`，界面只暴露一个开关
const cropEnabled = ref(false)
const cropRuntime = ref(null)
const cropAvailable = ref(false)

// 纯函数便于从源码提取后单测（见 verify_cropper.py）
// 从裁剪配置读出开关初值：engine 为 null / 缺失即视为关闭
function readCropEnabled(crop) {
  return !!(crop && crop.engine && crop.engine.name)
}

// 按开关构造写回用的 engine 载荷
function buildCropEngine(enabled) {
  return enabled ? { name: 'slimeface' } : null
}

// 从 /api/config/runtime 的返回判断 slimeface 是否真的可用
function isCropAvailable(runtime) {
  const c = runtime && runtime.cover_crop
  return !!(c && c.available)
}

// 整理完成后关于封面裁剪的提示文案；无需提示时返回 null
function cropResultNote(crop) {
  if (!crop || !crop.engine) return null
  if (crop.applied) return '封面已按人脸检测位置裁剪'
  return `已开启 ${crop.engine} 裁剪，但本次未生效：${crop.reason || '未知原因'}，已回退为默认裁剪`
}

// 输出开关：把可能缺失的布尔项归一化为 true/false（旧配置没有这些键）
function readOutputToggles(summarizer) {
  const s = summarizer || {}
  const pick = (section, key, value) => (value === undefined ? true : !!value)
  return {
    poster: pick('cover', 'enabled', (s.cover || {}).enabled),
    fanart: pick('fanart', 'enabled', (s.fanart || {}).enabled),
    extrafanart: pick('extra_fanarts', 'enabled', (s.extra_fanarts || {}).enabled),
    nfo: pick('nfo', 'enabled', (s.nfo || {}).enabled),
  }
}

// 整理结果里「本次按设置跳过了哪些输出」的提示；无需提示时返回 null
function skippedOutputNote(result) {
  const skipped = (result && result.skipped) || []
  if (!skipped.length) return null
  const names = { cover: '封面下载', poster: '封面 poster', fanart: '封面 fanart', extrafanart: '剧照', nfo: 'NFO' }
  const human = skipped.map((k) => names[k] || k)
  return `按「输出控制」设置跳过了：${human.join('、')}`
}

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

// ---------------- 目录选择器 ----------------
const browserVisible = ref(false)
const browseLoading = ref(false)
const browseCurrent = ref('')
const browseParent = ref(null)
const browseDirs = ref([])
const browseInput = ref('')
const browseHover = ref('')
const browseRoot = ref('')
// 选完后写入哪个绑定值。默认 'scanPath'; 设置页传 'config.input_directory'。
const browseTarget = ref('scanPath')

// 扫描目录预填, 优先级: 用户在「设置」里保存的 scanner.input_directory > 允许浏览的根。
// 设置项本身就是「默认扫描位置」的语义, 保存后应当自动回填到扫描框, 否则用户每次打开
// 页面都得再点一次「浏览目录…」重选 —— 配置存了却不起作用。
// 放在状态声明之后注册, 避免 onMounted 回调引用尚未初始化的绑定。
onMounted(async () => {
  let root = ''
  let browseCurrent = ''
  try {
    const b = await api.browse('')
    if (b && b.current) {
      root = b.root || ''
      browseCurrent = b.current
    }
  } catch (_) {
    /* 列举失败就留空, 用户仍可手输路径或用浏览按钮 */
  }
  browseRoot.value = root

  // 再取配置里的默认扫描目录(失败不阻塞页面)
  let saved = ''
  try {
    const c = await api.getConfig()
    saved = (c && c.scanner && c.scanner.input_directory) || ''
  } catch (_) {
    /* 配置读不到就退回浏览根 */
  }
  if (saved) {
    // 配置里可能是 CLI 用的相对路径或宿主机路径, 与 Web 端容器内路径不一定一致;
    // 后端 /api/scan 会用 isdir 校验, 填了但扫不了时用户能看到明确报错, 不静默改写。
    scanPath.value = saved
  } else if (browseCurrent) {
    scanPath.value = browseCurrent
  }
})

async function loadBrowse(path) {
  // 路径为空时让后端从「允许浏览的根」开始(后端已处理, 不要在前端硬编码 '/')
  const target = (path === undefined || path === null || path === '') ? '' : path
  browseLoading.value = true
  try {
    const r = await api.browse(target)
    browseCurrent.value = r.current
    browseParent.value = r.parent
    browseDirs.value = r.dirs || []
    browseInput.value = r.current
    browseRoot.value = r.root || ''
  } catch (e) {
    ElMessage.error('读取目录失败: ' + e.message)
  } finally {
    browseLoading.value = false
  }
}

// 打开选择器。target 指定选完后写入哪个绑定值('scanPath' / 'config.input_directory')
function openBrowser(target = 'scanPath') {
  browseTarget.value = target
  browserVisible.value = true
  // 已有输入值就以它为起点，否则留空让后端从允许浏览的根开始。
  // 注意: 残留旧值在新部署环境里可能越界, 后端会自动回退到根, 不会失败。
  const cur = target === 'scanPath'
    ? scanPath.value
    : ((configObj.value && configObj.value.scanner && configObj.value.scanner.input_directory) || '')
  loadBrowse(cur || '')
}

// 选定目录：写回对应输入框并关闭对话框（不立即扫描，避免误触直接扫根目录）
function chooseDir(p) {
  if (browseTarget.value === 'config.input_directory') {
    // scanner 段在旧配置里可能整段缺失，直接赋值会抛 TypeError（表现为点击无反应）
    if (!configObj.value.scanner) configObj.value.scanner = {}
    configObj.value.scanner.input_directory = p
  } else {
    scanPath.value = p
  }
  browserVisible.value = false
}

async function doScan() {
  scanMsg.value = '扫描中...'
  try {
    const r = await api.scan(scanPath.value)
    movies.value = r.movies || []
    scanMsg.value = `共扫描到 ${r.count} 部影片`
    // 重新扫描 = 换了一批影片, 之前那批的批量结果已无意义(其中的 guid 也不再存在于
    // 当前列表)。原先不清空 -> el-alert 的 v-if 是 `batch.running || batch.log.length`,
    // 只要 log 非空就永久显示, 用户换目录后旧任务窗体还挂在页面上。
    batch.value = { running: false, index: 0, total: 0, current: '', crawlers: [], log: [], done: null }
    selectedGuids.value = []
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
      // 封面裁剪是否真的用上了 AI：没用上时要点明，否则用户会以为配置已生效
      const note = cropResultNote(d.result && d.result.crop)
      // 被「输出控制」跳过的内容也要说一声，否则用户不知道为什么媒体库里没有封面
      const skippedNote = skippedOutputNote(d.result)
      if (note) {
        taskLog.value += '\n' + note
        ElMessage.warning(note)
      } else {
        ElMessage.success('整理完成')
      }
      if (skippedNote) {
        taskLog.value += '\n' + skippedNote
        ElMessage.info(skippedNote)
      }
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
        batch.value.done = { success: d.success, fail: d.fail, total: d.total }
        ElMessage.success(`批量完成：成功 ${d.success} / 失败 ${d.fail}`)
      }
    })
  } catch (e) {
    ElMessage.error('批量任务中断: ' + e.message)
  } finally {
    // 无条件复位: 若 SSE 流「正常结束却没收到 all_done」(后端生成器被中断/代理截断),
    // 原先两个复位分支都不执行 -> batch.running 永久为 true -> 按钮永久禁用, 只能刷新页面
    batch.value.running = false
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
    // 翻译引擎归一化为 {name}，避免 Web 表单改动时残留其他引擎的必填字段。
    // 注意: 必须保留 api_key/app_id/url/model —— 后端 GET 已把它们替换为掩码串,
    // 若这里丢弃, 保存时后端就收不到密钥字段, model_validate 会因缺键直接失败
    // (表现为「配了翻译密钥后点保存就报错」)。后端 PUT 会把掩码串还原为真实值。
    if (c.translator && c.translator.engine) {
      const e = c.translator.engine
      c.translator.engine = { name: e.name || 'none' }
      if (e.app_id !== undefined) c.translator.engine.app_id = e.app_id
      if (e.api_key !== undefined) c.translator.engine.api_key = e.api_key
      if (e.url !== undefined) c.translator.engine.url = e.url
      if (e.model !== undefined) c.translator.engine.model = e.model
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
    // 封面裁剪段：补齐结构（旧配置可能整段缺失），并把引擎归一化成开关
    if (c.summarizer) {
      const cover = (c.summarizer.cover = c.summarizer.cover || {})
      const crop = (cover.crop = cover.crop || {})
      crop.on_id_pattern = crop.on_id_pattern || []
      cropEnabled.value = readCropEnabled(crop)
      crop.engine = crop.engine || { name: 'slimeface' }
      // 输出开关：旧配置没有这些键，缺失一律按「启用」处理
      if (cover.enabled === undefined) cover.enabled = true
      const fanart = (c.summarizer.fanart = c.summarizer.fanart || {})
      if (fanart.enabled === undefined) fanart.enabled = true
      const extraFanarts = (c.summarizer.extra_fanarts = c.summarizer.extra_fanarts || {})
      if (extraFanarts.enabled === undefined) extraFanarts.enabled = true
      const nfo = (c.summarizer.nfo = c.summarizer.nfo || {})
      if (nfo.enabled === undefined) nfo.enabled = true
    }
    configObj.value = c
    // 顺带查一次运行时，让「依赖是否装了」无需额外操作就能看到（失败不影响配置编辑）
    loadRuntime(true)
  } catch (e) {
    ElMessage.error('加载失败: ' + e.message)
  }
}

async function loadRuntime(silent) {
  try {
    const r = await api.getConfigRuntime()
    cropRuntime.value = r.runtime || null
    cropAvailable.value = isCropAvailable(r.runtime)
    if (!silent) ElMessage.success('已读取运行时配置')
  } catch (e) {
    if (!silent) ElMessage.error('读取运行时配置失败: ' + e.message)
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
    // 封面裁剪：开关 → engine 载荷（关闭即 null，写回为 `engine: null`）
    if (payload.summarizer && payload.summarizer.cover && payload.summarizer.cover.crop) {
      payload.summarizer.cover.crop.engine = buildCropEngine(cropEnabled.value)
    }
    const r = await api.putConfig(payload)
    configMsg.value = r.note || '已保存'
    // 设置里的扫描目录是「默认扫描位置」——保存后同步到扫描页输入框,
    // 免得用户保存完还要切 tab 重新选一次(且切过去看到的仍是旧值)。
    if (payload.scanner && payload.scanner.input_directory) {
      scanPath.value = payload.scanner.input_directory
    }
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
