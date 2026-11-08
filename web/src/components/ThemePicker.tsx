import { useEffect, useRef, useState } from 'react'
import { THEMES, kindIcon } from '../lib/theme'

interface Props {
  theme: string
  onChange: (id: string) => void
}

export function ThemePicker({ theme, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const current = THEMES.find((t) => t.id === theme) ?? THEMES[0]!
  return (
    <div className="theme-picker" ref={box}>
      <button type="button" className="ghost" aria-haspopup="menu" aria-expanded={open}
              onClick={() => setOpen((v) => !v)} title="換主題">
        {kindIcon(current.kind)} {current.name}
      </button>
      {open && (
        <div className="theme-menu" role="menu">
          {(['dark', 'light'] as const).map((kind) => (
            <div key={kind}>
              <div className="group">{kind === 'dark' ? '深色' : '淺色'}</div>
              {THEMES.filter((t) => t.kind === kind).map((t) => (
                <button key={t.id} type="button" role="menuitemradio" aria-checked={t.id === theme}
                        onClick={() => { onChange(t.id); setOpen(false) }}>
                  <span className="swatch" style={{ background: t.dot, boxShadow: `inset 0 0 0 3px ${t.accent}` }} />
                  {t.name}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
