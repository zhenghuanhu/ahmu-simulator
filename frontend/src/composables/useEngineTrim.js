import { ref, reactive, computed } from 'vue'
import { useWebSocket } from './useWebSocket'

/**
 * 发动机配平功能数据流 composable (4.3.9)
 *
 * 职责:
 *   - 指令下发 (POST /engine-trim/command)
 *   - 指令列表查询 (GET /engine-trim/commands) 与筛选
 *   - 实时配平数据订阅 (WebSocket engine_trim_data, 推送机制)
 *   - 指令状态订阅 (WebSocket engine_trim_command_state)
 *   - 暂停/恢复、手动刷新
 *   - 边界状态: loading / error / empty / 断线重连
 *
 * 数据流方向:
 *   指令下发: 表单 → submitCommand() → POST → 后端状态机 → WebSocket 状态事件 → 更新列表
 *   实时数据: EMU → 后端 → WebSocket 广播 → 本 composable 订阅 → 视图展示
 *             (推送为主, fetchTrimData() 手动刷新为兜底)
 */
export function useEngineTrim() {
  const { connected: wsConnected, on } = useWebSocket()

  // ---------- 指令下发 ----------
  const submitting = ref(false)

  // ---------- 指令列表 ----------
  const commands = ref([])
  const commandsLoading = ref(false)
  const commandsError = ref('')
  const commandTotal = ref(0)
  const filterSource = ref('')   // '' | ground | cockpit | pmat
  const filterStatus = ref('')   // '' | pending | completed | timeout | rejected | ...
  const filterEngine = ref(null) // null | 1..4
  const commandPage = ref(1)
  const commandSize = ref(20)

  // ---------- 实时数据 ----------
  // 以 engine_id 为键缓存每台发动机的最新配平数据
  const trimData = reactive({})
  const lastUpdateAt = ref(null)      // 最新更新时间
  const paused = ref(false)           // 暂停实时推送
  const dataError = ref('')

  const api = (path) => {
    const port = window.location.port === '5173' ? '8443' : window.location.port
    return `http://${window.location.hostname}:${port}${path}`
  }

  // ---------- 指令下发 ----------
  async function submitCommand(payload) {
    submitting.value = true
    try {
      const resp = await fetch(api('/api/v1/engine-trim/command'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await resp.json()
      return data
    } finally {
      submitting.value = false
    }
  }

  // ---------- 指令列表 ----------
  async function fetchCommands() {
    commandsLoading.value = true
    commandsError.value = ''
    const params = new URLSearchParams()
    params.set('page', commandPage.value)
    params.set('size', commandSize.value)
    if (filterSource.value) params.set('source', filterSource.value)
    if (filterEngine.value) params.set('engine_id', filterEngine.value)
    try {
      const resp = await fetch(api(`/api/v1/engine-trim/commands?${params}`))
      const data = await resp.json()
      commands.value = data.items || []
      commandTotal.value = data.total || 0
      if (filterStatus.value) {
        commands.value = commands.value.filter(c => c.status === filterStatus.value)
      }
    } catch (e) {
      commandsError.value = '指令列表加载失败'
    } finally {
      commandsLoading.value = false
    }
  }

  // ---------- 实时数据 ----------
  async function fetchTrimData() {
    dataError.value = ''
    try {
      const resp = await fetch(api('/api/v1/engine-trim/data'))
      const data = await resp.json()
      const list = data?.data || []
      for (const d of list) {
        if (d) trimData[d.engine_id] = d
      }
      lastUpdateAt.value = new Date()
    } catch (e) {
      dataError.value = '实时数据加载失败'
    }
  }

  function togglePause() {
    paused.value = !paused.value
    // 恢复时立即拉取一次最新数据, 消除暂停期间的数据空窗
    if (!paused.value) fetchTrimData()
  }

  // ---------- WebSocket 订阅 ----------
  function subscribe() {
    // 实时配平数据推送 (EMU → 后端 → 前端)
    on('engine_trim_data', (data) => {
      if (paused.value) return   // 暂停时忽略推送
      trimData[data.engine_id] = data
      lastUpdateAt.value = new Date()
    })

    // 指令状态变化 (状态机流转)
    on('engine_trim_command_state', (data) => {
      const cmd = commands.value.find(c => c.command_id === data.command_id)
      if (cmd) cmd.status = data.state
      // 终态后刷新列表, 获取 completed_at / applied_trim 等落库字段
      if (['completed', 'timeout', 'rejected'].includes(data.state)) {
        fetchCommands()
      }
    })

    // 新指令接收
    on('engine_trim_command_received', () => {
      fetchCommands()
    })
  }

  // 按 engine_id 排序的实时数据列表 (供视图渲染)
  const sortedTrimData = computed(() => {
    return Object.keys(trimData)
      .map(Number)
      .sort((a, b) => a - b)
      .map(id => ({ engine_id: id, ...trimData[id] }))
  })

  return {
    // 状态
    wsConnected, submitting,
    commands, commandsLoading, commandsError, commandTotal,
    filterSource, filterStatus, filterEngine,
    commandPage, commandSize,
    trimData, sortedTrimData, lastUpdateAt, paused, dataError,
    // 方法
    submitCommand, fetchCommands, fetchTrimData, togglePause, subscribe,
  }
}
