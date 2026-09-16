<template>
  <div>
    <!-- ==================== 1. 重置操作面板 ==================== -->
    <div class="ohms-panel" style="margin-bottom: 16px;">
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
        <span class="ohms-title" style="font-size: 14px;">NVM DATA RESET / FAULT HISTORY RESET</span>
        <span class="ohms-dim" style="font-size: 12px;">数据重置管理（仅维护模式可操作）</span>
        <span class="status-dot" :class="mode === 'maintenance' ? 'green' : 'red'"></span>
        <span :class="mode === 'maintenance' ? 'ohms-green' : 'ohms-red'" style="font-size: 12px;">
          {{ mode === 'maintenance' ? 'MAINTENANCE MODE' : 'NORMAL MODE (需维护模式)' }}
        </span>
      </div>

      <div style="margin-top: 10px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
        <span class="ohms-dim" style="font-size: 12px;">重置类型：</span>
        <el-radio-group v-model="resetType">
          <el-radio label="full">NVM 全量重置</el-radio>
          <el-radio label="partial">NVM 部分重置</el-radio>
          <el-radio label="fault_history">故障历史重置</el-radio>
        </el-radio-group>
        <span style="flex: 1;"></span>
        <span class="ohms-dim" style="font-size: 12px;">电子盘打印目录：{{ printDir }}</span>
        <el-button size="small" @click="fetchMembers">刷新成员系统</el-button>
      </div>

      <!-- 成员系统选择 -->
      <div style="margin-top: 10px;">
        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 6px;">
          <span class="ohms-dim" style="font-size: 12px;">
            已选 <span class="ohms-cyan">{{ selectedMembers.length }}</span> / {{ enabledCount }} 个启用成员系统
          </span>
          <span style="flex: 1;"></span>
          <el-button size="small" type="primary" :loading="submitting" :disabled="mode !== 'maintenance' || !selectedMembers.length" @click="onReset">
            {{ resetType === 'fault_history' ? 'RESET HISTORY 开始重置' : 'RESET NVM 开始重置' }}
          </el-button>
        </div>
        <el-table
          :data="pagedMembers"
          size="small"
          height="260"
          v-loading="membersLoading"
          @selection-change="onSelectionChange"
          empty-text="暂无启用成员系统"
        >
          <el-table-column type="selection" width="45" :selectable="() => mode === 'maintenance'" />
          <el-table-column prop="member" label="成员系统" width="140" />
          <el-table-column label="操作状态" min-width="120">
            <template #default="{ row }">
              <span v-if="activeResets.includes(row.member)" class="ohms-yellow">重置中</span>
              <span v-else class="ohms-dim">待命</span>
            </template>
          </el-table-column>
        </el-table>
        <div style="margin-top: 6px; text-align: right;">
          <el-pagination
            layout="prev, pager, next, total"
            :total="enabledCount"
            :page-size="pageSize"
            v-model:current-page="currentPage"
            small
          />
        </div>
      </div>
    </div>

    <!-- ==================== 2. 重置日志列表 ==================== -->
    <el-card>
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px;">
          <span>◆ NVM / 故障历史重置日志</span>
          <el-select v-model="filterStatus" placeholder="状态" clearable style="width: 130px;" @change="fetchLogs">
            <el-option label="成功" value="success" />
            <el-option label="失败" value="failed" />
            <el-option label="执行中" value="sending" />
            <el-option label="等待响应" value="waiting_ack" />
            <el-option label="超时" value="timeout" />
          </el-select>
          <span style="flex: 1;"></span>
          <span class="ohms-dim">共 {{ logTotal }} 条</span>
          <el-button size="small" @click="fetchLogs">刷新</el-button>
        </div>
      </template>

      <el-alert v-if="logsError" :title="logsError" type="error" show-icon :closable="false" style="margin-bottom: 10px;" />

      <el-table :data="logs" size="small" v-loading="logsLoading" empty-text="暂无重置日志">
        <el-table-column prop="reset_id" label="重置号" width="180" />
        <el-table-column prop="member_system" label="成员系统" width="110" />
        <el-table-column prop="reset_type" label="类型" width="120">
          <template #default="{ row }">{{ resetTypeText(row.reset_type) }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <span :class="stateClass(row.status)">{{ stateText(row.status) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="result_code" label="结果码" width="80">
          <template #default="{ row }">
            <span v-if="row.result_code === '0'" class="ohms-green">0</span>
            <span v-else-if="row.result_code" class="ohms-red">{{ row.result_code }}</span>
            <span v-else class="ohms-dim">--</span>
          </template>
        </el-table-column>
        <el-table-column prop="result_message" label="结果描述" min-width="160">
          <template #default="{ row }">
            <span v-if="row.result_message" :class="row.result_code === '0' ? 'ohms-green' : 'ohms-red'">{{ row.result_message }}</span>
            <span v-else class="ohms-dim">--</span>
          </template>
        </el-table-column>
        <el-table-column prop="operator" label="操作员" width="80" />
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :disabled="row.status !== 'success'" :loading="printingId === row.reset_id" @click="onPrint(row)">
              打印
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'
import { systemMode, fetchSystemMode } from '../composables/useSystemMode'

