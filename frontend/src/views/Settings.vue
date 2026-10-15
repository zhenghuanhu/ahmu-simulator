<template>
  <div class="st-container">
    <!-- 顶部标题栏 -->
    <div class="ohms-panel st-toolbar">
      <span class="ohms-title" style="font-size: 14px;">SETTINGS / 配置</span>
      <span class="ohms-dim" style="margin-left: 12px; font-size: 12px;">AHMU 仿真器界面显示与系统配置</span>
    </div>

    <!-- 标签栏中英文显示 -->
    <div class="ohms-panel st-section">
      <div class="st-section-header">
        <span class="ohms-title" style="font-size: 13px;">LABEL DISPLAY / 标签栏显示</span>
      </div>
      <div class="st-section-body">
        <div class="st-desc">
          选择导航标签栏的显示方式，切换后标签栏立即生效：
        </div>
        <el-radio-group v-model="mode" @change="onModeChange">
          <el-radio v-for="opt in LABEL_MODE_OPTIONS" :key="opt.value" :label="opt.value">
            {{ opt.label }}
          </el-radio>
        </el-radio-group>

        <div class="st-current">
          当前模式：<span class="ohms-cyan">{{ currentModeText }}</span>
        </div>

        <div class="st-preview">
          <div class="st-preview-label">标签栏预览：</div>
          <div class="st-preview-tabs">
            <span v-for="t in previewTabs" :key="t.label" class="st-preview-tab">
              {{ formatLabel(t.label, t.title) }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 故障诊断配置 -->
    <div class="ohms-panel st-section">
      <div class="st-section-header">
        <span class="ohms-title" style="font-size: 13px;">FAULT DIAGNOSIS / 故障诊断配置</span>
      </div>
      <div class="st-section-body">
        <div class="st-desc">
          控制成员系统故障报告的周期自动上报行为，保存后立即生效：
        </div>

        <div class="st-field">
          <span class="st-field-label">故障自动上报</span>
          <el-switch v-model="faultDiag.auto_report_enabled" />
          <span class="st-field-hint">
            {{ faultDiag.auto_report_enabled ? '已启用：成员系统按频率周期自发上报故障' : '已关闭：故障仅通过 ICD 注入手动产生' }}
          </span>
        </div>

        <div class="st-field">
          <span class="st-field-label">自动上报概率</span>
          <el-input-number
            v-model="faultDiag.auto_report_probability"
            :min="0" :max="1" :step="0.001" :precision="3"
            :disabled="!faultDiag.auto_report_enabled"
          />
          <span class="st-field-hint">每个成员每次上报周期产生故障的概率（0~1）</span>
        </div>

        <div class="st-field">
          <span class="st-field-label">上报频率档位</span>
          <span class="st-field-value">{{ faultDiag.report_frequencies.join(' / ') }} Hz</span>
          <span class="st-field-hint">成员系统故障上报频率（仅展示，不可编辑）</span>
        </div>

        <div class="st-actions">
          <el-button type="primary" size="small" @click="saveFaultDiag" :loading="savingFaultDiag">
            保存配置
          </el-button>
          <span v-if="faultDiagSaved" class="ohms-green" style="font-size: 12px;">{{ faultDiagSaved }}</span>
        </div>

        <div class="st-divider"></div>

        <div class="st-actions">
          <el-button type="danger" size="small" @click="clearHistoryReports" :loading="clearingReports">
            清理历史报告
          </el-button>
          <span class="st-field-hint">清除全部故障报告与失效报告（不可恢复）</span>
          <span v-if="clearResult" class="ohms-green" style="font-size: 12px;">{{ clearResult }}</span>
        </div>
      </div>
    </div>

    <!-- 维护模式调试 -->
    <div class="ohms-panel st-section">
      <div class="st-section-header">
        <span class="ohms-title" style="font-size: 13px;">MAINTENANCE MODE DEBUG / 维护模式调试</span>
      </div>
      <div class="st-section-body">
        <div class="st-desc">
          管理员可一键快速切换系统模式，跳过 30 秒持续计时立即生效。原有 30 秒自动切换机制保持不变。
        </div>

        <div class="st-field">
          <span class="st-field-label">当前模式</span>
          <span class="st-field-value" :class="modeClass">{{ modeText }}</span>
          <span v-if="modeInfo.hold_remaining > 0 && modeInfo.mode !== 'maintenance'" class="st-field-hint">
            （维护条件计时中，剩余 {{ modeInfo.hold_remaining }} 秒后自动进入维护模式）
          </span>
        </div>

        <div class="st-field">
          <span class="st-field-label">快速切换</span>
          <el-button
            size="small"
            type="warning"
            @click="forceMode('maintenance')"
            :loading="switchingMode"
            :disabled="modeInfo.mode === 'maintenance'"
          >
            切换到维护模式
          </el-button>
          <el-button
            size="small"
            @click="forceMode('normal')"
            :loading="switchingMode"
            :disabled="modeInfo.mode === 'normal'"
          >
            切换到正常模式
          </el-button>
          <span v-if="switchResult" class="ohms-green" style="font-size: 12px;">{{ switchResult }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useLabelMode, LABEL_MODE_OPTIONS } from '../composables/useUiConfig'
import { useWebSocket } from '../composables/useWebSocket'

const { on } = useWebSocket()

const { labelMode, setLabelMode, formatLabel } = useLabelMode()

const mode = ref(labelMode.value)

const previewTabs = [
  { label: 'CENTRAL MAINTENANCE', title: '系统总览' },
  { label: 'CONDITION MONITORING', title: '参数显示' },
  { label: 'AIRCRAFT STATUS', title: '飞机状态消息' },
  { label: 'FAILURE REPORTS', title: '失效报告' },
  { label: 'TIME CYCLE', title: '生命周期' },
]

const currentModeText = computed(() => {
  const m = LABEL_MODE_OPTIONS.find(o => o.value === labelMode.value)
  return m ? m.label : labelMode.value
})

const onModeChange = (val) => {
  setLabelMode(val)
  ElMessage.success(`标签栏显示已切换为：${currentModeText.value}`)
}

// ==================== 故障诊断配置 ====================

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const faultDiag = ref({
  auto_report_enabled: false,
  auto_report_probability: 0.005,
  report_frequencies: [0.25, 0.5, 1.0, 2.0],
})
const savingFaultDiag = ref(false)
const faultDiagSaved = ref('')
const clearingReports = ref(false)
const clearResult = ref('')

const fetchFaultDiagConfig = () => {
  fetch(api('/api/v1/system/fault-diagnosis-config'))
    .then(r => r.json())
    .then(data => {
      faultDiag.value.auto_report_enabled = !!data.auto_report_enabled
      faultDiag.value.auto_report_probability = data.auto_report_probability ?? 0.005
      if (Array.isArray(data.report_frequencies)) {
        faultDiag.value.report_frequencies = data.report_frequencies
      }
    })
    .catch(() => { /* 忽略 */ })
}

const saveFaultDiag = () => {
  // 校验: 概率需在 0~1
  const p = faultDiag.value.auto_report_probability
  if (p === null || p === undefined || p < 0 || p > 1) {
    ElMessage.warning('自动上报概率需在 0~1 之间')
    return
  }
  savingFaultDiag.value = true
  faultDiagSaved.value = ''
  fetch(api('/api/v1/system/fault-diagnosis-config'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      auto_report_enabled: faultDiag.value.auto_report_enabled,
      auto_report_probability: p,
    }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        faultDiag.value.auto_report_enabled = data.auto_report_enabled
        faultDiag.value.auto_report_probability = data.auto_report_probability
        faultDiagSaved.value = `已保存并生效：自动上报${data.auto_report_enabled ? '启用' : '关闭'}，概率 ${data.auto_report_probability}`
        ElMessage.success('故障诊断配置已保存并立即生效')
      } else {
        ElMessage.error(data.message || '保存失败')
      }
    })
    .finally(() => { savingFaultDiag.value = false })
}

