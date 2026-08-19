<template>
  <div>
    <!-- 打印机状态 + 提交打印 -->
    <div class="ohms-panel" style="margin-bottom: 16px; display: flex; gap: 12px; align-items: center;">
      <span class="ohms-title" style="font-size: 14px;">打印管理 (ARINC744A)</span>
      <span class="status-dot" :class="printerStatus === 'ready' ? 'green' : printerStatus === 'busy' ? 'yellow' : 'red'"></span>
      <span :class="printerStatusClass">{{ printerStatusText }}</span>
      <span style="flex: 1;"></span>
      <el-select v-model="printType" style="width: 160px;">
        <el-option label="文件传输模式" value="file_transfer" />
        <el-option label="块传输模式" value="block_transfer" />
      </el-select>
      <el-input v-model="printContent" placeholder="打印内容" style="width: 250px;" />
      <el-button type="primary" @click="submitPrint">提交打印</el-button>
    </div>

    <!-- 实时打印进度 -->
    <el-card v-if="activePrint" style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; justify-content: space-between;">
          <span>◆ 打印任务: {{ activePrint.job_id?.slice(0, 8) }}...</span>
          <span :class="printStateClass">{{ activePrint.state }}</span>
        </div>
      </template>
      <el-progress v-if="printProgress > 0" :percentage="printProgress" :stroke-width="12" />
      <div v-else class="ohms-dim" style="margin-top: 8px;">
        状态流转: RTS → CTS → 数据传输 → 完成
      </div>
    </el-card>

    <!-- 任务列表 -->
    <el-card>
      <template #header>◆ 打印任务记录</template>
      <el-table :data="jobs" size="small" v-loading="loading">
        <el-table-column prop="job_type" label="模式" width="130">
          <template #default="{ row }">
            {{ row.job_type === 'file_transfer' ? '文件传输' : '块传输' }}
          </template>
        </el-table-column>
        <el-table-column prop="content" label="内容" min-width="200" />
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <span :class="jobStatusClass(row.status)">{{ jobStatusText(row.status) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="printer_status" label="打印机" width="90" />
        <el-table-column prop="created_at" label="提交时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { on } = useWebSocket()

const jobs = ref([])
const loading = ref(false)
const printerStatus = ref('ready')
const printType = ref('file_transfer')
const printContent = ref('')
const activePrint = ref(null)
const printProgress = ref(0)

const printerStatusText = computed(() => ({
  ready: '就绪 (SDI=000, Code=000)',
  busy: '繁忙',
  open: '开机 (SDI=011)',
  error: '错误',
}[printerStatus.value] || printerStatus.value))

const printerStatusClass = computed(() => ({
  ready: 'ohms-green',
  busy: 'ohms-yellow',
  open: 'ohms-cyan',
  error: 'ohms-red',
}[printerStatus.value] || 'ohms-dim'))

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const submitPrint = () => {
  if (!printContent.value) {
    ElMessage.warning('请输入打印内容')
    return
  }
  fetch(api(`/api/v1/print/submit?content=${encodeURIComponent(printContent.value)}&job_type=${printType.value}`), {
    method: 'POST',
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success('打印任务已提交')
        printContent.value = ''
      }
    })
}

const fetchJobs = () => {
  loading.value = true
  fetch(api('/api/v1/print/jobs?page=1&size=20'))
    .then(r => r.json())
    .then(data => {
      jobs.value = data.items || []
    })
    .finally(() => { loading.value = false })
}

const printStateClass = computed(() => ({
  'ohms-cyan': activePrint.value?.state === 'rts_sent' || activePrint.value?.state === 'sending',
  'ohms-yellow': activePrint.value?.state === 'waiting_cts',
  'ohms-green': activePrint.value?.state === 'completed',
  'ohms-red': activePrint.value?.state === 'failed',
}))

const jobStatusClass = (s) => ({
  'ohms-green': s === 'completed',
  'ohms-red': s === 'failed',
  'ohms-cyan': s === 'sending',
  'ohms-dim': s === 'queued',
})

const jobStatusText = (s) => ({
  completed: '完成',
  failed: '失败',
  sending: '发送中',
  queued: '排队',
}[s] || s)

const formatTime = (t) => t ? new Date(t).toLocaleString() : '--'

onMounted(() => {
  fetchJobs()

  on('printer_status', (data) => {
    printerStatus.value = data.status
  })

  on('print_state', (data) => {
    activePrint.value = data
    if (data.state === 'rts_sent') printProgress.value = 0
  })

  on('print_progress', (data) => {
    printProgress.value = data.progress
  })

  on('print_completed', (data) => {
    if (activePrint.value?.job_id === data.job_id) {
      activePrint.value.state = 'completed'
      printProgress.value = 100
    }
    setTimeout(() => {
      activePrint.value = null
      printProgress.value = 0
      fetchJobs()
    }, 2000)
  })

  on('print_failed', () => {
    fetchJobs()
  })
})
</script>
