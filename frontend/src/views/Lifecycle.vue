<template>
  <div class="tc-container">
    <!-- 顶部操作栏 -->
    <div class="ohms-panel tc-toolbar">
      <span class="ohms-title" style="font-size: 14px;">TIME CYCLE</span>
      <span class="ohms-dim" style="margin-left: 12px; font-size: 12px;">
        {{ totalEquips }} equipments / {{ totalAtas }} ATA chapters
      </span>
      <span style="flex: 1;"></span>
      <span class="ohms-dim" :class="{'ohms-yellow': mode === 'maintenance'}" style="font-size: 12px; margin-right: 16px;">
        {{ mode === 'maintenance' ? 'MAINTENANCE MODE (仅地面测试/数据加载可操作)' : 'NORMAL MODE' }}
      </span>
      <el-button type="primary" size="small" @click="batchRetrieve" :loading="batchLoading">
        BATCH RETRIEVE (200)
      </el-button>
    </div>

    <!-- 批量进度 -->
    <div v-if="batchProgress.show" class="ohms-panel tc-batch-progress">
      <span class="ohms-label">BATCH PROGRESS</span>
      <div class="tc-progress-bar">
        <div class="tc-progress-fill" :style="{width: batchProgress.progress + '%'}"></div>
      </div>
      <span class="ohms-dim" style="margin-left: 12px;">
        {{ batchProgress.completed }} / {{ batchProgress.total }} ({{ batchProgress.progress }}%)
      </span>
    </div>

    <!-- 获取结果通知 -->
    <div v-if="retrievalResult" class="ohms-panel tc-result-panel">
      <span class="ohms-label">LAST RETRIEVAL</span>
      <span style="margin-left: 16px; color: #fff;">
        {{ retrievalResult.equip_name }}
      </span>
      <span class="ohms-cyan" style="margin-left: 16px;">
        {{ retrievalResult.status_string }}
      </span>
      <span class="ohms-dim" style="margin-left: 16px;">
        Cycles: {{ retrievalResult.power_cycle_count }}
      </span>
    </div>

    <!-- 主体: 左右分栏 -->
    <div class="tc-main">
      <!-- 左侧: ATA分类列表 -->
      <div class="ohms-panel tc-ata-panel">
        <div class="tc-panel-header">
          <span class="ohms-title" style="font-size: 13px;">ATA CHAPTERS</span>
        </div>
        <div class="tc-ata-list" v-loading="ataLoading">
          <div
            v-for="ata in ataList"
            :key="ata.ataCode"
            class="tc-ata-item"
            :class="{active: selectedAta === ata.ataCode}"
            @click="selectAta(ata.ataCode)"
          >
            <span class="tc-ata-code">{{ ata.ataCode }}</span>
            <span class="tc-ata-name">{{ ata.ataName }}</span>
            <span class="tc-ata-count">{{ ata.equipCount }}</span>
          </div>
          <div v-if="!ataLoading && ataList.length === 0" class="tc-empty">
            No ATA data
          </div>
        </div>
      </div>

      <!-- 右侧: 设备列表 -->
      <div class="ohms-panel tc-equip-panel">
        <div class="tc-panel-header">
          <span class="ohms-title" style="font-size: 13px;">
            EQUIPMENTS{{ selectedAta ? ' - ATA ' + selectedAta : '' }}
          </span>
          <span class="ohms-dim" style="margin-left: 12px; font-size: 12px;">
            {{ equipTotal }} total
          </span>
          <span style="flex: 1;"></span>
          <el-button v-if="selectedAta" size="small" @click="selectAta(null)">SHOW ALL</el-button>
        </div>

        <div class="tc-equip-table" v-loading="equipLoading">
          <div class="tc-equip-header">
            <span class="tc-col-id">EQUIP ID</span>
            <span class="tc-col-name">NAME</span>
            <span class="tc-col-avail">AVAILABILITY</span>
            <span class="tc-col-time">POWER ON TIME</span>
            <span class="tc-col-cycle">CYCLES</span>
            <span class="tc-col-status">STATUS</span>
            <span class="tc-col-action">ACTION</span>
          </div>
          <div
            v-for="eq in equipList"
            :key="eq.equipID"
            class="tc-equip-row"
            :class="{
              unavailable: eq.isAvailable === 2,
              abnormal: eq.isAvailable === 0,
              retrieving: retrievingId === eq.equipID,
            }"
          >
            <span class="tc-col-id">{{ eq.equipID }}</span>
            <span class="tc-col-name">{{ eq.equipName }}</span>
            <span class="tc-col-avail">
              <span v-if="eq.isAvailable === 1" class="tc-badge ok">RETRIEVABLE</span>
              <span v-else-if="eq.isAvailable === 2" class="tc-badge na">N/A</span>
              <span v-else class="tc-badge err">ABNORMAL</span>
            </span>
            <span class="tc-col-time">{{ formatTime(eq.powerOnTime) }}</span>
            <span class="tc-col-cycle">{{ eq.powerCycleCount }}</span>
            <span class="tc-col-status">
              <span :class="eq.statusString ? 'ohms-cyan' : 'ohms-dim'">
                {{ eq.statusString || '--:--:--' }}
              </span>
            </span>
            <span class="tc-col-action">
              <button
                v-if="eq.isAvailable === 1"
                class="tc-action-btn"
                :disabled="retrievingId === eq.equipID"
                @click="retrieveOne(eq.equipID)"
              >
                {{ retrievingId === eq.equipID ? 'RETRIEVING...' : 'RETRIEVE' }}
              </button>
              <span v-else class="ohms-dim" style="font-size: 11px;">--</span>
            </span>
          </div>
          <div v-if="!equipLoading && equipList.length === 0" class="tc-empty">
            No equipment data
          </div>
        </div>

        <!-- 分页 -->
        <div class="tc-pagination" v-if="equipTotal > equipPageSize">
          <button class="tc-page-btn" :disabled="equipPage <= 1" @click="changePage(-1)">&lt; PREV</button>
          <span class="ohms-dim" style="margin: 0 12px;">
            Page {{ equipPage }} / {{ Math.ceil(equipTotal / equipPageSize) }}
          </span>
          <button class="tc-page-btn" :disabled="equipPage * equipPageSize >= equipTotal" @click="changePage(1)">NEXT &gt;</button>
        </div>
      </div>
    </div>

    <!-- 底部: 状态摘要 -->
    <div class="ohms-panel tc-summary">
      <span class="ohms-label">STATUS SUMMARY</span>
      <span class="tc-summary-item">
        <span class="ohms-green">●</span> Retrievable: {{ summary.retrievable }}
      </span>
      <span class="tc-summary-item">
        <span class="ohms-yellow">●</span> N/A: {{ summary.na }}
      </span>
      <span class="tc-summary-item">
        <span class="ohms-red">●</span> Abnormal: {{ summary.abnormal }}
      </span>
      <span class="tc-summary-item">
        <span class="ohms-cyan">●</span> Retrieved: {{ summary.retrieved }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'

const { on } = useWebSocket()

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

// ATA data
const ataList = ref([])
const ataLoading = ref(false)
const totalAtas = ref(0)
const selectedAta = ref(null)

// Equipment data
const equipList = ref([])
const equipLoading = ref(false)
const equipTotal = ref(0)
const equipPage = ref(1)
const equipPageSize = ref(16)
const totalEquips = ref(0)

// Retrieval state
const retrievingId = ref(null)
const retrievalResult = ref(null)
const batchLoading = ref(false)
const batchProgress = reactive({ show: false, total: 0, completed: 0, progress: 0 })

// Mode
const mode = ref('normal')

// Summary
const summary = reactive({ retrievable: 0, na: 0, abnormal: 0, retrieved: 0 })

const fetchAtas = () => {
  ataLoading.value = true
  fetch(api('/api/v1/lifecycle/atas?page=1&size=200'))
    .then(r => r.json())
    .then(data => {
      ataList.value = data.tcEquipStructList || []
      totalAtas.value = data.total || 0
    })
    .finally(() => { ataLoading.value = false })
}

const fetchEquips = () => {
  equipLoading.value = true
  const ataParam = selectedAta.value ? `&ata=${selectedAta.value}` : ''
  fetch(api(`/api/v1/lifecycle/equips?page=${equipPage.value}&size=${equipPageSize.value}${ataParam}`))
    .then(r => r.json())
    .then(data => {
      equipList.value = data.tcEquipStructList || []
      equipTotal.value = data.total || 0
      updateSummary()
    })
    .finally(() => { equipLoading.value = false })
}

const fetchStatus = () => {
  fetch(api('/api/v1/lifecycle/status?page=1&size=5000'))
    .then(r => r.json())
    .then(data => {
      totalEquips.value = data.total || 0
    })
}

const updateSummary = () => {
  summary.retrievable = equipList.value.filter(e => e.isAvailable === 1).length
  summary.na = equipList.value.filter(e => e.isAvailable === 2).length
  summary.abnormal = equipList.value.filter(e => e.isAvailable === 0).length
  summary.retrieved = equipList.value.filter(e => e.statusString).length
}

const selectAta = (ataCode) => {
  selectedAta.value = ataCode
  equipPage.value = 1
  fetchEquips()
}

const changePage = (delta) => {
  equipPage.value += delta
  if (equipPage.value < 1) equipPage.value = 1
  fetchEquips()
}

const retrieveOne = (equipId) => {
  retrievingId.value = equipId
  fetch(api(`/api/v1/lifecycle/retrieve/${equipId}`), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        retrievalResult.value = {
          equip_name: data.equip_name,
          status_string: data.status_string,
          power_cycle_count: data.power_cycle_count,
          power_on_time: data.power_on_time,
        }
        ElMessage.success(`${data.equip_name}: ${data.status_string}, ${data.power_cycle_count} cycles`)
        fetchEquips()
      } else {
        ElMessage.error(data.message)
      }
    })
    .catch(() => { ElMessage.error('Network error') })
    .finally(() => { retrievingId.value = null })
}

