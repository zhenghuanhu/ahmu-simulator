<template>
  <div>
    <!-- ==================== 1. 指令下发面板 ==================== -->
    <div class="ohms-panel" style="margin-bottom: 16px;">
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
        <span class="ohms-title" style="font-size: 14px;">ENGINE TRIM COMMAND</span>
        <span class="ohms-dim" style="font-size: 12px;">配平指令下发（地面 HMI / 驾驶舱 / PMAT）</span>
      </div>
      <el-form :inline="true" style="margin-top: 10px;">
        <el-form-item label="发动机">
          <el-select v-model="form.engine_id" style="width: 100px;">
            <el-option v-for="n in 4" :key="n" :label="`ENGINE ${n}`" :value="n" />
          </el-select>
        </el-form-item>
        <el-form-item label="配平类型">
          <el-select v-model="form.trim_type" style="width: 140px;">
            <el-option label="THRUST 推力" value="thrust" />
            <el-option label="POWER 功率" value="power" />
            <el-option label="FUEL FLOW 燃油" value="fuel_flow" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标配平值 (%)">
          <el-input-number v-model="form.target_trim" :min="-5" :max="5" :step="0.1" :precision="1" style="width: 130px;" />
        </el-form-item>
        <el-form-item label="指令来源">
          <el-radio-group v-model="form.source">
            <el-radio label="ground">GROUND 地面</el-radio>
            <el-radio label="cockpit">COCKPIT 驾驶舱</el-radio>
            <el-radio label="pmat">PMAT</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="onSubmit">下发指令</el-button>
        </el-form-item>
      </el-form>
    </div>

    <!-- ==================== 2. 实时数据面板 ==================== -->
    <el-card style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px;">
          <span>◆ 发动机配平实时数据 (EMU 推送)</span>
          <span class="status-dot" :class="wsConnected ? 'green' : 'red'"></span>
          <span class="ohms-dim" style="font-size: 12px;">
            {{ wsConnected ? 'LIVE 连接正常' : 'OFFLINE 断线' }}
          </span>
          <span class="ohms-dim" style="font-size: 12px;">· 更新 {{ formatTime(lastUpdateAt) }}</span>
          <span style="flex: 1;"></span>
          <el-button size="small" @click="fetchTrimData">手动刷新</el-button>
          <el-button size="small" :type="paused ? 'warning' : 'primary'" @click="togglePause">
            {{ paused ? '恢复推送' : '暂停' }}
          </el-button>
        </div>
      </template>

      <el-alert v-if="dataError" :title="dataError" type="error" show-icon :closable="false" style="margin-bottom: 10px;" />

      <el-row :gutter="12">
        <el-col :span="6" v-for="e in sortedTrimData" :key="e.engine_id" style="margin-bottom: 12px;">
          <div class="engine-card" :class="{ 'engine-paused': paused }">
            <div class="engine-head">
              <span class="ohms-label">ENGINE {{ e.engine_id }}</span>
              <span class="engine-trim-state" :class="trimStateClass(e.trim_status)">
                {{ trimStateText(e.trim_status) }}
              </span>
            </div>
            <div class="engine-metrics">
              <div class="metric">
                <div class="metric-label">N1</div>
                <div class="metric-value" :class="validityClass(e.validity)">{{ e.n1?.toFixed(2) ?? '--' }}<span class="metric-unit">%</span></div>
              </div>
              <div class="metric">
                <div class="metric-label">N2</div>
                <div class="metric-value" :class="validityClass(e.validity)">{{ e.n2?.toFixed(2) ?? '--' }}<span class="metric-unit">%</span></div>
              </div>
              <div class="metric">
                <div class="metric-label">EGT</div>
                <div class="metric-value" :class="validityClass(e.validity)">{{ e.egt?.toFixed(1) ?? '--' }}<span class="metric-unit">℃</span></div>
              </div>
              <div class="metric">
                <div class="metric-label">FF</div>
                <div class="metric-value" :class="validityClass(e.validity)">{{ e.fuel_flow?.toFixed(1) ?? '--' }}<span class="metric-unit">kg/h</span></div>
              </div>
              <div class="metric">
                <div class="metric-label">THRUST</div>
                <div class="metric-value" :class="validityClass(e.validity)">{{ e.thrust_rating?.toFixed(1) ?? '--' }}<span class="metric-unit">%</span></div>
              </div>
              <div class="metric metric-trim">
                <div class="metric-label">TRIM</div>
                <div class="metric-value ohms-cyan">{{ e.trim_value?.toFixed(2) ?? '--' }}<span class="metric-unit">%</span></div>
              </div>
            </div>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <!-- ==================== 3. 指令列表 ==================== -->
    <el-card>
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px;">
          <span>◆ 配平指令记录</span>
          <el-select v-model="filterSource" placeholder="来源" clearable style="width: 130px;" @change="onFilterChange">
            <el-option label="GROUND 地面" value="ground" />
            <el-option label="COCKPIT 驾驶舱" value="cockpit" />
            <el-option label="PMAT" value="pmat" />
          </el-select>
          <el-select v-model="filterStatus" placeholder="状态" clearable style="width: 140px;" @change="onFilterChange">
            <el-option label="已完成" value="completed" />
            <el-option label="执行中" value="applied" />
            <el-option label="等待确认" value="waiting_ack" />
            <el-option label="超时" value="timeout" />
            <el-option label="已拒绝" value="rejected" />
          </el-select>
          <span style="flex: 1;"></span>
          <span class="ohms-dim">共 {{ commandTotal }} 条</span>
          <el-button size="small" @click="fetchCommands">刷新</el-button>
        </div>
      </template>

      <el-alert v-if="commandsError" :title="commandsError" type="error" show-icon :closable="false" style="margin-bottom: 10px;" />

      <el-table :data="commands" size="small" v-loading="commandsLoading" empty-text="暂无配平指令">
        <el-table-column prop="command_id" label="指令号" width="210" />
        <el-table-column prop="engine_id" label="发动机" width="80">
          <template #default="{ row }">ENG {{ row.engine_id }}</template>
        </el-table-column>
        <el-table-column prop="trim_type" label="类型" width="110">
          <template #default="{ row }">{{ trimTypeText(row.trim_type) }}</template>
        </el-table-column>
        <el-table-column prop="target_trim" label="目标值" width="90">
          <template #default="{ row }">
            <span :class="row.target_trim >= 0 ? 'ohms-green' : 'ohms-red'">{{ row.target_trim >= 0 ? '+' : '' }}{{ row.target_trim }}%</span>
          </template>
        </el-table-column>
        <el-table-column prop="source" label="来源" width="120">
          <template #default="{ row }">{{ sourceText(row.source) }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="110">
          <template #default="{ row }">
            <span :class="commandStateClass(row.status)">{{ commandStateText(row.status) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="applied_trim" label="实际配平" width="90">
          <template #default="{ row }">
            <span v-if="row.applied_trim != null">{{ row.applied_trim }}%</span>
            <span v-else class="ohms-dim">--</span>
          </template>
        </el-table-column>
        <el-table-column prop="reject_reason" label="结果/原因" min-width="160">
          <template #default="{ row }">
            <span v-if="row.reject_reason" class="ohms-yellow">{{ row.reject_reason }}</span>
            <span v-else-if="row.status === 'completed'" class="ohms-green">配平完成</span>
            <span v-else class="ohms-dim">--</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="下发时间" width="90">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useEngineTrim } from '../composables/useEngineTrim'

const {
  wsConnected, submitting,
  commands, commandsLoading, commandsError, commandTotal,
  filterSource, filterStatus,
  trimData, sortedTrimData, lastUpdateAt, paused, dataError,
  submitCommand, fetchCommands, fetchTrimData, togglePause, subscribe,
} = useEngineTrim()

// 指令下发表单
const form = reactive({
  engine_id: 1,
  trim_type: 'thrust',
  target_trim: 1.0,
  source: 'ground',
})

// ---------- 交互逻辑 ----------

const onSubmit = async () => {
  if (form.target_trim == null || isNaN(form.target_trim)) {
    ElMessage.warning('请输入目标配平值')
    return
  }
  const data = await submitCommand({
    engine_id: form.engine_id,
    trim_type: form.trim_type,
    target_trim: form.target_trim,
    source: form.source,
    source_terminal: form.source === 'ground' ? 'GROUND_HMI_01'
      : form.source === 'cockpit' ? 'CPT_MCDU' : 'PMAT_01',
    operator: 'TEST',
  })
  if (data.status === 'ok') {
    ElMessage.success(`配平指令已下发: ${data.command_id}`)
    fetchCommands()
  } else {
    ElMessage.error(data.message || '下发失败')
  }
}

const onFilterChange = () => {
  fetchCommands()
}

// ---------- 文本/样式映射 ----------

const sourceText = (s) => ({ ground: 'GROUND 地面', cockpit: 'COCKPIT 驾驶舱', pmat: 'PMAT' }[s] || s)
const trimTypeText = (t) => ({ thrust: '推力', power: '功率', fuel_flow: '燃油流量' }[t] || t)

const commandStateText = (s) => ({
  pending: '待处理', validating: '校验中', sending: '下发中', waiting_ack: '等待确认',
  applied: '执行中', completed: '已完成', timeout: '超时', rejected: '已拒绝',
}[s] || s)

const commandStateClass = (s) => ({
  completed: 'ohms-green', applied: 'ohms-cyan', waiting_ack: 'ohms-yellow',
  timeout: 'ohms-yellow', rejected: 'ohms-red', pending: 'ohms-dim',
  validating: 'ohms-dim', sending: 'ohms-dim',
}[s] || 'ohms-dim')

const trimStateText = (s) => ({
  monitoring: '监测中', applying: '配平中', applied: '已到位',
}[s] || s)

const trimStateClass = (s) => ({
  monitoring: 'ohms-dim', applying: 'ohms-yellow', applied: 'ohms-green',
}[s] || 'ohms-dim')

const validityClass = (v) => ({
  'ohms-red': v === 'out_of_range',
  'ohms-dim': v === 'unavailable',
  'ohms-cyan': !v || v === 'valid',
})

const formatTime = (t) => t ? new Date(t).toLocaleTimeString() : '--'

// ---------- 生命周期 ----------

onMounted(() => {
  subscribe()        // 订阅 WebSocket 实时推送
  fetchCommands()    // 初始加载指令列表
  fetchTrimData()    // 初始加载实时数据 (兜底)
})
</script>

<style scoped>
.engine-card {
  background: #000000;
  border: 1px solid #888888;
  padding: 10px;
  transition: border-color 0.2s;
}

.engine-card.engine-paused {
  border-color: #ffff00;
  opacity: 0.7;
}

.engine-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  padding-bottom: 6px;
  border-bottom: 1px solid #555555;
}

.engine-trim-state {
  font-size: 11px;
  font-weight: bold;
}

.engine-metrics {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}

.metric {
  background: #111111;
  border: 1px solid #333333;
  padding: 6px 8px;
}

.metric-label {
  color: #888888;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 1px;
}

.metric-value {
  font-size: 16px;
  font-weight: bold;
  margin-top: 2px;
  color: #00ffff;
}

.metric-unit {
  font-size: 10px;
  color: #888888;
  margin-left: 2px;
}

.metric-trim .metric-value {
  color: #00ffff;
}
</style>
