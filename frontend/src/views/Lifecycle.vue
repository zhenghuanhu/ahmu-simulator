<template>
  <div class="tc-container">
    <!-- 顶部操作栏 -->
    <div class="ohms-panel tc-toolbar">
      <span class="ohms-title" style="font-size: 14px;">TIME CYCLE / 生命周期</span>
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
        <span :class="['tc-source-badge', memberResult.data_source === 'real' ? 'tc-source-real' : 'tc-source-sim']">
          {{ memberResult.data_source === 'real' ? '真实数据' : '模拟数据' }}
        </span>
        <span class="ohms-dim">上电运行时间: </span>
        <span class="ohms-green">{{ formatTime(memberResult.power_on_time) }}</span>
        <span class="ohms-dim" style="margin-left: 12px;">上电循环计数: </span>
        <span class="ohms-green">{{ memberResult.power_cycle_count }}</span>
        <span v-if="memberResult.icd_message" class="ohms-dim" style="margin-left: 12px;">
          ICD: <span class="ohms-cyan">{{ memberResult.icd_message }}</span> (VL {{ memberResult.vl_id }})
        </span>
        <template v-if="memberResult.data_source === 'real'">
          <span class="ohms-dim" style="margin-left: 12px;">指令发送: </span>
          <span class="ohms-green">{{ formatDateTime(memberResult.command_sent_at) }}</span>
          <span class="ohms-dim" style="margin-left: 8px;">设备响应: </span>
          <span class="ohms-green">{{ formatDateTime(memberResult.response_received_at) }}</span>
          <span class="ohms-dim" style="margin-left: 8px;">结果: </span>
          <span class="ohms-green">{{ memberResult.response_result }}</span>
        </template>
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
          生命周期获取日志（操作时间 / 操作用户 / 成员系统 / 操作状态 / 数据来源）
        </span>
        <span style="flex: 1;"></span>
        <el-select v-model="dataSourceFilter" size="small" style="width: 130px; margin-right: 8px;" @change="fetchLogs">
          <el-option label="全部来源" value="" />
          <el-option label="模拟数据" value="simulated" />
          <el-option label="真实数据" value="real" />
        </el-select>
        <el-button size="small" @click="fetchLogs">刷新</el-button>
      </div>
      <el-table
        :data="logs"
        size="small"
        border
        v-loading="logsLoading"
        class="tc-log-el-table"
        style="width: 100%;"
        :header-cell-style="logHeaderStyle"
      >
        <el-table-column label="操作时间" min-width="165">
          <template #default="{ row }">{{ formatDateTime(row.operated_at) }}</template>
        </el-table-column>
        <el-table-column prop="equip_id" label="成员系统" min-width="110">
          <template #default="{ row }"><span class="tc-log-id">{{ row.equip_id }}</span></template>
        </el-table-column>
        <el-table-column prop="equip_name" label="成员系统名称" min-width="150" show-overflow-tooltip>
          <template #default="{ row }"><span class="tc-log-name">{{ row.equip_name || '--' }}</span></template>
        </el-table-column>
        <el-table-column prop="operator" label="操作用户" min-width="90" />
        <el-table-column label="数据来源" min-width="90">
          <template #default="{ row }">
            <span :class="['tc-source-badge', row.data_source === 'real' ? 'tc-source-real' : 'tc-source-sim']">
              {{ row.data_source === 'real' ? '真实' : '模拟' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="上电运行时间" min-width="110">
          <template #default="{ row }">{{ row.power_on_time ? formatTime(row.power_on_time) : '--' }}</template>
        </el-table-column>
        <el-table-column label="上电循环计数" min-width="100">
          <template #default="{ row }">{{ row.power_cycle_count || '--' }}</template>
        </el-table-column>
        <el-table-column label="指令/响应" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <template v-if="row.data_source === 'real' && row.response_received_at">
              <span class="ohms-dim">{{ formatTimeShort(row.command_sent_at) }} → {{ formatTimeShort(row.response_received_at) }}</span>
              <span v-if="row.response_result" class="tc-real-result">({{ row.response_result }})</span>
            </template>
            <span v-else class="ohms-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" min-width="110">
          <template #default="{ row }">
            <span :class="logStatusClass(row.status)">{{ logStatusText(row.status) }}</span>
            <span v-if="row.error_message" class="ohms-red" style="font-size: 11px;" :title="row.error_message">
              · {{ row.error_message.slice(0, 30) }}
            </span>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!logsLoading && logs.length === 0" class="tc-empty">暂无获取日志</div>
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
const dataSourceFilter = ref('')  // 数据来源筛选: ''=全部 / simulated / real

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
        ElMessage.success(`${data.member_name}: 运行 ${data.status_string}, 上电循环计数 ${data.power_cycle_count}`)
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
  const ds = dataSourceFilter.value ? `&data_source=${dataSourceFilter.value}` : ''
  fetch(api(`/api/v1/lifecycle/logs?page=1&size=50${ds}`))
    .then(r => r.json())
    .then(data => { logs.value = data.items || [] })
    .catch(() => {})
    .finally(() => { logsLoading.value = false })
}

const logStatusText = (s) => ({ success: '成功', failed: '失败', rejected: '拒绝' }[s] || s)
const logStatusClass = (s) => ({ success: 'ohms-green', failed: 'ohms-red', rejected: 'ohms-yellow' }[s] || 'ohms-dim')

// 日志表头样式 (深色, 与项目风格一致)
const logHeaderStyle = () => ({
  background: '#1f1f1f',
  color: '#aaa',
  fontSize: '11px',
  textTransform: 'uppercase',
  letterSpacing: '1px',
  borderColor: '#444',
})

const formatDateTime = (t) => t ? new Date(t).toLocaleString() : '--'

const formatTimeShort = (t) => t ? new Date(t).toLocaleTimeString() : '--'

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

.tc-log-el-table {
  flex: 1;
  min-height: 0;
}

/* el-table 深色主题适配 */
.tc-log-el-table :deep(.el-table__inner-wrapper) {
  background: transparent;
}

.tc-log-el-table :deep(th.el-table__cell) {
  background: #1f1f1f !important;
  border-color: #444 !important;
}

.tc-log-el-table :deep(td.el-table__cell) {
  background: transparent;
  border-color: #222;
  color: #fff;
}

.tc-log-el-table :deep(.el-table__row:hover > td.el-table__cell) {
  background: #1a1a1a !important;
}

.tc-log-id {
  color: #00ffff;
  font-size: 11px;
}

.tc-log-name {
  color: #bbb;
}

/* 数据来源徽章 */
.tc-source-badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: bold;
  line-height: 18px;
  letter-spacing: 0.5px;
  white-space: nowrap;
}

.tc-source-sim {
  background: #333;
  color: #bbb;
  border: 1px solid #555;
}

.tc-source-real {
  background: #0a3d2e;
  color: #00e68a;
  border: 1px solid #00b36b;
}

.tc-real-result {
  color: #00e6a0;
  font-size: 11px;
  margin-left: 4px;
}
</style>
