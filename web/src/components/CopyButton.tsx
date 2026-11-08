import { useEffect, useRef, useState } from 'react'

/** 剪貼簿：區網的 http 網址不是 secure context，`navigator.clipboard` 不存在，退回 execCommand。 */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    /* 退回舊做法 */
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(area)
  }
}

export function CopyButton({ text, label = '複製' }: { text: string; label?: string }) {
  const [state, setState] = useState<'idle' | 'done' | 'failed'>('idle')
  const timer = useRef<number>(0)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const copy = () => {
    void copyText(text).then((ok) => {
      setState(ok ? 'done' : 'failed')
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setState('idle'), 2000)
    })
  }

  return (
    <button type="button" onClick={copy} aria-live="polite">
      {state === 'done' ? '已複製 ✓' : state === 'failed' ? '複製失敗，請手動選取' : label}
    </button>
  )
}
