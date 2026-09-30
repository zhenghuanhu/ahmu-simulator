<template>
  <div class="pr-container">
    <!-- 顶部工具栏: 打印机状态 -->
    <div class="ohms-panel pr-toolbar">
      <span class="ohms-title" style="font-size: var(--pr-fs-title);">PRINT MANAGEMENT / 打印管理</span>
      <span class="ohms-dim" style="margin-left: 8px; font-size: var(--pr-fs-aux);">ARINC744A</span>
      <span class="status-dot" :class="printerStatus === 'ready' ? 'green' : printerStatus === 'busy' ? 'yellow' : 'red'"></span>
      <span :class="printerStatusClass">{{ printerStatusText }}</span>
      <span style="flex: 1;"></span>
      <span class="ohms-dim" style="font-size: var(--pr-fs-aux);">电子盘打印目录：{{ printDir }}</span>
    </div>

    <!-- 提交打印 -->
    <div class="ohms-panel pr-submit-panel">
      <div class="pr-panel-header">
        <span class="ohms-title" style="font-size: var(--pr-fs-panel);">SUBMIT PRINT / 提交打印</span>
      </div>
      <div class="pr-submit-row">
        <span class="ohms-label">模式</span>
        <el-select v-model="printType" size="small" style="width: 160px;">
          <el-option label="文件传输模式" value="file_transfer" />
          <el-option label="块传输模式" value="block_transfer" />
        </el-select>
        <span class="ohms-label">打印内容</span>
        <el-input v-model="printContent" type="textarea" :rows="2" placeholder="打印内容" size="small" style="flex: 1;" />
        <el-button type="primary" size="small" @click="submitPrint">提交打印</el-button>
      </div>
    </div>

    <!-- 协议交互流程 -->
    <div class="ohms-panel pr-frames-panel">
      <div class="pr-panel-header">
        <span class="ohms-title" style="font-size: var(--pr-fs-panel);">PROTOCOL EXCHANGE / 协议交互流程</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: var(--pr-fs-aux);">{{ frames.length }} 帧</span>
        <span style="flex: 1;"></span>
        <span class="ohms-dim" style="font-size: var(--pr-fs-aux);">→ AHMU发送 · ← 打印机响应</span>
      </div>
      <div class="pr-frames" ref="framesRef">
        <div v-for="(f, i) in frames" :key="i" class="pr-frame" :class="frameClass(f)">
          <span class="pr-dir" :class="f.direction === 'tx' ? 'pr-tx' : 'pr-rx'">
            {{ f.direction === 'tx' ? '→' : '←' }}
          </span>
          <span class="pr-type">{{ f.frame_type }}</span>
          <span class="pr-text">{{ frameText(f) }}</span>
        </div>
        <div v-if="frames.length === 0" class="pr-empty">提交打印后展示 ARINC744A 协议交互帧序列（URQ / RTS / CTS / STX / CTL / DATA / ACK / NAK / EOT）...</div>
      </div>
    </div>

    <!-- 实时进度 -->
    <div class="ohms-panel pr-progress-panel" v-if="activePrint">
      <div class="pr-panel-header">
        <span class="ohms-title" style="font-size: var(--pr-fs-panel);">PRINT PROGRESS / 打印进度</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: var(--pr-fs-aux);">{{ activePrint.job_id?.slice(0, 8) }}...</span>
        <span style="flex: 1;"></span>
        <span :class="printStateClass">{{ activePrint.state }}</span>
      </div>
      <div style="padding: 10px 14px;">
        <el-progress v-if="printProgress > 0" :percentage="printProgress" :stroke-width="12" />
        <div v-else class="ohms-dim" style="font-size: var(--pr-fs-aux);">状态流转: RTS → CTS → 数据传输 → 完成</div>
      </div>
    </div>

    <!-- 任务列表 -->
    <div class="ohms-panel pr-jobs-panel">
      <div class="pr-panel-header">
        <span class="ohms-title" style="font-size: var(--pr-fs-panel);">PRINT JOBS / 打印任务记录</span>
      </div>
      <el-table :data="jobs" size="small" max-height="220" v-loading="loading">
        <el-table-column label="模式" width="110">
          <template #default="{ row }">{{ row.job_type === 'file_transfer' ? '文件传输' : '块传输' }}</template>
        </el-table-column>
        <el-table-column prop="content" label="内容" min-width="180" show-overflow-tooltip />
        <el-table-column label="状态" width="110">
          <template #default="{ row }"><span :class="jobStatusClass(row.status)">{{ jobStatusText(row.status) }}</span></template>
        </el-table-column>
        <el-table-column prop="file_path" label="输出文件 (ps)" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.file_path" class="ohms-cyan" style="font-size: 12px;">{{ row.file_path }}</span>
            <span v-else class="ohms-dim">--</span>
          </template>
        </el-table-column>
        <el-table-column label="提交时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { on, off } = useWebSocket()

const jobs = ref([])
const loading = ref(false)
const printerStatus = ref('ready')
const printType = ref('file_transfer')
const printContent = ref('')
const activePrint = ref(null)
const printProgress = ref(0)
const printDir = ref('A:\\printlog')
const frames = ref([])
const framesRef = ref(null)

