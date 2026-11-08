export interface ApiErrorBody {
  code: string
  message: string
  retryable: boolean
  hint: string
  upstream?: string
  upstream_code?: string
}

export interface VersionInfo {
  ok: true
  version: string
  build: string
  full: string
  auth_required: boolean
}

export interface ConfigInfo {
  ok: true
  path: string
  config: Record<string, string | boolean>
}

export interface Check {
  name: string
  ok: boolean
  required: boolean
  detail: string
  hint: string
}

export interface Doctor {
  ok: boolean
  checks: Check[]
  skipped: string[]
}

export type Level = 'ok' | 'warn' | 'bad' | 'run'

export type ThemeKind = 'dark' | 'light'

export interface ThemeDef {
  id: string
  name: string
  kind: ThemeKind
  dot: string
  accent: string
}

export type JobStatus = 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'

export interface JobStage {
  name: string
  status: 'running' | 'succeeded' | 'failed' | 'cancelled'
  detail: string
  started_at: string
  finished_at: string | null
}

export interface Job {
  id: string
  kind: string
  short_id: string | null
  status: JobStatus
  args: Record<string, unknown>
  pid: number | null
  host: string
  created_at: string
  started_at: string
  finished_at: string | null
  updated_at: string
  stage: string | null
  stages: JobStage[]
  progress: number | null
  result: Record<string, unknown> | null
  error: ApiErrorBody | null
}

export interface JobEvent {
  seq: number
  t: number
  kind: string
  message: string
  level?: string
  hint?: string
  code?: string
}

export interface JobList {
  ok: true
  jobs: Job[]
}

export interface JobDetail {
  ok: true
  job: Job
  events: JobEvent[]
}

/* ---------------------------------------------------------- 作品 */

export type ShortStage = 'new' | 'narrated' | 'rendered'

export interface ShortFile {
  area: 'out' | 'work'
  name: string
  size_bytes: number
  url: string
}

/** 工作的簡要版（作品列表與詳細頁用；完整內容走 /api/job/<id>）。 */
export interface JobBrief {
  id: string
  kind: string
  status: JobStatus
  stage: string | null
  progress: number | null
  created_at: string
  finished_at: string | null
}

export interface CreditsBrief {
  assets: number
  attribution_required: number
  unclear: number
}

export interface ShortSummary {
  id: string
  title: string
  stage: ShortStage
  scenes: number
  total: number | null
  files: ShortFile[]
  updated_at: string | null
  /** 還沒渲染過（沒有 sources.json）時是 null。 */
  credits: CreditsBrief | null
  last_job: JobBrief | null
}

export interface ShortList {
  ok: true
  shorts: ShortSummary[]
}

export interface TimelineSentence {
  index: number
  scene: string
  text: string
  en: string
  start: number
  end: number
}

export interface TimelineScene {
  id: string
  index: number
  start: number
  end: number
  duration: number
  speech_end: number
  sentences: TimelineSentence[]
}

export interface ShortTimeline {
  total: number
  total_frames: number
  fps: number
  scenes: TimelineScene[]
  warnings: string[]
  narration: { duration?: number } | null
  subtitles: { burn_in?: boolean; langs?: string; entries?: number; font?: string } | null
}

export interface SourceLicense {
  kind: string
  label: string
  name: string
}

export interface SourceCredit {
  kind: 'asset' | 'file'
  asset_id?: string
  clip_id?: string
  path?: string
  title?: string
  author?: string
  author_url?: string
  webpage_url?: string
  license?: SourceLicense
  credit_hint?: string
}

export interface ShortSources {
  scenes: { scene: string; offset: number; looped: boolean; source: SourceCredit | null }[]
  music: { offset: number; looped: boolean; source: SourceCredit } | null
}

export interface ManifestScene {
  id?: string
  text?: string
  sentences?: { text: string; en?: string }[]
  query?: string | string[]
  clip?: { asset_id?: string; path?: string }
  [key: string]: unknown
}

export interface ShortDetail extends ShortSummary {
  manifest: { id: string; title?: string; scenes: ManifestScene[]; [key: string]: unknown } | null
  timeline: ShortTimeline | null
  sources: ShortSources | null
  jobs: JobBrief[]
}

export interface ShortDetailResponse {
  ok: true
  short: ShortDetail
}
