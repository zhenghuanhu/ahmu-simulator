<template>
  <div class="tc-container">
    <!-- 顶部操作栏 -->
    <div class="ohms-panel tc-toolbar">
      <span class="ohms-title" style="font-size: 14px;">TIME CYCLE</span>
      <span class="ohms-dim" style="margin-left: 12px; font-size: 12px;">
        {{ memberList.length }} member systems
      </span>
      <span style="flex: 1;"></span>
      <span class="ohms-dim" :class="{'ohms-green': mode === 'maintenance', 'ohms-red': mode !== 'maintenance'}" style="font-size: 12px; margin-right: 16px;">
        {{ mode === 'maintenance' ? 'MAINTENANCE MODE (可获取生命周期数据)' : 'NORMAL MODE (需维护模式)' }}
      </span>
    </div>

    <!-- 成员系统生命周期查看 -->
    <div class="ohms-panel tc-member-panel">
      <span class="ohms-label">MEMBER SYSTEM</span>
      <el-select
        v-model="selectedMember"
        filterable
        size="small"
        placeholder="选择成员系统 (如 HF_HSCU)"
        class="tc-member-select"
        :disabled="mode !== 'maintenance'"
      >
        <el-option
          v-for="m in memberList"
          :key="m.memberSystem"
          :label="`${m.memberSystem} - ${m.memberName}`"
          :value="m.memberSystem"
        />
      </el-select>
      <el-button
        type="primary"
        size="small"
        @click="viewMemberLifecycle"
        :loading="memberLoading"
        :disabled="!selectedMember || mode !== 'maintenance'"
      >
        查看成员系统生命周期信息
      </el-button>
      <span v-if="memberResult" class="tc-member-result">
        <span class="ohms-cyan">{{ memberResult.member_name }}</span>
        <span class="ohms-dim">上电运行时间: </span>
        <span class="ohms-green">{{ formatTime(memberResult.power_on_time) }}</span>
        <span class="ohms-dim" style="margin-left: 12px;">循环上电循环计数: </span>
        <span class="ohms-green">{{ memberResult.power_cycle_count }}</span>
      </span>
    </div>

    <!-- 主动存储 -->
    <div class="ohms-panel tc-store-panel">
      <span class="ohms-label">STORAGE</span>
      <span class="ohms-dim" style="font-size: 11px;">存储位置:</span>
      <input
        v-model="storagePath"
        class="tc-store-input"
        placeholder="留空使用默认目录 (backend/data/lifecycle_storage)"
      />
      <el-button size="small" @click="saveData" :loading="saveLoading">主动存储</el-button>
      <span v-if="saveResult" class="ohms-green tc-store-result">{{ saveResult }}</span>
    </div>

    <!-- 生命周期获取日志 -->
    <div class="ohms-panel tc-log-panel">
      <div class="tc-panel-header" style="border-bottom: 1px solid #555;">
        <span class="ohms-title" style="font-size: 13px;">RETRIEVAL LOG</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: 12px;">
          生命周期获取日志 (操作时间/操作用户/被操作的成员系统/操作状态)
        </span>
        <span style="flex: 1;"></span>
        <el-button size="small" @click="fetchLogs">刷新</el-button>
      </div>
      <div class="tc-log-table" v-loading="logsLoading">
        <div class="tc-log-header">
          <span class="tc-log-time">操作时间</span>
          <span class="tc-log-id">成员系统</span>
          <span class="tc-log-name">成员系统名称</span>
          <span class="tc-log-user">操作用户</span>
          <span class="tc-log-timeval">上电运行时间</span>
          <span class="tc-log-cycle">循环上电循环计数</span>
          <span class="tc-log-status">状态</span>
        </div>
        <div v-for="log in logs" :key="log.operated_at + log.equip_id" class="tc-log-row">
          <span class="tc-log-time">{{ formatDateTime(log.operated_at) }}</span>
          <span class="tc-log-id">{{ log.equip_id }}</span>
          <span class="tc-log-name">{{ log.equip_name || '--' }}</span>
          <span class="tc-log-user">{{ log.operator }}</span>
          <span class="tc-log-timeval">{{ log.power_on_time ? formatTime(log.power_on_time) : '--' }}</span>
          <span class="tc-log-cycle">{{ log.power_cycle_count || '--' }}</span>
          <span class="tc-log-status">
            <span :class="logStatusClass(log.status)">{{ logStatusText(log.status) }}</span>
            <span v-if="log.error_message" class="ohms-red" style="font-size: 11px;" :title="log.error_message">
              · {{ log.error_message.slice(0, 30) }}
            </span>
          </span>
        </div>
        <div v-if="!logsLoading && logs.length === 0" class="tc-empty">暂无获取日志</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { on } = useWebSocket()

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

// 模式
const mode = ref('normal')

// 成员系统生命周期查看
const memberList = ref([])
const selectedMember = ref(null)
const memberLoading = ref(false)
const memberResult = ref(null)

