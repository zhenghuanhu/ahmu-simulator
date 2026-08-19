<template>
  <div>
    <!-- 发送消息 -->
    <div class="ohms-panel" style="margin-bottom: 16px; display: flex; gap: 12px; align-items: center;">
      <span class="ohms-title" style="font-size: 14px;">ACARS管理 (ARINC619)</span>
      <span class="status-dot" :class="linkStatus === 'idle' ? 'green' : linkStatus === 'busy' ? 'yellow' : 'red'"></span>
      <span :class="{'ohms-green': linkStatus === 'idle', 'ohms-yellow': linkStatus === 'busy', 'ohms-red': linkStatus === 'lost'}">
        链路: {{ linkStatusText }}
      </span>
      <span style="flex: 1;"></span>
      <el-select v-model="newMsg.type" style="width: 160px;">
        <el-option label="故障报告" value="fault_report" />
        <el-option label="失效报告" value="failure_report" />
        <el-option label="FDE" value="fde" />
        <el-option label="事件报告" value="event_report" />
        <el-option label="构型数据" value="config_data" />
      </el-select>
      <el-select v-model="newMsg.priority" style="width: 120px;">
        <el-option label="低优先级" :value="0" />
        <el-option label="普通" :value="1" />
        <el-option label="高优先级" :value="2" />
      </el-select>
      <el-input v-model="newMsg.content" placeholder="消息内容" style="width: 250px;" />
      <el-button type="primary" @click="sendMessage">下传</el-button>
    </div>

    <!-- 消息列表 -->
    <el-card>
      <template #header>◆ ACARS消息记录</template>
      <el-table :data="messages" size="small" v-loading="loading">
        <el-table-column prop="direction" label="方向" width="80">
          <template #default="{ row }">
            <span :class="row.direction === 'downlink' ? 'ohms-cyan' : 'ohms-yellow'">
              {{ row.direction === 'downlink' ? '下行' : '上行' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="message_type" label="类型" width="120" />
        <el-table-column prop="priority" label="优先级" width="90">
          <template #default="{ row }">
            <span :class="priorityClass(row.priority)">{{ priorityText(row.priority) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="content" label="内容" min-width="250" />
        <el-table-column prop="link_status" label="链路" width="80" />
        <el-table-column prop="created_at" label="时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { on } = useWebSocket()

const messages = ref([])
const loading = ref(false)
const linkStatus = ref('idle')
const newMsg = reactive({ type: 'fault_report', priority: 1, content: '' })

const linkStatusText = computed(() => ({
  idle: '空闲',
  busy: '繁忙',
  lost: '丢失',
}[linkStatus.value] || linkStatus.value))

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const sendMessage = () => {
  if (!newMsg.content) {
    ElMessage.warning('请输入消息内容')
    return
  }
  fetch(api(`/api/v1/acars/send?message_type=${newMsg.type}&content=${encodeURIComponent(newMsg.content)}&priority=${newMsg.priority}`), {
    method: 'POST',
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success('消息已下传')
        newMsg.content = ''
        fetchMessages()
      }
    })
}

const fetchMessages = () => {
  loading.value = true
  fetch(api('/api/v1/acars/messages?page=1&size=20'))
    .then(r => r.json())
    .then(data => {
      messages.value = data.items || []
    })
    .finally(() => { loading.value = false })
}

const priorityClass = (p) => ({
  0: 'ohms-dim',
  1: 'ohms-cyan',
  2: 'ohms-red',
}[p] || 'ohms-dim')

const priorityText = (p) => ({
  0: '低',
  1: '普通',
  2: '高',
}[p] || '普通')

const formatTime = (t) => t ? new Date(t).toLocaleString() : '--'

onMounted(() => {
  fetchMessages()

  on('acars_link_status', (data) => {
    linkStatus.value = data.status
  })

  on('acars_message', () => fetchMessages())
})
</script>
