import type { Check, JobStatus, Level, ShortStage } from '../types'

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
  make: '做成片',
  narrate: '旁白與時間軸',
  render: '渲染',
  cover: '封面',
  credits: '素材清單',
  plan: '挑素材',
  quick: '文案直出',
  'quick-batch': '文案直出（批次）',
}

const STAGE_TEXT: Record<string, string> = {
  storyboard: '分鏡',
  item: '批次進度',
  encode: '編碼',
  trim: '裁切',
  loop: '循環補長',
  concat: '串接',
  narrate: '合成旁白',
  timeline: '排時間軸',
  assets: '準備素材',
  segments: '處理場景',
  audio: '混音與響度',
  mux: '合成影片',
  search: '搜尋素材',
  music: '挑音樂',
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

/* ---------------------------------------------------------- 作品 */

export const SHORT_STAGE_TEXT: Record<ShortStage, string> = {
  new: '只有草稿',
  planned: '素材已挑好',
  narrated: '已有旁白',
  rendered: '已完成',
}

export const SHORT_STAGE_LEVEL: Record<ShortStage, 'ok' | 'note' | 'plain'> = {
  new: 'plain',
  planned: 'note',
  narrated: 'note',
  rendered: 'ok',
}

/** 影片秒數：`19.2 秒`；超過一分鐘用 `1:05`。 */
export function durationText(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return '—'
  if (sec < 60) return `${sec.toFixed(1)} 秒`
  return `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, '0')}`
}

/** 時間軸上的時間點：`m:ss.s`。 */
export function stamp(sec: number): string {
  const whole = Math.floor(sec)
  const tenth = Math.floor((sec - whole) * 10)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}.${tenth}`
}

export function bytesText(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export type LicenseTone = 'ok' | 'note' | 'warn'

/** 授權的提醒程度：可自由使用／要標註／要自己確認（check、unknown、本機檔案）。 */
export function licenseTone(kind: string | undefined): LicenseTone {
  if (kind === 'free') return 'ok'
  if (kind === 'free-attribution') return 'note'
  return 'warn'
}

export const ORIENT_TEXT: Record<string, string> = {
  portrait: '直式',
  landscape: '橫式',
  square: '正方形',
  unknown: '比例不明',
}

/** 進行中的工作用一句現在進行式；沒列的就「正在」加上名稱。 */
export const RUNNING_TEXT: Record<string, string> = {
  make: '正在做成片',
  narrate: '正在合成旁白',
  render: '正在渲染',
  cover: '正在做封面',
  credits: '正在整理素材清單',
  plan: '正在挑素材',
  quick: '正在把文案做成片',
  'quick-batch': '正在整批做成片',
}
