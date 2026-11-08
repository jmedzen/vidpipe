import type { Check, JobStatus, Level } from '../types'

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
  run: '進行中',
}

export const JOB_TEXT: Record<JobStatus, string> = {
  running: '執行中',
  succeeded: '完成',
  failed: '失敗',
  cancelled: '已取消',
  interrupted: '中斷',
}

export const JOB_LEVEL: Record<JobStatus, Level> = {
  running: 'run',
  succeeded: 'ok',
  failed: 'bad',
  cancelled: 'warn',
  interrupted: 'warn',
}

export const KIND_TEXT: Record<string, string> = {
  normalize: '統一規格與重構圖',
  trim: '裁切',
  fill: '湊成指定長度',
  concat: '串接',
}

const STAGE_TEXT: Record<string, string> = {
  encode: '編碼',
  trim: '裁切',
  loop: '循環補長',
  concat: '串接',
}

export function stageText(name: string): string {
  return STAGE_TEXT[name] ?? name
}

export function clockIso(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString('zh-TW', { hour12: false })
}

export function dateTimeIso(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.toLocaleDateString('zh-TW')} ${d.toLocaleTimeString('zh-TW', { hour12: false })}`
}

/** 兩個時間點之間的長度；`end` 為 null 就算到現在（進行中的工作）。 */
export function elapsed(start: string | null, end: string | null): string {
  if (!start) return '—'
  const ms = (end ? new Date(end).getTime() : Date.now()) - new Date(start).getTime()
  const sec = Math.max(0, Math.round(ms / 1000))
  if (sec < 60) return `${sec} 秒`
  const m = Math.floor(sec / 60)
  return m < 60 ? `${m} 分 ${String(sec % 60).padStart(2, '0')} 秒` : `${Math.floor(m / 60)} 時 ${m % 60} 分`
}

/** 工作的一行摘要：優先顯示輸出檔名，其次輸入數量。 */
export function summarize(args: Record<string, unknown>): string {
  const base = (v: unknown) => String(v).split(/[\\/]/).pop() ?? ''
  if (typeof args.output === 'string' && args.output) return `→ ${base(args.output)}`
  if (Array.isArray(args.inputs)) return `${args.inputs.length} 支`
  return ''
}

export function clock(ms: number | null): string {
  if (ms === null) return '—'
  return new Date(ms).toLocaleTimeString('zh-TW', { hour12: false })
}
