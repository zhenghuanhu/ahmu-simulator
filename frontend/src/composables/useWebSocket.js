import { ref, onMounted, onUnmounted } from 'vue'

/**
 * WebSocket 实时通信 composable
 * 自动重连 + 心跳 + 事件订阅
 */
export function useWebSocket() {
  const ws = ref(null)
  const connected = ref(false)
  const listeners = {}
  let reconnectTimer = null
  let heartbeatTimer = null

  const getWsUrl = () => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.hostname
    // 开发模式直连后端, 生产模式同域
    const port = window.location.port === '5173' ? '8443' : window.location.port
    return `${proto}//${host}:${port}/ws/ahmu`
  }

  const connect = () => {
    try {
      ws.value = new WebSocket(getWsUrl())

      ws.value.onopen = () => {
        connected.value = true
        console.log('[WS] 已连接')
        startHeartbeat()
        emit('connected', {})
      }

      ws.value.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          emit(msg.event, msg.data || msg)
        } catch (e) {
          console.warn('[WS] 消息解析失败:', e)
        }
      }

      ws.value.onclose = () => {
        connected.value = false
        console.log('[WS] 已断开')
        stopHeartbeat()
        scheduleReconnect()
      }

      ws.value.onerror = (err) => {
        console.error('[WS] 错误:', err)
      }
    } catch (e) {
      console.error('[WS] 连接失败:', e)
      scheduleReconnect()
    }
  }

  const scheduleReconnect = () => {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(() => {
      console.log('[WS] 重连中...')
      connect()
    }, 3000)
  }

  const startHeartbeat = () => {
    stopHeartbeat()
    heartbeatTimer = setInterval(() => {
      if (ws.value && ws.value.readyState === WebSocket.OPEN) {
        ws.value.send(JSON.stringify({ type: 'ping' }))
      }
    }, 30000)
  }

  const stopHeartbeat = () => {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  const on = (event, callback) => {
    if (!listeners[event]) listeners[event] = []
    listeners[event].push(callback)
  }

  const off = (event, callback) => {
    if (listeners[event]) {
      listeners[event] = listeners[event].filter(cb => cb !== callback)
    }
  }

  const emit = (event, data) => {
    if (listeners[event]) {
      listeners[event].forEach(cb => cb(data))
    }
  }

  const send = (data) => {
    if (ws.value && ws.value.readyState === WebSocket.OPEN) {
      ws.value.send(JSON.stringify(data))
    }
  }

  const disconnect = () => {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    stopHeartbeat()
    if (ws.value) {
      ws.value.onclose = null // 阻止自动重连
      ws.value.close()
    }
    connected.value = false
  }

  onMounted(() => {
    connect()
  })

  onUnmounted(() => {
    disconnect()
  })

  return { ws, connected, connect, disconnect, on, off, send }
}
