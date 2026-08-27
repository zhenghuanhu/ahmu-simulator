<template>
  <div class="ohms-layout">
    <!-- 顶部导航 - 两排斜角标签 -->
    <div class="top-nav">
      <div class="nav-row nav-row-main">
        <component
          :is="isTabAllowed(item.path) ? 'router-link' : 'span'"
          v-for="item in mainTabs"
          :key="item.path"
          :to="isTabAllowed(item.path) ? item.path : undefined"
          class="nav-tab"
          :class="{ active: route.path === item.path, disabled: !isTabAllowed(item.path) }"
          :title="!isTabAllowed(item.path) ? tabLockReason(item.path) : ''"
        >
          {{ item.label }}
        </component>
      </div>
      <div class="nav-row nav-row-sub">
        <component
          :is="isTabAllowed(item.path) ? 'router-link' : 'span'"
          v-for="item in subTabs"
          :key="item.path"
          :to="isTabAllowed(item.path) ? item.path : undefined"
          class="nav-tab"
          :class="{ active: route.path === item.path, disabled: !isTabAllowed(item.path) }"
          :title="!isTabAllowed(item.path) ? tabLockReason(item.path) : ''"
        >
          {{ item.label }}
        </component>
      </div>
    </div>

    <!-- 状态栏 -->
    <div class="status-bar">
      <div class="status-left">
        <span class="page-title">{{ pageTitle }}</span>
        <span v-if="modeInfo.hold_remaining > 0" class="hold-timer">
          MAINT IN {{ modeInfo.hold_remaining.toFixed(0) }}s
        </span>
      </div>
      <div class="status-right">
        <span class="mode-badge" :class="systemMode === 'maintenance' ? 'mode-maint' : 'mode-normal'">
          {{ systemMode === 'maintenance' ? 'MAINTENANCE MODE' : 'NORMAL MODE' }}
        </span>
        <button class="ohms-btn-sm sim-btn" @click="toggleSimPanel">SIGNAL SIM</button>
        <span class="ws-status">
          <span class="status-dot" :class="wsConnected ? 'green' : 'red'"></span>
          {{ wsConnected ? 'LIVE' : 'OFFLINE' }}
        </span>
        <span class="user-label">{{ currentUser }}</span>
        <button class="ohms-btn-sm" @click="logout">LOGOUT</button>
      </div>
    </div>

    <!-- 信号模拟调试面板 (模拟RDCU→ARINC664离散量消息) -->
    <div v-if="simPanelOpen" class="sim-panel">
      <div class="sim-header">
        <span class="sim-title">SIGNAL SIMULATION - RDCU / ARINC664 DISCRETE INPUTS</span>
        <button class="ohms-btn-sm" @click="toggleSimPanel">CLOSE</button>
      </div>
      <div class="sim-body">
        <!-- 维护开关 -->
        <div class="sim-group">
          <div class="sim-label">MAINTENANCE SWITCH</div>
          <div class="sim-switch">
            <button
              v-for="opt in switchOptions"
              :key="opt.value"
              class="sim-opt"
              :class="{ active: currentSwitch === opt.value }"
              @click="setSignals({ maintenance_switch: opt.value })"
            >{{ opt.label }}</button>
          </div>
        </div>

        <!-- 空/地 (轮载) -->
        <div class="sim-group">
          <div class="sim-label">ALL_GEAR_WOW (AIR / GROUND)</div>
          <div class="sim-switch">
            <button class="sim-opt" :class="{ active: currentWow === true }" @click="setSignals({ All_Gear_WOW: true })">GROUND</button>
            <button class="sim-opt" :class="{ active: currentWow === false }" @click="setSignals({ All_Gear_WOW: false })">AIR</button>
          </div>
        </div>

        <!-- 空速 -->
        <div class="sim-group">
          <div class="sim-label">VOTED_CALIBRATED_AIRSPEED (kts)</div>
          <div class="sim-airspeed">
            <input v-model.number="airspeedInput" type="number" min="0" max="600" class="sim-input" @keyup.enter="applyAirspeed" />
            <button class="sim-opt" @click="applyAirspeed">SET</button>
            <span class="sim-note">&lt;80 维护条件 / &gt;80 正常条件</span>
          </div>
        </div>

        <!-- 条件状态 -->
        <div class="sim-group sim-conditions">
          <div class="sim-label">CONDITION STATUS</div>
          <div class="cond-row" v-for="(label, key) in maintCondLabels" :key="key">
            <span class="cond-name">{{ label }}</span>
            <span class="cond-val" :class="modeInfo.conditions?.maintenance?.[key] ? 'ok' : 'no'">
              {{ modeInfo.conditions?.maintenance?.[key] ? 'MET' : 'NOT MET' }}
            </span>
          </div>
          <div class="cond-row">
            <span class="cond-name">HOLD TIMER (30s)</span>
            <span class="cond-val" :class="holding ? 'warn' : 'no'">
              {{ holding ? `${modeInfo.hold_elapsed.toFixed(0)}s / 30s` : 'IDLE' }}
            </span>
          </div>
        </div>

        <!-- 快捷预设 -->
        <div class="sim-group">
          <div class="sim-label">TEST PRESETS</div>
          <div class="sim-presets">
            <button class="sim-preset" @click="preset('maintenance')">
              MAINT COND<br /><small>GT + GND + 70k</small>
            </button>
            <button class="sim-preset" @click="preset('boundary')">
              BOUNDARY<br /><small>DL + GND + 80k</small>
            </button>
            <button class="sim-preset" @click="preset('normal')">
              NORMAL COND<br /><small>NORM + AIR + 90k</small>
            </button>
          </div>
        </div>

        <!-- 当前信号摘要 -->
        <div class="sim-group sim-summary">
          <div class="sim-label">CURRENT SIGNALS</div>
          <div class="sig-line">Switch: <b>{{ switchLabel }}</b></div>
          <div class="sig-line">WOW: <b>{{ currentWow ? 'GROUND' : 'AIR' }}</b></div>
          <div class="sig-line">Airspeed: <b>{{ currentAirspeed }} kts</b></div>
          <div class="sig-line">Mode: <b :class="systemMode === 'maintenance' ? 'txt-yellow' : 'txt-green'">{{ systemMode.toUpperCase() }}</b></div>
        </div>
      </div>
    </div>

    <!-- 主内容区 -->
    <div class="main-content">
      <router-view />
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useWebSocket } from '../composables/useWebSocket'
import { systemMode, setSystemMode, isPathAllowed, fallbackPath, fetchSystemMode } from '../composables/useSystemMode'