const clearHistoryReports = () => {
  ElMessageBox.confirm(
    '将清除全部历史故障报告与失效报告，此操作不可恢复，是否继续？',
    '清理历史报告',
    { confirmButtonText: '确认清理', cancelButtonText: '取消', type: 'warning' },
  )
    .then(() => {
      clearingReports.value = true
      clearResult.value = ''
      fetch(api('/api/v1/system/fault-history-clear'), { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          if (data.status === 'ok') {
            clearResult.value = `已清理：故障报告 ${data.fault_reports_cleared} 条、失效报告 ${data.failure_reports_cleared} 条`
            ElMessage.success(clearResult.value)
          } else {
            ElMessage.error(data.message || '清理失败')
          }
        })
        .finally(() => { clearingReports.value = false })
    })
    .catch(() => { /* 用户取消 */ })
}

// ==================== 维护模式调试 ====================

const modeInfo = ref({
  mode: 'normal',
  hold_remaining: 0,
  hold_elapsed: 0,
  hold_duration: 30,
})
const switchingMode = ref(false)
const switchResult = ref('')

const modeText = computed(() => {
  const m = modeInfo.value.mode
  if (m === 'maintenance') return '维护模式 (MAINTENANCE)'
  if (m === 'transitioning') return '切换中 (TRANSITIONING)'
  return '正常模式 (NORMAL)'
})

