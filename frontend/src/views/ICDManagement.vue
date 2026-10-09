<template>
  <div>
    <!-- 标题与操作区 -->
    <div class="ohms-panel" style="margin-bottom: 16px;">
      <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
        <span class="ohms-title" style="font-size: 14px;">ICD MANAGEMENT / ICD管理</span>
        <span class="ohms-dim" style="font-size: 12px;">标准格式ICD导入 · 自动解析 · 功能模型生成</span>
        <span style="flex: 1;"></span>
        <span class="ohms-dim">
          已加载: <span class="ohms-cyan">{{ members.length }}</span> 个成员系统
          <span v-if="icdLoaded" class="ohms-green">✓</span>
          <span v-else class="ohms-red">✗</span>
        </span>
      </div>
      <div style="display: flex; gap: 12px; align-items: center; margin-top: 12px; flex-wrap: wrap;">
        <input ref="fileInput" type="file" accept=".json" style="display: none;" @change="onFileChange" />
        <el-button type="primary" @click="pickFile" :loading="importing">
          导入ICD文件
        </el-button>
        <el-button @click="generateDemo" :loading="generating">生成演示ICD</el-button>
        <span v-if="selectedFile" class="ohms-dim" style="font-size: 12px;">
          已选: <span class="ohms-cyan">{{ selectedFile }}</span>
        </span>
      </div>
    </div>

    <!-- 导入结果统计 -->
    <el-card v-if="importResult" style="margin-bottom: 16px;">
      <template #header>◆ 最近导入结果</template>
      <el-row :gutter="16">
        <el-col :span="6"><div class="stat-item"><div class="ohms-label">消息帧</div><div class="ohms-value">{{ importResult.total_messages }}</div></div></el-col>
        <el-col :span="6"><div class="stat-item"><div class="ohms-label">信号</div><div class="ohms-value">{{ importResult.total_signals }}</div></div></el-col>
        <el-col :span="6"><div class="stat-item"><div class="ohms-label">成员系统</div><div class="ohms-value">{{ importResult.total_members }}</div></div></el-col>
        <el-col :span="6"><div class="stat-item"><div class="ohms-label">冲突</div><div class="ohms-value" :style="{color: importResult.conflicts.length ? '#ff3333' : '#00ff00'}">{{ importResult.conflicts.length }}</div></div></el-col>
      </el-row>
      <div v-if="importResult.db_saved != null" class="ohms-dim" style="margin-top: 8px; font-size: 12px;">
        已入库 <span class="ohms-cyan">{{ importResult.db_saved }}</span> 条信号记录
      </div>
      <div v-if="importResult.conflicts.length" style="margin-top: 12px;">
        <div v-for="(c, i) in importResult.conflicts" :key="i" class="ohms-yellow" style="font-size: 12px;">⚠ {{ c }}</div>
      </div>
      <div v-if="importResult.errors.length" style="margin-top: 12px;">
        <div v-for="(e, i) in importResult.errors" :key="i" class="ohms-red" style="font-size: 12px;">✗ {{ e }}</div>
      </div>
    </el-card>

    <!-- 导入日志 -->
    <el-card v-if="importLog.length" style="margin-bottom: 16px;">
      <template #header>◆ 导入日志</template>
      <div class="log-list">
        <div v-for="(l, i) in importLog" :key="i" class="log-item">
          <span class="log-time ohms-dim">{{ l.timestamp }}</span>
          <span class="log-step">{{ l.step }}</span>
          <span class="log-msg" :class="levelClass(l.level)">{{ l.message }}</span>
        </div>
      </div>
    </el-card>

    <!-- 功能模型概览 -->
    <el-card style="margin-bottom: 16px;">
      <template #header>◆ AHMU功能模型 (MemberModel) — 成员系统 → 消息帧 → 信号</template>
      <div class="member-grid">
        <div v-for="m in model" :key="m.member_system" class="member-card" @click="filterByMember(m.member_system)">
          <div class="member-name ohms-cyan">{{ m.member_system }}</div>
          <div class="member-meta ohms-dim">帧 {{ m.frame_count }} · 信号 {{ m.signal_count }}</div>
        </div>
      </div>
      <div v-if="!model.length" class="ohms-dim" style="text-align: center; padding: 40px;">
        暂无ICD数据, 请先导入或生成演示ICD
      </div>
    </el-card>

    <!-- 消息帧列表 -->
    <el-card v-if="frames.length">
      <template #header>
        ◆ 消息帧列表
        <el-tag v-if="filterMember" size="small" closable @close="clearFilter" style="margin-left: 8px;">
          {{ filterMember }}
        </el-tag>
      </template>
      <el-table :data="frames" size="small" style="width: 100%;" @row-click="onFrameClick">
        <el-table-column prop="message_id" label="消息帧标识" min-width="220" show-overflow-tooltip />
        <el-table-column prop="member_system" label="成员系统" width="120" />
        <el-table-column prop="protocol" label="协议" width="80" />
        <el-table-column label="协议标识" width="100">
          <template #default="{ row }">
            <span v-if="row.protocol === 'A429'">Label {{ row.label_id }}</span>
            <span v-else-if="row.protocol === 'A825'">CAN 0x{{ (row.can_id || 0).toString(16).toUpperCase() }}</span>
            <span v-else-if="row.protocol === 'A664'">VL {{ row.vl_id }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="signal_count" label="信号数" width="80" />
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click.stop="onFrameClick(row)">信号字典</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 信号字典详情弹窗 -->
    <el-dialog v-model="dictVisible" :title="`信号字典 — ${dictFrame?.message_id || ''}`" width="760px">
      <div v-if="dictFrame" class="ohms-dim" style="font-size: 12px; margin-bottom: 12px;">
        协议 {{ dictFrame.protocol }} ·
        <span v-if="dictFrame.protocol === 'A429'">Label {{ dictFrame.label_id }}</span>
        <span v-else-if="dictFrame.protocol === 'A825'">CAN 0x{{ (dictFrame.can_id || 0).toString(16).toUpperCase() }}</span>
        <span v-else-if="dictFrame.protocol === 'A664'">VL {{ dictFrame.vl_id }} · Port {{ dictFrame.port_id }}</span>
      </div>
      <el-table :data="dictSignals" size="small" style="width: 100%;">
        <el-table-column prop="name" label="信号名称" min-width="130" />
        <el-table-column prop="bit_offset" label="位偏移" width="70" />
        <el-table-column prop="bit_length" label="位长度" width="70" />
        <el-table-column prop="resolution" label="分辨率" width="80" />
        <el-table-column prop="offset" label="偏移量" width="80" />
        <el-table-column prop="unit" label="单位" width="70" />
        <el-table-column prop="data_type" label="类型" width="70" />
        <el-table-column label="范围" width="110">
          <template #default="{ row }">
            {{ row.min_val ?? '—' }} ~ {{ row.max_val ?? '—' }}
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- parse_any 解析演示 -->
    <el-card v-if="icdLoaded" style="margin-top: 16px;">
      <template #header>◆ parse_any 解析演示 (ICD变更自动适配, 业务脚本无需修改)</template>
      <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
        <el-select v-model="parseMember" placeholder="成员系统" size="small" style="width: 160px;" @change="loadParseFrames">
          <el-option v-for="m in members" :key="m" :label="m" :value="m" />
        </el-select>
        <el-select v-model="parseFrame" placeholder="消息帧" size="small" style="width: 180px;">
          <el-option v-for="f in parseFrames" :key="f" :label="f" :value="f" />
        </el-select>
        <el-input v-model="parseHex" placeholder="原始数据 hex (如 001F) " size="small" style="width: 200px;" />
        <el-button type="primary" size="small" @click="runParse" :loading="parsing">解析</el-button>
      </div>
      <div v-if="parseResult" style="margin-top: 12px;">
        <div v-if="parseResult.error" class="ohms-red">{{ parseResult.error }}</div>
        <el-table v-else :data="parseRows" size="small" style="width: 100%;">
          <el-table-column prop="name" label="信号" width="150" />
          <el-table-column prop="value" label="值" width="150" />
          <el-table-column prop="unit" label="单位" width="90" />
          <el-table-column prop="validity" label="有效性" width="110" />
        </el-table>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'

const members = ref([])
const icdLoaded = ref(false)
const generating = ref(false)
const importing = ref(false)
const importResult = ref(null)
const importLog = ref([])
const model = ref([])
const frames = ref([])
const filterMember = ref('')
const selectedFile = ref('')
const fileInput = ref(null)

// 信号字典弹窗
const dictVisible = ref(false)
const dictFrame = ref(null)
const dictSignals = ref([])

// parse 演示
const parseMember = ref('')
const parseFrame = ref('')
const parseHex = ref('')
const parseFrames = ref([])
const parseResult = ref(null)
const parsing = ref(false)

const parseRows = computed(() => {
  if (!parseResult.value || !parseResult.value.parsed) return []
  return Object.entries(parseResult.value.parsed).map(([name, v]) => ({
    name,
    value: v.value,
    unit: v.unit,
    validity: v.validity,
  }))
})

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const levelClass = (level) => {
  if (level === 'error') return 'ohms-red'
  if (level === 'warning') return 'ohms-yellow'
  return 'ohms-green'
}

const fetchAll = () => {
  fetch(api('/api/v1/icd/members')).then(r => r.json()).then(data => {
    members.value = data.members || []
    icdLoaded.value = data.is_loaded
  })
  fetch(api('/api/v1/icd/model')).then(r => r.json()).then(data => {
    model.value = data.model || []
  })
  fetch(api('/api/v1/icd/frames')).then(r => r.json()).then(data => {
    frames.value = data.frames || []
  })
  fetch(api('/api/v1/icd/log')).then(r => r.json()).then(data => {
    importLog.value = data.import_log || []
  })
}

const pickFile = () => {
  fileInput.value?.click()
}

const onFileChange = (e) => {
  const file = e.target.files[0]
  if (!file) return
  selectedFile.value = file.name
  uploadFile(file)
  e.target.value = ''
}

const uploadFile = (file) => {
  importing.value = true
  const form = new FormData()
  form.append('file', file)
  fetch(api('/api/v1/icd/upload'), { method: 'POST', body: form })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        importResult.value = data.result
        importLog.value = data.result.import_log || []
        ElMessage.success(`ICD导入完成: ${data.result.total_messages}条消息帧, ${data.result.total_signals}个信号`)
        fetchAll()
      } else {
        ElMessage.error(data.message || '导入失败')
      }
    })
    .catch(e => ElMessage.error('导入失败: ' + e.message))
    .finally(() => { importing.value = false })
}

