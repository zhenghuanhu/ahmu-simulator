<template>
  <div class="pm-container">
    <!-- 顶部工具栏 -->
    <div class="ohms-panel pm-toolbar">
      <span class="ohms-title" style="font-size: var(--pm-fs-title);">PARAMETER MONITORING / 参数监控</span>
      <span class="ohms-dim" style="margin-left: 12px; font-size: var(--pm-fs-body);">{{ paramList.length }} 个参数</span>
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

    <!-- 监控服务运行状态 -->
    <div class="ohms-panel pm-monitor-status-panel">
      <div class="pm-panel-header">
        <span class="ohms-title" style="font-size: var(--pm-fs-panel);">MONITOR SERVICE STATUS / 监控服务运行状态</span>
        <span style="flex: 1;"></span>
        <span class="pm-status-summary">
          已启用 <b class="ohms-cyan">{{ enabledCount }}</b> / {{ memberList.length }} 个成员系统
        </span>
        <span class="pm-status-summary" :class="simulationEnabled ? 'ohms-yellow' : 'ohms-dim'">
          模拟数据 {{ simulationEnabled ? 'ON' : 'OFF' }}
        </span>
      </div>
      <div class="pm-monitor-status-body">
        <template v-if="enabledMembers.length > 0">
          <div v-for="m in enabledMembers" :key="m.memberSystem" class="pm-monitor-item">
            <span class="pm-monitor-dot"></span>
            <span class="pm-monitor-code">{{ m.memberSystem }}</span>
            <span class="pm-monitor-name">{{ m.memberName }}</span>
            <span class="pm-monitor-tag">运行中</span>
            <span class="pm-monitor-meta">参数 {{ m.paramCount }} · 真实 {{ m.realCount }} · 模拟 {{ m.simCount }}</span>
            <span class="pm-monitor-meta">最近更新 {{ formatTime(m.lastUpdate) }}</span>
          </div>
        </template>
        <div v-else class="pm-empty">尚未启动任何成员系统监控服务，请在上方选择成员系统并点击「启动监控服务」</div>
      </div>
    </div>

    <!-- 参数查询与选择 (按 ATA 查询 + 勾选保存为快捷列表) -->
    <div class="ohms-panel pm-query-panel">
      <div class="pm-panel-header">
        <span class="ohms-title" style="font-size: var(--pm-fs-panel);">PARAMETER QUERY / 参数查询与选择</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: var(--pm-fs-body);">{{ paramList.length }} 个结果</span>
        <span style="flex: 1;"></span>
        <span class="ohms-dim" style="font-size: var(--pm-fs-aux);">ATA章节:</span>
        <el-select v-model="selectedAta" size="small" clearable placeholder="全部" style="width: 150px;" @change="onAtaChange">
          <el-option v-for="a in ataList" :key="a.ata" :label="`ATA ${a.ata} (${a.paramCount})`" :value="a.ata" />
        </el-select>
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

    <!-- 参数配置管理 (供应商配置工具) -->
    <div class="ohms-panel pm-config-panel">
      <div class="pm-panel-header">
        <span class="ohms-title" style="font-size: var(--pm-fs-panel);">PARAMETER CONFIG / 参数配置管理</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: var(--pm-fs-body);">{{ configTotal }} 个参数</span>
        <span style="flex: 1;"></span>
        <el-select v-model="configFilterRecorded" size="small" clearable placeholder="是否记录" style="width: 110px;" @change="onConfigFilterChange">
          <el-option label="已记录" :value="true" />
          <el-option label="未记录" :value="false" />
        </el-select>
        <el-input
          v-model="configKeyword"
          size="small"
          placeholder="搜索参数名称"
          clearable
          style="width: 170px;"
          @keyup.enter="onConfigFilterChange"
          @clear="onConfigFilterChange"
        />
        <el-button size="small" @click="onConfigFilterChange">搜索</el-button>
        <el-button size="small" @click="exportConfig">导出配置</el-button>
      </div>
      <el-table :data="configList" size="small" max-height="280" v-loading="configLoading">
        <el-table-column prop="name" label="参数名称" min-width="170" show-overflow-tooltip />
        <el-table-column prop="type" label="类型" width="80" />
        <el-table-column label="采样频率" width="90">
          <template #default="{ row }">{{ row.sample_rate }} Hz</template>
        </el-table-column>
        <el-table-column prop="sample_precision" label="采样精度" width="90" />
        <el-table-column prop="unit" label="单位" width="90" />
        <el-table-column label="是否记录" width="90">
          <template #default="{ row }">
            <span :class="row.is_recorded ? 'ohms-green' : 'ohms-dim'">{{ row.is_recorded ? '记录' : '不记录' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="是否显示" width="90">
          <template #default="{ row }">
            <span :class="row.is_displayed ? 'ohms-green' : 'ohms-dim'">{{ row.is_displayed ? '显示' : '不显示' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="config_source" label="来源" width="80" />
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button link size="small" type="primary" @click="openConfigEdit(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 参数配置编辑抽屉 -->
    <el-drawer v-model="configEditVisible" title="编辑参数配置" size="40%" direction="rtl">
      <div v-if="configEditForm" class="pm-config-form">
        <div class="pm-config-section">
          <div class="pm-config-section-title">基础配置项</div>
          <div class="pm-config-field">
            <span class="pm-config-label">参数名称</span>
            <span class="ohms-cyan">{{ configEditForm.name }}</span>
          </div>
          <div class="pm-config-field">
            <span class="pm-config-label">参数类型</span>
            <el-select v-model="configEditForm.type" size="small" style="width: 160px;">
              <el-option label="float" value="float" />
              <el-option label="int" value="int" />
              <el-option label="bool" value="bool" />
              <el-option label="string" value="string" />
            </el-select>
          </div>
          <div class="pm-config-field">
            <span class="pm-config-label">采样频率(Hz)</span>
            <el-input-number v-model="configEditForm.sample_rate" size="small" :min="1" :max="10" :step="1" />
          </div>
          <div class="pm-config-field">
            <span class="pm-config-label">采样精度</span>
            <el-input-number v-model="configEditForm.sample_precision" size="small" :min="0" :max="6" :step="1" />
          </div>
          <div class="pm-config-field">
            <span class="pm-config-label">参数单位</span>
            <el-input v-model="configEditForm.unit" size="small" style="width: 160px;" />
          </div>
          <div class="pm-config-field">
            <span class="pm-config-label">是否记录</span>
            <el-switch v-model="configEditForm.is_recorded" />
          </div>
          <div class="pm-config-field">
            <span class="pm-config-label">是否显示</span>
            <el-switch v-model="configEditForm.is_displayed" />
          </div>
        </div>
        <div v-if="configEditForm.is_recorded" class="pm-config-section">
          <div class="pm-config-section-title">记录配置项（是否记录开启时生效）</div>
          <div class="pm-config-field">
            <span class="pm-config-label">记录精度</span>
            <el-input-number v-model="configEditForm.record_precision" size="small" :min="0" :max="6" :step="1" />
          </div>
          <div class="pm-config-field">
            <span class="pm-config-label">记录频率(Hz)</span>
            <el-input-number v-model="configEditForm.record_rate" size="small" :min="1" :max="10" :step="1" />
          </div>
          <div class="pm-config-field">
            <span class="pm-config-label">开始记录逻辑</span>
            <el-input v-model="configEditForm.record_start_logic" size="small" style="width: 220px;" />
          </div>
          <div class="pm-config-field">
            <span class="pm-config-label">结束记录逻辑</span>
            <el-input v-model="configEditForm.record_stop_logic" size="small" style="width: 220px;" />
          </div>
        </div>
        <div class="pm-config-actions">
          <el-button size="small" type="primary" @click="saveConfigEdit" :loading="configSaving">保存</el-button>
          <el-button size="small" @click="configEditVisible = false">取消</el-button>
        </div>
      </div>
    </el-drawer>

    <!-- 实时参数网格 -->
    <div class="ohms-panel pm-grid-panel">
      <div class="pm-panel-header">
        <span class="ohms-title" style="font-size: var(--pm-fs-panel);">REALTIME PARAMETERS / 实时参数</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: var(--pm-fs-body);">
          {{ Object.keys(realtimeParams).length }} 个参数（真实 / 模拟来源见卡片标注）
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
            <span v-if="p?.data_source === 'real'" class="pm-source-badge">真实数据</span>
            <span v-else-if="p?.data_source === 'simulated'" class="pm-source-badge pm-source-badge-sim">模拟数据</span>
          </div>
        </div>
        <div v-if="Object.keys(realtimeParams).length === 0" class="pm-empty">
          {{ hasEnabledMember ? '监控已启动, 等待接收参数数据...' : '监控服务未启动, 请先启动成员系统监控服务' }}
        </div>
      </div>
    </div>

    <!-- 参数报告与历史记录 -->
    <div class="ohms-panel pm-report-panel">
      <div class="pm-panel-header">
        <span class="ohms-title" style="font-size: var(--pm-fs-panel);">PARAMETER REPORT &amp; HISTORY / 参数报告与历史记录</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: var(--pm-fs-body);">{{ historyTotal }} 条记录</span>
        <span style="flex: 1;"></span>
        <el-button size="small" type="primary" @click="fetchReport" :loading="reportLoading">获取参数报告</el-button>
        <el-button size="small" type="danger" plain @click="deleteAllReports" :disabled="historyTotal === 0" :loading="deletingReports">删除全部</el-button>
      </div>

      <!-- 筛选栏 -->
      <div class="pm-history-toolbar">
        <span class="ohms-dim" style="font-size: var(--pm-fs-aux);">时间范围:</span>
        <el-date-picker
          v-model="historyDateRange"
          type="daterange"
          size="small"
          value-format="YYYY-MM-DD"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          style="width: 240px;"
          @change="onHistoryFilterChange"
        />
        <el-input
          v-model="historyKeyword"
          size="small"
          placeholder="搜索报告名称 / 报告 ID"
          clearable
          style="width: 220px;"
          @keyup.enter="onHistoryFilterChange"
          @clear="onHistoryFilterChange"
        />
        <el-button size="small" @click="onHistoryFilterChange">搜索</el-button>
        <el-button size="small" @click="resetHistoryFilter">重置</el-button>
      </div>

      <!-- 历史列表 -->
      <el-table :data="historyList" size="small" max-height="280" v-loading="historyLoading">
        <el-table-column prop="reportName" label="报告名称" min-width="180" show-overflow-tooltip />
        <el-table-column label="获取时间" width="150">
          <template #default="{ row }">{{ formatTime(row.getTime) }}</template>
        </el-table-column>
        <el-table-column prop="summary" label="参数摘要" min-width="260" show-overflow-tooltip />
        <el-table-column label="获取状态" width="90">
          <template #default="{ row }">
            <span :class="row.status === 'success' ? 'ohms-green' : 'ohms-red'">{{ row.status === 'success' ? '成功' : '失败' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="150">
          <template #default="{ row }">
            <div class="pm-op-actions">
              <el-button link size="small" type="primary" @click="viewHistoryDetail(row)">查看</el-button>
              <el-button link size="small" @click="exportHistory(row)">导出</el-button>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <div class="pm-empty">暂无历史记录，点击「获取参数报告」生成并保存</div>
        </template>
      </el-table>

      <!-- 分页 -->
      <div class="pm-history-pager">
        <span class="ohms-dim" style="font-size: var(--pm-fs-aux);">共 {{ historyTotal }} 条</span>
        <span style="flex: 1;"></span>
        <el-pagination
          v-model:current-page="historyPage"
          :page-size="historySize"
          :total="historyTotal"
          layout="prev, pager, next"
          small
          @current-change="fetchHistory"
        />
      </div>
    </div>

    <!-- 参数报告详情抽屉 -->
    <el-drawer v-model="detailVisible" title="参数报告详情" size="55%" direction="rtl">
      <div v-if="detailReport" class="pm-detail">
        <div class="pm-detail-head">
          <div class="pm-detail-row"><span class="pm-detail-label">报告名称</span><span>{{ detailReport.reportName }}</span></div>
          <div class="pm-detail-row"><span class="pm-detail-label">报告标识</span><span class="ohms-cyan">{{ detailReport.reportId }}</span></div>
          <div class="pm-detail-row"><span class="pm-detail-label">获取时间</span><span>{{ formatTime(detailReport.getTime || detailReport.generatedAt) }}</span></div>
          <div class="pm-detail-row"><span class="pm-detail-label">参数摘要</span><span>{{ detailReport.summary }}</span></div>
          <div class="pm-detail-row"><span class="pm-detail-label">数据来源</span><span>{{ dataSourceText(detailReport.dataSource) }}</span></div>
        </div>
        <el-table :data="detailReport.params || []" size="small" max-height="420">
          <el-table-column prop="paramName" label="参数名称" min-width="150" />
          <el-table-column prop="paramType" label="类型" width="80" />
          <el-table-column label="数值" width="110">
            <template #default="{ row }">{{ row.paramValue ?? '--' }}</template>
          </el-table-column>
          <el-table-column prop="paramUnit" label="单位" width="80" />
          <el-table-column prop="ata" label="ATA" width="90" />
          <el-table-column label="有效性" width="110">
            <template #default="{ row }"><span :class="validityClass(row)">{{ validityText(row.validity) }}</span></template>
          </el-table-column>
          <el-table-column label="记录时间" min-width="150">
            <template #default="{ row }">{{ formatTime(row.recordTime) }}</template>
          </el-table-column>
        </el-table>
        <div class="pm-detail-actions">
          <el-button size="small" @click="exportDetail" :loading="exportLoading">重新导出</el-button>
        </div>
      </div>
    </el-drawer>

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
import { ElMessage, ElMessageBox } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { connected: wsConnected, on } = useWebSocket()

const realtimeParams = ref({})
const paramList = ref([])
const ataList = ref([])
const selectedAta = ref(null)

const report = ref({})
const reportLoading = ref(false)
const exportLoading = ref(false)
const deletingReports = ref(false)

// 参数报告历史记录
const historyList = ref([])
const historyTotal = ref(0)
const historyPage = ref(1)
const historySize = ref(20)
const historyKeyword = ref('')
const historyDateRange = ref(null)
const historyLoading = ref(false)

const detailVisible = ref(false)
const detailReport = ref(null)

const memberList = ref([])
const selectedMember = ref(null)
const memberStatus = ref('')
const simulationEnabled = ref(false)

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

// 是否存在已启动监控服务的成员系统 (用于空状态提示)
const hasEnabledMember = computed(() => memberList.value.some(m => m.enabled))

// 已启用监控服务的成员系统列表 (运行状态展示)
const enabledMembers = computed(() => memberList.value.filter(m => m.enabled))
const enabledCount = computed(() => enabledMembers.value.length)

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
    .then(data => {
      memberList.value = data.memberList || []
      if (data.summary) {
        simulationEnabled.value = !!data.summary.simulationEnabled
      }
    })
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
  const body = {}
  if (selectedAta.value) body.ata = selectedAta.value
  fetch(api('/api/v1/params/report'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
    .then(r => r.json())
    .then(data => {
      report.value = data
      ElMessage.success(`参数报告 ${data.reportId}: ${data.paramCount} 个参数（已保存历史）`)
      fetchHistory()
    })
    .catch(() => ElMessage.error('获取参数报告失败'))
    .finally(() => { reportLoading.value = false })
}

const fetchHistory = () => {
  historyLoading.value = true
  const params = new URLSearchParams()
  params.set('page', historyPage.value)
  params.set('size', historySize.value)
  if (historyDateRange.value && historyDateRange.value.length === 2) {
    params.set('start', historyDateRange.value[0])
    params.set('end', historyDateRange.value[1])
  }
  if (historyKeyword.value.trim()) {
    params.set('keyword', historyKeyword.value.trim())
  }
  fetch(api(`/api/v1/params/report/history?${params.toString()}`))
    .then(r => r.json())
    .then(data => {
      historyList.value = data.items || []
      historyTotal.value = data.total || 0
    })
    .catch(() => ElMessage.error('获取历史记录失败'))
    .finally(() => { historyLoading.value = false })
}

const onHistoryFilterChange = () => {
  historyPage.value = 1
  fetchHistory()
}

const resetHistoryFilter = () => {
  historyKeyword.value = ''
  historyDateRange.value = null
  historyPage.value = 1
  fetchHistory()
}

const viewHistoryDetail = (row) => {
  fetch(api(`/api/v1/params/report/history/${row.reportId}`))
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        detailReport.value = data.report
        detailVisible.value = true
      } else {
        ElMessage.error(data.message || '加载详情失败')
      }
    })
    .catch(() => ElMessage.error('加载详情失败'))
}

const exportHistory = (row) => {
  exportLoading.value = true
  fetch(api(`/api/v1/params/report/history/${row.reportId}/export`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success(`已导出 → ${data.file_path}`)
        fetchHistory()
      } else {
        ElMessage.error(data.message || '导出失败')
      }
    })
    .finally(() => { exportLoading.value = false })
}

const exportDetail = () => {
  if (!detailReport.value || !detailReport.value.reportId) return
  exportHistory({ reportId: detailReport.value.reportId })
}

const deleteAllReports = () => {
  ElMessageBox.confirm(
    `确定删除全部 ${historyTotal.value} 条参数报告历史记录？此操作不可恢复。`,
    '删除所有报告记录',
    { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' },
  )
    .then(() => {
      deletingReports.value = true
      fetch(api('/api/v1/params/report/history'), { method: 'DELETE' })
        .then(r => r.json())
        .then(data => {
          if (data.status === 'ok') {
            ElMessage.success(`已删除 ${data.deleted} 条报告记录`)
            historyPage.value = 1
            fetchHistory()
          } else {
            ElMessage.error(data.message || '删除失败')
          }
        })
        .catch(() => ElMessage.error('Network error'))
        .finally(() => { deletingReports.value = false })
    })
    .catch(() => { /* 用户取消 */ })
}

const dataSourceText = (src) => {
  if (src === 'real') return '真实数据'
  if (src === 'simulated') return '模拟数据'
  if (src === 'mixed') return '真实 + 模拟'
  return '—'
}

// ==================== 参数配置管理 ====================
const configList = ref([])
const configTotal = ref(0)
const configKeyword = ref('')
const configFilterRecorded = ref(null)
const configLoading = ref(false)

const configEditVisible = ref(false)
const configEditForm = ref(null)
const configSaving = ref(false)

const fetchConfigList = () => {
  configLoading.value = true
  const params = new URLSearchParams()
  params.set('page', 1)
  params.set('size', 200)
  if (configKeyword.value.trim()) {
    params.set('keyword', configKeyword.value.trim())
  }
  if (configFilterRecorded.value === true || configFilterRecorded.value === false) {
    params.set('recorded', configFilterRecorded.value)
  }
  fetch(api(`/api/v1/params/config?${params.toString()}`))
    .then(r => r.json())
    .then(data => {
      configList.value = data.items || []
      configTotal.value = data.total || 0
    })
    .catch(() => ElMessage.error('获取参数配置失败'))
    .finally(() => { configLoading.value = false })
}

const onConfigFilterChange = () => {
  fetchConfigList()
}

const openConfigEdit = (row) => {
  configEditForm.value = { ...row }
  configEditVisible.value = true
}

const saveConfigEdit = () => {
  const f = configEditForm.value
  if (!f || !f.name) return
  configSaving.value = true
  fetch(api(`/api/v1/params/config/${f.name}`), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      param_type: f.type,
      sample_rate: f.sample_rate,
      sample_precision: f.sample_precision,
      param_unit: f.unit,
      is_recorded: f.is_recorded,
      is_displayed: f.is_displayed,
      record_precision: f.record_precision,
      record_rate: f.record_rate,
      record_start_logic: f.record_start_logic,
      record_stop_logic: f.record_stop_logic,
      operator: 'TEST',
    }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success(`参数「${f.name}」配置已保存`)
        configEditVisible.value = false
        fetchConfigList()
        fetchParamList()
      } else {
        ElMessage.error(data.message || '保存失败')
      }
    })
    .catch(() => ElMessage.error('Network error'))
    .finally(() => { configSaving.value = false })
}

const exportConfig = () => {
  fetch(api('/api/v1/params/config/export'))
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success(`已导出 ${data.paramCount} 个参数配置 → ${data.file_path}`)
      } else {
        ElMessage.error(data.message || '导出失败')
      }
    })
    .catch(() => ElMessage.error('导出失败'))
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
  fetchHistory()
  fetchConfigList()

  on('param_update', (data) => {
    realtimeParams.value = data
    // 参数更新后同步刷新运行状态计数 (真实/模拟数量、最近更新时间)
    fetchMembers()
  })

  // 定时轮询监控服务运行状态 (含模拟数据开关状态), 保证跨页面配置联动一致
  setInterval(fetchMembers, 2000)
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

