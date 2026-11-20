import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { CopyButton } from '../components/CopyButton'
import { ErrorBanner } from '../components/ErrorBanner'
import { Pill } from '../components/Pills'
import { postJson, putJson, useApi } from '../lib/api'
import { useAction } from '../lib/useAction'

export interface BgmTrack {
  id: string
  name: string
  title: string
  author: string
  tags: string[]
  folder_tags: string[]
  license: 'own' | 'free' | 'free-attribution' | 'unknown'
  credit: string
  credit_text: string
  url: string
  start: number
  volume: number
  enabled: boolean
  note: string
  duration: number | null
  uses: number
  last_used_by: string | null
}

export interface BgmLibrary {
  dir: string
  exists: boolean
  tracks: BgmTrack[]
  tags: { tag: string; count: number }[]
  usable: number
}

const LICENSE_TEXT: Record<BgmTrack['license'], string> = {
  own: '自己做的',
  free: '免標註（CC0 等）',
  'free-attribution': '要標註（CC BY 等）',
  unknown: '⚠ 未標示授權',
}

/** 推薦的合法來源；授權條款會變，用之前到原網站確認。 */
const SOURCES: { name: string; url: string; note: string }[] = [
  { name: 'YouTube 音效庫', url: 'https://studio.youtube.com/', note: 'YouTube 工作室 → 音效庫。可篩「不需要標註」；只用在 YouTube 最單純。' },
  { name: 'Pixabay Music', url: 'https://pixabay.com/music/', note: '免標註、可商用；搜 meditation、zen、guzheng、flute。' },
  { name: 'Free Music Archive', url: 'https://freemusicarchive.org/', note: '每首授權不同：挑 CC BY 或 CC0，避開 NC（非商業）與 ND。' },
  { name: 'Musopen', url: 'https://musopen.org/music/', note: '古典樂的公有領域錄音，多數可自由使用（每首確認）。' },
  { name: 'Incompetech', url: 'https://incompetech.com/music/royalty-free/music.html', note: 'Kevin MacLeod，CC BY 4.0，要標註作者。' },
]

