import { ref } from 'vue'

/**
 * 系统模式全局状态
 * - normal:       正常模式  → 可访问除地面测试/数据加载外的所有页面
 * - maintenance:  维护模式  → 仅可访问地面测试(GROUND TEST)与数据加载(DATA LOAD)
 */
export const systemMode = ref('normal')

/** 模式切换时间戳 */
export const modeChangedAt = ref(null)

export function setSystemMode(mode) {
  if (systemMode.value !== mode) {
    systemMode.value = mode
    modeChangedAt.value = Date.now()
  }
}

/** 维护模式下允许访问的页面 (仅地面测试与数据加载) */
export const MAINTENANCE_ONLY_PATHS = ['/groundtest', '/dataload']

/** 判断路径在当前模式下是否可访问 */
export function isPathAllowed(path, mode = systemMode.value) {
  if (path === '/login') return true
  if (mode === 'maintenance') {
    return MAINTENANCE_ONLY_PATHS.includes(path)
  }
  // 正常模式: 禁止地面测试与数据加载
  return !MAINTENANCE_ONLY_PATHS.includes(path)
}

/** 当前模式下不可访问时的回退页面 */
export function fallbackPath(mode = systemMode.value) {
  return mode === 'maintenance' ? '/groundtest' : '/fault'
}

/** 从后端同步当前模式 */
export async function fetchSystemMode() {
  try {
    const port = window.location.port === '5173' ? '8443' : window.location.port
    const r = await fetch(`http://${window.location.hostname}:${port}/api/v1/system/mode`)
    const data = await r.json()
    setSystemMode(data.mode)
    return data
  } catch (e) {
    return null
  }
}
