import { useState } from 'react'
import type { FormEvent } from 'react'
import { ErrorBanner } from '../components/ErrorBanner'
import { postJson, useApi } from '../lib/api'
import { useAction } from '../lib/useAction'
import type { ShortList } from '../types'

const ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/

const EXAMPLE = `台北的夜晚，從一碗熱湯開始。蒸氣升起來，整條街都暖了。
關鍵字：night market soup, steaming bowl

夜市的燈，一盞一盞亮起來。
關鍵字：city lights night`

/** 新增作品：貼一份腳本 → vidpipe 替每個場景去 vidgrab 搜尋、挑素材（`POST /api/plan`）。 */
export function NewShortPage() {
  const existing = useApi<ShortList>('/api/shorts')
  const action = useAction()
  const [script, setScript] = useState('')
  const [id, setId] = useState('')
  const [title, setTitle] = useState('')
  const [music, setMusic] = useState('')
  const [allowUnknown, setAllowUnknown] = useState(false)

  const taken = !!existing.data?.shorts.some((s) => s.id === id)
  const idProblem = id === '' ? null
    : !ID_RE.test(id) ? '只能用小寫英文、數字、底線與連字號，開頭要是英文或數字，最長 64 字'
      : taken ? '已經有這個 id 的作品了，換一個；或到那支片刪掉再重貼' : null
  const ready = script.trim() !== '' && id !== '' && idProblem === null

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!ready) return
    void action.run(() => postJson<{ short_id: string }>('/api/plan', {
      script,
      id,
      title: title.trim() || undefined,
      music: music.trim() || undefined,
      allow_unknown_license: allowUnknown,
    })).then((r) => {
      if (r) window.location.hash = `#/shorts/${encodeURIComponent(r.short_id)}/plan`
    })
  }

  return (
    <>
      <p className="crumb"><a href="#/shorts">← 全部作品</a></p>
      <header className="page-head">
        <div>
          <h2>新增作品</h2>
          <p className="muted">貼一份腳本，vidpipe 會替每個場景挑素材與音樂；挑好之後你看過再做成片。</p>
        </div>
      </header>
      {existing.error && <ErrorBanner error={existing.error} onRetry={existing.reload} />}
      {action.error && <ErrorBanner error={action.error} />}

      <form className="card form" onSubmit={submit}>
        <div className="field">
          <label htmlFor="ns-script">腳本</label>
          <textarea id="ns-script" rows={12} value={script} placeholder={EXAMPLE}
                    onChange={(e) => setScript(e.target.value)} spellCheck={false} />
          <p className="muted small">
            空白行隔開場景，每段是一到幾句旁白。<code>關鍵字：a, b</code> 那行是搜尋素材用的
            （英文效果最好），不會被唸出來；沒寫就拿旁白去搜，比較不準。
          </p>
        </div>

        <div className="field-row">
          <div className="field">
            <label htmlFor="ns-id">作品 id</label>
            <input id="ns-id" type="text" value={id} placeholder="taipei-night-01"
                   onChange={(e) => setId(e.target.value.trim())} autoComplete="off" spellCheck={false}
                   aria-invalid={idProblem !== null} aria-describedby="ns-id-note" />
            <p id="ns-id-note" className={idProblem ? 'field-problem small' : 'muted small'}>
              {idProblem ?? '檔名會用它：<id>.mp4。之後改不了。'}
            </p>
          </div>
          <div className="field">
            <label htmlFor="ns-title">標題（選填）</label>
            <input id="ns-title" type="text" value={title} placeholder="台北夜市"
                   onChange={(e) => setTitle(e.target.value)} autoComplete="off" />
            <p className="muted small">只用在這裡的列表與素材清單。</p>
          </div>
        </div>

        <div className="field">
          <label htmlFor="ns-music">背景音樂關鍵字（選填）</label>
          <input id="ns-music" type="text" value={music} placeholder="calm piano"
                 onChange={(e) => setMusic(e.target.value)} autoComplete="off" />
          <p className="muted small">不填就不放音樂。</p>
        </div>

        <div className="field tick">
          <input id="ns-unknown" type="checkbox" checked={allowUnknown}
                 onChange={(e) => setAllowUnknown(e.target.checked)} />
          <label htmlFor="ns-unknown">
            也考慮授權不明、要自己確認的素材
            <span className="muted small">　預設只挑可自由使用與要標註的；放行的會在素材清單標 ⚠，公開前自己確認。</span>
          </label>
        </div>

        <div className="form-actions">
          <button type="submit" className="primary" disabled={!ready || action.busy}>
            {action.busy ? '送出中…' : '挑素材'}
          </button>
          <span className="muted small">會搜尋 vidgrab，不會合成旁白也不會下載素材。</span>
        </div>
      </form>
    </>
  )
}