const modeClass = computed(() => {
  const m = modeInfo.value.mode
  if (m === 'maintenance') return 'ohms-yellow'
  if (m === 'transitioning') return 'ohms-yellow'
  return 'ohms-green'
})

const fetchModeInfo = () => {
  fetch(api('/api/v1/system/mode'))
    .then(r => r.json())
    .then(data => {
      modeInfo.value.mode = data.mode || 'normal'
      modeInfo.value.hold_remaining = data.hold_remaining ?? 0
      modeInfo.value.hold_elapsed = data.hold_elapsed ?? 0
      modeInfo.value.hold_duration = data.hold_duration ?? 30
    })
    .catch(() => { /* 忽略 */ })
}

const forceMode = (target) => {
  const label = target === 'maintenance' ? '维护模式' : '正常模式'
  ElMessageBox.confirm(
    `确定立即切换到${label}？此操作将跳过 30 秒持续计时，立即生效。`,
    '强制切换系统模式',
    { confirmButtonText: '确认切换', cancelButtonText: '取消', type: 'warning' },
  )
    .then(() => {
      switchingMode.value = true
      switchResult.value = ''
      fetch(api(`/api/v1/system/force-mode?mode=${target}&operator=admin`), { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          if (data.status === 'ok') {
            switchResult.value = `已强制切换到${label}（跳过 30 秒计时，操作人 admin）`
            ElMessage.success(switchResult.value)
            fetchModeInfo()
          } else {
            ElMessage.error(data.message || '切换失败')
          }
        })
        .catch(() => ElMessage.error('Network error'))
        .finally(() => { switchingMode.value = false })
    })
    .catch(() => { /* 用户取消 */ })
}

onMounted(() => {
  fetchFaultDiagConfig()
  fetchModeInfo()
  setInterval(fetchModeInfo, 5000)

  on('mode_change', (data) => { fetchModeInfo() })
})
</script>

<style scoped>
.st-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  padding: 4px;
}

.st-toolbar {
  display: flex;
  align-items: center;
  padding: 10px 16px;
}

.st-section {
  padding: 0;
}

.st-section-header {
  padding: 10px 14px;
  border-bottom: 1px solid #555;
  background: #1f1f1f;
}

.st-section-body {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.st-desc {
  color: #bbb;
  font-size: 12px;
}

.st-current {
  color: #aaa;
  font-size: 13px;
}

.st-preview {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.st-preview-label {
  color: #888;
  font-size: 12px;
}

.st-preview-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.st-preview-tab {
  background: #444;
  color: #ddd;
  font-size: 12px;
  font-weight: bold;
  text-transform: uppercase;
  padding: 8px 14px;
  clip-path: polygon(8px 0, 100% 0, calc(100% - 8px) 100%, 0 100%);
  letter-spacing: 0.5px;
}

/* 配置字段 */
.st-field {
  display: flex;
  align-items: center;
  gap: 14px;
}

.st-field-label {
  color: #ccc;
  font-size: 13px;
  min-width: 120px;
}

.st-field-hint {
  color: #777;
  font-size: 12px;
}

.st-field-value {
  color: #00ffff;
  font-size: 13px;
}

.st-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 4px;
}

.st-divider {
  border-top: 1px solid #333;
  margin: 4px 0;
}
</style>
