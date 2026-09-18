<template>
  <div>
    <!-- ==================== 1. 飞行阶段核心状态 ==================== -->
    <div class="ohms-panel" style="margin-bottom: 16px;">
      <div style="display: flex; align-items: center; gap: 16px; flex-wrap: wrap;">
        <span class="ohms-title" style="font-size: 14px;">AIRCRAFT STATUS / 飞机状态消息</span>
        <span class="status-dot" :class="wsConnected ? 'green' : 'red'"></span>
        <span class="ohms-dim" style="font-size: 12px;">
          {{ wsConnected ? 'LIVE 1Hz 实时发布' : 'OFFLINE 断线' }}
        </span>
        <span class="ohms-dim" style="font-size: 12px;">· 更新 {{ formatTime(latestMsg?.utc_time) }}</span>
        <span style="flex: 1;"></span>
        <el-button size="small" @click="fetchStatus">手动刷新</el-button>
      </div>

      <div style="display: flex; gap: 24px; align-items: center; flex-wrap: wrap; margin-top: 12px;">
        <!-- 飞行阶段大字 -->
        <div style="text-align: center; min-width: 200px;">
          <div class="ohms-label">OHMS 飞行阶段</div>
          <div class="phase-value" :class="phaseColorClass">
            {{ latestMsg?.flight_phase ?? '--' }}
          </div>
          <div class="phase-name" :class="phaseColorClass">{{ latestMsg?.phase_name || '--' }}</div>
        </div>

        <!-- 航段 / 终止标志 / 生效源 -->
        <div style="flex: 1; min-width: 260px;">
          <div class="info-row">
            <span class="ohms-label">飞行航段</span>
            <span class="ohms-value">{{ latestMsg?.flight_leg ?? '--' }}</span>
          </div>
          <div class="info-row">
            <span class="ohms-label">全局终止标志</span>
            <span :class="latestMsg?.global_abort_flag ? 'ohms-red' : 'ohms-green'">
              {{ latestMsg?.global_abort_flag ? 'TRUE (置位)' : 'FALSE (未置位)' }}
            </span>
          </div>
          <div class="info-row">
            <span class="ohms-label">生效数据源</span>
            <span :class="sourceClass">
              {{ sourceText(latestMsg?.source) }}
            </span>
          </div>
          <div class="info-row">
            <span class="ohms-label">空地状态 / 空速</span>
            <span class="ohms-cyan">{{ latestMsg?.air_ground }} · {{ latestMsg?.airspeed?.toFixed(1) }} kts</span>
          </div>
        </div>
      </div>
    </div>

    <!-- ==================== 2. 飞机身份信息 ==================== -->
    <el-card style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px;">
          <span>◆ 飞机身份信息（其他系统提供）</span>
          <span style="flex: 1;"></span>
          <el-button size="small" type="primary" @click="identityPanelOpen = !identityPanelOpen">设置身份信息</el-button>
        </div>
      </template>

      <el-row :gutter="12">
        <el-col :xs="12" :sm="8" :md="4" v-for="item in identityItems" :key="item.label">
          <div class="identity-card">
            <div class="ohms-label">{{ item.label }}</div>
            <div class="identity-value">{{ item.value }}</div>
          </div>
        </el-col>
      </el-row>

      <!-- 身份设置表单 -->
      <div v-if="identityPanelOpen" class="identity-form">
        <el-form :inline="true">
          <el-form-item label="ICAO 应答机码">
            <el-input v-model="identityForm.icao_code" style="width: 110px;" />
          </el-form-item>
          <el-form-item label="注册号">
            <el-input v-model="identityForm.registration" style="width: 110px;" />
          </el-form-item>
          <el-form-item label="航班号">
            <el-input v-model="identityForm.flight_number" style="width: 110px;" />
          </el-form-item>
          <el-form-item label="出发机场">
            <el-input v-model="identityForm.departure_airport" style="width: 90px;" />
          </el-form-item>
          <el-form-item label="目的地机场">
            <el-input v-model="identityForm.destination_airport" style="width: 90px;" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" size="small" @click="submitIdentity">保存</el-button>
          </el-form-item>
        </el-form>
      </div>
    </el-card>

    <!-- ==================== 3. 双源参数面板 ==================== -->
    <el-card style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px;">
          <span>◆ 双源参数状态（左源失效自动切右源）</span>
          <span class="ohms-dim" style="font-size: 12px;">当前生效源：{{ sourceText(latestMsg?.source) }}</span>
        </div>
      </template>

      <el-table :data="paramRows" size="small" v-loading="loading" empty-text="暂无参数数据">
        <el-table-column prop="label" label="参数" min-width="180" />
        <el-table-column label="左源" min-width="120">
          <template #default="{ row }">
            <span :class="row.left_valid ? '' : 'ohms-red'">
              {{ formatValue(row.left) }}<span v-if="!row.left_valid" class="ohms-red"> (失效)</span>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="右源" min-width="120">
          <template #default="{ row }">
            <span :class="row.right_valid ? '' : 'ohms-red'">
              {{ formatValue(row.right) }}<span v-if="!row.right_valid" class="ohms-red"> (失效)</span>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="生效源" width="100">
          <template #default="{ row }">
            <span :class="row.effective_source === 'left' ? 'ohms-green' : row.effective_source === 'right' ? 'ohms-cyan' : 'ohms-red'">
              {{ row.effective_source === 'left' ? 'LEFT' : row.effective_source === 'right' ? 'RIGHT' : 'NONE' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="openInject(row.key, 'left')">左源注入</el-button>
            <el-button size="small" @click="openInject(row.key, 'right')">右源注入</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ==================== 4. 信号注入面板 ==================== -->
    <el-card>
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px;">
          <span>◆ 信号注入（模拟起落架 / 大气 / 飞管系统）</span>
          <span class="ohms-dim" style="font-size: 12px;">用于测试飞行阶段计算与双源切换</span>
        </div>
      </template>

      <div v-if="injectForm.param" style="margin-bottom: 12px;">
        <el-alert
          :title="`正在注入: ${paramLabel(injectForm.param)} (${injectForm.source.toUpperCase()} 源)`"
          type="info" :closable="false" show-icon
        />
      </div>

      <el-form :inline="true">
        <el-form-item label="数据源">
          <el-radio-group v-model="injectForm.source">
            <el-radio label="left">左源</el-radio>
            <el-radio label="right">右源</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="参数">
          <el-select v-model="injectForm.param" style="width: 200px;">
            <el-option v-for="p in paramOptions" :key="p.key" :label="p.label" :value="p.key" />
          </el-select>
        </el-form-item>
        <el-form-item label="值">
          <el-input v-model="injectForm.value" style="width: 140px;" />
        </el-form-item>
        <el-form-item label="有效">
          <el-switch v-model="injectForm.valid" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="injecting" @click="submitInject">注入信号</el-button>
        </el-form-item>
      </el-form>

      <!-- 快捷预设 -->
      <div class="ohms-dim" style="font-size: 12px; margin-top: 8px;">
        快捷预设：
        <el-button size="small" @click="preset('taxi_out')">滑出</el-button>
        <el-button size="small" @click="preset('takeoff')">起飞滑跑</el-button>
        <el-button size="small" @click="preset('cruise')">巡航</el-button>
        <el-button size="small" @click="preset('descent')">下降</el-button>
        <el-button size="small" @click="preset('maintenance')">维护模式</el-button>
        <el-button size="small" @click="preset('left_fail')">左源失效→切右源</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { on, connected } = useWebSocket()
const wsConnected = connected

// ---------- 状态 ----------
const latestMsg = ref(null)
const params = ref({})
const loading = ref(false)
const identityPanelOpen = ref(false)
const injecting = ref(false)

const identityForm = reactive({
  icao_code: '',
  registration: '',
  flight_number: '',
  departure_airport: '',
  destination_airport: '',
})

const injectForm = reactive({
  source: 'left',
  param: 'airspeed',
  value: '',
  valid: true,
})

// ---------- API ----------
const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

// ---------- 参数配置 ----------
const paramOptions = [
  { key: 'air_ground_status', label: '空地状态 (ground/air)' },
  { key: 'airspeed', label: '空速 (kts)' },
  { key: 'ground_speed', label: '地速 (kts)' },
  { key: 'fcm_corrected_altitude_rate', label: 'FCM修正高度变化率 (ft/min)' },
  { key: 'engine_thrust_lever_angle', label: '发动机油门推力角度 (度)' },
  { key: 'flight_altitude', label: '飞行高度 (ft)' },
  { key: 'brake_status', label: '刹车状态 (applied/released)' },
  { key: 'maintenance_switch_position', label: '维护开关位置 (normal/ground_test/data_load)' },
]

const paramLabelMap = Object.fromEntries(paramOptions.map(p => [p.key, p.label]))

const paramLabel = (key) => paramLabelMap[key] || key

// ---------- 身份信息展示 ----------
const identityItems = computed(() => {
  const m = latestMsg.value
  return [
    { label: 'ICAO 应答机码', value: m?.icao_code || '--' },
    { label: '注册号', value: m?.registration || '--' },
    { label: '航班号', value: m?.flight_number || '--' },
    { label: '出发机场', value: m?.departure_airport || '--' },
    { label: '目的地机场', value: m?.destination_airport || '--' },
    { label: 'UTC 日期', value: m?.utc_date || '--' },
  ]
})

// ---------- 双源参数表格 ----------
const paramRows = computed(() => {
  return paramOptions.map(p => {
    const e = params.value?.[p.key]
    return {
      key: p.key,
      label: p.label,
      left: e?.left?.value,
      left_valid: e?.left?.valid !== false,
      right: e?.right?.value,
      right_valid: e?.right?.valid !== false,
      effective_source: e?.source || 'none',
    }
  })
})

// ---------- 显示辅助 ----------
const phaseColorClass = computed(() => {
  const p = latestMsg.value?.flight_phase
  if (p === 14) return 'phase-maint'
  if (p === 15) return 'phase-unknown'
  if ([1, 2, 3, 4, 5, 11, 12, 13].includes(p)) return 'phase-ground'
  return 'phase-air'
})

const sourceClass = computed(() => ({
  'ohms-green': latestMsg.value?.source === 'left',
  'ohms-cyan': latestMsg.value?.source === 'right',
  'ohms-red': latestMsg.value?.source === 'none',
}))

const sourceText = (s) => ({ left: 'LEFT 左源', right: 'RIGHT 右源', none: 'NONE 双源失效' }[s] || s || '--')

const formatValue = (v) => {
  if (v === null || v === undefined) return '--'
  if (typeof v === 'number') return v.toFixed(1)
  return String(v)
}

const formatTime = (t) => t ? new Date(t).toLocaleTimeString() : '--'

// ---------- 数据加载 ----------
const fetchStatus = () => {
  loading.value = true
  fetch(api('/api/v1/aircraft-status'))
    .then(r => r.json())
    .then(data => {
      latestMsg.value = data.message
      params.value = data.params || {}
      Object.assign(identityForm, {
        icao_code: data.identity?.icao_code || '',
        registration: data.identity?.registration || '',
        flight_number: data.identity?.flight_number || '',
        departure_airport: data.identity?.departure_airport || '',
        destination_airport: data.identity?.destination_airport || '',
      })
    })
    .catch(() => ElMessage.error('飞机状态加载失败'))
    .finally(() => { loading.value = false })
}

// ---------- 操作 ----------
const submitIdentity = () => {
  fetch(api('/api/v1/aircraft-status/identity'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(identityForm),
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success('身份信息已更新')
        fetchStatus()
      }
    })
    .catch(() => ElMessage.error('网络错误'))
}

const openInject = (param, source) => {
  injectForm.param = param
  injectForm.source = source
}

const submitInject = () => {
  if (!injectForm.param || injectForm.value === '') {
    ElMessage.warning('请填写参数和值')
    return
  }
  // 数值型参数尝试转数字
  let value = injectForm.value
  const numericParams = ['airspeed', 'ground_speed', 'fcm_corrected_altitude_rate',
    'engine_thrust_lever_angle', 'flight_altitude']
  if (numericParams.includes(injectForm.param) && !isNaN(Number(value))) {
    value = Number(value)
  }
  injecting.value = true
  fetch(api('/api/v1/aircraft-status/signal'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source: injectForm.source,
      param: injectForm.param,
      value,
      valid: injectForm.valid,
    }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success(`已注入 ${paramLabel(injectForm.param)}`)
        fetchStatus()
      } else {
        ElMessage.error(data.message)
      }
    })
    .catch(() => ElMessage.error('网络错误'))
    .finally(() => { injecting.value = false })
}

