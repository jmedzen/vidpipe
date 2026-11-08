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