const route = useRoute()
const router = useRouter()
const { connected: wsConnected, on } = useWebSocket()

const currentUser = ref(localStorage.getItem('ahmu_user') || 'TEST')

// 模式信息 (含条件状态/计时)
const modeInfo = reactive({
  hold_elapsed: 0,
  hold_remaining: 0,
  conditions: null,
  signals: null,
})

const simPanelOpen = ref(false)
const airspeedInput = ref(0)

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const pageTitle = computed(() => {
  const all = [...mainTabs, ...subTabs]
  const item = all.find(t => t.path === route.path)
  return item?.title || item?.label || ''
})

const mainTabs = [
  { path: '/dashboard', label: 'CENTRAL MAINTENANCE', title: '系统总览' },
  { path: '/params', label: 'CONDITION MONITORING', title: '参数显示' },
  { path: '/dataload', label: 'DATA LOAD', title: '数据加载' },
  { path: '/utility', label: 'UTILITY', title: '工具' },
  { path: '/login', label: 'LOGOUT', title: '退出' },
]

const subTabs = [
  { path: '/fault', label: 'FAILURE REPORTS', title: '失效报告' },
  { path: '/groundtest', label: 'GROUND TEST', title: '地面测试' },
  { path: '/events', label: 'EVENT REPORTS', title: '事件报告' },
  { path: '/lifecycle', label: 'TIME CYCLE', title: '生命周期' },
  { path: '/lru', label: 'LRU FAULT HISTORY', title: 'LRU故障历史' },
  { path: '/config', label: 'CONFIGURATION REPORTS', title: '构型报告' },
]

const switchOptions = [
  { value: 'normal', label: 'NORMAL' },
  { value: 'ground_test', label: 'GROUND TEST' },
  { value: 'data_load', label: 'DATA LOAD' },
]

const maintCondLabels = {
  wow_is_ground: 'AIR/GND = GROUND (WOW=True)',
  airspeed_below_80: 'AIRSPEED < 80 kts',
  switch_in_test_position: 'SWITCH = GT / DL',
}

// 当前信号值 (来自后端)
const currentSwitch = computed(() => modeInfo.signals?.maintenance_switch || 'normal')
const currentWow = computed(() => modeInfo.signals?.All_Gear_WOW !== false)
const currentAirspeed = computed(() => modeInfo.signals?.Voted_Calibrated_Airspeed ?? 0)
const switchLabel = computed(() =>
  ({ normal: 'NORMAL', ground_test: 'GROUND TEST', data_load: 'DATA LOAD' })[currentSwitch.value] || currentSwitch.value)
