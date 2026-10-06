<template>
  <el-container style="height: 100vh">
    <el-header style="display: flex; align-items: center; font-size: 20px; font-weight: bold; border-bottom: 1px solid #eee">
      JavSP WebUI
      <span style="margin-left: 8px; font-size: 12px; color: #888">v{{ version }}</span>
    </el-header>
    <el-main>
      <el-tabs v-model="active">
        <!-- 扫描目录 -->
        <el-tab-pane label="扫描目录" name="scan">
          <el-input v-model="scanPath" placeholder="输入影片目录绝对路径，如 D:/Movies" style="max-width: 600px">
            <template #append><el-button type="primary" @click="doScan">扫描</el-button></template>
          </el-input>
          <el-alert v-if="scanMsg" :title="scanMsg" type="info" style="margin-top: 10px; max-width: 600px" />
          <el-table v-if="movies.length" :data="movies" style="margin-top: 14px" max-height="62vh">
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
        </el-tab-pane>

        <!-- 设置 -->
        <el-tab-pane label="设置" name="settings">
          <el-button @click="loadConfig">加载配置</el-button>
          <el-input type="textarea" :rows="22" v-model="configText" style="margin-top: 10px; font-family: monospace" />
          <el-button type="primary" style="margin-top: 10px" @click="saveConfig">保存并写回 config.yml</el-button>
          <el-alert v-if="configMsg" :title="configMsg" type="success" style="margin-top: 10px; max-width: 600px" />
        </el-tab-pane>
      </el-tabs>
    </el-main>
  </el-container>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import * as api from './api.js'

const version = ref('0.1.1')
const active = ref('scan')
const scanPath = ref('')
const movies = ref([])
const scanMsg = ref('')
const taskLog = ref('')
const avid = ref('')
const scrapeProgress = ref([])
const scrapeInfo = ref(null)
const configText = ref('')
const configMsg = ref('')

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
  await api.scrapeStream({ guid: row.guid }, (d) => {
    if (d.type === 'progress') scrapeProgress.value.push({ crawler: d.crawler, status: d.status })
    if (d.type === 'result') {
      scrapeInfo.value = d.info
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
  await api.scrapeStream({ avid: avid.value }, (d) => {
    if (d.type === 'progress') scrapeProgress.value.push({ crawler: d.crawler, status: d.status })
    if (d.type === 'result') scrapeInfo.value = d.info
    if (d.type === 'error') ElMessage.error(d.msg)
  })
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
    configText.value = JSON.stringify(c, null, 2)
  } catch (e) {
    ElMessage.error('加载失败: ' + e.message)
  }
}

async function saveConfig() {
  try {
    const cfg = JSON.parse(configText.value)
    const r = await api.putConfig(cfg)
    configMsg.value = r.note || '已保存'
    ElMessage.success('已写回 config.yml')
  } catch (e) {
    ElMessage.error('保存失败: ' + e.message)
  }
}
</script>
