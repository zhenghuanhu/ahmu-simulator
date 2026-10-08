<template>
  <div class="cs-container">
    <!-- 顶部工具栏 -->
    <div class="ohms-panel cs-toolbar">
      <span class="ohms-title" style="font-size: var(--cs-fs-title);">COMM SECURITY / 通信安全防护</span>
      <span class="ohms-dim" style="margin-left: 8px; font-size: var(--cs-fs-aux);">OHMS 4.3.5</span>
      <span style="flex: 1;"></span>
      <span class="ohms-dim" style="font-size: var(--cs-fs-aux);">VPN 隧道:</span>
      <span :class="vpnActive ? 'ohms-green' : 'ohms-dim'" style="font-size: var(--cs-fs-body);">{{ vpnActive ? '加密中' : '未建立' }}</span>
      <span class="ohms-dim" style="margin-left: 16px; font-size: var(--cs-fs-aux);">数据包 通过/丢弃:</span>
      <span class="ohms-green" style="font-size: var(--cs-fs-body);">{{ stats.packets_passed || 0 }}</span>
      <span class="ohms-dim" style="font-size: var(--cs-fs-body);">/</span>
      <span class="ohms-red" style="font-size: var(--cs-fs-body);">{{ stats.packets_dropped || 0 }}</span>
    </div>

    <el-tabs v-model="activeTab" type="card" class="cs-tabs">
      <!-- 完整性校验 -->
      <el-tab-pane label="完整性校验" name="crc">
        <div class="ohms-panel cs-tab-panel">
          <div class="cs-panel-header">
            <span class="ohms-title" style="font-size: var(--cs-fs-panel);">DATA INTEGRITY / 链路数据完整性校验 (CRC32)</span>
          </div>
          <div class="cs-body">
            <div class="cs-field">
              <span class="cs-label">数据包内容</span>
              <el-input v-model="crcPayload" type="textarea" :rows="2" placeholder="输入要发送的数据包内容" size="small" style="flex: 1;" />
            </div>
            <div class="cs-actions">
              <el-button size="small" type="primary" @click="appendCrc">追加 CRC (发送方)</el-button>
              <span v-if="crcResult" class="ohms-cyan" style="font-size: var(--cs-fs-body);">{{ crcResult }}</span>
            </div>
            <div class="cs-divider"></div>
            <div class="cs-field">
              <span class="cs-label">接收数据包</span>
              <el-input v-model="receivePayload" placeholder="payload" size="small" style="flex: 1;" />
              <span class="cs-label">CRC</span>
              <el-input v-model="receiveCrc" placeholder="CRC 值" size="small" style="width: 120px;" />
            </div>
            <div class="cs-actions">
              <el-button size="small" @click="receivePacket">接收校验</el-button>
              <span :class="receiveResult?.valid ? 'ohms-green' : 'ohms-red'" style="font-size: var(--cs-fs-body);">{{ receiveResult?.message || '' }}</span>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- VPN 隧道 -->
      <el-tab-pane label="VPN 隧道" name="vpn">
        <div class="ohms-panel cs-tab-panel">
          <div class="cs-panel-header">
            <span class="ohms-title" style="font-size: var(--cs-fs-panel);">VPN TUNNEL / 加密隧道</span>
            <span :class="vpnActive ? 'ohms-green' : 'ohms-dim'" style="margin-left: 12px; font-size: var(--cs-fs-body);">{{ vpnActive ? '隧道已建立' : '隧道未建立' }}</span>
          </div>
          <div class="cs-body">
            <div class="cs-desc">对无线链路建立加密隧道，防止数据暴露或外部攻击。</div>
            <div class="cs-actions">
              <el-button size="small" type="primary" @click="establishVpn" :disabled="vpnActive">建立加密隧道</el-button>
              <el-button size="small" type="danger" @click="teardownVpn" :disabled="!vpnActive">断开隧道</el-button>
            </div>
            <div v-if="vpnInfo" class="cs-vpn-info">
              <div class="cs-vpn-row"><span class="cs-label">协议</span><span class="ohms-text">{{ vpnInfo.protocol }}</span></div>
              <div class="cs-vpn-row"><span class="cs-label">加密算法</span><span class="ohms-text">{{ vpnInfo.cipher }}</span></div>
              <div class="cs-vpn-row"><span class="cs-label">认证</span><span class="ohms-text">{{ vpnInfo.auth }}</span></div>
              <div class="cs-vpn-row"><span class="cs-label">建立时间</span><span class="ohms-text">{{ formatTime(vpnInfo.established_at) }}</span></div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- WiFi 安全 -->
      <el-tab-pane label="WiFi 安全" name="wifi">
        <div class="cs-two-col">
          <div class="ohms-panel cs-tab-panel">
            <div class="cs-panel-header">
              <span class="ohms-title" style="font-size: var(--cs-fs-panel);">WIFI WHITELIST / 白名单</span>
            </div>
            <div class="cs-body">
              <el-table :data="whitelist" size="small" max-height="200">
                <el-table-column prop="ssid" label="SSID" width="160" />
                <el-table-column prop="psk" label="预共享密钥 (PSK)" min-width="140" />
                <el-table-column prop="description" label="描述" width="140" show-overflow-tooltip />
                <el-table-column label="操作" width="70">
                  <template #default="{ row }">
                    <el-button size="small" type="danger" @click="removeWhitelist(row.ssid)">移除</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <div class="cs-field" style="margin-top: 10px;">
                <el-input v-model="newSsid" placeholder="SSID" size="small" style="width: 150px;" />
                <el-input v-model="newPsk" placeholder="预共享密钥 PSK" size="small" style="width: 170px;" />
                <el-button size="small" type="primary" @click="addWhitelist">加入白名单</el-button>
              </div>
            </div>
          </div>

          <div class="ohms-panel cs-tab-panel">
            <div class="cs-panel-header">
              <span class="ohms-title" style="font-size: var(--cs-fs-panel);">WIFI SCAN & AUTH / 扫描与认证</span>
              <span style="flex: 1;"></span>
              <el-button size="small" @click="scanWifi">Wi-Fi 扫描</el-button>
            </div>
            <div class="cs-body">
              <el-table :data="scanResult" size="small" max-height="180">
                <el-table-column prop="ssid" label="SSID" min-width="140" />
                <el-table-column label="信号" width="80">
                  <template #default="{ row }">{{ row.signal }} dBm</template>
                </el-table-column>
                <el-table-column label="可信" width="80">
                  <template #default="{ row }">
                    <span :class="row.trusted ? 'ohms-green' : 'ohms-red'">{{ row.trusted ? '可信' : '不可信' }}</span>
                  </template>
                </el-table-column>
              </el-table>
              <div class="cs-divider"></div>
              <div class="cs-field">
                <span class="cs-label">认证</span>
                <el-input v-model="authSsid" placeholder="SSID" size="small" style="width: 150px;" />
                <el-input v-model="authPsk" placeholder="PSK" size="small" style="width: 150px;" />
                <el-button size="small" type="primary" @click="authenticate">PSK 认证</el-button>
              </div>
              <div v-if="authResult" :class="authResult.authenticated ? 'ohms-green' : 'ohms-red'" style="font-size: var(--cs-fs-body);">{{ authResult.message }}</div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- 安全日志 -->
      <el-tab-pane label="安全日志" name="logs">
        <div class="ohms-panel cs-tab-panel">
          <div class="cs-panel-header">
            <span class="ohms-title" style="font-size: var(--cs-fs-panel);">SECURITY LOGS / 通信安全日志</span>
            <span class="ohms-dim" style="margin-left: 12px; font-size: var(--cs-fs-body);">{{ logTotal }} 条</span>
          </div>
          <el-table :data="logs" size="small" max-height="360" v-loading="logLoading">
            <el-table-column label="事件类型" width="130">
              <template #default="{ row }">{{ eventTypeText(row.event_type) }}</template>
            </el-table-column>
            <el-table-column prop="detail" label="详情" min-width="280" show-overflow-tooltip />
            <el-table-column label="结果" width="90">
              <template #default="{ row }">
                <span :class="row.result === 'dropped' || row.result === 'failed' ? 'ohms-red' : 'ohms-green'">
                  {{ resultText(row.result) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="时间" width="170">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'

const activeTab = ref('crc')
const stats = ref({})
const vpnActive = ref(false)
const vpnInfo = ref(null)

const crcPayload = ref('AHMU COMM SECURITY TEST')
const crcResult = ref('')
const receivePayload = ref('')
const receiveCrc = ref('')
const receiveResult = ref(null)

const whitelist = ref([])
const newSsid = ref('')
const newPsk = ref('')
const scanResult = ref([])
const authSsid = ref('')
const authPsk = ref('')
const authResult = ref(null)

const logs = ref([])
const logTotal = ref(0)
const logLoading = ref(false)

const api = (path) => {
  const port = window.location.port === '5173' ? '8443' : window.location.port
  return `http://${window.location.hostname}:${port}${path}`
}

const appendCrc = () => {
  if (!crcPayload.value) { ElMessage.warning('请输入数据包内容'); return }
  fetch(api('/api/v1/comm-security/append-crc'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ payload: crcPayload.value }),
  })
    .then(r => r.json())
    .then(data => {
      crcResult.value = `CRC: ${data.crc}  完整数据包: ${data.packet}`
      receivePayload.value = data.payload
      receiveCrc.value = data.crc
    })
}

