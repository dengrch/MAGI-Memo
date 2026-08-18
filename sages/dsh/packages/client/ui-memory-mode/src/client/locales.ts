export const zh = {
  'trigger.aria': '记忆模式：{mode}',
  'trigger.title': '切换本会话的 MAGI 记忆模式',
  'trigger.label': '记忆',
  'mode.auto': '自动',
  'mode.manual': '手动',
  'mode.off': '关闭',
  'error': '切换记忆模式失败',
} satisfies Record<string, string>

export type MemoryModeKey = keyof typeof zh

export const en = {
  'trigger.aria': 'Memory mode: {mode}',
  'trigger.title': 'Change MAGI memory mode for this session',
  'trigger.label': 'Memory',
  'mode.auto': 'Auto',
  'mode.manual': 'Manual',
  'mode.off': 'Off',
  'error': 'Failed to change memory mode',
} satisfies Record<MemoryModeKey, string>
