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
  vidgrab_open: { url: string | null; source: 'public' | 'derived' | 'none'; hint: string }
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

export type ShortStage = 'new' | 'planned' | 'narrated' | 'rendered'

/** 現在算數的 manifest 從哪來：送去做的、plan 挑好的、還沒挑的草稿。 */
export type ManifestSource = 'manifest' | 'plan' | 'draft'

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
  clip?: { asset_id?: string; path?: string; start?: number }
  fit?: string
  min_duration?: number
  [key: string]: unknown
}

export interface ManifestMusic {
  asset_id?: string
  path?: string
  start?: number
  volume?: number
  duck_under_voice?: boolean
}

export interface Manifest {
  id: string
  title?: string
  scenes: ManifestScene[]
  music?: ManifestMusic
  [key: string]: unknown
}

export interface PlanCandidate {
  asset_id: string
  title: string
  author: string
  license: { kind: string; label: string }
  duration: number | null
  width: number | null
  height: number | null
  orientation: 'portrait' | 'landscape' | 'square' | 'unknown'
  webpage_url: string
  /** 素材站給的封面網址（只會是 http(s)；沒有就是空字串）。 */
  thumbnail?: string
  credit_hint: string
  /** 為什麼挑它（授權、方向、長度）。只有被選中的那個有。 */
  why?: string
}

/** 換素材時搜尋的候選：多帶「已經用在哪些場景」。 */
export interface SearchCandidate extends PlanCandidate {
  used_in: string[]
  current: boolean
  /** 夠長（比需要的多一點餘裕）；false 就會循環補長。 */
  enough: boolean
}

export interface SearchResponse {
  ok: true
  short_id: string
  target: string
  query: string[]
  platform: string
  /** 這個場景（或整支片）需要的秒數，粗估。 */
  need: number
  total: number
  /** 授權不明、沒有列入的有幾支。 */
  skipped_license: number
  candidates: SearchCandidate[]
}

export interface PlanPick {
  query?: string[]
  chosen?: PlanCandidate
  alternatives?: PlanCandidate[]
  warnings?: string[]
  skipped_license?: number
}

export interface PlanScene extends PlanPick {
  id: string
  estimated_seconds: number
  /** 這個場景原本就指定了素材，plan 沒動它。 */
  clip?: 'given'
}

export interface PlanReport {
  short_id: string
  estimated_seconds: number
  scenes: PlanScene[]
  music: (PlanPick & { query: string[] }) | null
  without_clip: string[]
  warnings: string[]
}

export interface ShortDetail extends ShortSummary {
  manifest: Manifest | null
  manifest_source: ManifestSource | null
  /** 編輯時帶回去的版本號；PUT 時內容已經不是這一份就會 409。 */
  manifest_revision: string | null
  /** manifest 比上次做出來的新：true 要重做、false 是最新、null 還沒做過。 */
  outdated: boolean | null
  plan: PlanReport | null
  timeline: ShortTimeline | null
  sources: ShortSources | null
  jobs: JobBrief[]
}

export interface ShortDetailResponse {
  ok: true
  short: ShortDetail
}
