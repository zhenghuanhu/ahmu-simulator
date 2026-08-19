<template>
  <div class="login-container">
    <div class="login-box">
      <div class="login-title">AHMU 仿真器</div>
      <div class="login-subtitle">OHMS 地面人机界面</div>
      <el-form :model="form" @keyup.enter="handleLogin">
        <el-form-item>
          <el-input v-model="form.username" placeholder="用户名 (TEST)" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" type="password" placeholder="密码 (123456)" size="large" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" size="large" style="width: 100%" @click="handleLogin" :loading="loading">
            登 录
          </el-button>
        </el-form-item>
      </el-form>
      <div class="login-hint">默认账号: TEST / 123456</div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

const router = useRouter()
const loading = ref(false)
const form = reactive({ username: 'TEST', password: '123456' })

const handleLogin = async () => {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const port = window.location.port === '5173' ? '8443' : window.location.port
    const resp = await fetch(`http://${window.location.hostname}:${port}/api/v1/auth/login?username=${form.username}&password=${form.password}`, {
      method: 'POST',
    })
    const data = await resp.json()
    if (data.status === 'ok') {
      localStorage.setItem('ahmu_token', data.token)
      localStorage.setItem('ahmu_user', data.user)
      ElMessage.success('登录成功')
      router.push('/dashboard')
    } else {
      ElMessage.error(data.message || '登录失败')
    }
  } catch (e) {
    ElMessage.error('网络错误, 请确认后端服务已启动')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: radial-gradient(ellipse at center, #001122 0%, #000000 70%);
}

.login-box {
  width: 380px;
  padding: 40px;
  background: rgba(10, 10, 10, 0.9);
  border: 1px solid #00ffff44;
  border-radius: 8px;
  box-shadow: 0 0 30px rgba(0, 255, 255, 0.1);
}

.login-title {
  text-align: center;
  color: #00ffff;
  font-size: 26px;
  font-weight: bold;
  letter-spacing: 6px;
  margin-bottom: 8px;
}

.login-subtitle {
  text-align: center;
  color: #aaaaaa;
  font-size: 14px;
  margin-bottom: 32px;
  letter-spacing: 2px;
}

.login-hint {
  text-align: center;
  color: #666;
  font-size: 12px;
  margin-top: 12px;
}
</style>