const batchRetrieve = () => {
  if (mode.value === 'maintenance') {
    ElMessage.warning('Maintenance mode: only Ground Test / Data Load operations allowed')
    return
  }
  batchLoading.value = true
  batchProgress.show = true
  batchProgress.total = 200
  batchProgress.completed = 0
  batchProgress.progress = 0

  fetch(api('/api/v1/lifecycle/batch-retrieve?count=200'), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok' || data.success !== undefined) {
        ElMessage.success(`Batch complete: ${data.success} success, ${data.failed} failed`)
      } else {
        ElMessage.error(data.message)
      }
      fetchEquips()
    })
    .finally(() => {
      batchLoading.value = false
      setTimeout(() => { batchProgress.show = false }, 3000)
    })
}

const fetchMode = () => {
  fetch(api('/api/v1/system/mode'))
    .then(r => r.json())
    .then(data => { mode.value = data.mode })
    .catch(() => {})
}

const formatTime = (sec) => {
  if (!sec || sec <= 0) return '--'
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

onMounted(() => {
  fetchAtas()
  fetchEquips()
  fetchStatus()
  fetchMode()
  setInterval(fetchMode, 5000)

  on('mode_change', (data) => { mode.value = data.mode })

  on('lifecycle_batch_progress', (data) => {
    batchProgress.total = data.total
    batchProgress.completed = data.completed
    batchProgress.progress = data.progress
  })

  on('lifecycle_batch_completed', () => {
    fetchEquips()
    fetchStatus()
  })

  on('lifecycle_retrieved', () => {
    fetchEquips()
  })
})
</script>

<style scoped>
.tc-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
}