const receivePacket = () => {
  if (!receivePayload.value) { ElMessage.warning('请输入接收数据包'); return }
  fetch(api('/api/v1/comm-security/receive-packet'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ payload: receivePayload.value, crc: receiveCrc.value || null }),
  })
    .then(r => r.json())
    .then(data => {
      receiveResult.value = data
      fetchStats()
      fetchLogs()
    })
}

const establishVpn = () => {
  fetch(api('/api/v1/comm-security/vpn/establish'), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        vpnActive.value = true
        vpnInfo.value = data.tunnel
        ElMessage.success('VPN 加密隧道已建立')
        fetchStats(); fetchLogs()
      }
    })
}

const teardownVpn = () => {
  fetch(api('/api/v1/comm-security/vpn/teardown'), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        vpnActive.value = false
        vpnInfo.value = null
        ElMessage.success('VPN 隧道已断开')
        fetchLogs()
      }
    })
}

const fetchVpnStatus = () => {
  fetch(api('/api/v1/comm-security/vpn/status'))
    .then(r => r.json())
    .then(data => {
      vpnActive.value = !!data.tunnel_active
      vpnInfo.value = data.tunnel
    })
}

const fetchWhitelist = () => {
  fetch(api('/api/v1/comm-security/whitelist'))
    .then(r => r.json())
    .then(data => { whitelist.value = data.items || [] })
}