// 主动存储
const storagePath = ref('')
const saveLoading = ref(false)
const saveResult = ref('')

// 获取日志
const logs = ref([])
const logsLoading = ref(false)

const fetchMembers = () => {
  fetch(api('/api/v1/lifecycle/members'))
    .then(r => r.json())
    .then(data => { memberList.value = data.memberList || [] })
    .catch(() => {})
}

const viewMemberLifecycle = () => {
  if (!selectedMember.value) {
    ElMessage.warning('请先选择成员系统')
    return
  }
  if (mode.value !== 'maintenance') {
    ElMessage.warning('需维护模式才能获取生命周期数据')
    return
  }
  memberLoading.value = true
  memberResult.value = null
  fetch(api(`/api/v1/lifecycle/member-retrieve/${selectedMember.value}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        memberResult.value = data
        ElMessage.success(`${data.member_name}: 运行 ${data.status_string}, 循环上电循环计数 ${data.power_cycle_count}`)
        fetchLogs()
      } else {
        ElMessage.error(data.message || '获取失败')
      }
    })
    .catch(() => { ElMessage.error('Network error') })
    .finally(() => { memberLoading.value = false })
}

const saveData = () => {
  saveLoading.value = true
  saveResult.value = ''
  fetch(api('/api/v1/lifecycle/save'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      storage_path: storagePath.value,
      member_system: selectedMember.value || null,
    }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        saveResult.value = `已保存 ${data.count} 条 → ${data.file_path}`
        ElMessage.success(`生命周期数据已存储: ${data.file_path}`)
      } else {
        ElMessage.error(data.message || '存储失败')
      }
    })
    .catch(() => { ElMessage.error('Network error') })
    .finally(() => { saveLoading.value = false })
}

const fetchMode = () => {
  fetch(api('/api/v1/system/mode'))
    .then(r => r.json())
    .then(data => { mode.value = data.mode })
    .catch(() => {})
}

const fetchLogs = () => {
  logsLoading.value = true
  fetch(api('/api/v1/lifecycle/logs?page=1&size=50'))
    .then(r => r.json())
    .then(data => { logs.value = data.items || [] })
    .catch(() => {})
    .finally(() => { logsLoading.value = false })
}

const logStatusText = (s) => ({ success: '成功', failed: '失败', rejected: '拒绝' }[s] || s)
const logStatusClass = (s) => ({ success: 'ohms-green', failed: 'ohms-red', rejected: 'ohms-yellow' }[s] || 'ohms-dim')

const formatDateTime = (t) => t ? new Date(t).toLocaleString() : '--'

const formatTime = (sec) => {
  if (!sec || sec <= 0) return '--'
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

onMounted(() => {
  fetchMode()
  fetchMembers()
  fetchLogs()
  setInterval(fetchMode, 5000)

  on('mode_change', (data) => { mode.value = data.mode })

  on('lifecycle_retrieved', () => {
    fetchLogs()
  })
})
</script>

<style scoped>
.tc-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
}

.tc-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
}

.tc-member-panel,
.tc-store-panel {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
}

.tc-member-select {
  width: 300px;
}

.tc-member-result {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  margin-left: 4px;
}

.tc-store-input {
  flex: 1;
  min-width: 200px;
  background: #000;
  border: 1px solid #666;
  color: #00ff00;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 12px;
  padding: 6px 10px;
}

.tc-store-input:focus {
  outline: none;
  border-color: #00ccff;
}

.tc-store-result {
  font-size: 11px;
  font-family: 'Consolas', 'Courier New', monospace;
  word-break: break-all;
}

.tc-panel-header {
  padding: 10px 14px;
  border-bottom: 1px solid #555;
  display: flex;
  align-items: center;
  background: #1f1f1f;
}

.tc-empty {
  padding: 40px;
  text-align: center;
  color: #555;
  font-size: 13px;
}

.tc-log-panel {
  display: flex;
  flex-direction: column;
  padding: 0;
  flex: 1;
  min-height: 0;
}

.tc-log-table {
  overflow-y: auto;
  flex: 1;
}

.tc-log-header,
.tc-log-row {
  display: grid;
  grid-template-columns: 160px 100px 1fr 80px 120px 130px 140px;
  align-items: center;
  padding: 0 12px;
  gap: 8px;
}

.tc-log-header {
  height: 32px;
  border-bottom: 1px solid #444;
  background: #1f1f1f;
  font-size: 11px;
  text-transform: uppercase;
  color: #aaa;
  letter-spacing: 1px;
  position: sticky;
  top: 0;
  z-index: 1;
}

.tc-log-row {
  height: 34px;
  border-bottom: 1px solid #222;
  font-size: 12px;
  color: #fff;
}

.tc-log-row:hover {
  background: #1a1a1a;
}

.tc-log-id {
  color: #00ffff;
  font-size: 11px;
}

.tc-log-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #bbb;
}

.tc-log-time,
.tc-log-user,
.tc-log-timeval,
.tc-log-cycle {
  color: #ccc;
}
</style>
