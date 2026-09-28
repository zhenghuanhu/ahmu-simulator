import { ref } from 'vue'

// 标签显示模式持久化 key
const LABEL_MODE_KEY = 'ahmu_label_mode'

// 标签显示模式: 'en' 纯英文 | 'bilingual' 中英文对照 | 'zh' 纯中文
// 模块级 ref (单例), 跨组件共享, 切换后所有引用处立即响应式更新
const labelMode = ref(localStorage.getItem(LABEL_MODE_KEY) || 'en')

/**
 * 标签栏显示模式管理
 * 用于在「纯英文 / 中英文对照 / 纯中文」之间切换导航标签显示
 */
export function useLabelMode() {
  const setLabelMode = (mode) => {
    if (!['en', 'bilingual', 'zh'].includes(mode)) return
    labelMode.value = mode
    localStorage.setItem(LABEL_MODE_KEY, mode)
  }

  /**
   * 根据当前模式格式化标签
   * @param {string} en 英文标签
   * @param {string} zh 中文标签
   */
  const formatLabel = (en, zh) => {
    if (labelMode.value === 'zh') return zh || en
    if (labelMode.value === 'bilingual') return zh ? `${en} / ${zh}` : en
    return en
  }

  return { labelMode, setLabelMode, formatLabel }
}

export const LABEL_MODE_OPTIONS = [
  { value: 'en', label: '纯英文 (English only)' },
  { value: 'bilingual', label: '中英文对照 (Bilingual)' },
  { value: 'zh', label: '纯中文 (中文 only)' },
]