function fmtDuration(sec: number | null): string {
  if (!sec) return '讀不到長度'
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function TrackEditor({ track, onSaved, onCancel }: {
  track: BgmTrack
  onSaved: () => void
  onCancel: () => void
}) {
  const action = useAction()
  const extra = track.tags.filter((t) => !track.folder_tags.includes(t))
  const [tags, setTags] = useState(extra.join('，'))
  const [license, setLicense] = useState(track.license)
  const [author, setAuthor] = useState(track.author)
  const [credit, setCredit] = useState(track.credit)
  const [url, setUrl] = useState(track.url)
  const [start, setStart] = useState(String(track.start))
  const [volume, setVolume] = useState(String(track.volume))
  const [enabled, setEnabled] = useState(track.enabled)
  const p = `bgm-${track.id}`

  const submit = (e: FormEvent) => {
    e.preventDefault()
    void action.run(() => putJson('/api/bgm/track', {
      id: track.id,
      changes: {
        tags, license, author, credit, url, enabled,
        start: Number(start) || 0,
        volume: volume.trim() === '' ? 0.22 : Number(volume),
      },
    })).then((r) => { if (r) onSaved() })
  }

  return (
    <form className="form bgm-edit" onSubmit={submit}>
      {action.error && <ErrorBanner error={action.error} />}
      <div className="field">
        <label htmlFor={`${p}-tags`}>標籤</label>
        <input id={`${p}-tags`} type="text" value={tags} placeholder="舒緩，夜晚" autoComplete="off"
               onChange={(e) => setTags(e.target.value)} />
        <p className="muted small">
          逗號隔開。{track.folder_tags.length > 0 && <>資料夾已經給了：{track.folder_tags.join('、')}。</>}
        </p>
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor={`${p}-license`}>授權</label>
          <select id={`${p}-license`} value={license}
                  onChange={(e) => setLicense(e.target.value as BgmTrack['license'])}>
            <option value="unknown">未標示</option>
            <option value="free">免標註（CC0、Pixabay 等）</option>
            <option value="free-attribution">要標註（CC BY 等）</option>
            <option value="own">自己做的</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${p}-author`}>作者</label>
          <input id={`${p}-author`} type="text" value={author} autoComplete="off"
                 onChange={(e) => setAuthor(e.target.value)} />
        </div>
      </div>
      <div className="field">
        <label htmlFor={`${p}-credit`}>標註文字（選填）</label>
        <input id={`${p}-credit`} type="text" value={credit} autoComplete="off"
               placeholder="作者《曲名》（CC BY 4.0）— 網址"
               onChange={(e) => setCredit(e.target.value)} />
        <p className="muted small">會原樣放進 credits 與影片說明；不填就用「作者《曲名》— 網址」。</p>
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor={`${p}-url`}>出處網址</label>
          <input id={`${p}-url`} type="text" value={url} autoComplete="off"
                 onChange={(e) => setUrl(e.target.value)} />
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor={`${p}-start`}>從第幾秒放</label>
            <input id={`${p}-start`} type="number" min={0} step={0.5} value={start}
                   onChange={(e) => setStart(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor={`${p}-volume`}>音量</label>
            <input id={`${p}-volume`} type="number" min={0} max={2} step={0.02} value={volume}
                   onChange={(e) => setVolume(e.target.value)} />
          </div>
        </div>
      </div>
      <div className="field tick">
        <input id={`${p}-enabled`} type="checkbox" checked={enabled}
               onChange={(e) => setEnabled(e.target.checked)} />
        <label htmlFor={`${p}-enabled`}>
          出片時會挑到這首<span className="muted small">　取消勾選就先不用，檔案留著。</span>
        </label>
      </div>
      <div className="form-actions">
        <button type="submit" className="primary" disabled={action.busy}>
          {action.busy ? '儲存中…' : '儲存'}
        </button>
        <button type="button" onClick={onCancel}>取消</button>
        <span className="muted small">存成音檔旁邊的同名 .json。</span>
      </div>
    </form>
  )
}

function TrackCard({ track, onChanged }: { track: BgmTrack; onChanged: () => void }) {
  const [editing, setEditing] = useState(false)
  const tone = track.license === 'unknown' ? 'warn' : track.license === 'free-attribution' ? 'note' : 'ok'
  return (
    <li className={track.enabled ? 'card bgm-track' : 'card bgm-track off'}>
      <div className="bgm-head">
        <div className="bgm-title">
          <strong>{track.title}</strong>
          {track.author && <span className="muted small">　{track.author}</span>}
        </div>
        {!editing && <button type="button" onClick={() => setEditing(true)}>編輯</button>}
      </div>
      <div className="pills">
        {track.tags.map((t) => <Pill key={t}>{t}</Pill>)}
        <Pill tone={tone}>{LICENSE_TEXT[track.license]}</Pill>
        <Pill tone={track.duration ? 'plain' : 'warn'}>{fmtDuration(track.duration)}</Pill>
        {!track.enabled && <Pill tone="warn">停用</Pill>}
        <span className="muted small">
          {track.uses ? `用過 ${track.uses} 次，最近：${track.last_used_by}` : '還沒用過'}
        </span>
      </div>
      <audio className="bgm-audio" controls preload="none"
             src={`/api/bgm/audio?id=${encodeURIComponent(track.id)}`}
             aria-label={`試聽 ${track.title}`} />
      <p className="muted small bgm-file"><code>{track.id}</code></p>
      {editing && (
        <TrackEditor track={track} onCancel={() => setEditing(false)}
                     onSaved={() => { setEditing(false); onChanged() }} />
      )}
    </li>
  )
}

/** 背景音樂曲庫：放音檔就能用、子資料夾＝標籤；在這裡試聽、加標籤、填授權。 */
export function MusicPage() {
  const lib = useApi<BgmLibrary>('/api/bgm')
  const creating = useAction()
  const [filter, setFilter] = useState('')
  const data = lib.data

  const shown = useMemo(() => (data?.tracks ?? []).filter(
    (t) => !filter || t.tags.includes(filter)), [data, filter])

  return (
    <>
      <header className="page-head">
        <div>
          <h2>背景音樂</h2>
          <p className="muted">
            把音樂檔放進曲庫資料夾，出片時就會自動挑一首、輪流用「最久沒用過」的，不會每支都同一首。
            子資料夾名稱就是標籤（例如 <code>古琴/</code>、<code>明亮/</code>），文案直出可以指定只用某類。
          </p>
        </div>
        <div className="head-actions">
          <button type="button" onClick={lib.reload} disabled={lib.loading}>重新掃描</button>
        </div>
      </header>

      {lib.error && <ErrorBanner error={lib.error} />}
      {creating.error && <ErrorBanner error={creating.error} />}

      {data && (
        <section className="card bgm-where" aria-label="曲庫資料夾">
          <div className="bgm-dir">
            <span className="muted small">曲庫資料夾</span>
            <code>{data.dir}</code>
            <CopyButton text={data.dir} />
          </div>
          {data.exists
            ? <p className="muted small">
                {data.usable} 首可以用{data.tracks.length > data.usable && `（另有 ${data.tracks.length - data.usable} 首停用或讀不到長度）`}。
                {data.usable === 0 && '曲庫是空的時候，會改從 vidgrab 的音樂庫找（曲風比較單一）。'}
                換資料夾到「設定 → 聲音」。
              </p>
            : <p className="small">
                資料夾還不存在。
                <button type="button" className="primary" disabled={creating.busy}
                        onClick={() => { void creating.run(() => postJson('/api/bgm/init')).then(lib.reload) }}>
                  建立資料夾
                </button>
              </p>}
        </section>
      )}

      {data && data.tags.length > 0 && (
        <div className="chips bgm-filter" role="group" aria-label="依標籤篩選">
          <button type="button" className="chip" aria-pressed={filter === ''} onClick={() => setFilter('')}>
            全部 {data.tracks.length}
          </button>
          {data.tags.map((t) => (
            <button key={t.tag} type="button" className="chip" aria-pressed={filter === t.tag}
                    onClick={() => setFilter(filter === t.tag ? '' : t.tag)}>
              {t.tag} {t.count}
            </button>
          ))}
        </div>
      )}

      {data && shown.length > 0 && (
        <ul className="bgm-list">
          {shown.map((t) => <TrackCard key={t.id} track={t} onChanged={lib.reload} />)}
        </ul>
      )}
      {data && data.exists && data.tracks.length === 0 && (
        <div className="card empty">
          <p>曲庫裡還沒有音樂。</p>
          <p className="muted small">支援 mp3、m4a、aac、wav、flac、ogg、opus。放進去之後按「重新掃描」。</p>
        </div>
      )}

      <section className="card bgm-sources" aria-label="哪裡找可以用的音樂">
        <h3>哪裡找可以合法使用的音樂</h3>
        <p className="muted small">
          只放你確定可以用的音樂。授權條款會改，下載前到網站上再確認一次；要標註的，在上面「編輯」填作者與標註文字，
          vidpipe 會放進 credits 與影片說明。
        </p>
        <ul className="bgm-source-list">
          {SOURCES.map((s) => (
            <li key={s.name}>
              <a href={s.url} target="_blank" rel="noreferrer">{s.name}</a>
              <span className="muted small">　{s.note}</span>
            </li>
          ))}
        </ul>
      </section>
    </>
  )
}