// ---------- 快捷预设 ----------
const preset = (name) => {
  const sig = (source, param, value, valid = true) => {
    fetch(api('/api/v1/aircraft-status/signal'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, param, value, valid }),
    })
  }
  const presets = {
    taxi_out: () => {
      sig('left', 'air_ground_status', 'ground'); sig('left', 'brake_status', 'released')
      sig('left', 'ground_speed', 10); sig('left', 'airspeed', 10); sig('left', 'engine_thrust_lever_angle', 20)
      sig('left', 'maintenance_switch_position', 'normal')
    },
    takeoff: () => {
      sig('left', 'air_ground_status', 'ground'); sig('left', 'brake_status', 'released')
      sig('left', 'engine_thrust_lever_angle', 50); sig('left', 'airspeed', 100); sig('left', 'ground_speed', 50)
    },
    cruise: () => {
      sig('left', 'air_ground_status', 'air'); sig('left', 'flight_altitude', 30000)
      sig('left', 'fcm_corrected_altitude_rate', 0); sig('left', 'airspeed', 250)
    },
    descent: () => {
      sig('left', 'air_ground_status', 'air'); sig('left', 'flight_altitude', 20000)
      sig('left', 'fcm_corrected_altitude_rate', -500); sig('left', 'airspeed', 200)
    },
    maintenance: () => {
      sig('left', 'maintenance_switch_position', 'ground_test')
    },
    left_fail: () => {
      // 左源失效, 右源保持有效 → 应切换右源
      sig('left', 'airspeed', 150, false); sig('right', 'airspeed', 150, true)
    },
  }
  presets[name]?.()
  ElMessage.success('预设已注入')
  setTimeout(fetchStatus, 1200)
}

// ---------- 生命周期 ----------
onMounted(() => {
  fetchStatus()

  // 订阅 1Hz 飞机状态推送
  on('aircraft_status', (data) => {
    latestMsg.value = data
  })
})
</script>

<style scoped>
.phase-value {
  font-size: 64px;
  font-weight: bold;
  line-height: 1;
  font-family: Consolas, monospace;
}
.phase-name {
  font-size: 16px;
  margin-top: 8px;
  letter-spacing: 2px;
}
.phase-ground { color: #00ff00; }
.phase-air { color: #00ffff; }
.phase-maint { color: #ffff00; }
.phase-unknown { color: #ff3333; }

.info-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
}
.info-row .ohms-label {
  width: 120px;
  flex-shrink: 0;
}
.identity-card {
  padding: 8px 4px;
}
.identity-value {
  color: #00ffff;
  font-size: 16px;
  font-family: Consolas, monospace;
  margin-top: 4px;
  word-break: break-all;
}
.identity-form {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed #333;
}
</style>
