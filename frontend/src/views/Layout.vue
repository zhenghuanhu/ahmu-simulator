<template>
  <el-container style="height: 100vh">
    <el-aside width="220px">
      <div class="logo-box">
        <div class="logo-title">AHMU</div>
        <div class="logo-sub">仿真器 v1.0</div>
      </div>
      <el-menu :default-active="activeMenu" router>
        <el-menu-item index="/dashboard">
          <span>◈ 系统总览</span>
        </el-menu-item>
        <el-menu-item index="/fault">
          <span>⚠ 故障诊断</span>
        </el-menu-item>
        <el-menu-item index="/params">
          <span>∿ 参数监控</span>
        </el-menu-item>
        <el-menu-item index="/config">
          <span>⚙ 构型管理</span>
        </el-menu-item>
        <el-menu-item index="/groundtest">
          <span>▶ 启动测试</span>
        </el-menu-item>
        <el-menu-item index="/dataload">
          <span>⇩ 数据加载</span>
        </el-menu-item>
        <el-menu-item index="/lifecycle">
          <span>⟳ 生命周期</span>
        </el-menu-item>
        <el-menu-item index="/acars">
          <span>✈ ACARS管理</span>
        </el-menu-item>
        <el-menu-item index="/print">
          <span>⎙ 打印管理</span>
        </el-menu-item>
        <el-menu-item index="/icd">
          <span>▤ ICD管理</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <header class="ohms-header">
        <div class="header-left">
          <span class="ohms-title">{{ pageTitle }}</span>
        </div>
        <div class="header-right">
          <span class="mode-badge" :class="systemMode === 'maintenance' ? 'mode-maint' : 'mode-normal'">
            {{ systemMode === 'maintenance' ? '维护模式' : '正常模式' }}
          </span>
          <span class="ws-status">
            <span class="status-dot" :class="wsConnected ? 'green' : 'red'"></span>
            {{ wsConnected ? '实时连接' : '连接断开' }}
          </span>
          <span class="ohms-dim">{{ currentUser }}</span>
          <el-button size="small" @click="logout">退出</el-button>
        </div>
      </header>
      <el-main style="background: #000; padding: 20px; overflow-y: auto;">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
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

const activeMenu = computed(() => route.path)
const pageTitle = computed(() => route.meta.title || '')

const logout = () => {
  localStorage.removeItem('ahmu_token')
  localStorage.removeItem('ahmu_user')
  router.push('/login')
}

onMounted(() => {
  on('mode_change', (data) => {
    systemMode.value = data.mode
  })

  // 获取当前模式
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
.logo-box {
  padding: 20px;
  text-align: center;
  border-bottom: 1px solid #333;
}

.logo-title {
  color: #00ffff;
  font-size: 24px;
  font-weight: bold;
  letter-spacing: 4px;
}

.logo-sub {
  color: #666;
  font-size: 12px;
  margin-top: 4px;
}

.header-left {
  display: flex;
  align-items: center;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.ws-status {
  color: #aaa;
  font-size: 13px;
  display: flex;
  align-items: center;
}

.mode-badge {
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 13px;
  font-weight: bold;
}

.mode-normal {
  background: #002200;
  color: #00ff00;
  border: 1px solid #00ff0066;
}

.mode-maint {
  background: #222200;
  color: #ffff00;
  border: 1px solid #ffff0066;
}
</style>
