<template>
  <div class="ac-container">
    <!-- 顶部工具栏: 链路状态 + 收发统计 -->
    <div class="ohms-panel ac-toolbar">
      <span class="ohms-title" style="font-size: var(--ac-fs-title);">ACARS MANAGEMENT / ACARS 管理</span>
      <span class="ohms-dim" style="margin-left: 8px; font-size: var(--ac-fs-aux);">ARINC619</span>
      <span class="status-dot" :class="linkStatus === 'idle' ? 'green' : linkStatus === 'busy' ? 'yellow' : 'red'"></span>
      <span :class="linkStatusClass">{{ linkStatusText }}</span>
      <span class="ac-badge" :class="channelClass">下传链路: {{ channelText }}</span>
      <span style="flex: 1;"></span>
      <span class="ohms-dim" style="font-size: var(--ac-fs-aux);">上行 {{ rxCount }} · 下行 {{ txCount }}</span>
    </div>

    <!-- 传输配置 -->
    <div class="ohms-panel ac-panel">
      <div class="ac-panel-header">
        <span class="ohms-title" style="font-size: var(--ac-fs-panel);">TRANSMISSION CONFIG / 传输配置</span>
      </div>
      <div class="ac-config-body">
        <div class="ac-field">
          <span class="ac-label">默认优先级</span>
          <el-select v-model="cfg.priority" size="small" style="width: 130px;">
            <el-option label="低 (0)" :value="0" />
            <el-option label="普通 (1)" :value="1" />
            <el-option label="高 (2)" :value="2" />
          </el-select>
        </div>
        <div class="ac-field">
          <span class="ac-label">下传链路</span>
          <el-select v-model="cfg.downlink_channel" size="small" style="width: 130px;">
            <el-option label="A664 (主)" value="a664" />
            <el-option label="A429 (备)" value="a429" />
          </el-select>
        </div>
        <div class="ac-field">
          <span class="ac-label">触发逻辑</span>
          <el-select v-model="cfg.trigger_mode" size="small" style="width: 150px;">
            <el-option label="上行请求触发" value="on_request" />
            <el-option label="周期触发" value="periodic" />
            <el-option label="事件触发" value="event" />
          </el-select>
        </div>
        <el-button type="primary" size="small" @click="saveConfig" :loading="savingCfg">保存配置</el-button>
        <span v-if="cfgSaved" class="ohms-green" style="font-size: 12px;">{{ cfgSaved }}</span>
      </div>
    </div>

    <!-- 下行消息下发 -->
    <div class="ohms-panel ac-panel">
      <div class="ac-panel-header">
        <span class="ohms-title" style="font-size: var(--ac-fs-panel);">DOWNLINK TRANSMIT / 下行消息下发</span>
        <span style="flex: 1;"></span>
        <span class="ohms-dim" style="font-size: var(--ac-fs-aux);">经 ARINC619 握手下传，需 CPM/CMU 在线响应</span>
      </div>
      <div class="ac-tx-body">
        <div class="ac-tx-row">
          <span class="ac-label">消息类型</span>
          <el-select v-model="dl.type" size="small" style="width: 150px;">
            <el-option label="故障报告" value="fault_report" />
            <el-option label="失效报告" value="failure_report" />
            <el-option label="FDE" value="fde" />
            <el-option label="事件报告" value="event_report" />
            <el-option label="构型数据" value="config_data" />
          </el-select>
          <span class="ac-label">优先级</span>
          <el-select v-model="dl.priority" size="small" style="width: 110px;">
            <el-option label="低" :value="0" />
            <el-option label="普通" :value="1" />
            <el-option label="高" :value="2" />
          </el-select>
        </div>
        <div class="ac-tx-row">
          <span class="ac-label">下发内容</span>
          <el-input v-model="dl.content" size="small" placeholder="请输入要下传的维护数据内容" style="flex: 1;" />
          <el-button type="primary" size="small" @click="sendDownlink" :loading="sendingDl">下传</el-button>
        </div>
      </div>
    </div>

    <!-- 消息记录 -->
    <div class="ohms-panel ac-panel">
      <div class="ac-panel-header">
        <span class="ohms-title" style="font-size: var(--ac-fs-panel);">MESSAGE LOG / 消息记录</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: var(--ac-fs-aux);">{{ messages.length }} 条</span>
        <span style="flex: 1;"></span>
        <el-radio-group v-model="directionFilter" size="small" @change="fetchMessages">
          <el-radio-button label="">全部</el-radio-button>
          <el-radio-button label="uplink">上行</el-radio-button>
          <el-radio-button label="downlink">下行</el-radio-button>
        </el-radio-group>
        <el-button size="small" @click="fetchMessages" :loading="loading" style="margin-left: 8px;">刷新</el-button>
        <el-button size="small" type="danger" plain @click="clearMessages" :disabled="messages.length === 0" :loading="clearing" style="margin-left: 4px;">清除</el-button>
      </div>
      <el-table :data="messages" size="small" max-height="320" v-loading="loading">
        <el-table-column label="方向" width="80">
          <template #default="{ row }">
            <span :class="row.direction === 'downlink' ? 'ohms-cyan' : 'ohms-yellow'">
              {{ row.direction === 'downlink' ? '下行' : '上行' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="140">
          <template #default="{ row }">
            <span :class="row.message_type === 'eor' || row.message_type === 'alo' ? 'ohms-yellow' : ''">
              {{ messageTypeText(row.message_type) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="优先级" width="90">
          <template #default="{ row }">
            <span :class="priorityClass(row.priority)">{{ priorityText(row.priority) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="content" label="内容" min-width="260" show-overflow-tooltip />
        <el-table-column label="链路" width="90">
          <template #default="{ row }">
            <span :class="linkClass(row.link_status)">{{ linkText(row.link_status) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { on, off } = useWebSocket()

const messages = ref([])
const loading = ref(false)
const clearing = ref(false)
const linkStatus = ref('idle')
const rxCount = ref(0)
const txCount = ref(0)

// 传输配置 (仅用于配置, 与消息记录优先级字段相互独立)
const cfg = reactive({ priority: 1, downlink_channel: 'a664', trigger_mode: 'on_request' })
const savingCfg = ref(false)
const cfgSaved = ref('')

// 下行发送
const dl = reactive({ type: 'fault_report', priority: 1, content: '' })
const sendingDl = ref(false)
const directionFilter = ref('')

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const linkStatusText = computed(() => ({ idle: '空闲', busy: '繁忙', lost: '丢失' }[linkStatus.value] || linkStatus.value))
const linkStatusClass = computed(() => ({ idle: 'ohms-green', busy: 'ohms-yellow', lost: 'ohms-red' }[linkStatus.value] || 'ohms-dim'))
const channelText = computed(() => cfg.downlink_channel === 'a429' ? 'A429 (备)' : 'A664 (主)')
const channelClass = computed(() => cfg.downlink_channel === 'a429' ? 'off' : 'on')

const priorityClass = (p) => ({ 0: 'ohms-dim', 1: 'ohms-cyan', 2: 'ohms-red' }[p] || 'ohms-dim')
const priorityText = (p) => ({ 0: '低', 1: '普通', 2: '高' }[p] ?? '普通')
const linkClass = (s) => ({ idle: 'ohms-green', busy: 'ohms-yellow', lost: 'ohms-red' }[s] || 'ohms-dim')
const linkText = (s) => ({ idle: '空闲', busy: '繁忙', lost: '丢失' }[s] || s || '—')

// 消息类型 -> 中文 (与"下行消息下发"下拉标签保持一致)
const messageTypeText = (t) => ({
  fault_report: '故障报告', failure_report: '失效报告', fde: 'FDE',
  event_report: '事件报告', config_data: '构型数据',
  eor: '上行请求(EOR)', alo: '上行请求(ALO)', alr: '上行响应(ALR)',
  rts: '请求发送(RTS)', cts: '允许发送(CTS)', ack: '确认(ACK)', data: '数据帧',
}[t] || t || '—')

// 时间显示: 后端返回 UTC(带 Z), 统一转本地时区显示
const formatTime = (t) => {
  if (!t) return '—'
  const s = /Z$|[+-]\d{2}:?\d{2}$/.test(t) ? t : t + 'Z'
  const d = new Date(s)
  return isNaN(d.getTime()) ? t : d.toLocaleString()
}

const fetchConfig = () => {
  fetch(api('/api/v1/acars/config'))
    .then(r => r.json())
    .then(d => {
      cfg.priority = d.priority ?? 1
      cfg.downlink_channel = d.downlink_channel ?? 'a664'
      cfg.trigger_mode = d.trigger_mode ?? 'on_request'
      rxCount.value = d.rx_count ?? 0
      txCount.value = d.tx_count ?? 0
      if (d.link_status) linkStatus.value = d.link_status
    })
    .catch(() => { /* 忽略 */ })
}

const saveConfig = () => {
  savingCfg.value = true
  cfgSaved.value = ''
  fetch(api('/api/v1/acars/config'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      priority: cfg.priority,
      downlink_channel: cfg.downlink_channel,
      trigger_mode: cfg.trigger_mode,
    }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.status === 'ok') {
        cfgSaved.value = '已保存并生效'
        ElMessage.success('ACARS 传输配置已保存')
      } else {
        ElMessage.error(d.message || '保存失败')
      }
    })
    .catch(() => ElMessage.error('后端服务不可达，请确认 AHMU 仿真器已启动'))
    .finally(() => { savingCfg.value = false })
}

const sendDownlink = () => {
  if (!dl.content.trim()) {
    ElMessage.warning('请输入下发内容')
    return
  }
  sendingDl.value = true
  fetch(api(`/api/v1/acars/send?message_type=${dl.type}&content=${encodeURIComponent(dl.content)}&priority=${dl.priority}`), {
    method: 'POST',
  })
    .then(r => r.json())
    .then(d => {
      if (d.status === 'ok') {
        ElMessage.success('消息已下传')
        dl.content = ''
        fetchMessages()
      } else {
        ElMessage.error(d.message || '下传失败（需 CPM/CMU 在线响应）')
      }
    })
    .catch(() => ElMessage.error('后端服务不可达，请确认 AHMU 仿真器已启动'))
    .finally(() => { sendingDl.value = false })
}

const fetchMessages = () => {
  loading.value = true
  const q = directionFilter.value ? `&direction=${directionFilter.value}` : ''
  fetch(api(`/api/v1/acars/messages?page=1&size=50${q}`))
    .then(r => r.json())
    .then(d => { messages.value = d.items || [] })
    .finally(() => { loading.value = false })
}

const clearMessages = () => {
  ElMessageBox.confirm(
    `确定清除全部 ${messages.value.length} 条 ACARS 消息记录？此操作不可恢复。`,
    '清除消息记录',
    { confirmButtonText: '确认清除', cancelButtonText: '取消', type: 'warning' },
  )
    .then(() => {
      clearing.value = true
      fetch(api('/api/v1/acars/messages'), { method: 'DELETE' })
        .then(r => r.json())
        .then(d => {
          if (d.status === 'ok') {
            ElMessage.success(`已清除 ${d.deleted} 条消息记录`)
            fetchMessages()
          } else {
            ElMessage.error(d.message || '清除失败')
          }
        })
        .catch(() => ElMessage.error('后端服务不可达，请确认 AHMU 仿真器已启动'))
        .finally(() => { clearing.value = false })
    })
    .catch(() => { /* 用户取消 */ })
}

const refreshStatus = () => {
  fetch(api('/api/v1/acars/status'))
    .then(r => r.json())
    .then(d => {
      if (d.status) linkStatus.value = d.status
      rxCount.value = d.rx_count ?? 0
      txCount.value = d.tx_count ?? 0
    })
    .catch(() => { /* 忽略 */ })
}

onMounted(() => {
  fetchConfig()
  fetchMessages()

  on('acars_link_status', (d) => { linkStatus.value = d.status })
  on('acars_message', () => { fetchMessages() })

  setInterval(refreshStatus, 3000)
})

onUnmounted(() => {
  off('acars_link_status')
  off('acars_message')
})
</script>

<style scoped>
.ac-container {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  overflow-y: auto;
  padding: 4px;
  --ac-fs-title: 13px;
  --ac-fs-panel: 12px;
  --ac-fs-body: 11px;
  --ac-fs-aux: 10px;
}

.ac-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  flex-wrap: wrap;
}

.ac-panel {
  padding: 0;
}

.ac-panel-header {
  padding: 8px 12px;
  border-bottom: 1px solid #555;
  display: flex;
  align-items: center;
  background: #1f1f1f;
}

.ac-badge {
  font-size: var(--ac-fs-aux);
  padding: 2px 8px;
  border-radius: 2px;
  border: 1px solid;
}
.ac-badge.on { color: #00ff88; border-color: #00ff88; background: rgba(0,255,136,0.08); }
.ac-badge.off { color: #ffd966; border-color: #b38600; background: rgba(255,217,102,0.08); }

.ac-config-body {
  padding: 12px 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  align-items: center;
}

.ac-field {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ac-label {
  color: #ccc;
  font-size: var(--ac-fs-body);
  min-width: 76px;
}

.ac-tx-body {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ac-tx-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.ohms-title { text-transform: none; }
</style>
