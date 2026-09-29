<template>
  <div class="fd-container">
    <!-- 顶部工具栏 -->
    <div class="ohms-panel fd-toolbar">
      <span class="ohms-title" style="font-size: var(--fd-fs-title);">FAULT DIAGNOSIS / 故障诊断</span>
      <span class="ohms-dim" style="margin-left: 12px; font-size: var(--fd-fs-body);">{{ memberTotal }} 个成员系统</span>
      <span style="flex: 1;"></span>
      <span class="ohms-dim" style="font-size: var(--fd-fs-aux);">故障报告:</span>
      <span class="ohms-red" style="margin-left: 4px; font-size: var(--fd-fs-body);">{{ faultTotal }}</span>
      <span class="ohms-dim" style="margin-left: 16px; font-size: var(--fd-fs-aux);">失效报告:</span>
      <span class="ohms-yellow" style="margin-left: 4px; font-size: var(--fd-fs-body);">{{ failureTotal }}</span>
      <span class="ohms-dim" style="margin-left: 16px; font-size: var(--fd-fs-aux);">当前航段:</span>
      <el-input-number v-model="currentSegment" :min="-128" :max="127" size="small" style="width: 100px; margin-left: 6px;" />
      <el-button size="small" style="margin-left: 12px;" @click="refreshAll">刷新</el-button>
    </div>

    <!-- 成员系统控制 + ICD 注入 -->
    <div class="ohms-panel fd-control-panel">
      <div class="fd-panel-header">
        <span class="ohms-title" style="font-size: var(--fd-fs-panel);">MEMBER SYSTEM CONTROL / 成员系统控制</span>
      </div>
      <div class="fd-control-row">
        <span class="ohms-label">MEMBER SYSTEM / 成员系统</span>
        <el-select v-model="selectedMember" size="small" filterable placeholder="选择成员系统" style="width: 280px;" @change="onMemberChange">
          <el-option v-for="m in memberList" :key="m.memberSystem"
            :label="`${m.memberSystem} - ${m.memberName}`" :value="m.memberSystem" />
        </el-select>
        <span v-if="selectedMemberInfo" class="ohms-dim" style="font-size: var(--fd-fs-aux);">
          上报频率 {{ selectedMemberInfo.frequency }}Hz · 失效报告 {{ selectedMemberInfo.failureEnabled ? '已启用' : '已禁用' }}
        </span>
        <span style="flex: 1;"></span>
        <el-button size="small" type="success" @click="toggleFailure(true)" :disabled="!selectedMember">启用失效报告</el-button>
        <el-button size="small" type="danger" @click="toggleFailure(false)" :disabled="!selectedMember">禁用失效报告</el-button>
      </div>
      <div class="fd-control-row">
        <span class="ohms-label">ICD FAULT INJECTION / 故障注入</span>
        <el-select v-model="injectFaultCode" size="small" filterable placeholder="故障代码" style="width: 220px;">
          <el-option v-for="f in memberFaultCodes" :key="f.fault_code"
            :label="`${f.fault_code} (${f.severity})`" :value="f.fault_code" />
        </el-select>
        <el-select v-model="injectSeverity" size="small" style="width: 110px;">
          <el-option label="轻微" value="minor" />
          <el-option label="主要" value="major" />
          <el-option label="严重" value="critical" />
        </el-select>
        <span class="ohms-dim" style="font-size: var(--fd-fs-aux);">持续(秒):</span>
        <el-input-number v-model="injectDuration" :min="0" :max="3600" size="small" style="width: 100px;" />
        <el-button size="small" type="primary" @click="injectFault" :disabled="!injectFaultCode" :loading="injecting">注入故障</el-button>
        <span v-if="injectResult" class="ohms-cyan" style="font-size: var(--fd-fs-body); margin-left: 12px;">{{ injectResult }}</span>
      </div>
    </div>

    <!-- 标签页 -->
    <el-tabs v-model="activeTab" type="card" class="fd-tabs">
      <!-- 故障报告 -->
      <el-tab-pane label="故障报告" name="faults">
        <div class="ohms-panel fd-tab-panel">
          <div class="fd-panel-header">
            <span class="ohms-title" style="font-size: var(--fd-fs-panel);">FAULT REPORTS / 故障报告</span>
            <span class="ohms-dim" style="margin-left: 12px; font-size: var(--fd-fs-body);">{{ faultTotal }} 条</span>
            <span style="flex: 1;"></span>
            <el-input v-model="filterMember" placeholder="成员系统" size="small" clearable style="width: 160px;" @change="fetchFaults" />
            <el-button size="small" style="margin-left: 8px;" @click="fetchFaults">查询</el-button>
          </div>
          <el-table :data="faults" size="small" max-height="320" v-loading="loadingFaults">
            <el-table-column prop="member_system" label="成员系统" width="110" />
            <el-table-column prop="fault_code" label="故障代码" width="140" />
            <el-table-column prop="fault_text" label="故障描述" min-width="180" show-overflow-tooltip />
            <el-table-column label="等级" width="80">
              <template #default="{ row }"><span :class="severityClass(row.severity)">{{ severityText(row.severity) }}</span></template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <span :class="statusClass(row.status)">{{ statusText(row.status) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="ata_chapter" label="ATA" width="60" />
            <el-table-column label="航段" width="60"><template #default="{ row }">{{ row.flight_segment }}</template></el-table-column>
            <el-table-column label="FDE" width="110">
              <template #default="{ row }"><span v-if="row.fde_code" class="ohms-yellow">{{ row.fde_code }}</span><span v-else class="ohms-dim">--</span></template>
            </el-table-column>
            <el-table-column prop="lru_code" label="LRU" width="120" />
            <el-table-column label="时间" width="160">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="80">
              <template #default="{ row }">
                <el-button v-if="row.status === 'active'" size="small" type="primary" @click="resolveFault(row.id)">解决</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <!-- 失效报告 -->
      <el-tab-pane label="失效报告" name="failures">
        <div class="ohms-panel fd-tab-panel">
          <div class="fd-panel-header">
            <span class="ohms-title" style="font-size: var(--fd-fs-panel);">FAILURE REPORTS / 失效报告</span>
            <span class="ohms-dim" style="margin-left: 12px; font-size: var(--fd-fs-body);">{{ failureTotal }} 条</span>
            <span style="flex: 1;"></span>
            <el-input v-model="failureCode" placeholder="失效报告代码" size="small" style="width: 180px;" />
            <el-button size="small" style="margin-left: 8px;" @click="locateRoot">定位根源故障</el-button>
          </div>
          <el-table :data="failures" size="small" max-height="300" v-loading="loadingFailures">
            <el-table-column prop="member_system" label="成员系统" width="110" />
            <el-table-column prop="failure_code" label="失效报告代码" width="160" />
            <el-table-column prop="failure_text" label="描述" min-width="160" show-overflow-tooltip />
            <el-table-column label="等级" width="80">
              <template #default="{ row }"><span :class="severityClass(row.severity)">{{ severityText(row.severity) }}</span></template>
            </el-table-column>
            <el-table-column label="根源故障" width="160">
              <template #default="{ row }"><span class="ohms-red">{{ row.root_fault_code || '--' }}</span></template>
            </el-table-column>
            <el-table-column label="逻辑" width="60"><template #default="{ row }">{{ row.logic_type }}</template></el-table-column>
            <el-table-column prop="lru_code" label="LRU" width="120" />
            <el-table-column label="航段" width="60"><template #default="{ row }">{{ row.flight_segment }}</template></el-table-column>
            <el-table-column label="时间" width="160">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
          <div v-if="rootResult" class="fd-root-result">
            <span class="ohms-cyan">根源定位结果:</span>
            <span class="ohms-text">失效报告 {{ rootResult.failure_code }} · 逻辑 {{ rootResult.logic }} · 根源故障:
              <span class="ohms-red">{{ rootResult.root_faults?.length ? rootResult.root_faults.join(', ') : '未定位' }}</span>
            </span>
          </div>
        </div>
      </el-tab-pane>

      <!-- 历史航段 -->
      <el-tab-pane label="历史航段" name="history">
        <div class="ohms-panel fd-tab-panel">
          <div class="fd-panel-header">
            <span class="ohms-title" style="font-size: var(--fd-fs-panel);">HISTORICAL SEGMENT FAILURES / 历史航段失效报告</span>
            <span class="ohms-dim" style="margin-left: 12px; font-size: var(--fd-fs-aux);">航段范围 -128 ~ 127</span>
            <span style="flex: 1;"></span>
            <span class="ohms-dim" style="font-size: var(--fd-fs-aux);">航段:</span>
            <el-input-number v-model="historySegment" :min="-128" :max="127" size="small" style="width: 100px; margin-left: 6px;" @change="fetchHistory" />
          </div>
          <el-table :data="historyFailures" size="small" max-height="340" v-loading="loadingHistory">
            <el-table-column prop="member_system" label="成员系统" width="110" />
            <el-table-column prop="failure_code" label="失效报告代码" width="160" />
            <el-table-column prop="failure_text" label="描述" min-width="160" show-overflow-tooltip />
            <el-table-column label="根源故障" width="160">
              <template #default="{ row }"><span class="ohms-red">{{ row.root_fault_code || '--' }}</span></template>
            </el-table-column>
            <el-table-column prop="lru_code" label="LRU" width="120" />
            <el-table-column label="时间" width="160">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <!-- FDE / LRU -->
      <el-tab-pane label="FDE / LRU" name="fde_lru">
        <div class="fd-two-col">
          <div class="ohms-panel fd-tab-panel">
            <div class="fd-panel-header">
              <span class="ohms-title" style="font-size: var(--fd-fs-panel);">FDE LIST / FDE 列表</span>
              <span class="ohms-dim" style="margin-left: 12px; font-size: var(--fd-fs-body);">{{ fdeTotal }} 条</span>
              <span style="flex: 1;"></span>
              <el-button size="small" @click="fetchFde">刷新</el-button>
            </div>
            <el-table :data="fdeList" size="small" max-height="340" v-loading="loadingFde">
              <el-table-column prop="fde_code" label="FDE代码" width="110" />
              <el-table-column prop="fde_text" label="FDE描述" min-width="180" show-overflow-tooltip />
              <el-table-column label="等级" width="80">
                <template #default="{ row }"><span :class="severityClass(row.severity)">{{ severityText(row.severity) }}</span></template>
              </el-table-column>
              <el-table-column prop="ata_chapter" label="ATA" width="60" />
            </el-table>
          </div>
          <div class="ohms-panel fd-tab-panel">
            <div class="fd-panel-header">
              <span class="ohms-title" style="font-size: var(--fd-fs-panel);">LRU FAILURE REPORTS / LRU 失效报告</span>
              <span class="ohms-dim" style="margin-left: 12px; font-size: var(--fd-fs-body);">{{ lruTotal }} 个LRU</span>
            </div>
            <el-table :data="lruReports" size="small" max-height="340" v-loading="loadingLru">
              <el-table-column prop="lru_code" label="LRU" width="130" />
              <el-table-column prop="member_system" label="成员系统" width="110" />
              <el-table-column label="失效数" width="70"><template #default="{ row }">{{ row.failure_count }}</template></el-table-column>
              <el-table-column label="根源故障数" width="90"><template #default="{ row }">{{ row.fault_count }}</template></el-table-column>
              <el-table-column label="根源故障" min-width="160" show-overflow-tooltip>
                <template #default="{ row }"><span class="ohms-red">{{ row.root_fault_codes?.join(', ') }}</span></template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </el-tab-pane>

      <!-- 故障模型 -->
      <el-tab-pane label="故障模型" name="model">
        <div class="ohms-panel fd-tab-panel">
          <div class="fd-panel-header">
            <span class="ohms-title" style="font-size: var(--fd-fs-panel);">FAULT MODEL CONFIG / 故障模型配置</span>
            <span class="ohms-dim" style="margin-left: 12px; font-size: var(--fd-fs-body);">{{ modelTotal }} 条</span>
          </div>
          <el-table :data="faultModel" size="small" max-height="340" v-loading="loadingModel">
            <el-table-column prop="member_system" label="成员系统" width="110" />
            <el-table-column prop="fault_code" label="故障代码" width="140" />
            <el-table-column prop="fault_text" label="故障描述" min-width="180" show-overflow-tooltip />
            <el-table-column label="级联父故障" width="150">
              <template #default="{ row }"><span class="ohms-dim">{{ row.cascade_parents?.join(', ') || '--' }}</span></template>
            </el-table-column>
            <el-table-column label="FDE" width="110">
              <template #default="{ row }"><span v-if="row.fde_code" class="ohms-yellow">{{ row.fde_code }}</span><span v-else class="ohms-dim">--</span></template>
            </el-table-column>
            <el-table-column label="失效报告" min-width="160" show-overflow-tooltip>
              <template #default="{ row }">{{ row.failure_codes?.join(', ') }}</template>
            </el-table-column>
            <el-table-column prop="lru_code" label="LRU" width="120" />
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { on, off } = useWebSocket()