.tc-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
}

.tc-batch-progress {
  display: flex;
  align-items: center;
  padding: 10px 16px;
}

.tc-progress-bar {
  flex: 1;
  height: 14px;
  background: #111;
  border: 1px solid #555;
  margin-left: 16px;
  overflow: hidden;
}

.tc-progress-fill {
  height: 100%;
  background: #00ffff;
  transition: width 0.3s;
}

.tc-result-panel {
  display: flex;
  align-items: center;
  padding: 10px 16px;
}

.tc-main {
  display: flex;
  gap: 12px;
  flex: 1;
  min-height: 0;
}

.tc-ata-panel {
  width: 280px;
  display: flex;
  flex-direction: column;
  padding: 0;
}

.tc-panel-header {
  padding: 10px 14px;
  border-bottom: 1px solid #555;
  display: flex;
  align-items: center;
  background: #1f1f1f;
}

.tc-ata-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}

.tc-ata-item {
  display: flex;
  align-items: center;
  padding: 8px 14px;
  cursor: pointer;
  border-bottom: 1px solid #222;
  transition: background 0.15s;
}

.tc-ata-item:hover {
  background: #2a2a2a;
}

.tc-ata-item.active {
  background: #444;
}

.tc-ata-item.active .tc-ata-code {
  color: #00ffff;
}

