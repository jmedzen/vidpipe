import type { Check, Level } from '../types'

export function levelOf(check: Check): Level {
  if (check.ok) return 'ok'
  return check.required ? 'bad' : 'warn'
}

export function worst(levels: Level[]): Level {
  if (levels.includes('bad')) return 'bad'
  if (levels.includes('warn')) return 'warn'
  return 'ok'
}

export const LEVEL_TEXT: Record<Level, string> = {
  ok: '正常',
  warn: '有警告',
  bad: '有問題',
}

export function clock(ms: number | null): string {
  if (ms === null) return '—'
  return new Date(ms).toLocaleTimeString('zh-TW', { hour12: false })
}
