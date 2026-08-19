<template>
  <div class="ohms-layout">
    <!-- 顶部导航 - 两排斜角标签 -->
    <div class="top-nav">
      <div class="nav-row nav-row-main">
        <router-link
          v-for="item in mainTabs"
          :key="item.path"
          :to="item.path"
          class="nav-tab"
          :class="{ active: route.path === item.path }"
        >
          {{ item.label }}
        </router-link>
      </div>
      <div class="nav-row nav-row-sub">
        <router-link
          v-for="item in subTabs"
          :key="item.path"
          :to="item.path"
          class="nav-tab"
          :class="{ active: route.path === item.path }"
        >
          {{ item.label }}
        </router-link>
      </div>
    </div>

    <!-- 状态栏 -->
    <div class="status-bar">
      <div class="status-left">
        <span class="page-title">{{ pageTitle }}</span>
      </div>
      <div class="status-right">
        <span class="mode-badge" :class="systemMode === 'maintenance' ? 'mode-maint' : 'mode-normal'">
          {{ systemMode === 'maintenance' ? 'MAINTENANCE MODE' : 'NORMAL MODE' }}
        </span>
        <span class="ws-status">
          <span class="status-dot" :class="wsConnected ? 'green' : 'red'"></span>
          {{ wsConnected ? 'LIVE' : 'OFFLINE' }}
        </span>
        <span class="user-label">{{ currentUser }}</span>
        <button class="ohms-btn-sm" @click="logout">LOGOUT</button>
      </div>
    </div>

    <!-- 主内容区 -->
    <div class="main-content">
      <router-view />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useWebSocket } from '../composables/useWebSocket'

const route = useRoute()
const router = useRouter()
const { connected: wsConnected, on } = useWebSocket()

const systemMode = ref('normal')
const currentUser = ref(localStorage.getItem('ahmu_user') || 'TEST')

const pageTitle = computed(() => {
  const all = [...mainTabs, ...subTabs]
  const item = all.find(t => t.path === route.path)
  return item?.title || item?.label || ''
})

const mainTabs = [
  { path: '/dashboard', label: 'CENTRAL MAINTENANCE', title: '系统总览' },
  { path: '/params', label: 'CONDITION MONITORING', title: '参数监控' },
  { path: '/dataload', label: 'DATA LOAD', title: '数据加载' },
  { path: '/utility', label: 'UTILITY', title: '工具' },
  { path: '/login', label: 'LOGOUT', title: '退出' },
]

const subTabs = [
  { path: '/fault', label: 'FAILURE REPORTS', title: '故障报告' },
  { path: '/groundtest', label: 'GROUND TEST', title: '地面测试' },
  { path: '/lifecycle', label: 'TIME CYCLE', title: '生命周期' },
  { path: '/lru', label: 'LRU FAULT HISTORY', title: 'LRU故障历史' },
  { path: '/config', label: 'CONFIGURATION REPORTS', title: '构型报告' },
]

const logout = () => {
  localStorage.removeItem('ahmu_token')
  localStorage.removeItem('ahmu_user')
  router.push('/login')
}

onMounted(() => {
  on('mode_change', (data) => {
    systemMode.value = data.mode
  })

  const port = window.location.port === '5173' ? '8443' : window.location.port
  fetch(`http://${window.location.hostname}:${port}/api/v1/system/mode`)
    .then(r => r.json())
    .then(data => {
      systemMode.value = data.mode
    })
    .catch(() => {})
})
</script>

<style scoped>
.ohms-layout {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #000000;
}

.top-nav {
  background: #000000;
  padding: 4px 4px 0 4px;
  flex-shrink: 0;
}

.nav-row {
  display: flex;
  gap: 4px;
  margin-bottom: 4px;
  justify-content: space-between;
}

.nav-row-main .nav-tab {
  flex: 1;
  text-align: center;
}

.nav-row-sub .nav-tab {
  flex: 1;
  text-align: center;
}

.nav-tab {
  display: block;
  padding: 8px 8px;
  background: #444444;
  color: #000000;
  font-size: 12px;
  font-weight: bold;
  text-decoration: none;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  clip-path: polygon(10px 0, 100% 0, calc(100% - 10px) 100%, 0 100%);
  transition: background 0.15s, color 0.15s;
  white-space: nowrap;
}

.nav-tab:hover {
  background: #666666;
  color: #ffffff;
}

.nav-tab.active {
  background: #888888;
  color: #ffffff;
}

.status-bar {
  background: #111111;
  border-top: 2px solid #888888;
  border-bottom: 2px solid #888888;
  padding: 6px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.page-title {
  color: #ffffff;
  font-size: 14px;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 1px;
}

.status-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.mode-badge {
  padding: 4px 12px;
  font-size: 12px;
  font-weight: bold;
  border: 1px solid #888888;
}

.mode-normal {
  background: #002200;
  color: #00ff00;
}

.mode-maint {
  background: #222200;
  color: #ffff00;
}

.ws-status {
  color: #aaaaaa;
  font-size: 12px;
  display: flex;
  align-items: center;
}

.user-label {
  color: #ffffff;
  font-size: 12px;
  font-weight: bold;
}

.ohms-btn-sm {
  background: #666666;
  border: 2px solid #888888;
  color: #ffffff;
  padding: 4px 16px;
  font-size: 12px;
  cursor: pointer;
  clip-path: polygon(6px 0, 100% 0, calc(100% - 6px) 100%, 0 100%);
  font-family: 'Consolas', 'Courier New', monospace;
  text-transform: uppercase;
}

.ohms-btn-sm:hover {
  background: #999999;
  color: #000000;
}

.main-content {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  background: #000000;
}
</style>