const memberList = ref([])
const memberTotal = ref(0)
const selectedMember = ref(null)
const selectedMemberInfo = ref(null)
const memberFaultCodes = ref([])
const injectFaultCode = ref(null)
const injectSeverity = ref('minor')
const injectDuration = ref(0)
const injecting = ref(false)
const injectResult = ref('')

const currentSegment = ref(0)

const activeTab = ref('faults')
const faults = ref([])
const faultTotal = ref(0)
const loadingFaults = ref(false)
const filterMember = ref('')

const failures = ref([])
const failureTotal = ref(0)
const loadingFailures = ref(false)
const failureCode = ref('')
const rootResult = ref(null)

const historySegment = ref(0)
const historyFailures = ref([])
const loadingHistory = ref(false)

const fdeList = ref([])
const fdeTotal = ref(0)
const loadingFde = ref(false)

const lruReports = ref([])
const lruTotal = ref(0)
const loadingLru = ref(false)

const faultModel = ref([])
const modelTotal = ref(0)
const loadingModel = ref(false)

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const fetchMembers = () => {
  fetch(api('/api/v1/fault/members'))
    .then(r => r.json())
    .then(data => {
      memberList.value = data.memberList || []
      memberTotal.value = data.total || 0
    })
}

const onMemberChange = () => {
  selectedMemberInfo.value = memberList.value.find(m => m.memberSystem === selectedMember.value) || null
  injectFaultCode.value = null
  memberFaultCodes.value = []
  if (selectedMember.value) {
    fetch(api(`/api/v1/fault/model?member=${selectedMember.value}&size=200`))
      .then(r => r.json())
      .then(data => { memberFaultCodes.value = data.items || [] })
  }
}

