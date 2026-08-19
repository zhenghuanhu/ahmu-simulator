<template>
  <div class="login-container">
    <!-- 右上角飞机准星标志 -->
    <div class="corner-marker">
      <svg viewBox="0 0 60 60" width="48" height="48">
        <line x1="30" y1="5" x2="30" y2="20" stroke="#ffffff" stroke-width="3" />
        <line x1="30" y1="40" x2="30" y2="55" stroke="#ffffff" stroke-width="3" />
        <line x1="5" y1="30" x2="20" y2="30" stroke="#ffffff" stroke-width="3" />
        <line x1="40" y1="30" x2="55" y2="30" stroke="#ffffff" stroke-width="3" />
        <circle cx="30" cy="30" r="4" fill="none" stroke="#ffffff" stroke-width="2" />
      </svg>
    </div>

    <div class="login-panel">
      <h1 class="login-title">Onboard Maintenance System</h1>

      <div class="login-field">
        <label>Username</label>
        <input v-model="form.username" type="text" @keyup.enter="handleLogin" />
      </div>

      <div class="login-field">
        <label>Password</label>
        <input v-model="form.password" type="password" @keyup.enter="handleLogin" />
      </div>

      <div class="login-actions">
        <button class="ohms-btn" :disabled="loading" @click="handleLogin">
          {{ loading ? 'Logging in...' : 'Login' }}
        </button>
      </div>
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
    ElMessage.warning('Please enter username and password')
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
      ElMessage.success('Login successful')
      router.push('/dashboard')
    } else {
      ElMessage.error(data.message || 'Login failed')
    }
  } catch (e) {
    ElMessage.error('Network error, please confirm backend service is running')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  height: 100vh;
  width: 100vw;
  background: #000000;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.corner-marker {
  position: absolute;
  top: 30px;
  right: 40px;
}

.login-panel {
  width: 420px;
  background: #222222;
  border: 2px solid #888888;
  padding: 36px 40px;
  box-shadow: 0 0 0 1px #000000;
}

.login-title {
  color: #ffffff;
  font-size: 18px;
  font-weight: normal;
  text-align: center;
  margin: 0 0 32px 0;
  letter-spacing: 1px;
  font-family: 'Consolas', 'Courier New', monospace;
}

.login-field {
  display: flex;
  align-items: center;
  margin-bottom: 16px;
}

.login-field label {
  color: #ffffff;
  width: 90px;
  font-size: 14px;
  font-family: 'Consolas', 'Courier New', monospace;
}

.login-field input {
  flex: 1;
  background: #000000;
  border: 1px solid #888888;
  color: #ffffff;
  padding: 6px 10px;
  font-size: 14px;
  outline: none;
  font-family: 'Consolas', 'Courier New', monospace;
}

.login-field input:focus {
  border-color: #ffffff;
}

.login-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 24px;
}

.ohms-btn {
  background: #666666;
  border: 2px solid #888888;
  color: #ffffff;
  padding: 6px 28px;
  font-size: 14px;
  cursor: pointer;
  clip-path: polygon(8px 0, 100% 0, calc(100% - 8px) 100%, 0 100%);
  font-family: 'Consolas', 'Courier New', monospace;
}

.ohms-btn:hover:not(:disabled) {
  background: #999999;
  color: #000000;
}

.ohms-btn:disabled {
  background: #444444;
  color: #999999;
  cursor: not-allowed;
}
</style>
