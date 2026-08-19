<template>
  <div>
    <!-- 状态指标卡片 -->
    <el-row :gutter="16" style="margin-bottom: 20px;">
      <el-col :span="6">
        <div class="ohms-panel stat-card">
          <div class="ohms-label">当前模式</div>
          <div class="ohms-value" :style="{color: mode === 'maintenance' ? '#ffff00' : '#00ff00'}">
            {{ mode === 'maintenance' ? '维护' : '正常' }}
          </div>
          <div class="ohms-dim" style="font-size: 12px;">空速: {{ signals.airspeed?.toFixed(1) || '--' }} KTS</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="ohms-panel stat-card">
          <div class="ohms-label">故障总数</div>
          <div class="ohms-value" style="color: #ff3333;">{{ totalFaults }}</div>
          <div class="ohms-dim" style="font-size: 12px;">实时统计</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="ohms-panel stat-card">
          <div class="ohms-label">ICD成员系统</div>
          <div class="ohms-value">{{ memberCount }}</div>
          <div class="ohms-dim" style="font-size: 12px;">已加载</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="ohms-panel stat-card">
          <div class="ohms-label">硬件接口</div>
          <div class="ohms-value" :style="{color: hardwareOk ? '#00ff00' : '#ff3333'}">
            {{ hardwareOk ? '正常' : '异常' }}
          </div>
          <div class="ohms-dim" style="font-size: 12px;">Mock模式</div>
        </div>
      </el-col>
    </el-row>

    <!-- 实时数据 -->
    <el-row :gutter="16">
      <el-col :span="12">
        <el-card>
          <template #header>
            <span>◆ 最新故障</span>
          </template>
          <el-table :data="recentFaults" size="small" style="width: 100%" max-height="300">
            <el-table-column prop="member_system" label="成员系统" width="100" />
            <el-table-column prop="fault_code" label="故障代码" width="130" />
            <el-table-column prop="severity" label="等级" width="80">
              <template #default="{ row }">
                <span :class="severityClass(row.severity)">{{ row.severity }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="时间">
              <template #default="{ row }">
                {{ formatTime(row.created_at) }}
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card>
          <template #header>
            <span>◆ 实时参数 (1Hz)</span>
          </template>
          <div class="param-grid">
            <div v-for="(p, name) in realtimeParams" :key="name" class="param-item">
              <div class="ohms-label">{{ name }}</div>
              <div class="param-value" :class="validityClass(p)">
                {{ p?.value?.toFixed(1) || '--' }} {{ p?.unit }}
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 系统状态行 -->
    <el-row :gutter="16" style="margin-top: 20px;">
      <el-col :span="24">
        <div class="ohms-panel">
          <el-row>
            <el-col :span="4" v-for="(item, idx) in statusItems" :key="idx">
              <div style="text-align: center;">
                <div class="ohms-label">{{ item.label }}</div>
                <div style="margin-top: 6px;">
                  <span class="status-dot" :class="item.ok ? 'green' : 'red'"></span>
                  <span :style="{color: item.ok ? '#00ff00' : '#ff3333'}">{{ item.text }}</span>
                </div>
              </div>
            </el-col>
          </el-row>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const { on } = useWebSocket()

const mode = ref('normal')
const signals = reactive({})
const totalFaults = ref(0)
const memberCount = ref(0)
const hardwareOk = ref(false)
const recentFaults = ref([])
const realtimeParams = ref({})
const acarsLink = ref('idle')
const printerStatus = ref('ready')
const activeLoads = ref(0)

const statusItems = computed(() => [
  { label: 'ACARS链路', ok: acarsLink.value !== 'lost', text: acarsLink.value },
  { label: '打印机', ok: printerStatus.value === 'ready', text: printerStatus.value },
  { label: '数据加载', ok: true, text: `${activeLoads.value} 活跃` },
  { label: 'WebSocket', ok: true, text: '已连接' },
  { label: '数据库', ok: true, text: 'SQLite WAL' },
  { label: '共享内存', ok: true, text: '已初始化' },
])

const severityClass = (s) => ({
  'ohms-red': s === 'critical',
  'ohms-yellow': s === 'major',
  'ohms-dim': s === 'minor',
})

const validityClass = (p) => ({
  'ohms-red': p?.validity === 'out_of_range',
  'ohms-yellow': p?.validity === 'invalid',
  'ohms-dim': p?.validity === 'unavailable',
  'ohms-cyan': p?.validity === 'valid',
})

const formatTime = (t) => t ? new Date(t).toLocaleTimeString() : '--'

const fetchStatus = () => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  fetch(`http://${window.location.hostname}:${port}/api/v1/system/status`)
    .then(r => r.json())
    .then(data => {
      mode.value = data.mode?.mode || 'normal'
      Object.assign(signals, data.mode?.signals || {})
      totalFaults.value = data.total_faults || 0
      memberCount.value = data.member_count || 0
      hardwareOk.value = data.hardware_initialized
      acarsLink.value = data.acars_link
      printerStatus.value = data.printer_status
      activeLoads.value = data.active_loads
    })
    .catch(() => {})
}

const fetchFaults = () => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  fetch(`http://${window.location.hostname}:${port}/api/v1/fault/reports?page=1&size=10`)
    .then(r => r.json())
    .then(data => {
      recentFaults.value = data.items || []
      totalFaults.value = data.total
    })
    .catch(() => {})
}

onMounted(() => {
  fetchStatus()
  fetchFaults()
  setInterval(fetchStatus, 5000)
  setInterval(fetchFaults, 5000)

  // WebSocket实时事件
  on('fault_new', (data) => {
    recentFaults.value.unshift(data)
    if (recentFaults.value.length > 10) recentFaults.value.pop()
    totalFaults.value++
  })

  on('param_update', (data) => {
    realtimeParams.value = data
  })

  on('mode_change', (data) => {
    mode.value = data.mode
  })
})
</script>

<style scoped>
.stat-card {
  text-align: center;
  min-height: 100px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.param-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.param-item {
  background: #0a0a0a;
  border: 1px solid #222;
  padding: 10px;
  border-radius: 4px;
  text-align: center;
}

.param-value {
  font-size: 20px;
  font-weight: bold;
  margin-top: 4px;
}
</style>
