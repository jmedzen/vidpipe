import { useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { ErrorBanner } from '../components/ErrorBanner'
import { postJson } from '../lib/api'
import { useAction } from '../lib/useAction'

const ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/

const EXAMPLE = `為什麼越想放下，反而越放不下？睡前一躺下，白天的事又自己跑出來。

佛陀說，苦來自渴愛，就是一直想抓住的心。

今晚念頭又來，就在心裡說一聲，我看到了。然後輕輕鬆手，回到呼吸。`

const BATCH_EXAMPLE = `id,title,text
letting-go-01,為什麼越想放下反而越放不下？,"為什麼越想放下，反而越放不下？

今晚念頭又來，就說一聲，我看到了。"
anger-01,,"為什麼你一生氣，先痛的人卻是你？"`

interface Beat {
  id: string
  text: string
  theme: string
}

interface Preview {
  id: string
  title: string
  chars: number
  estimated_seconds: number
  beats: Beat[]
  themes: string[]
}

interface Started {
  job: string
  short_id?: string
  ids?: string[]
}

interface Options {
  series: string
  music: boolean
  allowUnknown: boolean
  subtitleSize: string
}

function optionBody(o: Options) {
  return {
    series: o.series.trim() || undefined,
    music: o.music,
    allow_unknown_license: o.allowUnknown,
    subtitle_size: o.subtitleSize,
  }
}

function formatOf(name: string, text: string): string {
  const ext = name.toLowerCase().split('.').pop() ?? ''
  if (ext === 'csv' || ext === 'jsonl' || ext === 'json') return ext
  return /^\s*[[{]/.test(text) ? 'json' : 'csv'
}

/** 共用的選項：系列名稱、字幕字級、音樂、授權。 */
function OptionFields({ value, onChange, prefix }: {
  value: Options
  onChange: (next: Options) => void
  prefix: string
}) {
  return (
    <>
      <div className="field-row">
        <div className="field">
          <label htmlFor={`${prefix}-series`}>系列名稱（選填）</label>
          <input id={`${prefix}-series`} type="text" value={value.series} placeholder="一分鐘佛法"
                 onChange={(e) => onChange({ ...value, series: e.target.value })} autoComplete="off" />
          <p className="muted small">小字，整支片都在畫面下方。</p>
        </div>
        <div className="field">
          <label htmlFor={`${prefix}-size`}>字幕字級</label>
          <select id={`${prefix}-size`} value={value.subtitleSize}
                  onChange={(e) => onChange({ ...value, subtitleSize: e.target.value })}>
            <option value="normal">一般</option>
            <option value="large">大（預設）</option>
            <option value="xlarge">特大</option>
          </select>
          <p className="muted small">字越大，每行放得下的字越少。</p>
        </div>
      </div>
      <div className="field tick">
        <input id={`${prefix}-music`} type="checkbox" checked={value.music}
               onChange={(e) => onChange({ ...value, music: e.target.checked })} />
        <label htmlFor={`${prefix}-music`}>
          放背景音樂
          <span className="muted small">　從 vidgrab 的音樂庫挑一首（CC0 或 CC BY），旁白出聲時自動壓低。</span>
        </label>
      </div>
      <div className="field tick">
        <input id={`${prefix}-unknown`} type="checkbox" checked={value.allowUnknown}
               onChange={(e) => onChange({ ...value, allowUnknown: e.target.checked })} />
        <label htmlFor={`${prefix}-unknown`}>
          也用授權未標示的素材
          <span className="muted small">　授權明確的永遠優先；放行的會在素材清單標 ⚠，公開前自己確認。</span>
        </label>
      </div>
    </>
  )
}

function goToJob(started: Started | undefined) {
  if (started) window.location.hash = `#/jobs/${encodeURIComponent(started.job)}`
}

function SingleForm({ options, setOptions }: { options: Options; setOptions: (o: Options) => void }) {
  const action = useAction()
  const previewing = useAction()
  const [text, setText] = useState('')
  const [title, setTitle] = useState('')
  const [id, setId] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)

  const idProblem = id !== '' && !ID_RE.test(id)
    ? '只能用小寫英文、數字、底線與連字號，開頭要是英文或數字，最長 64 字' : null
  const ready = text.trim() !== '' && idProblem === null
  const body = () => ({
    text,
    title: title.trim() || undefined,
    id: id || undefined,
    ...optionBody(options),
  })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!ready) return
    void action.run(() => postJson<Started>('/api/quick', body())).then(goToJob)
  }
  const showPreview = () => {
    void previewing.run(() => postJson<Preview>('/api/quick/preview', body())).then((p) => {
      if (p) setPreview(p)
    })
  }

  return (
    <form className="card form" onSubmit={submit}>
      {(action.error ?? previewing.error) && <ErrorBanner error={(action.error ?? previewing.error)!} />}
      <div className="field">
        <label htmlFor="qk-text">文案</label>
        <textarea id="qk-text" rows={12} value={text} placeholder={EXAMPLE} spellCheck={false}
                  onChange={(e) => { setText(e.target.value); setPreview(null) }} />
        <p className="muted small">
          空白行隔開場景，每段一到三句。畫面會依文字自動挑；想自己指定，在段落裡加一行
          <code>畫面：ocean</code>（主題名稱按「預覽分鏡」可以看到）。不需要寫搜尋關鍵字。
        </p>
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="qk-title">標題（選填）</label>
          <input id="qk-title" type="text" value={title} placeholder="不填就用第一句" maxLength={40}
                 onChange={(e) => setTitle(e.target.value)} autoComplete="off" />
          <p className="muted small">黃色大字，整支片都在畫面上方；也是封面的標題。</p>
        </div>
        <div className="field">
          <label htmlFor="qk-id">作品 id（選填）</label>
          <input id="qk-id" type="text" value={id} placeholder="不填就自動產生"
                 onChange={(e) => setId(e.target.value.trim())} autoComplete="off" spellCheck={false}
                 aria-invalid={idProblem !== null} aria-describedby="qk-id-note" />
          <p id="qk-id-note" className={idProblem ? 'field-problem small' : 'muted small'}>
            {idProblem ?? '檔名會用它：<id>.mp4。同一個 id 再送一次會重做那支片。'}
          </p>
        </div>
      </div>

      <OptionFields value={options} onChange={setOptions} prefix="qk" />

      <div className="form-actions">
        <button type="submit" className="primary" disabled={!ready || action.busy}>
          {action.busy ? '送出中…' : '做成片'}
        </button>
        <button type="button" disabled={!ready || previewing.busy} onClick={showPreview}>
          {previewing.busy ? '分鏡中…' : '預覽分鏡'}
        </button>
        <span className="muted small">做成片會搜尋素材、合成旁白、渲染，一個工作做完。</span>
      </div>

      {preview && (
        <section aria-label="分鏡預覽" className="quick-preview">
          <p className="muted small">
            標題「{preview.title}」・{preview.chars} 字・約 {Math.round(preview.estimated_seconds)} 秒・
            {preview.beats.length} 個鏡頭。可用的畫面主題：{preview.themes.join('、')}
          </p>
          <table className="grid">
            <thead><tr><th>鏡頭</th><th>畫面主題</th><th>旁白</th></tr></thead>
            <tbody>
              {preview.beats.map((b) => (
                <tr key={b.id}><td><code>{b.id}</code></td><td>{b.theme}</td><td>{b.text}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </form>
  )
}

function BatchForm({ options, setOptions }: { options: Options; setOptions: (o: Options) => void }) {
  const action = useAction()
  const [text, setText] = useState('')
  const [fileName, setFileName] = useState('')
  const [readError, setReadError] = useState('')

  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setReadError('')
    file.text().then((content) => {
      setText(content)
      setFileName(file.name)
    }).catch(() => setReadError(`讀不了 ${file.name}`))
  }
  const ready = text.trim() !== ''
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!ready) return
    void action.run(() => postJson<Started>('/api/quick', {
      batch: text,
      format: formatOf(fileName, text),
      ...optionBody(options),
    })).then(goToJob)
  }

  return (
    <form className="card form" onSubmit={submit}>
      {action.error && <ErrorBanner error={action.error} />}
      <div className="field">
        <label htmlFor="qb-file">批次檔（CSV、JSON 或 JSONL）</label>
        <input id="qb-file" type="file" accept=".csv,.json,.jsonl,.txt,text/csv,application/json"
               onChange={pick} />
        {readError && <p className="field-problem small">{readError}</p>}
        <p className="muted small">
          CSV 的第一列是欄位名稱，一定要有 <code>text</code>；可以再加 <code>id</code>、<code>title</code>、
          <code>series</code>、<code>music</code>。JSON 是物件的清單，欄位相同。一批最多 50 筆。
        </p>
      </div>
      <div className="field">
        <label htmlFor="qb-text">內容{fileName && `（${fileName}）`}</label>
        <textarea id="qb-text" rows={10} value={text} placeholder={BATCH_EXAMPLE} spellCheck={false}
                  onChange={(e) => { setText(e.target.value); setFileName('') }} />
        <p className="muted small">選了檔案會放進這裡，也可以直接貼上或修改。文案裡的空白行一樣是隔開場景。</p>
      </div>

      <OptionFields value={options} onChange={setOptions} prefix="qb" />

      <div className="form-actions">
        <button type="submit" className="primary" disabled={!ready || action.busy}>
          {action.busy ? '送出中…' : '整批做成片'}
        </button>
        <span className="muted small">一個工作、一支接一支做；一支失敗不影響其他支，結果在工作頁逐筆列出。</span>
      </div>
    </form>
  )
}

/** 文案直出：貼文案（或上傳一批）→ 自動分鏡、挑畫面、配音、成片。不需要 LLM。 */
export function QuickPage({ mode }: { mode: string }) {
  const [options, setOptions] = useState<Options>({
    series: '', music: true, allowUnknown: true, subtitleSize: 'large',
  })
  const batch = mode === 'batch'
  return (
    <>
      <header className="page-head">
        <div>
          <h2>文案直出</h2>
          <p className="muted">
            貼上文案就做成一支 Short：自動分鏡、依文字挑畫面、配音、上字幕與標題。每支片都用沒用過的素材。
          </p>
        </div>
      </header>
      <nav className="tabs" aria-label="輸入方式">
        <a href="#/quick" aria-current={batch ? undefined : 'page'}>單篇</a>
        <a href="#/quick/batch" aria-current={batch ? 'page' : undefined}>批次</a>
      </nav>
      {batch
        ? <BatchForm options={options} setOptions={setOptions} />
        : <SingleForm options={options} setOptions={setOptions} />}
    </>
  )
}