const addWhitelist = () => {
  if (!newSsid.value || !newPsk.value) { ElMessage.warning('请输入 SSID 和 PSK'); return }
  fetch(api('/api/v1/comm-security/whitelist'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ssid: newSsid.value, psk: newPsk.value, description: '' }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        ElMessage.success(data.message)
        newSsid.value = ''; newPsk.value = ''
        fetchWhitelist()
      } else {
        ElMessage.error(data.message)
      }
    })
}

const removeWhitelist = (ssid) => {
  fetch(api(`/api/v1/comm-security/whitelist/${ssid}`), { method: 'DELETE' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') { ElMessage.success(data.message); fetchWhitelist() }
      else ElMessage.error(data.message)
    })
}

const scanWifi = () => {
  fetch(api('/api/v1/comm-security/wifi/scan'), { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      scanResult.value = data.networks || []
      ElMessage.success(`扫描到 ${data.total} 个网络，${data.trusted_count} 个可信`)
      fetchLogs()
    })
}

const authenticate = () => {
  if (!authSsid.value || !authPsk.value) { ElMessage.warning('请输入 SSID 和 PSK'); return }
  fetch(api('/api/v1/comm-security/wifi/authenticate'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ssid: authSsid.value, psk: authPsk.value }),
  })
    .then(r => r.json())
    .then(data => {
      authResult.value = data
      ElMessage[data.authenticated ? 'success' : 'error'](data.message)
      fetchLogs()
    })
}

const fetchStats = () => {
  fetch(api('/api/v1/comm-security/stats'))
    .then(r => r.json())
    .then(data => { stats.value = data })
}

const fetchLogs = () => {
  logLoading.value = true
  fetch(api('/api/v1/comm-security/logs?page=1&size=50'))
    .then(r => r.json())
    .then(data => {
      logs.value = data.items || []
      logTotal.value = data.total || 0
    })
    .finally(() => { logLoading.value = false })
}

const eventTypeText = (t) => ({
  crc_check: 'CRC 校验', packet_drop: '异常包丢弃', vpn_establish: 'VPN 建立',
  vpn_teardown: 'VPN 断开', wifi_scan: 'WiFi 扫描', psk_auth: 'PSK 认证',
}[t] || t)

const resultText = (r) => ({ success: '成功', failed: '失败', dropped: '丢弃' }[r] || r)

const formatTime = (t) => t ? new Date(t).toLocaleString() : '--'

onMounted(() => {
  fetchStats()
  fetchVpnStatus()
  fetchWhitelist()
  fetchLogs()
})
</script>

<style scoped>
.cs-container {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  overflow-y: auto;
  padding: 4px;
  --cs-fs-title: 13px;
  --cs-fs-panel: 12px;
  --cs-fs-body: 11px;
  --cs-fs-aux: 10px;
}

.cs-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
}

.cs-tab-panel {
  padding: 0;
  margin-bottom: 4px;
}

.cs-panel-header {
  padding: 8px 12px;
  border-bottom: 1px solid #555;
  display: flex;
  align-items: center;
  background: #1f1f1f;
}

.cs-body {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.cs-field {
  display: flex;
  align-items: center;
  gap: 10px;
}

.cs-label {
  color: #bbb;
  font-size: var(--cs-fs-body);
  min-width: 80px;
}

.cs-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.cs-desc {
  color: #888;
  font-size: var(--cs-fs-body);
}

.cs-divider {
  border-top: 1px solid #333;
}

.cs-two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.cs-vpn-info {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  background: rgba(0, 255, 255, 0.04);
  border: 1px solid #333;
}

.cs-vpn-row {
  display: flex;
  gap: 12px;
  font-size: var(--cs-fs-body);
}

.ohms-title {
  text-transform: none;
}
</style>
