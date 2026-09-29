<template>
  <div class="pm-container">
    <!-- 顶部工具栏 -->
    <div class="ohms-panel pm-toolbar">
      <span class="ohms-title" style="font-size: var(--pm-fs-title);">PARAMETER MONITORING / 参数监控</span>
      <span class="ohms-dim" style="margin-left: 12px; font-size: var(--pm-fs-body);">{{ paramList.length }} 个参数</span>
      <span style="flex: 1;"></span>
      <span class="ohms-dim" style="font-size: var(--pm-fs-aux);">ATA章节:</span>
      <el-select v-model="selectedAta" size="small" clearable placeholder="全部" style="width: 150px;" @change="onAtaChange">
        <el-option v-for="a in ataList" :key="a.ata" :label="`ATA ${a.ata} (${a.paramCount})`" :value="a.ata" />
      </el-select>
      <el-button size="small" @click="fetchReport" :loading="reportLoading">获取参数报告</el-button>
      <el-button size="small" @click="downloadReport" :disabled="!report.reportId" :loading="downloadLoading">下传</el-button>
    </div>

    <!-- 成员系统监控服务 -->
    <div class="ohms-panel pm-member-panel">
      <span class="ohms-label">MONITOR SERVICE / 监控服务</span>
      <el-select v-model="selectedMember" size="small" filterable placeholder="选择成员系统" style="width: 280px;">
        <el-option v-for="m in memberList" :key="m.memberSystem" :label="`${m.memberSystem} - ${m.memberName}`" :value="m.memberSystem" />
      </el-select>
      <el-button size="small" type="primary" @click="toggleService(true)" :disabled="!selectedMember">启动监控服务</el-button>
      <el-button size="small" @click="toggleService(false)" :disabled="!selectedMember">禁用监控服务</el-button>
      <span v-if="memberStatus" class="ohms-cyan" style="font-size: var(--pm-fs-body);">{{ memberStatus }}</span>
    </div>

    <!-- 参数查询与选择 (按 ATA 查询 + 勾选保存为快捷列表) -->
    <div class="ohms-panel pm-query-panel">
      <div class="pm-panel-header">
        <span class="ohms-title" style="font-size: var(--pm-fs-panel);">PARAMETER QUERY / 参数查询与选择</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: var(--pm-fs-body);">{{ paramList.length }} 个结果</span>
        <span style="flex: 1;"></span>
        <input v-model="saveListName" class="pm-input pm-input-sm" placeholder="快捷列表名称" />
        <el-button
          size="small"
          type="primary"
          @click="saveSelectedAsList"
          :disabled="selectedParams.length === 0 || !saveListName.trim()"
        >
          保存选中为快捷列表 ({{ selectedParams.length }})
        </el-button>
      </div>
      <el-table
        :data="paramList"
        ref="paramTableRef"
        size="small"
        max-height="320"
        :row-key="(row) => row.name"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="40" :reserve-selection="true" />
        <el-table-column prop="name" label="参数名称" min-width="160" />
        <el-table-column prop="type" label="参数类型" width="90" />
        <el-table-column prop="unit" label="参数单位" width="90" />
        <el-table-column prop="ata" label="参数所属ATA" width="110" />
        <el-table-column label="参数数值" width="120">
          <template #default="{ row }">{{ formatValue(row) }}</template>
        </el-table-column>
        <el-table-column label="有效性" width="120">
          <template #default="{ row }">
            <span :class="validityClass(row)">{{ validityText(row.validity) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 实时参数网格 -->
    <div class="ohms-panel pm-grid-panel">
      <div class="pm-panel-header">
        <span class="ohms-title" style="font-size: var(--pm-fs-panel);">REALTIME PARAMETERS (1Hz) / 实时参数 (1Hz)</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: var(--pm-fs-body);">
          {{ Object.keys(realtimeParams).length }} 个已显示
        </span>
      </div>
      <div class="pm-grid">
        <div v-for="(p, name) in realtimeParams" :key="name" class="pm-card" :class="validityBorder(p)">
          <div class="pm-card-name">{{ name }}</div>
          <div class="pm-card-value" :class="validityClass(p)">
            {{ formatValue(p) }}
            <span class="pm-card-unit">{{ p?.unit }}</span>
          </div>
          <div class="pm-card-meta">
            ATA {{ p?.ata }} · <span :class="validityClass(p)">{{ validityText(p?.validity) }}</span>
          </div>
        </div>
        <div v-if="Object.keys(realtimeParams).length === 0" class="pm-empty">无参数数据 (等待推送...)</div>
      </div>
    </div>

    <!-- 参数报告 -->
    <div v-if="report.reportId" class="ohms-panel pm-report-panel">
      <div class="pm-panel-header">
        <span class="ohms-title" style="font-size: var(--pm-fs-panel);">PARAMETER REPORT / 参数报告</span>
        <span class="ohms-cyan" style="margin-left: 12px; font-size: var(--pm-fs-body);">{{ report.reportId }}</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: var(--pm-fs-body);">{{ report.paramCount }} 个参数</span>
        <span style="flex: 1;"></span>
        <span v-if="downloadResult" class="ohms-green" style="font-size: var(--pm-fs-aux);">{{ downloadResult }}</span>
      </div>
      <el-table :data="report.params" size="small" max-height="280">
        <el-table-column prop="paramName" label="参数名称" min-width="150" />
        <el-table-column prop="paramType" label="参数类型" width="90" />
        <el-table-column label="参数数值" width="120">
          <template #default="{ row }">{{ row.paramValue }}</template>
        </el-table-column>
        <el-table-column prop="paramUnit" label="参数单位" width="90" />
        <el-table-column prop="ata" label="参数所属ATA" width="110" />
        <el-table-column label="有效性" width="110">
          <template #default="{ row }">
            <span :class="validityClass(row)">{{ validityText(row.validity) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="参数记录时间" min-width="170">
          <template #default="{ row }">{{ formatTime(row.recordTime) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 快捷访问列表 -->
    <div class="ohms-panel pm-quicklist-panel">
      <div class="pm-panel-header">
        <span class="ohms-title" style="font-size: var(--pm-fs-panel);">QUICK ACCESS LISTS / 快捷访问列表</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: var(--pm-fs-body);">{{ quickLists.length }} 张列表</span>
      </div>
      <div class="pm-ql-body">
        <!-- 左侧: 列表管理 -->
        <div class="pm-ql-left">
          <div class="pm-ql-create">
            <input v-model="newListName" class="pm-input" placeholder="新建列表名称" />
            <el-button size="small" @click="createList">创建</el-button>
          </div>
          <div class="pm-ql-list">
            <div
              v-for="ql in quickLists"
              :key="ql.id"
              class="pm-ql-item"
              :class="{ active: selectedListId === ql.id }"
              @click="selectList(ql.id)"
            >
              <span class="pm-ql-name">{{ ql.name }}</span>
              <span class="pm-ql-count">{{ ql.params.length }}</span>
            </div>
            <div v-if="quickLists.length === 0" class="pm-empty">暂无快捷访问列表</div>
          </div>
          <div v-if="selectedListId" class="pm-ql-actions">
            <el-button size="small" @click="deleteList">删除列表</el-button>
          </div>
        </div>
        <!-- 右侧: 选中列表参数 -->
        <div class="pm-ql-right">
          <div class="pm-ql-right-header">
            <span class="ohms-dim" style="font-size: var(--pm-fs-body);">列表参数</span>
            <span style="flex: 1;"></span>
            <el-select v-model="addParamName" size="small" filterable placeholder="添加参数" style="width: 220px;">
              <el-option v-for="p in paramList" :key="p.name" :label="`${p.name} (${p.ata})`" :value="p.name" />
            </el-select>
            <el-button size="small" @click="addParamToList" :disabled="!selectedListId || !addParamName">添加</el-button>
          </div>
          <div class="pm-ql-params">
            <div v-for="pn in selectedListParams" :key="pn" class="pm-ql-param-row">
              <span class="pm-ql-param-name">{{ pn }}</span>
              <span class="ohms-dim" style="font-size: var(--pm-fs-aux);">{{ paramMap[pn]?.ata }}</span>
              <span style="flex: 1;"></span>
              <button class="pm-del-btn" @click="removeParamFromList(pn)">×</button>
            </div>
            <div v-if="selectedListParams.length === 0" class="pm-empty">请从左侧选择列表, 或添加参数</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { connected: wsConnected, on } = useWebSocket()

const realtimeParams = ref({})
const paramList = ref([])
const ataList = ref([])
const selectedAta = ref(null)

const report = ref({})
const reportLoading = ref(false)
const downloadLoading = ref(false)
const downloadResult = ref('')

const memberList = ref([])
const selectedMember = ref(null)
const memberStatus = ref('')

const quickLists = ref([])
const selectedListId = ref(null)
const selectedListParams = ref([])
const newListName = ref('')
const addParamName = ref(null)

// 参数查询与选择
const selectedParams = ref([])
const saveListName = ref('')
const paramTableRef = ref(null)

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const paramMap = computed(() => {
  const m = {}
  paramList.value.forEach(p => { m[p.name] = p })
  return m
})

const fetchParamList = (ata = null) => {
  const q = ata ? `?ata=${ata}` : ''
  fetch(api(`/api/v1/params/list${q}`))
    .then(r => r.json())
    .then(data => { paramList.value = data.items || [] })
}

const fetchAtas = () => {
  fetch(api('/api/v1/params/atas'))
    .then(r => r.json())
    .then(data => { ataList.value = data.items || [] })
}

const fetchMembers = () => {
  fetch(api('/api/v1/params/monitor-services'))
    .then(r => r.json())
    .then(data => { memberList.value = data.memberList || [] })
}

const fetchQuickLists = () => {
  fetch(api('/api/v1/params/quicklists'))
    .then(r => r.json())
    .then(data => {
      quickLists.value = data.items || []
      if (selectedListId.value) selectList(selectedListId.value, true)
    })
}

const selectList = (id, skipFetch = false) => {
  selectedListId.value = id
  const ql = quickLists.value.find(q => q.id === id)
  selectedListParams.value = ql ? [...ql.params] : []
}

const onAtaChange = (ata) => {
  // ata 为 "21" 或 null, 后端按前缀匹配
  fetchParamList(ata)
}

const fetchReport = () => {
  reportLoading.value = true
  const q = selectedAta.value ? `?ata=${selectedAta.value}` : ''
  fetch(api(`/api/v1/params/report${q}`))
    .then(r => r.json())
    .then(data => {
      report.value = data
      ElMessage.success(`参数报告 ${data.reportId}: ${data.paramCount} 个参数`)
    })
    .finally(() => { reportLoading.value = false })
}

const downloadReport = () => {
  if (!report.value.reportId) return
  downloadLoading.value = true
  downloadResult.value = ''
  fetch(api('/api/v1/params/report/download'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ report_id: report.value.reportId }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        downloadResult.value = `已下传 → ${data.file_path}`
        ElMessage.success('参数报告已下传')
      } else {
        ElMessage.error(data.message || '下传失败')
      }
    })
    .finally(() => { downloadLoading.value = false })
}

const toggleService = (enabled) => {
  if (!selectedMember.value) return
  fetch(api(`/api/v1/params/monitor-service?member_system=${selectedMember.value}&enabled=${enabled}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      memberStatus.value = `${data.member_system} 监控服务已${data.enabled ? '启动' : '禁用'}`
      ElMessage.success(memberStatus.value)
      fetchMembers()
    })
}

const createList = () => {
  if (!newListName.value.trim()) {
    ElMessage.warning('请输入列表名称')
    return
  }
  fetch(api('/api/v1/params/quicklists'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: newListName.value.trim(), params: [] }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        newListName.value = ''
        ElMessage.success(`快捷列表已创建 (ID=${data.list_id})`)
        fetchQuickLists()
      }
    })
}

const deleteList = () => {
  if (!selectedListId.value) return
  fetch(api(`/api/v1/params/quicklists/${selectedListId.value}`), { method: 'DELETE' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        selectedListId.value = null
        selectedListParams.value = []
        ElMessage.success('快捷列表已删除')
        fetchQuickLists()
      } else {
        ElMessage.error(data.message)
      }
    })
}

const addParamToList = () => {
  if (!selectedListId.value || !addParamName.value) return
  fetch(api(`/api/v1/params/quicklists/${selectedListId.value}/params/${addParamName.value}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        selectedListParams.value = data.params || []
        addParamName.value = null
        fetchQuickLists()
      } else {
        ElMessage.error(data.message)
      }
    })
}

const removeParamFromList = (name) => {
  if (!selectedListId.value) return
  fetch(api(`/api/v1/params/quicklists/${selectedListId.value}/params/${name}`), { method: 'DELETE' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        selectedListParams.value = data.params || []
        fetchQuickLists()
      }
    })
}

