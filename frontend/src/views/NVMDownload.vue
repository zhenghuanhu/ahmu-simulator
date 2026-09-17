<template>
  <div>
    <!-- ==================== 1. 获取指令面板 ==================== -->
    <div class="ohms-panel" style="margin-bottom: 16px;">
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
        <span class="ohms-title" style="font-size: 14px;">NVM DATA DOWNLOAD</span>
        <span class="ohms-dim" style="font-size: 12px;">数据下载管理（仅维护模式可获取）</span>
        <span class="status-dot" :class="mode === 'maintenance' ? 'green' : 'red'"></span>
        <span :class="mode === 'maintenance' ? 'ohms-green' : 'ohms-red'" style="font-size: 12px;">
          {{ mode === 'maintenance' ? 'MAINTENANCE MODE' : 'NORMAL MODE (需维护模式)' }}
        </span>
      </div>

      <div style="margin-top: 10px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
        <span class="ohms-dim" style="font-size: 12px;">成员系统：</span>
        <el-input v-model="form.member" placeholder="如 MEM001" style="width: 140px;" />
        <span class="ohms-dim" style="font-size: 12px;">数据类型：</span>
        <el-select v-model="form.data_type" style="width: 180px;">
          <el-option label="故障快照 fault_snapshot" value="fault_snapshot" />
          <el-option label="构型快照 config_snapshot" value="config_snapshot" />
          <el-option label="生命周期 life_cycle" value="life_cycle" />
        </el-select>
        <el-button type="primary" :loading="submitting" :disabled="mode !== 'maintenance'" @click="onRetrieve">
          获取 NVM 数据
        </el-button>
        <span style="flex: 1;"></span>
        <span class="ohms-dim" style="font-size: 12px;">电子盘打印目录：{{ printDir }}</span>
      </div>
    </div>

    <!-- ==================== 2. 实时获取进度 ==================== -->
    <el-card v-if="activeRetrieves.length" style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px;">
          <span>◆ 实时获取进度</span>
          <span class="status-dot" :class="wsConnected ? 'green' : 'red'"></span>
          <span class="ohms-dim" style="font-size: 12px;">{{ wsConnected ? 'LIVE 推送' : 'OFFLINE' }}</span>
        </div>
      </template>
      <div v-for="r in activeRetrieves" :key="r.download_id" style="margin-bottom: 12px;">
        <div style="display: flex; gap: 12px; align-items: center; margin-bottom: 4px;">
          <span class="ohms-cyan" style="font-size: 13px;">{{ r.member_system }}</span>
          <span class="ohms-dim" style="font-size: 12px;">{{ r.download_id }}</span>
          <span style="flex: 1;"></span>
          <span :class="r.progress >= 100 ? 'ohms-green' : 'ohms-cyan'" style="font-size: 12px;">
            {{ r.progress.toFixed(1) }}%
          </span>
        </div>
        <el-progress :percentage="r.progress" :stroke-width="12" />
      </div>
    </el-card>

    <!-- ==================== 3. NVM 数据管理 (数据库) ==================== -->
    <el-card style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px;">
          <span>◆ NVM 数据库管理</span>
          <el-select v-model="filterDownloadStatus" placeholder="下载状态" clearable style="width: 140px;" @change="fetchNvmData">
            <el-option label="已存储 stored" value="stored" />
            <el-option label="已下载 downloaded" value="downloaded" />
          </el-select>
          <span style="flex: 1;"></span>
          <span class="ohms-dim">共 {{ nvmDataTotal }} 条</span>
          <el-button size="small" @click="fetchNvmData">刷新</el-button>
        </div>
      </template>
      <el-table :data="nvmData" size="small" v-loading="nvmDataLoading" empty-text="暂无 NVM 数据">
        <el-table-column prop="member_system" label="成员系统" width="120" />
        <el-table-column prop="data_type" label="数据类型" width="180">
          <template #default="{ row }">{{ dataTypeText(row.data_type) }}</template>
        </el-table-column>
        <el-table-column prop="data_size" label="大小" width="120">
          <template #default="{ row }">{{ formatSize(row.data_size) }}</template>
        </el-table-column>
        <el-table-column prop="retrieved_at" label="获取时间" width="180">
          <template #default="{ row }">{{ formatTime(row.retrieved_at) }}</template>
        </el-table-column>
        <el-table-column prop="download_status" label="下载状态" width="110">
          <template #default="{ row }">
            <span :class="row.download_status === 'downloaded' ? 'ohms-green' : 'ohms-yellow'">
              {{ row.download_status === 'downloaded' ? '已下载 PMAT' : '已存储' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :disabled="row.download_status === 'downloaded'" :loading="exportingId === row.id" @click="onExport(row)">
              下载到 PMAT
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ==================== 4. 下载日志列表 ==================== -->
    <el-card>
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px;">
          <span>◆ NVM 下载日志</span>
          <el-select v-model="filterStatus" placeholder="状态" clearable style="width: 130px;" @change="fetchLogs">
            <el-option label="已完成" value="completed" />
            <el-option label="获取中" value="retrieving" />
            <el-option label="失败" value="failed" />
            <el-option label="超时" value="timeout" />
          </el-select>
          <span style="flex: 1;"></span>
          <span class="ohms-dim">共 {{ logTotal }} 条</span>
          <el-button size="small" @click="fetchLogs">刷新</el-button>
        </div>
      </template>

      <el-alert v-if="logsError" :title="logsError" type="error" show-icon :closable="false" style="margin-bottom: 10px;" />

      <el-table :data="logs" size="small" v-loading="logsLoading" empty-text="暂无下载日志">
        <el-table-column prop="download_id" label="下载号" width="200" />
        <el-table-column prop="member_system" label="成员系统" width="110" />
        <el-table-column prop="data_type" label="类型" width="170">
          <template #default="{ row }">{{ dataTypeText(row.data_type) }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="110">
          <template #default="{ row }">
            <span :class="stateClass(row.status)">{{ stateText(row.status) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="progress" label="进度" width="100">
          <template #default="{ row }">
            <span :class="row.progress >= 100 ? 'ohms-green' : 'ohms-dim'">{{ row.progress?.toFixed(0) }}%</span>
          </template>
        </el-table-column>
        <el-table-column prop="data_size" label="大小" width="100">
          <template #default="{ row }">{{ formatSize(row.data_size) }}</template>
        </el-table-column>
        <el-table-column prop="operator" label="操作员" width="80" />
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :disabled="row.status !== 'completed'" :loading="printingId === row.download_id" @click="onPrint(row)">
              打印
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'
import { systemMode, fetchSystemMode } from '../composables/useSystemMode'

const { on, connected } = useWebSocket()
const mode = systemMode
const wsConnected = connected

// ---------- 状态 ----------
const form = reactive({ member: 'MEM001', data_type: 'fault_snapshot' })
const submitting = ref(false)
const activeRetrieves = ref([])
const printDir = ref('A:\\printlog')

const logs = ref([])
const logTotal = ref(0)
const logsLoading = ref(false)
const logsError = ref('')
const filterStatus = ref('')

const nvmData = ref([])
const nvmDataTotal = ref(0)
const nvmDataLoading = ref(false)
const filterDownloadStatus = ref('')
const exportingId = ref('')
const printingId = ref('')

// ---------- API ----------
const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

// ---------- 数据加载 ----------
const fetchLogs = () => {
  logsLoading.value = true
  logsError.value = ''
  const qs = filterStatus.value ? `&status=${filterStatus.value}` : ''
  fetch(api(`/api/v1/nvm-download/logs?page=1&size=50${qs}`))
    .then(r => r.json())
    .then(data => {
      logs.value = data.items || []
      logTotal.value = data.total || 0
    })
    .catch(() => { logsError.value = '下载日志加载失败' })
    .finally(() => { logsLoading.value = false })
}

const fetchNvmData = () => {
  nvmDataLoading.value = true
  const qs = filterDownloadStatus.value ? `&download_status=${filterDownloadStatus.value}` : ''
  fetch(api(`/api/v1/nvm-download/data?page=1&size=50${qs}`))
    .then(r => r.json())
    .then(data => {
      nvmData.value = data.items || []
      nvmDataTotal.value = data.total || 0
    })
    .finally(() => { nvmDataLoading.value = false })
}

const fetchPrintDir = () => {
  fetch(api('/api/v1/print/jobs?page=1&size=1'))
    .then(r => r.json())
    .then(data => { if (data.print_dir) printDir.value = data.print_dir })
    .catch(() => {})
}

// ---------- 操作 ----------
const onRetrieve = () => {
  if (!form.member.trim()) {
    ElMessage.warning('请输入成员系统')
    return
  }
  submitting.value = true
  fetch(api('/api/v1/nvm-download/retrieve'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ member_system: form.member.trim(), data_type: form.data_type, operator: 'TEST' }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success(`已下发获取指令: ${data.download_id}`)
        fetchLogs()
      } else {
        ElMessage.error(data.message)
      }
    })
    .catch(() => ElMessage.error('网络错误'))
    .finally(() => { submitting.value = false })
}

const onExport = (row) => {
  exportingId.value = row.id
  fetch(api(`/api/v1/nvm-download/export/${row.id}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success(`NVM 数据已下载到 PMAT: ${data.member_system}`)
        fetchNvmData()
      } else {
        ElMessage.error(data.message)
      }
    })
    .catch(() => ElMessage.error('网络错误'))
    .finally(() => { exportingId.value = '' })
}

const onPrint = (row) => {
  printingId.value = row.download_id
  fetch(api(`/api/v1/nvm-download/print/${row.download_id}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success(`获取结果已发送至打印机（电子盘 ${data.print_dir || printDir.value}）`)
        if (data.print_dir) printDir.value = data.print_dir
      } else {
        ElMessage.error(data.message)
      }
    })
    .catch(() => ElMessage.error('网络错误'))
    .finally(() => { printingId.value = '' })
}

// ---------- 显示辅助 ----------
const dataTypeText = (t) => ({
  fault_snapshot: '故障快照',
  config_snapshot: '构型快照',
  life_cycle: '生命周期',
}[t] || t)

const stateText = (s) => ({
  pending: '待获取',
  retrieving: '获取中',
  completed: 'Successful',
  failed: 'Failed',
  timeout: 'Timeout',
  rejected: 'Rejected',
}[s] || s)

const stateClass = (s) => ({
  pending: 'ohms-dim',
  retrieving: 'ohms-cyan',
  completed: 'ohms-green',
  failed: 'ohms-red',
  timeout: 'ohms-yellow',
  rejected: 'ohms-red',
}[s] || 'ohms-dim')

const formatSize = (bytes) => {
  if (!bytes) return '--'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

const formatTime = (t) => t ? new Date(t).toLocaleString() : '--'

// ---------- 生命周期 ----------
onMounted(() => {
  fetchLogs()
  fetchNvmData()
  fetchPrintDir()
  fetchSystemMode()

  // 订阅获取状态实时推送
  on('nvm_download_state', (data) => {
    if (data.state === 'retrieving') {
      const idx = activeRetrieves.value.findIndex(r => r.download_id === data.download_id)
      if (idx === -1) {
        activeRetrieves.value.push({ download_id: data.download_id, member_system: data.member_system, progress: 0 })
      }
    } else {
      activeRetrieves.value = activeRetrieves.value.filter(r => r.download_id !== data.download_id)
      fetchLogs()
      fetchNvmData()
    }
  })

  on('nvm_download_progress', (data) => {
    const r = activeRetrieves.value.find(r => r.download_id === data.download_id)
    if (r) r.progress = data.progress
  })
})
</script>
