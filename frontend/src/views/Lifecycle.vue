<template>
  <div>
    <!-- 批量获取 -->
    <div class="ohms-panel" style="margin-bottom: 16px; display: flex; gap: 12px; align-items: center;">
      <span class="ohms-title" style="font-size: 14px;">生命周期管理</span>
      <el-input v-model="memberInput" placeholder="成员系统" style="width: 140px;" />
      <el-button type="primary" @click="retrieveOne">获取单个</el-button>
      <el-button @click="batchRetrieve" :loading="batchLoading">批量获取 (200个)</el-button>
      <span style="flex: 1;"></span>
      <span class="ohms-dim" :class="{'ohms-yellow': mode !== 'maintenance'}">
        {{ mode === 'maintenance' ? '维护模式: 可操作' : '仅维护模式可获取 (当前: 正常模式)' }}
      </span>
    </div>

    <!-- 批量进度 -->
    <el-card v-if="batchProgress.show" style="margin-bottom: 16px;">
      <template #header>◆ 批量获取进度</template>
      <el-progress :percentage="batchProgress.progress" :stroke-width="12" />
      <div style="margin-top: 8px;" class="ohms-dim">
        {{ batchProgress.completed }} / {{ batchProgress.total }}
      </div>
    </el-card>

    <!-- 数据列表 -->
    <el-card>
      <template #header>◆ 生命周期数据</template>
      <el-table :data="items" size="small" v-loading="loading">
        <el-table-column prop="member_system" label="成员系统" width="120" />
        <el-table-column prop="power_on_time" label="上电运行时间" width="150">
          <template #default="{ row }">{{ formatDuration(row.power_on_time) }}</template>
        </el-table-column>
        <el-table-column prop="power_cycle_count" label="上电循环计数" width="120" />
        <el-table-column prop="retrieval_status" label="获取状态" width="100">
          <template #default="{ row }">
            <span :class="row.retrieval_status === 'success' ? 'ohms-green' : 'ohms-red'">
              {{ row.retrieval_status === 'success' ? '成功' : '失败' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="last_retrieved" label="最近获取时间">
          <template #default="{ row }">{{ formatTime(row.last_retrieved) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { on } = useWebSocket()

const memberInput = ref('MEM001')
const items = ref([])
const loading = ref(false)
const batchLoading = ref(false)
const mode = ref('normal')
const batchProgress = reactive({ show: false, total: 0, completed: 0, progress: 0 })

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const retrieveOne = () => {
  if (!memberInput.value) return
  fetch(api(`/api/v1/lifecycle/retrieve/${memberInput.value}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success(`获取成功: 运行${formatDuration(data.power_on_time)}, 循环${data.power_cycle_count}次`)
        fetchItems()
      } else {
        ElMessage.error(data.message)
      }
    })
}

const batchRetrieve = () => {
  batchLoading.value = true
  batchProgress.show = true
  batchProgress.total = 200
  batchProgress.completed = 0
  batchProgress.progress = 0

  fetch(api('/api/v1/lifecycle/batch-retrieve?count=200'), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success(`批量获取完成: 成功${data.success}, 失败${data.failed}`)
      } else {
        ElMessage.error(data.message)
      }
      fetchItems()
    })
    .finally(() => {
      batchLoading.value = false
      setTimeout(() => { batchProgress.show = false }, 3000)
    })
}

const fetchItems = () => {
  loading.value = true
  fetch(api('/api/v1/lifecycle/list?page=1&size=20'))
    .then(r => r.json())
    .then(data => {
      items.value = data.items || []
    })
    .finally(() => { loading.value = false })
}

const fetchMode = () => {
  fetch(api('/api/v1/system/mode'))
    .then(r => r.json())
    .then(data => {
      mode.value = data.mode
    })
}

const formatDuration = (sec) => {
  if (sec == null) return '--'
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  return `${h}h ${m}m`
}

const formatTime = (t) => t ? new Date(t).toLocaleString() : '--'

onMounted(() => {
  fetchItems()
  fetchMode()
  setInterval(fetchMode, 5000)

  on('mode_change', (data) => { mode.value = data.mode })

  on('lifecycle_batch_progress', (data) => {
    batchProgress.total = data.total
    batchProgress.completed = data.completed
    batchProgress.progress = data.progress
  })

  on('lifecycle_batch_completed', () => {
    fetchItems()
  })
})
</script>
