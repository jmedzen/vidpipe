import type { Level } from '../types'
import { LEVEL_TEXT } from '../lib/format'

/** 燈號：顏色之外一定帶文字（色盲看得懂，螢幕閱讀器也讀得到）。 */
export function StatusDot({ level, label }: { level: Level; label?: string }) {
  return (
    <span className={`dot dot-${level}`} role="img" aria-label={label ?? LEVEL_TEXT[level]}
          title={label ?? LEVEL_TEXT[level]} />
  )
}