const toggleFailure = (enabled) => {
  if (!selectedMember.value) return
  fetch(api(`/api/v1/fault/member-failure-enable?member=${selectedMember.value}&enabled=${enabled}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success(`${selectedMember.value} 失效报告已${enabled ? '启用' : '禁用'}`)
        fetchMembers()
        onMemberChange()
      }
    })
}

const injectFault = () => {
  if (!injectFaultCode.value) return
  injecting.value = true
  injectResult.value = ''
  fetch(api('/api/v1/fault/inject'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      member: selectedMember.value,
      fault_code: injectFaultCode.value,
      severity: injectSeverity.value,
      duration: injectDuration.value,
      segment: currentSegment.value,
    }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        const dur = data.duration ? ` (持续 ${data.duration}s)` : ''
        injectResult.value = `已注入 ${injectFaultCode.value}${dur}，${data.suppressed ? '已被级联过滤' : '生成 ' + (data.failure_codes?.length || 0) + ' 条失效报告'}`
        ElMessage.success('故障已注入')
        fetchFaults()
        fetchFailures()
      } else {
        ElMessage.error(data.message || '注入失败')
      }
    })
    .finally(() => { injecting.value = false })
}

const fetchFaults = () => {
  loadingFaults.value = true
  let url = `/api/v1/fault/reports?page=1&size=100`
  if (filterMember.value) url += `&member=${filterMember.value}`
  fetch(api(url))
    .then(r => r.json())
    .then(data => {
      faults.value = data.items || []
      faultTotal.value = data.total || 0
    })
    .finally(() => { loadingFaults.value = false })
}

const fetchFailures = () => {
  loadingFailures.value = true
  fetch(api('/api/v1/fault/failures?page=1&size=100'))
    .then(r => r.json())
    .then(data => {
      failures.value = data.items || []
      failureTotal.value = data.total || 0
    })
    .finally(() => { loadingFailures.value = false })
}

const fetchHistory = () => {
  loadingHistory.value = true
  fetch(api(`/api/v1/fault/failures/history/${historySegment.value}`))
    .then(r => r.json())
    .then(data => {
      historyFailures.value = data.items || []
      if (data.message) ElMessage.warning(data.message)
    })
    .finally(() => { loadingHistory.value = false })
}

const fetchFde = () => {
  loadingFde.value = true
  fetch(api('/api/v1/fault/fde?active_only=true'))
    .then(r => r.json())
    .then(data => {
      fdeList.value = data.items || []
      fdeTotal.value = data.total || 0
    })
    .finally(() => { loadingFde.value = false })
}

const fetchLru = () => {
  loadingLru.value = true
  fetch(api('/api/v1/fault/lru-reports'))
    .then(r => r.json())
    .then(data => {
      lruReports.value = data.items || []
      lruTotal.value = data.total || 0
    })
    .finally(() => { loadingLru.value = false })
}

const fetchModel = () => {
  loadingModel.value = true
  fetch(api('/api/v1/fault/model?page=1&size=200'))
    .then(r => r.json())
    .then(data => {
      faultModel.value = data.items || []
      modelTotal.value = data.total || 0
    })
    .finally(() => { loadingModel.value = false })
}

const locateRoot = () => {
  if (!failureCode.value.trim()) {
    ElMessage.warning('请输入失效报告代码')
    return
  }
  fetch(api(`/api/v1/fault/locate-root?failure_code=${encodeURIComponent(failureCode.value.trim())}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      rootResult.value = data
    })
}