const generateDemo = () => {
  generating.value = true
  fetch(api('/api/v1/icd/generate-demo?member_count=20'), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        importResult.value = data.result
        importLog.value = data.result.import_log || []
        ElMessage.success(`演示ICD已生成并导入: ${data.result.total_messages}条消息`)
        fetchAll()
      }
    })
    .finally(() => { generating.value = false })
}

const filterByMember = (m) => {
  filterMember.value = m
  fetch(api(`/api/v1/icd/frames?member=${encodeURIComponent(m)}`)).then(r => r.json()).then(data => {
    frames.value = data.frames || []
  })
}

const clearFilter = () => {
  filterMember.value = ''
  fetch(api('/api/v1/icd/frames')).then(r => r.json()).then(data => {
    frames.value = data.frames || []
  })
}

const onFrameClick = (row) => {
  fetch(api(`/api/v1/icd/frame/${encodeURIComponent(row.member_system)}/${encodeURIComponent(row.frame_name)}`))
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        dictFrame.value = data.frame
        dictSignals.value = data.frame.signals || []
        dictVisible.value = true
      } else {
        ElMessage.warning(data.message)
      }
    })
}

const loadParseFrames = () => {
  const m = model.value.find(x => x.member_system === parseMember.value)
  parseFrames.value = m ? m.frames : []
  parseFrame.value = ''
}