// 参数查询面板: 勾选变化
const onSelectionChange = (rows) => {
  selectedParams.value = rows
}

// 将勾选的参数保存为快捷访问列表
const saveSelectedAsList = () => {
  const name = saveListName.value.trim()
  if (!name) {
    ElMessage.warning('请输入快捷列表名称')
    return
  }
  if (selectedParams.value.length === 0) {
    ElMessage.warning('请先勾选要保存的参数')
    return
  }
  const params = selectedParams.value.map(r => r.name)
  fetch(api('/api/v1/params/quicklists'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, params }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        saveListName.value = ''
        ElMessage.success(`快捷列表「${name}」已保存 (${params.length} 个参数)`)
        fetchQuickLists()
      } else {
        ElMessage.error(data.message || '保存失败')
      }
    })
    .catch(() => { ElMessage.error('Network error') })
}

const formatValue = (p) => {
  if (p?.value === undefined || p?.value === null) return '--'
  if (typeof p.value === 'number') return p.value.toFixed(p.value % 1 === 0 ? 0 : 2)
  return p.value
}

const validityClass = (p) => ({
  'ohms-red': p?.validity === 'out_of_range',
  'ohms-yellow': p?.validity === 'invalid',
  'ohms-dim': p?.validity === 'unavailable',
  'ohms-cyan': !p?.validity || p?.validity === 'valid',
})