/* 监控服务运行状态 */
.pm-monitor-status-panel {
  padding: 0;
}

.pm-status-summary {
  font-size: var(--pm-fs-body);
  color: #bbb;
  margin-left: 14px;
}

.pm-status-summary b {
  font-size: 14px;
}

.pm-monitor-status-body {
  padding: 6px 0;
  max-height: 180px;
  overflow-y: auto;
}

.pm-monitor-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 16px;
  border-bottom: 1px solid #222;
  font-size: var(--pm-fs-body);
}

.pm-monitor-item:last-child {
  border-bottom: none;
}

.pm-monitor-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #00e68a;
  box-shadow: 0 0 6px #00e68a;
  flex: none;
}

.pm-monitor-code {
  color: #00ffff;
  font-weight: bold;
  min-width: 90px;
}

.pm-monitor-name {
  color: #ddd;
  min-width: 130px;
}

.pm-monitor-tag {
  display: inline-block;
  padding: 0 6px;
  border-radius: 2px;
  font-size: 10px;
  font-weight: bold;
  color: #00e68a;
  background: #0a3d2e;
  border: 1px solid #00b36b;
  flex: none;
}

.pm-monitor-meta {
  color: #888;
  font-size: var(--pm-fs-aux);
}

.pm-monitor-meta:last-child {
  margin-left: auto;
  color: #777;
}