const runParse = () => {
  if (!parseMember.value || !parseFrame.value || !parseHex.value) {
    ElMessage.warning('请选择成员系统、消息帧并输入 hex 数据')
    return
  }
  parsing.value = true
  fetch(api('/api/v1/icd/parse-demo'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ member_system: parseMember.value, frame_name: parseFrame.value, raw_hex: parseHex.value }),
  })
    .then(r => r.json())
    .then(data => { parseResult.value = data })
    .finally(() => { parsing.value = false })
}

onMounted(() => {
  fetchAll()
})
</script>

<style scoped>
.stat-item {
  text-align: center;
  padding: 8px;
}

.member-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 8px;
}

.member-card {
  background: #0a0a0a;
  border: 1px solid #222;
  padding: 10px;
  text-align: center;
  border-radius: 4px;
  cursor: pointer;
  transition: border-color 0.2s;
}

.member-card:hover {
  border-color: #00d4ff;
}

.member-name {
  font-size: 13px;
  font-weight: bold;
}

.member-meta {
  font-size: 11px;
  margin-top: 4px;
}

.log-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 260px;
  overflow-y: auto;
}

.log-item {
  display: flex;
  gap: 12px;
  align-items: baseline;
  font-size: 12px;
  border-bottom: 1px dashed #1a1a1a;
  padding-bottom: 4px;
}

.log-time {
  width: 60px;
  flex-shrink: 0;
}

.log-step {
  width: 70px;
  flex-shrink: 0;
  color: #aaa;
}

.log-msg {
  flex: 1;
  word-break: break-all;
}
</style>
