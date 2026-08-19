<template>
  <div>
    <!-- 发起加载 -->
    <div class="ohms-panel" style="margin-bottom: 16px; display: flex; gap: 12px; align-items: center;">
      <span class="ohms-title" style="font-size: 14px;">数据加载 (ARINC615A)</span>
      <el-input v-model="memberInput" placeholder="成员系统" style="width: 140px;" />
      <el-input v-model="fileInput" placeholder="文件名" style="width: 200px;" />
      <el-button type="primary" @click="startLoad">发起加载</el-button>
      <span style="flex: 1;"></span>
      <span class="ohms-dim">并发上限: 3 · 活跃: {{ activeLoads }}</span>
    </div>

    <!-- 活跃加载进度 -->
    <el-card v-if="activeLoadTasks.length" style="margin-bottom: 16px;">
      <template #header>◆ 加载进度 (实时)</template>
      <div v-for="task in activeLoadTasks" :key="task.task_id" style="margin-bottom: 12px;">
        <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
          <span>{{ task.member_system }} - {{ task.file_name }}</span>
          <span class="ohms-cyan">{{ task.progress }}%</span>
        </div>
        <el-progress :percentage="task.progress" :stroke-width="10" />
      </div>
    </el-card>

    <!-- 历史任务 -->
    <el-card>
      <template #header>◆ 加载任务记录</template>
      <el-table :data="tasks" size="small" v-loading="loading">
        <el-table-column prop="member_system" label="成员系统" width="100" />
        <el-table-column prop="file_name" label="文件名" min-width="150" />
        <el-table-column prop="file_size" label="大小" width="100">
          <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
        </el-table-column>
        <el-table-column prop="progress" label="进度" width="100">
          <template #default="{ row }">{{ row.progress }}%</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <span :class="loadStatusClass(row.status)">{{ loadStatusText(row.status) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="start_time" label="开始时间" width="150">
          <template #default="{ row }">{{ formatTime(row.start_time) }}</template>
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
const fileInput = ref('firmware_v2.1.bin')
const tasks = ref([])
const loading = ref(false)
const activeLoads = ref(0)
const activeLoadTasks = ref([])

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const startLoad = () => {
  if (!memberInput.value) {
    ElMessage.warning('请输入成员系统')
    return
  }
  fetch(api(`/api/v1/dataload/start?member=${memberInput.value}&file=${fileInput.value}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success('加载任务已启动')
      } else {
        ElMessage.error(data.message || '启动失败')
      }
    })
}

const fetchTasks = () => {
  loading.value = true
  fetch(api('/api/v1/dataload/list?page=1&size=20'))
    .then(r => r.json())
    .then(data => {
      tasks.value = data.items || []
      activeLoads.value = tasks.value.filter(t => t.status === 'loading').length
    })
    .finally(() => { loading.value = false })
}

const loadStatusClass = (s) => ({
  'ohms-green': s === 'completed',
  'ohms-red': s === 'failed',
  'ohms-cyan': s === 'loading',
  'ohms-dim': s === 'pending',
})

const loadStatusText = (s) => ({
  completed: '完成',
  failed: '失败',
  loading: '加载中',
  pending: '等待',
}[s] || s)

const formatSize = (b) => {
  if (!b) return '--'
  if (b > 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + ' MB'
  if (b > 1024) return (b / 1024).toFixed(1) + ' KB'
  return b + ' B'
}

const formatTime = (t) => t ? new Date(t).toLocaleTimeString() : '--'

onMounted(() => {
  fetchTasks()

  on('load_started', (data) => {
    activeLoadTasks.value.push({ ...data, progress: 0 })
    fetchTasks()
  })

  on('load_progress', (data) => {
    const idx = activeLoadTasks.value.findIndex(t => t.task_id === data.task_id)
    if (idx >= 0) {
      activeLoadTasks.value[idx] = data
    }
    if (data.status === 'completed') {
      setTimeout(() => {
        activeLoadTasks.value = activeLoadTasks.value.filter(t => t.task_id !== data.task_id)
        fetchTasks()
      }, 2000)
    }
  })
})
</script>