const holding = computed(() => modeInfo.hold_elapsed > 0 && modeInfo.hold_remaining > 0)

const isTabAllowed = (path) => isPathAllowed(path)

const tabLockReason = (path) => {
  if (systemMode.value === 'maintenance') return '维护模式下仅可访问地面测试与数据加载'
  return '正常模式下不可访问地面测试与数据加载 (需维护模式)'
}

// ---------- 信号模拟 ----------

const refreshMode = async () => {
  try {
    const port = window.location.port === '5173' ? '8443' : window.location.port
    const r = await fetch(`http://${window.location.hostname}:${port}/api/v1/system/mode`)
    const data = await r.json()
    setSystemMode(data.mode)
    modeInfo.hold_elapsed = data.hold_elapsed || 0
    modeInfo.hold_remaining = data.hold_remaining || 0
    modeInfo.conditions = data.conditions
    modeInfo.signals = data.signals
    airspeedInput.value = data.signals?.Voted_Calibrated_Airspeed ?? 0
  } catch (e) { /* 忽略 */ }
}

const setSignals = async (payload) => {
  try {
    await fetch(api('/api/v1/system/signals'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    await refreshMode()
  } catch (e) {
    console.error('信号设置失败:', e)
  }
}

const applyAirspeed = () => {
  if (airspeedInput.value === null || isNaN(airspeedInput.value)) return
  setSignals({ Voted_Calibrated_Airspeed: airspeedInput.value })
}

const preset = (name) => {
  const presets = {
    // 维护模式条件: 地 + 空速70 + 开关=地面测试, 持续30s进入维护模式
    maintenance: { Switch_Ground_Test: true, All_Gear_WOW: true, Voted_Calibrated_Airspeed: 70 },
    // 边界测试: 空速=80 不满足"低于80", 不进入维护模式
    boundary: { Switch_Data_Load: true, All_Gear_WOW: true, Voted_Calibrated_Airspeed: 80 },
    // 正常模式条件: 空 + 空速90 + 开关=正常
    normal: { Switch_Normal: true, All_Gear_WOW: false, Voted_Calibrated_Airspeed: 90 },
  }
  if (presets[name]) {
    setSignals(presets[name])
    airspeedInput.value = presets[name].Voted_Calibrated_Airspeed
  }
}

const toggleSimPanel = () => {
  simPanelOpen.value = !simPanelOpen.value
  if (simPanelOpen.value) refreshMode()
}

// ---------- 模式变化处理 ----------

watch(systemMode, () => {
  // 模式变化后, 若当前页面不再可访问则跳转
  if (route.path !== '/login' && !isPathAllowed(route.path)) {
    router.push(fallbackPath())
  }
})

watch(() => route.path, (p) => {
  if (p !== '/login' && !isPathAllowed(p)) {
    router.push(fallbackPath())
  }
})

const logout = () => {
  localStorage.removeItem('ahmu_token')
  localStorage.removeItem('ahmu_user')
  router.push('/login')
}

let pollTimer = null

onMounted(async () => {
  on('mode_change', (data) => {
    setSystemMode(data.mode)
    refreshMode()
  })
  on('mode_transition', () => refreshMode())
  on('mode_transition_cancelled', () => refreshMode())
  on('signals_changed', () => refreshMode())

  await fetchSystemMode()
  await refreshMode()
  // 2s轮询刷新计时进度 (WS事件做即时更新, 轮询兜底)
  pollTimer = setInterval(refreshMode, 2000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.ohms-layout {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #000000;
}

.top-nav {
  background: #000000;
  padding: 4px 4px 0 4px;
  flex-shrink: 0;
}

.nav-row {
  display: flex;
  gap: 4px;
  margin-bottom: 4px;
  justify-content: space-between;
}

.nav-row-main .nav-tab,
.nav-row-sub .nav-tab {
  flex: 1;
  text-align: center;
}

.nav-tab {
  display: block;
  padding: 8px 8px;
  background: #444444;
  color: #000000;
  font-size: 12px;
  font-weight: bold;
  text-decoration: none;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  clip-path: polygon(10px 0, 100% 0, calc(100% - 10px) 100%, 0 100%);
  transition: background 0.15s, color 0.15s;
  white-space: nowrap;
  user-select: none;
}

.nav-tab:hover {
  background: #666666;
  color: #ffffff;
}

.nav-tab.active {
  background: #888888;
  color: #ffffff;
}

/* 模式禁用的标签 */
.nav-tab.disabled {
  background: #1a1a1a;
  color: #555555;
  cursor: not-allowed;
  border-bottom: 2px solid #333333;
}

.nav-tab.disabled:hover {
  background: #1a1a1a;
  color: #555555;
}

.status-bar {
  background: #111111;
  border-top: 2px solid #888888;
  border-bottom: 2px solid #888888;
  padding: 6px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.status-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.page-title {
  color: #ffffff;
  font-size: 14px;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 1px;
}

.hold-timer {
  color: #ffff00;
  font-size: 12px;
  font-weight: bold;
  border: 1px solid #888800;
  padding: 2px 8px;
}

.status-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.mode-badge {
  padding: 4px 12px;
  font-size: 12px;
  font-weight: bold;
  border: 1px solid #888888;
}

.mode-normal {
  background: #002200;
  color: #00ff00;
}

.mode-maint {
  background: #222200;
  color: #ffff00;
}

.ws-status {
  color: #aaaaaa;
  font-size: 12px;
  display: flex;
  align-items: center;
}

.user-label {
  color: #ffffff;
  font-size: 12px;
  font-weight: bold;
}

.ohms-btn-sm {
  background: #666666;
  border: 2px solid #888888;
  color: #ffffff;
  padding: 4px 16px;
  font-size: 12px;
  cursor: pointer;
  clip-path: polygon(6px 0, 100% 0, calc(100% - 6px) 100%, 0 100%);
  font-family: 'Consolas', 'Courier New', monospace;
  text-transform: uppercase;
}

.ohms-btn-sm:hover {
  background: #999999;
  color: #000000;
}

.sim-btn {
  background: #003344;
  border-color: #0088aa;
}

.sim-btn:hover {
  background: #005577;
  color: #ffffff;
}

/* ---------- 信号模拟面板 ---------- */
.sim-panel {
  background: #0d0d0d;
  border-bottom: 2px solid #0088aa;
  padding: 8px 16px 12px 16px;
  flex-shrink: 0;
}

.sim-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.sim-title {
  color: #00ccff;
  font-size: 12px;
  font-weight: bold;
  letter-spacing: 1px;
}

.sim-body {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  align-items: flex-start;
}

.sim-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.sim-label {
  color: #888888;
  font-size: 11px;
  letter-spacing: 1px;
  border-bottom: 1px solid #333333;
  padding-bottom: 2px;
}

.sim-switch {
  display: flex;
  gap: 4px;
}

.sim-opt {
  background: #333333;
  color: #ffffff;
  border: 1px solid #666666;
  padding: 6px 12px;
  font-size: 11px;
  font-family: 'Consolas', 'Courier New', monospace;
  cursor: pointer;
  clip-path: polygon(5px 0, 100% 0, calc(100% - 5px) 100%, 0 100%);
  text-transform: uppercase;
}

.sim-opt:hover {
  background: #555555;
}

.sim-opt.active {
  background: #0088aa;
  color: #ffffff;
  border-color: #00ccff;
}

.sim-airspeed {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sim-input {
  background: #000000;
  border: 1px solid #666666;
  color: #00ff00;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 13px;
  padding: 5px 8px;
  width: 90px;
}

.sim-input:focus {
  outline: none;
  border-color: #00ccff;
}

.sim-note {
  color: #666666;
  font-size: 10px;
}

.cond-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  font-size: 11px;
  font-family: 'Consolas', 'Courier New', monospace;
}

.cond-name {
  color: #bbbbbb;
}

.cond-val.ok {
  color: #00ff00;
  font-weight: bold;
}

.cond-val.no {
  color: #666666;
}

.cond-val.warn {
  color: #ffff00;
  font-weight: bold;
}

.sim-presets {
  display: flex;
  gap: 6px;
}

.sim-preset {
  background: #222200;
  color: #ffff00;
  border: 1px solid #888800;
  padding: 6px 10px;
  font-size: 10px;
  font-family: 'Consolas', 'Courier New', monospace;
  cursor: pointer;
  text-align: center;
  line-height: 1.4;
  clip-path: polygon(5px 0, 100% 0, calc(100% - 5px) 100%, 0 100%);
}

.sim-preset:hover {
  background: #444400;
}

.sim-preset small {
  color: #aaaa00;
}

.sim-summary .sig-line {
  color: #bbbbbb;
  font-size: 11px;
  font-family: 'Consolas', 'Courier New', monospace;
}

.sim-summary b {
  color: #ffffff;
}

.txt-green {
  color: #00ff00;
}

.txt-yellow {
  color: #ffff00;
}

.main-content {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  background: #000000;
}
</style>
