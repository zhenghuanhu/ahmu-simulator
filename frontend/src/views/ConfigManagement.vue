<template>
  <div class="cm-container">
    <!-- 顶部工具栏 -->
    <div class="ohms-panel cm-toolbar">
      <span class="ohms-title" style="font-size: var(--cm-fs-title);">CONFIGURATION MANAGEMENT / 构型管理</span>
      <span class="ohms-dim" style="margin-left: 12px; font-size: var(--cm-fs-body);">{{ memberTotal }} 个成员系统</span>
      <span style="flex: 1;"></span>
      <span class="ohms-dim" style="font-size: var(--cm-fs-aux);">构型错误报告:</span>
      <span class="ohms-red" style="margin-left: 4px; font-size: var(--cm-fs-body);">{{ errorTotal }}</span>
      <el-button size="small" style="margin-left: 12px;" @click="fetchErrorReports">刷新</el-button>
    </div>

    <!-- 成员系统请求区 -->
    <div class="ohms-panel cm-member-panel">
      <span class="ohms-label">MEMBER SYSTEM / 成员系统</span>
      <el-select
        v-model="selectedMember"
        size="small"
        filterable
        placeholder="选择成员系统 (如 HF_HSCU)"
        style="width: 320px;"
        @change="onMemberChange"
      >
        <el-option
          v-for="m in memberList"
          :key="m.memberSystem"
          :label="`${m.memberSystem} - ${m.memberName}${m.hasError ? ' ⚠' : ''}`"
          :value="m.memberSystem"
        />
      </el-select>
      <el-button size="small" type="primary" @click="requestMemberConfig" :loading="requesting" :disabled="!selectedMember">
        查看成员系统构型信息
      </el-button>
      <span v-if="memberStatus" class="ohms-cyan" style="font-size: var(--cm-fs-body);">{{ memberStatus }}</span>
    </div>

    <!-- 成员系统构型信息展示 -->
    <div class="ohms-panel cm-config-panel">
      <div class="cm-panel-header">
        <span class="ohms-title" style="font-size: var(--cm-fs-panel);">MEMBER SYSTEM CONFIGURATION / 成员系统构型信息</span>
        <template v-if="memberConfig">
          <span class="ohms-cyan" style="margin-left: 12px; font-size: var(--cm-fs-body);">{{ memberConfig.memberSystem }}</span>
          <span class="ohms-dim" style="margin-left: 8px; font-size: var(--cm-fs-body);">{{ memberConfig.memberName }}</span>
          <span style="flex: 1;"></span>
          <span class="ohms-dim" style="font-size: var(--cm-fs-aux);">一致性:</span>
          <span :class="memberConfig.matchCount === memberConfig.totalCount ? 'ohms-green' : 'ohms-red'" style="margin-left: 4px; font-size: var(--cm-fs-body);">
            {{ memberConfig.matchCount }} / {{ memberConfig.totalCount }}
          </span>
        </template>
      </div>
      <el-table v-if="memberConfig" :data="memberConfig.items" size="small" max-height="320">
        <el-table-column label="构型项" width="200">
          <template #default="{ row }">
            <span class="ohms-text">{{ row.item }}</span>
            <span class="ohms-dim" style="margin-left: 6px;">{{ row.itemName }}</span>
          </template>
        </el-table-column>
        <el-table-column label="当前值 (接收)" min-width="180">
          <template #default="{ row }">
            <span :class="row.match ? 'ohms-green' : 'ohms-red'">{{ row.receivedValue }}</span>
          </template>
        </el-table-column>
        <el-table-column label="期望值 (基本构型)" min-width="180">
          <template #default="{ row }"><span class="ohms-dim">{{ row.expectedValue }}</span></template>
        </el-table-column>
        <el-table-column label="一致性" width="100">
          <template #default="{ row }">
            <span :class="row.match ? 'ohms-green' : 'ohms-red'">{{ row.match ? '一致' : '不一致' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <span class="ohms-dim">{{ row.configType === 'software' ? '软件' : '硬件' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="报告时间" width="180">
          <template #default="{ row }">{{ formatTime(row.reportTime) }}</template>
        </el-table-column>
      </el-table>
      <div v-else class="cm-empty">请选择成员系统并点击「查看成员系统构型信息」</div>
    </div>

    <!-- 构型错误报告 -->
    <div class="ohms-panel cm-error-panel">
      <div class="cm-panel-header">
        <span class="ohms-title" style="font-size: var(--cm-fs-panel);">CONFIGURATION ERROR REPORTS / 构型错误报告</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: var(--cm-fs-body);">{{ errorTotal }} 条</span>
      </div>
      <el-table :data="errorReports" size="small" max-height="280" v-loading="errorLoading">
        <el-table-column prop="error_report_id" label="错误报告标识" min-width="200" />
        <el-table-column label="成员系统" width="130">
          <template #default="{ row }">
            <span class="ohms-text">{{ row.member_system }}</span>
          </template>
        </el-table-column>
        <el-table-column label="构型项" width="170">
          <template #default="{ row }">
            <span class="ohms-text">{{ row.config_item }}</span>
            <span class="ohms-dim" style="margin-left: 4px;">{{ row.config_item_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="接收值" min-width="150">
          <template #default="{ row }"><span class="ohms-red">{{ row.received_value }}</span></template>
        </el-table-column>
        <el-table-column label="期望值" min-width="150">
          <template #default="{ row }"><span class="ohms-dim">{{ row.expected_value }}</span></template>
        </el-table-column>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <div style="margin-top: 12px; display: flex; justify-content: center;">
        <el-pagination
          v-model:current-page="errPage"
          :page-size="errSize"
          :total="errorTotal"
          layout="prev, pager, next"
          @current-change="fetchErrorReports"
        />
      </div>
    </div>

    <!-- 飞机基本构型报告 -->
    <div class="ohms-panel cm-base-panel">
      <div class="cm-panel-header">
        <span class="ohms-title" style="font-size: var(--cm-fs-panel);">BASELINE CONFIGURATION / 飞机基本构型报告</span>
        <span class="ohms-dim" style="margin-left: 12px; font-size: var(--cm-fs-body);">{{ baseTotal }} 条</span>
      </div>
      <el-table :data="baseConfig" size="small" max-height="280" v-loading="baseLoading">
        <el-table-column prop="member_system" label="成员系统" width="130" />
        <el-table-column label="构型项" width="200">
          <template #default="{ row }">
            <span class="ohms-text">{{ row.config_item }}</span>
            <span class="ohms-dim" style="margin-left: 6px;">{{ row.config_item_name }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="config_value" label="基本构型值" min-width="180" />
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <span class="ohms-dim">{{ row.config_type === 'software' ? '软件' : '硬件' }}</span>
          </template>
        </el-table-column>
      </el-table>
      <div style="margin-top: 12px; display: flex; justify-content: center;">
        <el-pagination
          v-model:current-page="basePage"
          :page-size="baseSize"
          :total="baseTotal"
          layout="prev, pager, next"
          @current-change="fetchBaseConfig"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { on, off } = useWebSocket()

const memberList = ref([])
const memberTotal = ref(0)
const selectedMember = ref(null)
const memberConfig = ref(null)
const memberStatus = ref('')
const requesting = ref(false)

const errorReports = ref([])
const errorTotal = ref(0)
const errorLoading = ref(false)
const errPage = ref(1)
const errSize = ref(20)

const baseConfig = ref([])
const baseTotal = ref(0)
const baseLoading = ref(false)
const basePage = ref(1)
const baseSize = ref(20)

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const fetchMembers = () => {
  fetch(api('/api/v1/config/members'))
    .then(r => r.json())
    .then(data => {
      memberList.value = data.memberList || []
      memberTotal.value = data.total || 0
    })
}

const fetchErrorReports = () => {
  errorLoading.value = true
  fetch(api(`/api/v1/config/error-reports?page=${errPage.value}&size=${errSize.value}`))
    .then(r => r.json())
    .then(data => {
      errorReports.value = data.items || []
      errorTotal.value = data.total || 0
    })
    .finally(() => { errorLoading.value = false })
}

const fetchBaseConfig = () => {
  baseLoading.value = true
  fetch(api(`/api/v1/config/base?page=${basePage.value}&size=${baseSize.value}`))
    .then(r => r.json())
    .then(data => {
      baseConfig.value = data.items || []
      baseTotal.value = data.total || 0
    })
    .finally(() => { baseLoading.value = false })
}

const onMemberChange = () => {
  memberConfig.value = null
  memberStatus.value = ''
}

const requestMemberConfig = () => {
  if (!selectedMember.value) return
  requesting.value = true
  memberStatus.value = ''
  fetch(api(`/api/v1/config/request/${selectedMember.value}?operator=TEST`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        memberConfig.value = data.config
        const msg = data.has_mismatch
          ? `已获取构型信息，检测到 ${data.mismatch_count} 项不一致`
          : '已获取构型信息，与基本构型一致'
        memberStatus.value = msg
        ElMessage[data.has_mismatch ? 'warning' : 'success'](msg)
        if (data.has_mismatch) fetchErrorReports()
      } else {
        ElMessage.error(data.message || '获取失败')
      }
    })
    .finally(() => { requesting.value = false })
}

const formatTime = (t) => t ? new Date(t).toLocaleString() : '--'

const onConfigMismatch = () => fetchErrorReports()

onMounted(() => {
  fetchMembers()
  fetchErrorReports()
  fetchBaseConfig()
  on('config_mismatch', onConfigMismatch)
})

onUnmounted(() => {
  off('config_mismatch', onConfigMismatch)
})
</script>

<style scoped>
.cm-container {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  overflow-y: auto;
  padding: 4px;
  /* 字号层级 (与参数监控页统一) */
  --cm-fs-title: 13px;   /* 页面主标题 */
  --cm-fs-panel: 12px;   /* 面板标题 */
  --cm-fs-body: 11px;    /* 正文 / 列表 */
  --cm-fs-aux: 10px;     /* 辅助 / 元信息 */
}

.cm-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
}

.cm-member-panel {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
}

.cm-config-panel,
.cm-error-panel,
.cm-base-panel {
  padding: 0;
}

.cm-panel-header {
  padding: 8px 12px;
  border-bottom: 1px solid #555;
  display: flex;
  align-items: center;
  background: #1f1f1f;
}

.cm-empty {
  padding: 24px;
  text-align: center;
  color: #555;
  font-size: var(--cm-fs-body);
}

/* 关闭全局 uppercase, 保持 (1Hz) 等大小写原样 */
.ohms-title {
  text-transform: none;
}
</style>
