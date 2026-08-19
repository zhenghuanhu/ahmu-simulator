<template>
  <div>
    <!-- ICD管理操作 -->
    <div class="ohms-panel" style="margin-bottom: 16px; display: flex; gap: 12px; align-items: center;">
      <span class="ohms-title" style="font-size: 14px;">ICD管理</span>
      <el-button type="primary" @click="generateDemo" :loading="generating">生成演示ICD</el-button>
      <el-button @click="importIcd" :loading="importing">导入ICD文件</el-button>
      <span style="flex: 1;"></span>
      <span class="ohms-dim">
        已加载: <span class="ohms-cyan">{{ members.length }}</span> 个成员系统
        <span v-if="icdLoaded" class="ohms-green">✓</span>
        <span v-else class="ohms-red">✗</span>
      </span>
    </div>

    <!-- 导入结果 -->
    <el-card v-if="importResult" style="margin-bottom: 16px;">
      <template #header>◆ 最近导入结果</template>
      <el-row :gutter="16">
        <el-col :span="6"><div class="stat-item"><div class="ohms-label">消息帧</div><div class="ohms-value">{{ importResult.total_messages }}</div></div></el-col>
        <el-col :span="6"><div class="stat-item"><div class="ohms-label">信号</div><div class="ohms-value">{{ importResult.total_signals }}</div></div></el-col>
        <el-col :span="6"><div class="stat-item"><div class="ohms-label">成员系统</div><div class="ohms-value">{{ importResult.total_members }}</div></div></el-col>
        <el-col :span="6"><div class="stat-item"><div class="ohms-label">冲突</div><div class="ohms-value" :style="{color: importResult.conflicts.length ? '#ff3333' : '#00ff00'}">{{ importResult.conflicts.length }}</div></div></el-col>
      </el-row>
      <div v-if="importResult.conflicts.length" style="margin-top: 12px;">
        <div v-for="(c, i) in importResult.conflicts" :key="i" class="ohms-yellow" style="font-size: 12px;">
          ⚠ {{ c }}
        </div>
      </div>
    </el-card>

    <!-- 成员系统列表 -->
    <el-card>
      <template #header>◆ ICD成员系统</template>
      <div class="member-grid">
        <div v-for="m in members" :key="m" class="member-item ohms-cyan">{{ m }}</div>
      </div>
      <div v-if="!members.length" class="ohms-dim" style="text-align: center; padding: 40px;">
        暂无ICD数据, 请先导入或生成演示ICD
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'

const members = ref([])
const icdLoaded = ref(false)
const generating = ref(false)
const importing = ref(false)
const importResult = ref(null)

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const fetchMembers = () => {
  fetch(api('/api/v1/icd/members'))
    .then(r => r.json())
    .then(data => {
      members.value = data.members || []
      icdLoaded.value = data.is_loaded
    })
}

const generateDemo = () => {
  generating.value = true
  fetch(api('/api/v1/icd/generate-demo?member_count=20'), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success('演示ICD已生成')
        // 导入生成的ICD
        return importIcdFile(data.file_path)
      }
    })
    .finally(() => { generating.value = false })
}

const importIcd = () => {
  const path = prompt('请输入ICD文件路径 (JSON格式):')
  if (path) importIcdFile(path)
}

const importIcdFile = (path) => {
  importing.value = true
  fetch(api(`/api/v1/icd/import?file_path=${encodeURIComponent(path)}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        importResult.value = data.result
        ElMessage.success(`ICD导入完成: ${data.result.total_messages}条消息`)
        fetchMembers()
      }
    })
    .finally(() => { importing.value = false })
}

onMounted(() => {
  fetchMembers()
})
</script>

<style scoped>
.stat-item {
  text-align: center;
  padding: 8px;
}

.member-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  gap: 8px;
}

.member-item {
  background: #0a0a0a;
  border: 1px solid #222;
  padding: 8px;
  text-align: center;
  border-radius: 4px;
  font-size: 13px;
}
</style>
