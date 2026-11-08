import type { ThemeDef, ThemeKind } from '../types'

export const THEMES: readonly ThemeDef[] = [
  { id: 'midnight', name: 'Midnight', kind: 'dark', dot: '#0b0d12', accent: '#6cc0ff' },
  { id: 'ink', name: 'Ink', kind: 'dark', dot: '#08090b', accent: '#c9a227' },
  { id: 'forest', name: 'Forest', kind: 'dark', dot: '#0a1210', accent: '#6fd6a8' },
  { id: 'paper', name: 'Paper', kind: 'light', dot: '#f7f4ee', accent: '#b5651d' },
  { id: 'mist', name: 'Mist', kind: 'light', dot: '#eef2f7', accent: '#2f6fd0' },
  { id: 'sakura', name: 'Sakura', kind: 'light', dot: '#fdf4f5', accent: '#c25a7a' },
] as const

export const THEME_KEY = 'vidpipe.theme'

export function findTheme(id: string | null): ThemeDef {
  return THEMES.find((t) => t.id === id) ?? THEMES[0]!
}

/** 讀初始主題，跟 public/theme-init.js 用同一套規則。 */
export function initialTheme(): string {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    if (saved) return findTheme(saved).id
  } catch {
    /* localStorage 可能被封鎖 */
  }
  if (typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-color-scheme: light)').matches) {
    return 'paper'
  }
  return 'midnight'
}

/** 套到 <html>：dataset 與 color-scheme（捲軸、表單控件才會跟著換）。 */
export function applyTheme(id: string): void {
  const theme = findTheme(id)
  document.documentElement.dataset.theme = theme.id
  document.documentElement.style.colorScheme = theme.kind
  try {
    localStorage.setItem(THEME_KEY, theme.id)
  } catch {
    /* 忽略 */
  }
}

export function kindIcon(kind: ThemeKind): string {
  return kind === 'dark' ? '◐' : '◑'
}