.tc-ata-code {
  color: #fff;
  font-weight: bold;
  width: 36px;
  font-size: 13px;
}

.tc-ata-name {
  color: #bbb;
  flex: 1;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tc-ata-count {
  color: #888;
  font-size: 11px;
  min-width: 24px;
  text-align: right;
}

.tc-equip-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 0;
  min-width: 0;
}

.tc-equip-table {
  flex: 1;
  overflow-y: auto;
}

.tc-equip-header,
.tc-equip-row {
  display: grid;
  grid-template-columns: 80px 1fr 100px 120px 60px 90px 90px;
  align-items: center;
  padding: 0 12px;
  gap: 8px;
}

.tc-equip-header {
  height: 36px;
  border-bottom: 1px solid #555;
  background: #1f1f1f;
  font-size: 11px;
  text-transform: uppercase;
  color: #aaa;
  letter-spacing: 1px;
  position: sticky;
  top: 0;
  z-index: 1;
}

.tc-equip-row {
  height: 40px;
  border-bottom: 1px solid #222;
  font-size: 12px;
  color: #fff;
}

.tc-equip-row:hover {
  background: #1a1a1a;
}

.tc-equip-row.unavailable {
  opacity: 0.5;
}

.tc-equip-row.abnormal {
  background: rgba(255, 50, 50, 0.08);
}

.tc-equip-row.retrieving {
  background: rgba(0, 255, 255, 0.08);
}

.tc-col-id {
  color: #00ffff;
  font-size: 11px;
}

.tc-col-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tc-badge {
  display: inline-block;
  padding: 2px 8px;
  font-size: 10px;
  font-weight: bold;
  letter-spacing: 1px;
}

.tc-badge.ok {
  background: #1a3a1a;
  color: #00ff00;
  border: 1px solid #2a5a2a;
}

.tc-badge.na {
  background: #3a3a00;
  color: #ffff00;
  border: 1px solid #5a5a00;
}

.tc-badge.err {
  background: #3a0000;
  color: #ff3333;
  border: 1px solid #5a0000;
}

.tc-action-btn {
  background: #444;
  border: 1px solid #888;
  color: #fff;
  padding: 4px 12px;
  font-size: 11px;
  cursor: pointer;
  font-family: inherit;
  clip-path: polygon(6px 0, 100% 0, calc(100% - 6px) 100%, 0 100%);
}

.tc-action-btn:hover:not(:disabled) {
  background: #666;
  color: #00ffff;
}

.tc-action-btn:disabled {
  background: #333;
  color: #666;
  cursor: wait;
}

.tc-pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 10px;
  border-top: 1px solid #555;
  background: #1f1f1f;
}

.tc-page-btn {
  background: #333;
  border: 1px solid #666;
  color: #fff;
  padding: 4px 16px;
  font-size: 11px;
  cursor: pointer;
  font-family: inherit;
}

.tc-page-btn:disabled {
  color: #555;
  cursor: not-allowed;
}

.tc-page-btn:hover:not(:disabled) {
  background: #555;
}

.tc-summary {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 8px 16px;
}

.tc-summary-item {
  font-size: 12px;
  color: #bbb;
}

.tc-empty {
  padding: 40px;
  text-align: center;
  color: #555;
  font-size: 13px;
}
</style>