const printerStatusText = ref('就绪 (SDI=000, Code=000)')
const printerStatusClass = ref('ohms-green')
const printStateClass = ref('ohms-cyan')

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const submitPrint = () => {
  if (!printContent.value.trim()) {
    ElMessage.warning('请输入打印内容')
    return
  }
  frames.value = []   // 清空上一任务的帧
  activePrint.value = { job_id: null, state: 'queued' }
  printProgress.value = 0
  fetch(api(`/api/v1/print/submit?content=${encodeURIComponent(printContent.value)}&job_type=${printType.value}`), {
    method: 'POST',
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success('打印任务已提交')
      } else {
        ElMessage.error(data.message || '提交失败')
      }
    })
}

const fetchJobs = () => {
  loading.value = true
  fetch(api('/api/v1/print/jobs?page=1&size=20'))
    .then(r => r.json())
    .then(data => {
      jobs.value = data.items || []
      if (data.print_dir) printDir.value = data.print_dir
    })
    .finally(() => { loading.value = false })
}

const frameText = (f) => {
  const d = f.detail || {}
  if (d.text) return d.text
  if (f.frame_type === 'DATA' && d.index) return `DATA ${d.index}/${d.total} (${d.length} 字)`
  if (f.frame_type === 'DATA' && d.word) return `数据字: ${d.word}`
  if (f.frame_type === 'RTS') return `RECORD 数量: ${d.record_count}`
  return ''
}

const frameClass = (f) => {
  const t = f.frame_type
  if (t === 'ACK' || t === 'CTS') return 'pr-frame-ok'
  if (t === 'NAK') return 'pr-frame-nak'
  if (t === 'STX' || t === 'EOT' || t === 'ETX') return 'pr-frame-marker'
  return ''
}

const jobStatusClass = (s) => ({
  'ohms-green': s === 'completed',
  'ohms-red': s === 'failed',
  'ohms-cyan': s === 'sending' || s === 'queued',
})

const jobStatusText = (s) => ({
  completed: 'Successful', failed: 'Failed', sending: '发送中', queued: '排队',
}[s] || s)

const formatTime = (t) => t ? new Date(t).toLocaleString() : '--'

const scrollFrames = async () => {
  await nextTick()
  if (framesRef.value) {
    framesRef.value.scrollTop = framesRef.value.scrollHeight
  }
}

onMounted(() => {
  fetchJobs()

  on('printer_status', (data) => {
    printerStatus.value = data.status
    printerStatusText.value = {
      ready: '就绪 (SDI=000, Code=000)',
      busy: '繁忙',
      open: '开机 (SDI=011)',
      error: '错误',
    }[data.status] || data.status
    printerStatusClass.value = {
      ready: 'ohms-green', busy: 'ohms-yellow', open: 'ohms-cyan', error: 'ohms-red',
    }[data.status] || 'ohms-dim'
  })

  on('print_frame', (data) => {
    if (activePrint.value) {
      activePrint.value.job_id = data.job_id
      frames.value.push(data)
      scrollFrames()
    }
  })

  on('print_state', (data) => {
    if (activePrint.value) {
      activePrint.value.state = data.state
      printStateClass.value = {
        'ohms-cyan': data.state === 'rts_sent' || data.state === 'sending',
        'ohms-yellow': data.state === 'waiting_cts',
        'ohms-green': data.state === 'completed',
        'ohms-red': data.state === 'failed',
      }[data.state] || 'ohms-dim'
    }
  })

  on('print_progress', (data) => { printProgress.value = data.progress })

  on('print_completed', (data) => {
    printProgress.value = 100
    setTimeout(() => { fetchJobs() }, 800)
  })

  on('print_failed', () => { fetchJobs() })
})

onUnmounted(() => {
  off('printer_status')
  off('print_frame')
  off('print_state')
  off('print_progress')
  off('print_completed')
  off('print_failed')
})
</script>

<style scoped>
.pr-container {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  overflow-y: auto;
  padding: 4px;
  --pr-fs-title: 13px;
  --pr-fs-panel: 12px;
  --pr-fs-body: 11px;
  --pr-fs-aux: 10px;
}

.pr-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
}

.pr-submit-panel,
.pr-frames-panel,
.pr-jobs-panel {
  padding: 0;
}

.pr-panel-header {
  padding: 8px 12px;
  border-bottom: 1px solid #555;
  display: flex;
  align-items: center;
  background: #1f1f1f;
}

.pr-submit-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
}

.pr-frames {
  max-height: 320px;
  overflow-y: auto;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pr-frame {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--pr-fs-body);
  padding: 3px 6px;
  border-radius: 2px;
}

.pr-frame-ok { background: rgba(0, 255, 0, 0.08); }
.pr-frame-nak { background: rgba(255, 51, 51, 0.12); }
.pr-frame-marker { background: rgba(0, 255, 255, 0.08); }

.pr-dir {
  width: 18px;
  text-align: center;
  font-weight: bold;
}
.pr-tx { color: #00ccff; }
.pr-rx { color: #00ff88; }

.pr-type {
  min-width: 48px;
  font-weight: bold;
  color: #ddd;
}

.pr-text {
  color: #999;
  word-break: break-all;
}

.pr-empty {
  padding: 20px;
  text-align: center;
  color: #555;
  font-size: var(--pr-fs-body);
}

.ohms-title {
  text-transform: none;
}
</style>
