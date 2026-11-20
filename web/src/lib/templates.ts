/** 排版模板（`vidpipe.template/1`）：型別與預設值，跟 `src/vidpipe/templates.py` 對齊。 */

export interface OverlayStyle {
  show: boolean
  position: 'top' | 'bottom'
  size: string
  color: string
}

export interface Template {
  schema: 'vidpipe.template/1'
  name: string
  label: string
  description: string
  aspect: '9:16' | '1:1' | '16:9'
  fit: 'crop' | 'blur_pad' | 'letterbox'
  subtitles: {
    size: 'normal' | 'large' | 'xlarge'
    color: string
    en_color: string
    margin: number | null
    box: boolean
    font: string
  }
  title: OverlayStyle
  series: OverlayStyle
  music: { volume: number }
}

export interface TemplateItem {
  name: string
  builtin: boolean
  path?: string
  template?: Template
  error?: string
}

export interface TemplateList {
  ok: true
  dir: string
  default: string
  colors: Record<string, string>
  items: TemplateItem[]
}

export const COLOR_LABEL: Record<string, string> = {
  white: '白', yellow: '黃', paleyellow: '淡黃', cyan: '青', pink: '粉紅', orange: '橘',
  green: '綠', black: '黑',
}
export const SUB_SIZE_LABEL: Record<string, string> = { normal: '一般', large: '大', xlarge: '特大' }
export const OVERLAY_SIZE_LABEL: Record<string, string> = {
  auto: '自動（直式特大、橫式中）', xlarge: '特大', large: '大', medium: '中', small: '小',
}
export const FIT_LABEL: Record<string, string> = {
  crop: '裁切填滿', blur_pad: '完整＋模糊背景', letterbox: '完整＋黑邊',
}

/** classic 的值（＝沒寫的欄位）。 */
export function blankTemplate(name = ''): Template {
  return {
    schema: 'vidpipe.template/1', name, label: '', description: '', aspect: '9:16', fit: 'crop',
    subtitles: { size: 'large', color: 'white', en_color: 'paleyellow', margin: null, box: false, font: '' },
    title: { show: true, position: 'top', size: 'auto', color: 'yellow' },
    series: { show: true, position: 'bottom', size: 'small', color: 'white' },
    music: { volume: 0.22 },
  }
}

/** 顏色名稱或 #RRGGBB → CSS 顏色。 */
export function cssColor(value: string, colors: Record<string, string>): string {
  return colors[value] ? `#${colors[value]}` : value
}
