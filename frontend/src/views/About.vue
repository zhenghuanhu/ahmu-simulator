<template>
  <div class="st-container">
    <!-- 顶部标题栏 -->
    <div class="ohms-panel st-toolbar">
      <span class="ohms-title" style="font-size: 14px;">ABOUT / 关于</span>
      <span class="ohms-dim" style="margin-left: 12px; font-size: 12px;">软件版本与授权信息展示</span>
    </div>

    <!-- 版本信息 -->
    <div class="ohms-panel st-section">
      <div class="st-section-header">
        <span class="ohms-title" style="font-size: 13px;">VERSION / 版本信息</span>
      </div>
      <div class="st-section-body">
        <div class="st-field" v-for="f in versionFields" :key="f.label">
          <span class="st-field-label">{{ f.label }}</span>
          <span class="st-field-value">{{ f.value || '—' }}</span>
        </div>
      </div>
    </div>

    <!-- 授权信息 -->
    <div class="ohms-panel st-section">
      <div class="st-section-header">
        <span class="ohms-title" style="font-size: 13px;">LICENSE / 授权信息</span>
      </div>
      <div class="st-section-body">
        <div class="st-field">
          <span class="st-field-label">授权类型</span>
          <span class="st-field-value">{{ license.license_type || '—' }}</span>
        </div>
        <div class="st-field">
          <span class="st-field-label">授权状态</span>
          <el-tag :type="statusTagType" size="small">{{ statusText }}</el-tag>
        </div>
        <div class="st-field">
          <span class="st-field-label">有效期</span>
          <span class="st-field-value">{{ validityText }}</span>
        </div>
        <div class="st-field">
          <span class="st-field-label">授权范围</span>
          <div class="st-scope">
            <div v-for="(s, i) in (license.scope || [])" :key="i" class="st-scope-item">
              {{ s }}
            </div>
            <span v-if="!(license.scope || []).length" class="st-field-hint">—</span>
          </div>
        </div>
        <div class="st-field">
          <span class="st-field-label">授权主体</span>
          <span class="st-field-value">{{ license.licensee || '—' }}</span>
        </div>
        <div class="st-field">
          <span class="st-field-label">设备序列号</span>
          <span class="st-field-value">{{ license.serial_number || '—' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const version = ref({})
const license = ref({})

const versionFields = computed(() => [
  { label: '软件名称', value: version.value.software_name },
  { label: '版本号', value: version.value.version },
  { label: '构建号', value: version.value.build },
  { label: '构建时间', value: version.value.build_time },
  { label: 'Git 提交', value: version.value.git_commit },
  { label: '适用机型', value: version.value.aircraft_model },
])

// 授权状态 -> 中文 + el-tag 类型
const STATUS_MAP = {
  valid: { text: '有效', type: 'success' },
  expiring: { text: '即将过期', type: 'warning' },
  expired: { text: '已过期', type: 'danger' },
  unlicensed: { text: '未授权', type: 'danger' },
  error: { text: '异常', type: 'danger' },
}

const statusText = computed(() => {
  return (STATUS_MAP[license.value.license_status] || { text: license.value.license_status || '未知' }).text
})
const statusTagType = computed(() => {
  return (STATUS_MAP[license.value.license_status] || { type: 'info' }).type
})
const validityText = computed(() => {
  const s = license.value.start_date || ''
  const e = license.value.end_date || ''
  if (!e) return s ? `${s} 起 · 永久有效` : '永久有效'
  return `${s} 至 ${e}`
})

onMounted(() => {
  fetch(api('/api/v1/system/version'))
    .then(r => r.json())
    .then(data => { if (data.version) version.value = data.version })
    .catch(() => { /* 忽略 */ })
  fetch(api('/api/v1/system/license'))
    .then(r => r.json())
    .then(data => { if (data.license) license.value = data.license })
    .catch(() => { /* 忽略 */ })
})
</script>

<style scoped>
.st-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  padding: 4px;
}
.st-toolbar {
  display: flex;
  align-items: center;
  padding: 10px 16px;
}
.st-section {
  padding: 0;
}
.st-section-header {
  padding: 10px 14px;
  border-bottom: 1px solid #555;
  background: #1f1f1f;
}
.st-section-body {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.st-field {
  display: flex;
  align-items: center;
  gap: 14px;
}
.st-field-label {
  color: #ccc;
  font-size: 13px;
  min-width: 120px;
}
.st-field-value {
  color: #00ffff;
  font-size: 13px;
}
.st-field-hint {
  color: #777;
  font-size: 12px;
}
.st-scope {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.st-scope-item {
  color: #ddd;
  font-size: 13px;
}
</style>
