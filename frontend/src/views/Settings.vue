<template>
  <div class="st-container">
    <!-- 顶部标题栏 -->
    <div class="ohms-panel st-toolbar">
      <span class="ohms-title" style="font-size: 14px;">INTERFACE SETTINGS / 界面配置</span>
      <span class="ohms-dim" style="margin-left: 12px; font-size: 12px;">AHMU 仿真器人机界面显示设置</span>
    </div>

    <!-- 标签栏中英文显示 -->
    <div class="ohms-panel st-section">
      <div class="st-section-header">
        <span class="ohms-title" style="font-size: 13px;">LABEL DISPLAY / 标签栏显示</span>
      </div>
      <div class="st-section-body">
        <div class="st-desc">
          选择导航标签栏的显示方式，切换后标签栏立即生效：
        </div>
        <el-radio-group v-model="mode" @change="onModeChange">
          <el-radio v-for="opt in LABEL_MODE_OPTIONS" :key="opt.value" :label="opt.value">
            {{ opt.label }}
          </el-radio>
        </el-radio-group>

        <div class="st-current">
          当前模式：<span class="ohms-cyan">{{ currentModeText }}</span>
        </div>

        <div class="st-preview">
          <div class="st-preview-label">标签栏预览：</div>
          <div class="st-preview-tabs">
            <span v-for="t in previewTabs" :key="t.label" class="st-preview-tab">
              {{ formatLabel(t.label, t.title) }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useLabelMode, LABEL_MODE_OPTIONS } from '../composables/useUiConfig'

const { labelMode, setLabelMode, formatLabel } = useLabelMode()

const mode = ref(labelMode.value)

const previewTabs = [
  { label: 'CENTRAL MAINTENANCE', title: '系统总览' },
  { label: 'CONDITION MONITORING', title: '参数显示' },
  { label: 'AIRCRAFT STATUS', title: '飞机状态消息' },
  { label: 'FAILURE REPORTS', title: '失效报告' },
  { label: 'TIME CYCLE', title: '生命周期' },
]

const currentModeText = computed(() => {
  const m = LABEL_MODE_OPTIONS.find(o => o.value === labelMode.value)
  return m ? m.label : labelMode.value
})

const onModeChange = (val) => {
  setLabelMode(val)
  ElMessage.success(`标签栏显示已切换为：${currentModeText.value}`)
}
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
  gap: 16px;
}

.st-desc {
  color: #bbb;
  font-size: 12px;
}

.st-current {
  color: #aaa;
  font-size: 13px;
}

.st-preview {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.st-preview-label {
  color: #888;
  font-size: 12px;
}

.st-preview-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.st-preview-tab {
  background: #444;
  color: #ddd;
  font-size: 12px;
  font-weight: bold;
  text-transform: uppercase;
  padding: 8px 14px;
  clip-path: polygon(8px 0, 100% 0, calc(100% - 8px) 100%, 0 100%);
  letter-spacing: 0.5px;
}
</style>