const resolveFault = (id) => {
  fetch(api(`/api/v1/fault/${id}/resolve`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success('故障已解决')
        fetchFaults()
        fetchFde()
      }
    })
}

const refreshAll = () => {
  fetchFaults()
  fetchFailures()
  fetchFde()
  fetchLru()
  fetchHistory()
}

const severityClass = (s) => ({ 'ohms-red': s === 'critical', 'ohms-yellow': s === 'major', 'ohms-dim': s === 'minor' })
const severityText = (s) => ({ critical: '严重', major: '主要', minor: '轻微' }[s] || s)
const statusClass = (s) => ({ 'ohms-red': s === 'active', 'ohms-yellow': s === 'suppressed', 'ohms-green': s === 'resolved' }[s] || 'ohms-dim')
const statusText = (s) => ({ active: '激活', suppressed: '已过滤', resolved: '已解决' }[s] || s)
const formatTime = (t) => t ? new Date(t).toLocaleString() : '--'

const onFaultChanged = () => { fetchFaults(); fetchFailures(); fetchFde(); fetchLru() }

onMounted(() => {
  fetchMembers()
  fetchFaults()
  fetchFailures()
  fetchFde()
  fetchLru()
  fetchModel()
  on('fault_new', onFaultChanged)
  on('fault_resolved', onFaultChanged)
})

onUnmounted(() => {
  off('fault_new', onFaultChanged)
  off('fault_resolved', onFaultChanged)
})
</script>

<style scoped>
.fd-container {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  overflow-y: auto;
  padding: 4px;
  --fd-fs-title: 13px;
  --fd-fs-panel: 12px;
  --fd-fs-body: 11px;
  --fd-fs-aux: 10px;
}

.fd-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
}

.fd-control-panel {
  padding: 0;
}

.fd-panel-header {
  padding: 8px 12px;
  border-bottom: 1px solid #555;
  display: flex;
  align-items: center;
  background: #1f1f1f;
}

.fd-control-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 14px;
  flex-wrap: wrap;
}

.fd-tab-panel {
  padding: 0;
  margin-bottom: 4px;
}

.fd-two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.fd-root-result {
  padding: 10px 14px;
  border-top: 1px solid #333;
  font-size: var(--fd-fs-body);
}

.ohms-title {
  text-transform: none;
}
</style>
