<template>
  <div>
    <div class="ohms-panel" style="margin-bottom: 16px; display: flex; gap: 12px; align-items: center;">
      <span class="ohms-title" style="font-size: 14px;">构型报告管理</span>
      <el-input v-model="filterMember" placeholder="成员系统" style="width: 160px;" clearable />
      <el-button type="primary" @click="fetchConfigs">查询</el-button>
      <el-button @click="batchVerify" :loading="verifying">批量验证 (400个)</el-button>
      <span style="flex: 1;"></span>
      <span class="ohms-dim">共 {{ total }} 条</span>
    </div>

    <el-card>
      <el-table :data="configs" size="small" v-loading="loading">
        <el-table-column prop="member_system" label="成员系统" width="100" />
        <el-table-column prop="config_item" label="构型项" width="100" />
        <el-table-column prop="config_value" label="当前值" min-width="120" />
        <el-table-column prop="expected_value" label="期望值" min-width="120" />
        <el-table-column prop="is_match" label="一致性" width="90">
          <template #default="{ row }">
            <span :class="row.is_match ? 'ohms-green' : 'ohms-red'">
              {{ row.is_match ? '一致' : '不一致' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="config_type" label="类型" width="90" />
        <el-table-column prop="updated_at" label="更新时间" width="150">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
      </el-table>

      <div style="margin-top: 16px; display: flex; justify-content: center;">
        <el-pagination
          v-model:current-page="page"
          :page-size="size"
          :total="total"
          layout="prev, pager, next"
          @current-change="fetchConfigs"
        />
      </div>
    </el-card>

    <!-- 批量验证结果 -->
    <el-dialog v-model="showVerifyResult" title="批量构型验证结果" width="600px">
      <div v-if="verifyResult">
        <el-row :gutter="16" style="margin-bottom: 16px;">
          <el-col :span="8">
            <div class="ohms-panel" style="text-align: center;">
              <div class="ohms-label">检查项</div>
              <div class="ohms-value">{{ verifyResult.total }}</div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="ohms-panel" style="text-align: center;">
              <div class="ohms-label">通过</div>
              <div class="ohms-value" style="color: #00ff00;">{{ verifyResult.pass }}</div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="ohms-panel" style="text-align: center;">
              <div class="ohms-label">不一致</div>
              <div class="ohms-value" style="color: #ff3333;">{{ verifyResult.mismatch }}</div>
            </div>
          </el-col>
        </el-row>
        <el-table v-if="verifyResult.details?.length" :data="verifyResult.details" size="small" max-height="300">
          <el-table-column prop="member" label="成员系统" width="100" />
          <el-table-column prop="item" label="构型项" width="100" />
          <el-table-column prop="value" label="当前值" />
          <el-table-column prop="expected" label="期望值" />
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const { on } = useWebSocket()

const configs = ref([])
const loading = ref(false)
const page = ref(1)
const size = ref(20)
const total = ref(0)
const filterMember = ref('')
const verifying = ref(false)
const showVerifyResult = ref(false)
const verifyResult = ref(null)

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const fetchConfigs = () => {
  loading.value = true
  let url = `/api/v1/config/reports?page=${page.value}&size=${size.value}`
  if (filterMember.value) url += `&member=${filterMember.value}`
  fetch(api(url))
    .then(r => r.json())
    .then(data => {
      configs.value = data.items || []
      total.value = data.total || 0
    })
    .finally(() => { loading.value = false })
}

const batchVerify = () => {
  verifying.value = true
  fetch(api('/api/v1/config/batch-verify?count=400'), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      verifyResult.value = data
      showVerifyResult.value = true
    })
    .finally(() => { verifying.value = false })
}

const formatTime = (t) => t ? new Date(t).toLocaleString() : '--'

onMounted(() => {
  fetchConfigs()
  on('config_mismatch', () => fetchConfigs())
})
</script>