const validityBorder = (p) => ({
  'pm-border-warn': p?.validity && p.validity !== 'valid',
})

const validityText = (v) => {
  const map = {
    valid: 'Valid',
    unavailable: 'unavailable',
    out_of_range: 'Out of Range',
    invalid: 'Invalid',
  }
  return map[v] || 'Valid'
}

const formatTime = (t) => t ? new Date(t).toLocaleString() : '--'

onMounted(() => {
  fetchParamList()
  fetchAtas()
  fetchMembers()
  fetchQuickLists()

  on('param_update', (data) => {
    realtimeParams.value = data
  })
})
</script>

<style scoped>
/* 标题含单位(如 1Hz), 关闭全局 uppercase 避免 Hz→HZ */
.ohms-title {
  text-transform: none;
}

.pm-container {
  /* 统一字号层级: 标题 > 面板标题 > 正文/标签 > 辅助 > (数值单独) */
  --pm-fs-title: 13px;   /* 页面主标题 */
  --pm-fs-panel: 12px;   /* 面板标题 */
  --pm-fs-body: 11px;    /* 正文 / 列表 / 按钮上下文 */
  --pm-fs-aux: 10px;     /* 辅助 / 元信息 */
  --pm-fs-value: 16px;   /* 参数数值 (强调) */
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  overflow-y: auto;
  padding: 4px;
}

