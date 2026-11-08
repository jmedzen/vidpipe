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

export type Level = 'ok' | 'warn' | 'bad'

export type ThemeKind = 'dark' | 'light'

export interface ThemeDef {
  id: string
  name: string
  kind: ThemeKind
  dot: string
  accent: string
}
