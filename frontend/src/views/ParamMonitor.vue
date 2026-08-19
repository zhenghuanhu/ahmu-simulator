<template>
  <div>
    <!-- 实时参数网格 -->
    <el-card style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span>◆ 实时参数监控 (1Hz推送)</span>
          <el-tag :type="wsConnected ? 'success' : 'danger'" size="small">
            {{ wsConnected ? '实时' : '离线' }}
          </el-tag>
        </div>
      </template>
      <el-row :gutter="12">
        <el-col :span="6" v-for="(p, name) in realtimeParams" :key="name" style="margin-bottom: 12px;">
          <div class="param-card" :class="validityBorder(p)">
            <div class="ohms-label">{{ name }}</div>
            <div class="param-value" :class="validityClass(p)">
              {{ p?.value?.toFixed(2) || '--' }}
              <span class="param-unit">{{ p?.unit }}</span>
            </div>
            <div class="ohms-dim" style="font-size: 11px;">
              ATA {{ p?.ata }} · {{ validityText(p?.validity) }}
            </div>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <!-- 历史数据 -->
    <el-card>
      <template #header>
        <div style="display: flex; gap: 12px; align-items: center;">
          <span>◆ 参数历史</span>
          <el-select v-model="selectedParam" placeholder="选择参数" style="width: 220px;" @change="fetchHistory">
            <el-option v-for="p in paramList" :key="p.name" :label="`${p.name} (${p.unit})`" :value="p.name" />
          </el-select>
        </div>
      </template>
      <el-table :data="history" size="small" max-height="400">
        <el-table-column prop="value" label="数值" width="150">
          <template #default="{ row }">{{ row.value?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column prop="unit" label="单位" width="80" />
        <el-table-column prop="validity" label="有效性" width="120">
          <template #default="{ row }">
            <span :class="validityClass(row)">{{ validityText(row.validity) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="timestamp" label="时间">
          <template #default="{ row }">{{ formatTime(row.timestamp) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const { connected: wsConnected, on } = useWebSocket()

const realtimeParams = ref({})
const paramList = ref([])
const selectedParam = ref('')
const history = ref([])

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const fetchParamList = () => {
  fetch(api('/api/v1/params/list'))
    .then(r => r.json())
    .then(data => {
      paramList.value = data.items || []
      if (!selectedParam.value && paramList.value.length) {
        selectedParam.value = paramList.value[0].name
        fetchHistory()
      }
    })
}

const fetchHistory = () => {
  if (!selectedParam.value) return
  fetch(api(`/api/v1/params/history/${selectedParam.value}?limit=100`))
    .then(r => r.json())
    .then(data => {
      history.value = (data.items || []).reverse()
    })
}

const validityClass = (p) => ({
  'ohms-red': p?.validity === 'out_of_range',
  'ohms-yellow': p?.validity === 'invalid',
  'ohms-dim': p?.validity === 'unavailable',
  'ohms-cyan': !p?.validity || p?.validity === 'valid',
})

const validityBorder = (p) => ({
  'border-danger': p?.validity && p.validity !== 'valid',
})

const validityText = (v) => {
  const map = {
    valid: '有效',
    unavailable: '不可用',
    out_of_range: '超限',
    invalid: '无效',
  }
  return map[v] || '有效'
}

const formatTime = (t) => t ? new Date(t).toLocaleString() : '--'

onMounted(() => {
  fetchParamList()
  on('param_update', (data) => {
    realtimeParams.value = data
  })
})
</script>

<style scoped>
.param-card {
  background: #0a0a0a;
  border: 1px solid #222;
  padding: 12px;
  border-radius: 4px;
  text-align: center;
}

.param-card.border-danger {
  border-color: #ff333366;
}

.param-value {
  font-size: 22px;
  font-weight: bold;
  margin: 6px 0;
  color: #00ffff;
}

.param-unit {
  font-size: 12px;
  color: #888;
}
</style>
