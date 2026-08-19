<template>
  <div>
    <!-- 工具栏 -->
    <div class="ohms-panel" style="margin-bottom: 16px; display: flex; gap: 12px; align-items: center;">
      <span class="ohms-title" style="font-size: 14px;">故障报告列表</span>
      <el-input v-model="filterMember" placeholder="成员系统 (如MEM001)" style="width: 180px;" clearable />
      <el-select v-model="filterStatus" placeholder="状态" style="width: 120px;" clearable>
        <el-option label="激活" value="active" />
        <el-option label="已解决" value="resolved" />
      </el-select>
      <el-button type="primary" @click="fetchFaults">查询</el-button>
      <el-button @click="showSimulate = true">模拟故障</el-button>
      <span style="flex: 1;"></span>
      <span class="ohms-dim">共 {{ total }} 条</span>
    </div>

    <!-- 故障列表 -->
    <el-card>
      <el-table :data="faults" size="small" style="width: 100%" v-loading="loading">
        <el-table-column prop="member_system" label="成员系统" width="100" />
        <el-table-column prop="fault_code" label="故障代码" width="130" />
        <el-table-column prop="fault_text" label="故障描述" min-width="200" />
        <el-table-column prop="severity" label="等级" width="80">
          <template #default="{ row }">
            <span :class="severityClass(row.severity)">{{ row.severity }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="80">
          <template #default="{ row }">
            <span :class="row.status === 'active' ? 'ohms-red' : 'ohms-green'">
              {{ row.status === 'active' ? '激活' : '已解决' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="ata_chapter" label="ATA" width="70" />
        <el-table-column prop="flight_segment" label="航段" width="70" />
        <el-table-column prop="fde_code" label="FDE" width="100">
          <template #default="{ row }">
            <span v-if="row.fde_code" class="ohms-yellow">{{ row.fde_code }}</span>
            <span v-else class="ohms-dim">--</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="发生时间" width="150">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button v-if="row.status === 'active'" size="small" type="primary" @click="resolveFault(row.id)">
              解决
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div style="margin-top: 16px; display: flex; justify-content: center;">
        <el-pagination
          v-model:current-page="page"
          :page-size="size"
          :total="total"
          layout="prev, pager, next"
          @current-change="fetchFaults"
        />
      </div>
    </el-card>

    <!-- 模拟故障对话框 -->
    <el-dialog v-model="showSimulate" title="模拟故障注入" width="460px">
      <el-form label-width="90px">
        <el-form-item label="成员系统">
          <el-input v-model="simForm.member" placeholder="MEM001" />
        </el-form-item>
        <el-form-item label="故障代码">
          <el-input v-model.number="simForm.code" placeholder="1234" />
        </el-form-item>
        <el-form-item label="严重等级">
          <el-select v-model="simForm.severity" style="width: 100%;">
            <el-option label="轻微 (minor)" value="minor" />
            <el-option label="主要 (major)" value="major" />
            <el-option label="严重 (critical)" value="critical" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showSimulate = false">取消</el-button>
        <el-button type="primary" @click="simulateFault">注入故障</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { on } = useWebSocket()

const faults = ref([])
const loading = ref(false)
const page = ref(1)
const size = ref(20)
const total = ref(0)
const filterMember = ref('')
const filterStatus = ref('')
const showSimulate = ref(false)
const simForm = reactive({ member: 'MEM001', code: 1001, severity: 'minor' })

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const fetchFaults = () => {
  loading.value = true
  let url = `/api/v1/fault/reports?page=${page.value}&size=${size.value}`
  if (filterMember.value) url += `&member=${filterMember.value}`
  if (filterStatus.value) url += `&status=${filterStatus.value}`
  fetch(api(url))
    .then(r => r.json())
    .then(data => {
      faults.value = data.items || []
      total.value = data.total || 0
    })
    .finally(() => { loading.value = false })
}

const resolveFault = (id) => {
  fetch(api(`/api/v1/fault/${id}/resolve`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success('故障已解决')
        fetchFaults()
      }
    })
}

const simulateFault = () => {
  const { member, code, severity } = simForm
  fetch(api(`/api/v1/fault/simulate?member=${member}&code=${code}&severity=${severity}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success('故障已注入')
        showSimulate.value = false
        fetchFaults()
      }
    })
}

const severityClass = (s) => ({
  'ohms-red': s === 'critical',
  'ohms-yellow': s === 'major',
  'ohms-dim': s === 'minor',
})

const formatTime = (t) => t ? new Date(t).toLocaleString() : '--'

onMounted(() => {
  fetchFaults()
  on('fault_new', () => fetchFaults())
  on('fault_resolved', () => fetchFaults())
})
</script>
