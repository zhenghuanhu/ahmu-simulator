<template>
  <div>
    <!-- 工具栏 -->
    <div class="ohms-panel" style="margin-bottom: 16px; display: flex; gap: 12px; align-items: center;">
      <span class="ohms-title" style="font-size: 14px;">事件报告列表 EVENT REPORTS</span>
      <el-input v-model="filterMember" placeholder="成员系统 (如MEM023)" style="width: 170px;" clearable @input="applyFilter" />
      <el-select v-model="filterType" placeholder="事件类型" style="width: 150px;" clearable @change="applyFilter">
        <el-option v-for="t in eventTypes" :key="t.value" :label="t.label" :value="t.value" />
      </el-select>
      <el-button type="primary" @click="refresh">刷新</el-button>
      <span style="flex: 1;"></span>
      <span class="ohms-dim">共 {{ filtered.length }} 条事件</span>
    </div>

    <!-- 事件列表 -->
    <el-card>
      <el-table :data="pagedEvents" size="small" style="width: 100%">
        <el-table-column prop="id" label="事件号" width="80" />
        <el-table-column prop="time" label="事件时间" width="170" />
        <el-table-column prop="flight_phase" label="飞行阶段" width="100" />
        <el-table-column prop="member_system" label="成员系统" width="100" />
        <el-table-column prop="type" label="事件类型" width="160">
          <template #default="{ row }">
            <span class="ohms-yellow">{{ row.type }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="事件描述" min-width="240" />
        <el-table-column prop="severity" label="等级" width="80">
          <template #default="{ row }">
            <span :class="severityClass(row.severity)">{{ row.severity }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <span :class="row.status === 'open' ? 'ohms-red' : 'ohms-green'">
              {{ row.status === 'open' ? 'OPEN' : 'CLOSED' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="ata" label="ATA" width="70" />
      </el-table>

      <div style="margin-top: 16px; display: flex; justify-content: center;">
        <el-pagination
          v-model:current-page="page"
          :page-size="size"
          :total="filtered.length"
          layout="prev, pager, next"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const filterMember = ref('')
const filterType = ref('')
const page = ref(1)
const size = ref(20)

const eventTypes = [
  { value: 'EXCEEDANCE', label: '参数超限' },
  { value: 'MODE CHANGE', label: '模式切换' },
  { value: 'POWER INTERRUPT', label: '电源中断' },
  { value: 'COMM LOSS', label: '通信中断' },
  { value: 'COMM RESTORE', label: '通信恢复' },
  { value: 'BITE FAILURE', label: 'BITE检测故障' },
  { value: 'CONFIG CHANGE', label: '构型变更' },
  { value: 'DFDRS EVENT', label: '数据记录事件' },
]

const phases = ['GROUND', 'TAKEOFF', 'CLIMB', 'CRUISE', 'DESCENT', 'APPROACH', 'LANDING']
const descriptions = {
  'EXCEEDANCE': ['N1转速超出红线限制 2.3%', 'EGT超温告警 (915°C)', '滑油压力低于最低限制', '液压系统B压力异常'],
  'MODE CHANGE': ['FDIU工作模式切换: NORMAL → DEGRADED', 'ADS切换至备用源', '飞控系统由NORMAL LAW进入ALTERNATE'],
  'POWER INTERRUPT': ['28V DC总线瞬时断电 (120ms)', '电源瞬变导致LRU重启'],
  'COMM LOSS': ['A664 VL与成员系统通信丢失', 'A429总线Rx通道校验失败', 'A825节点超时无响应'],
  'COMM RESTORE': ['A664 VL通信恢复正常', 'A429总线Rx通道校验恢复'],
  'BITE FAILURE': ['上电自检检测到RAM故障', '周期自检检测到传感器偏差', 'LRU内部温度超限告警'],
  'CONFIG CHANGE': ['软件构型由LSC-2024-11更新至LSC-2024-12', '数据库构型变更: NAVDB 2109 → 2110'],
  'DFDRS EVENT': '飞行数据记录器事件标记',
}

// 生成确定性模拟事件数据
const generateEvents = () => {
  const events = []
  let seed = 20260819
  const rnd = () => {
    seed = (seed * 9301 + 49297) % 233280
    return seed / 233280
  }
  const count = 126
  for (let i = 0; i < count; i++) {
    const type = eventTypes[Math.floor(rnd() * eventTypes.length)].value
    const member = `MEM${String(Math.floor(rnd() * 200) + 1).padStart(3, '0')}`
    const hour = Math.floor(rnd() * 24)
    const minute = Math.floor(rnd() * 60)
    const second = Math.floor(rnd() * 60)
    const day = Math.floor(rnd() * 28) + 1
    const desc = Array.isArray(descriptions[type])
      ? descriptions[type][Math.floor(rnd() * descriptions[type].length)]
      : descriptions[type]
    events.push({
      id: 1000 + i,
      time: `2026-08-${String(day).padStart(2, '0')} ${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')}`,
      flight_phase: phases[Math.floor(rnd() * phases.length)],
      member_system: member,
      type,
      description: `${desc}`,
      severity: rnd() > 0.85 ? 'major' : rnd() > 0.4 ? 'minor' : 'info',
      status: rnd() > 0.75 ? 'open' : 'closed',
      ata: String(Math.floor(rnd() * 34) + 21),
    })
  }
  return events.sort((a, b) => (a.time < b.time ? 1 : -1))
}

const events = ref(generateEvents())

const filtered = computed(() => {
  return events.value.filter(e => {
    if (filterMember.value && !e.member_system.toLowerCase().includes(filterMember.value.toLowerCase())) return false
    if (filterType.value && e.type !== filterType.value) return false
    return true
  })
})

const pagedEvents = computed(() => {
  const start = (page.value - 1) * size.value
  return filtered.value.slice(start, start + size.value)
})

const applyFilter = () => { page.value = 1 }
const refresh = () => { events.value = generateEvents(); page.value = 1 }

const severityClass = (s) => ({
  critical: 'ohms-red',
  major: 'ohms-yellow',
  minor: 'ohms-dim',
  info: 'ohms-dim',
}[s] || 'ohms-dim')
</script>