const { on } = useWebSocket()
const mode = systemMode

// ---------- 状态 ----------
const members = ref([])
const enabledCount = ref(0)
const membersLoading = ref(false)
const selectedMembers = ref([])
const resetType = ref('full')
const submitting = ref(false)
const activeResets = ref([])
const printDir = ref('A:\\printlog')

const logs = ref([])
const logTotal = ref(0)
const logsLoading = ref(false)
const logsError = ref('')
const filterStatus = ref('')
const printingId = ref('')

const currentPage = ref(1)
const pageSize = 20

// ---------- API ----------
const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

// ---------- 成员系统分页 ----------
const pagedMembers = computed(() => {
  const start = (currentPage.value - 1) * pageSize
  return members.value.slice(start, start + pageSize).map(m => ({ member: m }))
})

// ---------- 数据加载 ----------
const fetchMembers = () => {
  membersLoading.value = true
  fetch(api('/api/v1/nvm-reset/members'))
    .then(r => r.json())
    .then(data => {
      members.value = data.members || []
      enabledCount.value = data.count || 0
    })
    .finally(() => { membersLoading.value = false })
}

const fetchLogs = () => {
  logsLoading.value = true
  logsError.value = ''
  const qs = filterStatus.value ? `&status=${filterStatus.value}` : ''
  fetch(api(`/api/v1/nvm-reset/logs?page=1&size=50${qs}`))
    .then(r => r.json())
    .then(data => {
      logs.value = data.items || []
      logTotal.value = data.total || 0
    })
    .catch(() => { logsError.value = '重置日志加载失败' })
    .finally(() => { logsLoading.value = false })
}

const fetchPrintDir = () => {
  fetch(api('/api/v1/print/jobs?page=1&size=1'))
    .then(r => r.json())
    .then(data => {
      if (data.print_dir) printDir.value = data.print_dir
    })
    .catch(() => {})
}

// ---------- 操作 ----------
const onSelectionChange = (rows) => {
  selectedMembers.value = rows.map(r => r.member)
}

const onReset = () => {
  if (!selectedMembers.value.length) {
    ElMessage.warning('请先勾选成员系统设备')
    return
  }
  submitting.value = true
  const typeLabel = resetTypeText(resetType.value)
  fetch(api('/api/v1/nvm-reset/batch-reset'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      member_systems: selectedMembers.value,
      reset_type: resetType.value,
      operator: 'TEST',
    }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.accepted_count > 0) {
        ElMessage.success(`${typeLabel}：${data.accepted_count} 个成员系统已下发重置任务`)
        if (data.rejected_count > 0) {
          ElMessage.warning(`${data.rejected_count} 个被拒绝（可能已在重置中）`)
        }
        selectedMembers.value = []
        fetchLogs()
      } else {
        ElMessage.error(data.rejected?.[0]?.message || '重置失败')
      }
    })
    .catch(() => ElMessage.error('网络错误'))
    .finally(() => { submitting.value = false })
}

const onPrint = (row) => {
  printingId.value = row.reset_id
  fetch(api(`/api/v1/nvm-reset/print/${row.reset_id}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success(`重置结果已发送至打印机（电子盘 ${data.print_dir || printDir.value}）`)
        if (data.print_dir) printDir.value = data.print_dir
      } else {
        ElMessage.error(data.message)
      }
    })
    .catch(() => ElMessage.error('网络错误'))
    .finally(() => { printingId.value = '' })
}

// ---------- 显示辅助 ----------
const resetTypeText = (t) => ({
  full: 'NVM 全量重置',
  partial: 'NVM 部分重置',
  fault_history: '故障历史重置',
}[t] || t)

const stateText = (s) => ({
  pending: '待发送',
  sending: '发送中',
  waiting_ack: '等待响应',
  success: 'Successful',
  failed: 'Failed',
  timeout: 'Timeout',
  rejected: 'Rejected',
}[s] || s)

const stateClass = (s) => ({
  pending: 'ohms-dim',
  sending: 'ohms-cyan',
  waiting_ack: 'ohms-yellow',
  success: 'ohms-green',
  failed: 'ohms-red',
  timeout: 'ohms-yellow',
  rejected: 'ohms-red',
}[s] || 'ohms-dim')

// ---------- 生命周期 ----------
onMounted(() => {
  fetchMembers()
  fetchLogs()
  fetchPrintDir()
  fetchSystemMode()  // 同步当前系统模式

  // 订阅重置状态实时推送
  on('nvm_reset_state', (data) => {
    if (['sending', 'waiting_ack'].includes(data.state)) {
      if (!activeResets.value.includes(data.member_system)) {
        activeResets.value.push(data.member_system)
      }
    } else {
      activeResets.value = activeResets.value.filter(m => m !== data.member_system)
      // 终态刷新日志
      fetchLogs()
    }
  })
})
</script>