.pm-grid-panel,
.pm-query-panel,
.pm-report-panel,
.pm-quicklist-panel,
.pm-config-panel {
  padding: 0;
}

.pm-panel-header {
  padding: 8px 12px;
  border-bottom: 1px solid #555;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  row-gap: 8px;
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

.pm-source-badge {
  display: inline-block;
  margin-left: 4px;
  padding: 0 5px;
  border-radius: 2px;
  font-size: 9px;
  font-weight: bold;
  color: #00e68a;
  background: #0a3d2e;
  border: 1px solid #00b36b;
  vertical-align: middle;
}

.pm-source-badge-sim {
  color: #ffd966;
  background: #3a2f0a;
  border: 1px solid #b38600;
}

/* 参数报告历史记录 */
.pm-op-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: nowrap;
}

.pm-history-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid #333;
}

.pm-history-pager {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-top: 1px solid #333;
}

.pm-detail {
  padding: 0 4px;
}

.pm-detail-head {
  border: 1px solid #444;
  padding: 12px;
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pm-detail-row {
  display: flex;
  gap: 12px;
  font-size: var(--pm-fs-body);
  color: #ddd;
}

.pm-detail-label {
  color: #888;
  min-width: 72px;
  flex: none;
}

.pm-detail-actions {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

/* 参数配置管理 */
.pm-config-form {
  padding: 0 4px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.pm-config-section {
  border: 1px solid #444;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.pm-config-section-title {
  color: #00ffff;
  font-size: var(--pm-fs-body);
  font-weight: bold;
  border-bottom: 1px solid #333;
  padding-bottom: 6px;
}

.pm-config-field {
  display: flex;
  align-items: center;
  gap: 12px;
}

.pm-config-label {
  color: #bbb;
  font-size: var(--pm-fs-body);
  min-width: 100px;
}

.pm-config-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
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
