import type { CSSProperties } from 'react'
import { cssColor } from '../lib/templates'
import type { Template } from '../lib/templates'

// 跟 subtitles.py 同一套比例：字級＝短邊 × 0.06 × 倍率；字幕離底 20%（橫式 8%）
const BASE = 0.06
const SUB_SCALE: Record<string, number> = { normal: 1, large: 1.25, xlarge: 1.5 }
const OVERLAY_SCALE: Record<string, number> = { xlarge: 1.6, large: 1.2, medium: 1, small: 0.6 }
const RATIO: Record<string, [number, number]> = { '9:16': [9, 16], '1:1': [1, 1], '16:9': [16, 9] }

/**
 * 模板的示意圖：用 CSS 照 ASS 的比例畫出標題、字幕、系列名稱的位置與顏色。
 * 字型與折行是瀏覽器的，所以只是「大概長這樣」；實際成片以 ffmpeg 燒出來的為準。
 */
export function TemplatePreview({ tpl, colors, width = 150, title = '為什麼越想放下？',
  series = '一分鐘佛法', line = '睡前一躺下，白天的事又自己跑出來' }: {
  tpl: Template
  colors: Record<string, string>
  width?: number
  title?: string
  series?: string
  line?: string
}) {
  const [rw, rh] = RATIO[tpl.aspect] ?? [9, 16]
  const height = Math.round(width * rh / rw)
  const short = Math.min(width, height)
  const portrait = rh > rw
  const subSize = short * BASE * (SUB_SCALE[tpl.subtitles.size] ?? 1)
  const margin = tpl.subtitles.margin ?? (portrait ? 0.2 : 0.08)
  const side = width * 0.07
  const stroke = (size: number) => {
    const w = Math.max(0.6, size * 0.08)
    return `${-w}px ${-w}px 0 #000, ${w}px ${-w}px 0 #000, ${-w}px ${w}px 0 #000, ${w}px ${w}px 0 #000`
  }
  const overlay = (o: Template['title'], text: string, key: string) => {
    if (!o.show || !text) return null
    const name = o.size === 'auto' ? (portrait ? 'xlarge' : 'medium') : o.size
    const size = short * BASE * (OVERLAY_SCALE[name] ?? 1)
    const offset = o.position === 'top' ? (portrait ? 0.09 : 0.05) : (portrait ? 0.115 : 0.02)
    const style: CSSProperties = {
      left: side, right: side, fontSize: size, color: cssColor(o.color, colors),
      textShadow: stroke(size), [o.position]: height * offset,
    }
    return <div key={key} className="tp-text" style={style}>{text}</div>
  }
  const subStyle: CSSProperties = {
    left: side, right: side, bottom: height * margin, fontSize: subSize,
    color: cssColor(tpl.subtitles.color, colors),
    textShadow: tpl.subtitles.box ? undefined : stroke(subSize),
  }
  return (
    <div className={`tp tp-${tpl.fit}`} style={{ width, height }} role="img"
         aria-label={`${tpl.label || tpl.name} 的版面示意圖`}>
      <div className="tp-picture" />
      {overlay(tpl.title, title, 'title')}
      <div className="tp-text" style={subStyle}>
        <span className={tpl.subtitles.box ? 'tp-box' : undefined}
              style={tpl.subtitles.box ? { padding: `0 ${subSize * 0.22}px` } : undefined}>{line}</span>
      </div>
      {overlay(tpl.series, series, 'series')}
    </div>
  )
}