.pm-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
}

.pm-member-panel {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
}

.pm-grid-panel,
.pm-query-panel,
.pm-report-panel,
.pm-quicklist-panel {
  padding: 0;
}

.pm-panel-header {
  padding: 8px 12px;
  border-bottom: 1px solid #555;
  display: flex;
  align-items: center;
  background: #1f1f1f;
}

.pm-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 8px;
  padding: 10px;
  max-height: 300px;
  overflow-y: auto;
}

.pm-card {
  background: #000;
  border: 1px solid #666;
  padding: 8px;
  text-align: center;
}

.pm-card.pm-border-warn {
  border-color: #ff8800;
}

.pm-card-name {
  color: #bbb;
  font-size: var(--pm-fs-body);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pm-card-value {
  font-size: var(--pm-fs-value);
  font-weight: bold;
  margin: 4px 0;
  color: #00ffff;
}

.pm-card-unit {
  font-size: var(--pm-fs-aux);
  color: #888;
}

.pm-card-meta {
  font-size: var(--pm-fs-aux);
  color: #888;
}

.pm-empty {
  padding: 20px;
  text-align: center;
  color: #555;
  font-size: var(--pm-fs-body);
  grid-column: 1 / -1;
}

.pm-ql-body {
  display: flex;
  gap: 0;
  min-height: 220px;
}

.pm-ql-left {
  width: 300px;
  border-right: 1px solid #444;
  display: flex;
  flex-direction: column;
}

.pm-ql-create {
  display: flex;
  gap: 6px;
  padding: 8px 10px;
  border-bottom: 1px solid #333;
}

.pm-input {
  flex: 1;
  background: #000;
  border: 1px solid #666;
  color: #00ff00;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 12px;
  padding: 5px 8px;
}

.pm-input:focus {
  outline: none;
  border-color: #00ccff;
}

.pm-input-sm {
  width: 180px;
  flex: none;
}

.pm-ql-list {
  flex: 1;
  overflow-y: auto;
}

.pm-ql-item {
  display: flex;
  align-items: center;
  padding: 6px 12px;
  cursor: pointer;
  border-bottom: 1px solid #222;
}

.pm-ql-item:hover {
  background: #1a1a1a;
}

.pm-ql-item.active {
  background: #444;
}

.pm-ql-name {
  flex: 1;
  color: #ddd;
  font-size: var(--pm-fs-body);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pm-ql-count {
  color: #00ffff;
  font-size: var(--pm-fs-aux);
}

.pm-ql-actions {
  padding: 8px 10px;
  border-top: 1px solid #333;
}

.pm-ql-right {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.pm-ql-right-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid #333;
}

.pm-ql-params {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}

.pm-ql-param-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 12px;
  border-bottom: 1px solid #222;
  font-size: var(--pm-fs-body);
}

.pm-ql-param-name {
  color: #00ffff;
}

.pm-del-btn {
  background: #3a0000;
  color: #ff3333;
  border: 1px solid #5a0000;
  width: 20px;
  height: 20px;
  line-height: 18px;
  text-align: center;
  cursor: pointer;
  font-size: 12px;
  padding: 0;
}

.pm-del-btn:hover {
  background: #5a0000;
}
</style>
