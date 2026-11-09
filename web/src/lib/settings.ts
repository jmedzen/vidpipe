import type { SettingField, SettingScalar, SettingsInfo } from '../types'

/**
 * 設定頁的草稿：使用者對每個項目做了什麼（沒有紀錄 = 沒動）。
 * 一般項目的文字與數字都用字串存（輸入框怎麼打就怎麼存），送出時才轉型。
 */
export type Edit =
  | { kind: 'set'; value: string | boolean }
  | { kind: 'reset' }                     // 還原成預設：從設定檔拿掉
  | { kind: 'secret'; text: string }      // 換 token
  | { kind: 'clear' }                     // 清掉 token

export type Edits = Record<string, Edit>

export const SOURCE_TEXT = { env: '環境變數', file: '設定檔', default: '預設' } as const

export function asInput(value: SettingScalar | null): string | boolean {
  if (typeof value === 'boolean') return value
  return value === null ? '' : String(value)
}

/** 沒有修改時輸入框裡的值。 */
export function baseInput(f: SettingField): string | boolean {
  return asInput(f.value)
}

export function defaultInput(f: SettingField): string | boolean {
  return asInput(f.default)
}

/** 目前畫面上這個項目的值（草稿優先）。 */
export function shownInput(f: SettingField, edit: Edit | undefined): string | boolean {
  if (edit?.kind === 'set') return edit.value
  if (edit?.kind === 'reset') return defaultInput(f)
  return baseInput(f)
}

function same(f: SettingField, a: string | boolean, b: string | boolean): boolean {
  if (typeof a === 'boolean' || typeof b === 'boolean') return a === b
  if (f.kind === 'int' || f.kind === 'float') {
    const x = Number(a)
    const y = Number(b)
    return a.trim() !== '' && b.trim() !== '' && Number.isFinite(x) && x === y
  }
  return a.trim() === b.trim()
}

/** 草稿跟原本一樣就不算修改（打回原值，「未儲存」就消失）。 */
export function normalize(f: SettingField, edit: Edit | undefined): Edit | undefined {
  if (!edit || f.locked) return undefined
  if (edit.kind === 'set') return same(f, edit.value, baseInput(f)) ? undefined : edit
  if (edit.kind === 'secret') return edit.text.trim() === '' ? undefined : edit
  if (edit.kind === 'reset') return f.in_file ? edit : undefined
  return f.is_set && f.in_file ? edit : undefined   // clear：檔案裡沒有就沒東西可清
}

export function setEdit(edits: Edits, f: SettingField, edit: Edit | undefined): Edits {
  const next = { ...edits }
  const kept = normalize(f, edit)
  if (kept) next[f.name] = kept
  else delete next[f.name]
  return next
}

/** 跟伺服器的規則對齊的即時檢查（伺服器仍然是最後一關）。回空字串 = 合格。 */
export function validate(f: SettingField, value: string | boolean): string {
  if (f.kind === 'bool' || f.kind === 'secret' || typeof value === 'boolean') return ''
  const text = value.trim()
  if (f.kind === 'int' || f.kind === 'float') {
    if (text === '') return '要填數字'
    const n = Number(text)
    if (!Number.isFinite(n)) return '要是數字'
    if (f.kind === 'int' && !Number.isInteger(n)) return '要是整數'
    if (f.min !== null && f.max !== null && (n < f.min || n > f.max)) {
      return `要在 ${f.min} 到 ${f.max} 之間${f.range_hint ? `。${f.range_hint}` : ''}`
    }
    return ''
  }
  if (f.required && text === '') return '不能是空的'
  const bad = [...f.forbidden].some((c) => text.includes(c))
  if (bad) return `不能含${f.forbidden_label}`
  if (f.kind === 'choice') {
    return f.choices.some((c) => c.value === text) ? '' : '不是可選的項目'
  }
  // 選填的網址可以空著（空 = 預設）；必填的上面已經擋掉了
  if (f.kind === 'url') return text === '' ? '' : validateUrl(text, f.placeholder)
  return ''
}

function validateUrl(text: string, example: string): string {
  const hint = example ? `，例如 ${example}` : ''
  if (/\s/.test(text)) return '網址不能含空白'
  let url: URL
  try {
    url = new URL(text)
  } catch {
    return `要是 http:// 或 https:// 開頭的完整網址${hint}`
  }
  if ((url.protocol !== 'http:' && url.protocol !== 'https:') || !url.hostname) {
    return `要是 http:// 或 https:// 開頭的完整網址${hint}`
  }
  if (url.username || url.password) return '網址裡不要放帳號密碼；token 填在旁邊的 token 欄'
  if (url.search || url.hash) return '網址不要帶 ? 或 #'
  return ''
}

/** 這個項目現在用得到嗎？回「為什麼用不到」，用得到就回空字串。 */
export function idleReason(info: SettingsInfo, edits: Edits, f: SettingField): string {
  if (!f.depends_on) return ''
  const dep = info.fields.find((x) => x.name === f.depends_on)
  if (!dep) return ''
  const v = shownInput(dep, edits[dep.name])
  const on = typeof v === 'boolean' ? v : v.trim() !== ''
  if (on) return ''
  return dep.kind === 'bool' ? `「${dep.label}」關著` : `「${dep.label}」還沒填`
}

function typed(f: SettingField, value: string | boolean): unknown {
  if (typeof value === 'boolean') return value
  if (f.kind === 'int' || f.kind === 'float') return Number(value)
  return value.trim()
}

/** 儲存用：`{項目: 新值}`，`null` = 還原成預設（祕密：清掉）。 */
export function changesOf(info: SettingsInfo, edits: Edits): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const f of info.fields) {
    const e = edits[f.name]
    if (!e) continue
    if (e.kind === 'set') out[f.name] = typed(f, e.value)
    else if (e.kind === 'secret') out[f.name] = e.text.trim()
    else out[f.name] = null
  }
  return out
}

/**
 * 測試用：只帶「合格的」草稿。跟儲存不同的是祕密的「清掉」要送空字串
 * （`null` 在測試裡是「沒動，沿用現有的」）。
 */
export function testValuesOf(info: SettingsInfo, edits: Edits, errors: Record<string, string>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const f of info.fields) {
    const e = edits[f.name]
    if (!e || errors[f.name]) continue
    if (e.kind === 'set') out[f.name] = typed(f, e.value)
    else if (e.kind === 'secret') out[f.name] = e.text.trim()
    else out[f.name] = f.kind === 'secret' ? '' : null
  }
  return out
}

/** 項目的白話名稱，列「有哪幾項沒儲存」用。 */
export function labelsOf(info: SettingsInfo, names: string[]): string[] {
  return names.map((n) => info.fields.find((f) => f.name === n)?.label ?? n)
}
