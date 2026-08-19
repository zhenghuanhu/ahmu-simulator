<template>
  <div>
    <!-- 发起测试 -->
    <div class="ohms-panel" style="margin-bottom: 16px; display: flex; gap: 12px; align-items: center;">
      <span class="ohms-title" style="font-size: 14px;">启动测试 (ARINC624)</span>
      <el-input v-model="memberInput" placeholder="成员系统 (MEM001)" style="width: 160px;" />
      <el-select v-model="testType" style="width: 160px;">
        <el-option label="交互式测试" value="interactive" />
        <el-option label="非交互式测试" value="non_interactive" />
      </el-select>
      <el-button type="primary" @click="startTest">发起测试</el-button>
    </div>

    <!-- 活跃测试状态 -->
    <el-card v-if="activeTest" style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span>◆ 活跃测试: {{ activeTest.member_system }}</span>
          <el-tag :type="stateType(activeTest.state)" size="small">{{ activeTest.state }}</el-tag>
        </div>
      </template>

      <!-- 状态流转 -->
      <div class="state-flow">
        <div v-for="s in stateFlow" :key="s" class="state-node"
             :class="{ active: isStateActive(s), passed: isStatePassed(s) }">
          {{ stateText(s) }}
        </div>
      </div>

      <!-- 测试步骤 -->
      <div v-if="testSteps.length" style="margin-top: 16px;">
        <div v-for="(step, idx) in testSteps" :key="idx" class="step-item">
          <span class="ohms-green" v-if="step.result === 'pass'">✓</span>
          <span class="ohms-red" v-else-if="step.result === 'fail'">✗</span>
          <span class="ohms-yellow" v-else>...</span>
          <span style="margin-left: 8px;">{{ step.name }}</span>
          <span class="ohms-dim" style="margin-left: 12px;">{{ step.detail }}</span>
        </div>
      </div>

      <!-- ACK按钮 -->
      <div v-if="activeTest.state === 'waiting_ack'" style="margin-top: 16px; text-align: center;">
        <el-button type="primary" size="large" @click="sendAck">发送 ACK 确认</el-button>
      </div>
    </el-card>

    <!-- 历史测试记录 -->
    <el-card>
      <template #header>◆ 测试记录</template>
      <el-table :data="tests" size="small" v-loading="loading">
        <el-table-column prop="member_system" label="成员系统" width="100" />
        <el-table-column prop="test_type" label="类型" width="120">
          <template #default="{ row }">
            {{ row.test_type === 'interactive' ? '交互式' : '非交互式' }}
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }">
            <span :class="statusClass(row.status)">{{ statusText(row.status) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="result" label="结果" width="80">
          <template #default="{ row }">
            <span :class="resultClass(row.result)">{{ resultText(row.result) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="start_time" label="开始时间" width="160">
          <template #default="{ row }">{{ formatTime(row.start_time) }}</template>
        </el-table-column>
        <el-table-column prop="end_time" label="结束时间" width="160">
          <template #default="{ row }">{{ formatTime(row.end_time) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { on } = useWebSocket()

const memberInput = ref('MEM001')
const testType = ref('interactive')
const tests = ref([])
const loading = ref(false)
const activeTest = ref(null)
const testSteps = ref([])
const stateFlow = ['command_received', 'in_progress', 'waiting_ack', 'completed']

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const startTest = () => {
  if (!memberInput.value) {
    ElMessage.warning('请输入成员系统')
    return
  }
  testSteps.value = []
  fetch(api(`/api/v1/groundtest/start?member=${memberInput.value}&test_type=${testType.value}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success('测试已发起')
        activeTest.value = {
          test_id: data.test_id,
          member_system: memberInput.value,
          state: data.state,
        }
      } else {
        ElMessage.error(data.message || '发起失败')
      }
    })
}

const sendAck = () => {
  if (!activeTest.value) return
  fetch(api(`/api/v1/groundtest/${activeTest.value.test_id}/ack`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success('ACK已发送')
      } else {
        ElMessage.warning(data.message || 'ACK失败')
      }
    })
}

const fetchTests = () => {
  loading.value = true
  fetch(api('/api/v1/groundtest/list?page=1&size=20'))
    .then(r => r.json())
    .then(data => {
      tests.value = data.items || []
    })
    .finally(() => { loading.value = false })
}

const stateType = (s) => ({
  'warning': s === 'waiting_ack',
  'success': s === 'completed',
  'danger': s === 'aborted' || s === 'suppressed',
  'info': s === 'in_progress',
}[s] || 'info')

const isStateActive = (s) => activeTest.value?.state === s
const isStatePassed = (s) => {
  const order = stateFlow.indexOf(s)
  const current = stateFlow.indexOf(activeTest.value?.state)
  return current > order
}

const stateText = (s) => ({
  command_received: '指令接收',
  in_progress: '测试进行',
  waiting_ack: '等待ACK',
  completed: '已完成',
  suppressed: '被抑制',
  aborted: '已中止',
}[s] || s)

const statusClass = (s) => ({
  'ohms-green': s === 'completed',
  'ohms-red': s === 'aborted' || s === 'suppressed',
  'ohms-yellow': s === 'waiting_ack',
  'ohms-cyan': s === 'in_progress',
})

const statusText = stateText

const resultClass = (r) => ({
  'ohms-green': r === 'pass',
  'ohms-red': r === 'fail' || r === 'abort',
  'ohms-dim': r === 'pending',
})

const resultText = (r) => ({
  pass: '通过',
  fail: '失败',
  abort: '中止',
  pending: '待定',
}[r] || r)

const formatTime = (t) => t ? new Date(t).toLocaleString() : '--'

onMounted(() => {
  fetchTests()

  on('test_state_change', (data) => {
    if (activeTest.value?.test_id === data.test_id) {
      activeTest.value.state = data.state
    }
    fetchTests()
  })

  on('test_step', (data) => {
    if (activeTest.value?.test_id === data.test_id) {
      testSteps.value.push(data.step)
    }
  })

  on('test_completed', () => {
    setTimeout(() => {
      activeTest.value = null
      testSteps.value = []
      fetchTests()
    }, 3000)
  })
})
</script>

<style scoped>
.state-flow {
  display: flex;
  gap: 8px;
  align-items: center;
}

.state-node {
  padding: 8px 16px;
  border: 1px solid #333;
  border-radius: 4px;
  color: #666;
  font-size: 13px;
}

.state-node.active {
  border-color: #00ffff;
  color: #00ffff;
  box-shadow: 0 0 8px rgba(0, 255, 255, 0.3);
}

.state-node.passed {
  border-color: #00ff0066;
  color: #00ff00;
}

.step-item {
  padding: 6px 0;
  border-bottom: 1px solid #1a1a1a;
  font-size: 13px;
}
</style>
