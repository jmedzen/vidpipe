import { useEffect, useMemo, useRef, useState } from 'react'
import { CopyButton } from '../components/CopyButton'
import { ErrorBanner } from '../components/ErrorBanner'
import { JobDot } from '../components/JobDot'
import { CandidateLine, LicenseBadge, Pill } from '../components/Pills'
import { ProgressBar } from '../components/ProgressBar'
import { ApiFailure, deleteJson, getText, postJson, useApi } from '../lib/api'
import {
  JOB_TEXT, RUNNING_TEXT, SHORT_STAGE_LEVEL, SHORT_STAGE_TEXT, bytesText, dateTimeIso, durationText,
  elapsed, stageText, stamp,
} from '../lib/format'
import { useAction } from '../lib/useAction'
import type {
  CreditsBrief, JobBrief, JobDetail, ManifestScene, PlanCandidate, PlanPick, PlanReport,
  ShortDetail, ShortDetailResponse, ShortFile, ShortList, ShortSources, ShortSummary, SourceCredit,
  TimelineScene,
} from '../types'
import { CancelButton, kindText, useTick } from './Jobs'
import { SceneEditor } from './SceneEditor'

/* ---------------------------------------------------------- 共用小元件 */

function StagePill({ stage }: { stage: ShortSummary['stage'] }) {
  return <Pill tone={SHORT_STAGE_LEVEL[stage]}>{SHORT_STAGE_TEXT[stage]}</Pill>
}

function LicensePill({ credit }: { credit: SourceCredit }) {
  if (credit.kind === 'file') return <Pill tone="warn">⚠ 本機檔案，授權自己確認</Pill>
  return <LicenseBadge kind={credit.license?.kind} label={credit.license?.label} />
}

function CreditsPills({ credits }: { credits: CreditsBrief | null }) {
  if (!credits) return null
  return (
    <>
      {credits.unclear > 0 && <Pill tone="warn">⚠ {credits.unclear} 個素材的授權要確認</Pill>}
      {credits.attribution_required > 0 && <Pill tone="note">{credits.attribution_required} 個要標註</Pill>}
    </>
  )
}

function fileOf(files: ShortFile[], name: string): ShortFile | undefined {
  return files.find((f) => f.name === name)
}

/* ---------------------------------------------------------- 列表 */

function ShortCard({ item }: { item: ShortSummary }) {
  const cover = fileOf(item.files, `${item.id}.cover.jpg`)
  const job = item.last_job
  return (
    <li className="card short-card">
      {cover
        ? <img className="thumb" src={cover.url} alt="" loading="lazy" />
        : <div className="thumb thumb-empty" aria-hidden="true">▶</div>}
      <div className="short-info">
        <a className="short-link" href={`#/shorts/${encodeURIComponent(item.id)}`}>
          {item.title || item.id}
        </a>
        <div className="muted small"><code>{item.id}</code></div>
        <div className="pills">
          <StagePill stage={item.stage} />
          {item.total !== null && <Pill>{durationText(item.total)}</Pill>}
          <Pill>{item.scenes} 個場景</Pill>
          <CreditsPills credits={item.credits} />
        </div>
        <div className="muted small short-meta">
          {job && (
            <span className={job.status === 'failed' ? 'job-bad' : undefined}>
              <JobDot status={job.status} /> {kindText(job.kind)} · {JOB_TEXT[job.status]}
            </span>
          )}
          <span>更新 {dateTimeIso(item.updated_at)}</span>
        </div>
      </div>
    </li>
  )
}

export function ShortsPage() {
  const list = useApi<ShortList>('/api/shorts', 4000)
  const items = list.data?.shorts ?? []

  return (
    <>
      <header className="page-head">
        <div>
          <h2>作品</h2>
          <p className="muted">work 資料夾裡每一支片現在做到哪；每 4 秒更新一次。</p>
        </div>
        <div className="head-actions">
          <a className="button primary" href="#/new">＋ 新增作品</a>
        </div>
      </header>
      {list.error && <ErrorBanner error={list.error} onRetry={list.reload} />}
      {list.data && items.length === 0 && (
        <section className="card empty">
          <h3>還沒有作品</h3>
          <p className="muted">
            <a href="#/new">貼一份腳本</a>，vidpipe 會替每個場景挑素材；也可以用終端機：
            <code>vidpipe plan script.txt --id my-short -o shorts.json</code>，
            再 <code>vidpipe make shorts.json</code>。
          </p>
        </section>
      )}
      {items.length > 0 && (
        <ul className="short-grid">
          {items.map((item) => <ShortCard key={item.id} item={item} />)}
        </ul>
      )}
    </>
  )
}

/* ---------------------------------------------------------- 詳細頁：上方的狀態 */

/** 最近一次工作失敗：把原因直接講出來，不用點進去。 */
function LastFailure({ job }: { job: JobBrief }) {
  const detail = useApi<JobDetail>(`/api/job/${job.id}`)
  const error = detail.data?.job.error
  return (
    <div className="banner bad" role="alert">
      <div>
        <strong>最近一次「{kindText(job.kind)}」失敗了</strong>
        {error && <p>{error.message}</p>}
        {error?.hint && <p className="muted small">{error.hint}</p>}
      </div>
      <a href={`#/jobs/${job.id}`}>看工作紀錄 →</a>
    </div>
  )
}

function RunningJob({ job, onChanged }: { job: JobBrief; onChanged: () => void }) {
  return (
    <section className="card" aria-label="進行中的工作">
      <div className="card-head">
        <JobDot status="running" />
        <div>
          <h3>{RUNNING_TEXT[job.kind] ?? `正在${kindText(job.kind)}`}</h3>
          <p className="muted small">
            {job.stage ? `目前階段：${stageText(job.stage)}` : '準備中'}
            {'　'}<a href={`#/jobs/${job.id}`}>看工作紀錄</a>
          </p>
        </div>
      </div>
      <ProgressBar ratio={job.progress} label="工作進度" />
      <CancelButton id={job.id} onDone={onChanged} />
    </section>
  )
}

/* ---------------------------------------------------------- 分頁：場景 */

interface SceneRow {
  id: string
  index: number
  span: { start: number; end: number; duration: number } | null
  sentences: { key: number; text: string; en: string; start: number | null }[]
  source: SourceCredit | null
  looped: boolean
  clipHint: string
}

function manifestSentences(scene: ManifestScene): string[] {
  if (Array.isArray(scene.sentences)) return scene.sentences.map((s) => s.text)
  return scene.text ? [scene.text] : []
}

/** 有時間軸就用時間軸（句子有時間）；還沒旁白時退回 manifest（只有文字）。 */
function sceneRows(short: ShortDetail): SceneRow[] {
  const sources = new Map((short.sources?.scenes ?? []).map((s) => [s.scene, s]))
  const draft = short.manifest?.scenes ?? []
  const clipHint = (i: number) => {
    const clip = draft[i]?.clip
    return clip?.asset_id ?? (clip?.path ? clip.path.split(/[\\/]/).pop() ?? '' : '')
  }
  if (short.timeline) {
    return short.timeline.scenes.map((s: TimelineScene) => ({
      id: s.id, index: s.index, span: { start: s.start, end: s.end, duration: s.duration },
      sentences: s.sentences.map((x) => ({ key: x.index, text: x.text, en: x.en, start: x.start })),
      source: sources.get(s.id)?.source ?? null, looped: sources.get(s.id)?.looped ?? false,
      clipHint: clipHint(s.index),
    }))
  }
  let n = 0
  return draft.map((s, i) => ({
    id: s.id ?? `s${i + 1}`, index: i, span: null,
    sentences: manifestSentences(s).map((text) => ({ key: n++, text, en: '', start: null })),
    source: null, looped: false, clipHint: clipHint(i),
  }))
}

function TimeButton({ at, onSeek, label }: { at: number; onSeek?: (t: number) => void; label: string }) {
  if (!onSeek) return <span className="time-link plain">{stamp(at)}</span>
  return (
    <button type="button" className="time-link" onClick={() => onSeek(at)} aria-label={label}>
      {stamp(at)}
    </button>
  )
}

function ScenesPanel({ short, active, onSeek, onEdit }: {
  short: ShortDetail; active: number | null; onSeek?: (t: number) => void; onEdit?: () => void
}) {
  const rows = useMemo(() => sceneRows(short), [short])
  const warnings = short.timeline?.warnings ?? []
  return (
    <>
      {onEdit && (
        <div className="scenes-bar">
          <span className="muted small">旁白、畫面適配、素材與音樂都可以改；儲存後再重新做。</span>
          <button type="button" onClick={onEdit}>編輯場景</button>
        </div>
      )}
      {warnings.length > 0 && (
        <div className="banner warn" role="status">
          <div>
            <strong>時間軸有 {warnings.length} 則提醒</strong>
            {warnings.map((w) => <p key={w}>{w}</p>)}
          </div>
        </div>
      )}
      {!short.timeline && rows.length > 0 && (
        <p className="muted small note-line">還沒有旁白與時間軸；按右上角的「做成片」會一起做。下面是 manifest 裡的文字。</p>
      )}
      {rows.length === 0 && <section className="card empty"><p className="muted">這支片沒有場景。</p></section>}
      {rows.map((row) => (
        <section key={row.id} className="card scene" aria-label={`場景 ${row.index + 1}`}>
          <header className="scene-head">
            <span className="scene-no">{row.index + 1}</span>
            <code>{row.id}</code>
            {row.span && (
              <span className="muted small">
                <TimeButton at={row.span.start} onSeek={onSeek} label={`從場景 ${row.index + 1} 開頭播放`} />
                {' – '}{stamp(row.span.end)}（{row.span.duration.toFixed(1)} 秒）
              </span>
            )}
          </header>
          {(row.source || row.clipHint) && (
            <div className="scene-source">
              <span className="muted small clip">
                {row.source ? row.source.title || row.source.asset_id || row.source.path : row.clipHint}
              </span>
              {row.source && <LicensePill credit={row.source} />}
              {row.looped && <Pill tone="warn">素材比場景短，循環播放</Pill>}
            </div>
          )}
          {row.sentences.length === 0
            ? <p className="muted small">這個場景沒有旁白。</p>
            : (
              <ol className="sentences">
                {row.sentences.map((s) => (
                  <li key={s.key} className={active === s.key ? 'active' : undefined}>
                    {s.start !== null
                      ? <TimeButton at={s.start} onSeek={onSeek} label={`從「${s.text}」播放`} />
                      : <span className="time-link plain">—</span>}
                    <div>
                      <p>{s.text}</p>
                      {s.en && <p className="muted small">{s.en}</p>}
                    </div>
                  </li>
                ))}
              </ol>
            )}
        </section>
      ))}
    </>
  )
}

/* ---------------------------------------------------------- 分頁：素材與授權 */

interface UsedSource {
  credit: SourceCredit
  used: string[]
}

/** 同一個素材用在好幾個場景只列一次（跟 credits.md 的規則一樣）。 */
function usedSources(sources: ShortSources): UsedSource[] {
  const found = new Map<string, UsedSource>()
  const add = (credit: SourceCredit, where: string) => {
    const key = credit.asset_id ?? credit.path ?? credit.webpage_url ?? ''
    const hit = found.get(key)
    if (hit) hit.used.push(where)
    else found.set(key, { credit, used: [where] })
  }
  sources.scenes.forEach((s) => { if (s.source) add(s.source, `場景 ${s.scene}`) })
  if (sources.music) add(sources.music.source, '背景音樂')
  return [...found.values()]
}

function useTextFile(url: string | null) {
  const [state, setState] = useState<{ url: string | null; text: string | null; error: ApiFailure | null }>(
    { url: null, text: null, error: null })
  useEffect(() => {
    if (!url) return
    const ctl = new AbortController()
    getText(url, ctl.signal)
      .then((text) => setState({ url, text, error: null }))
      .catch((e: unknown) => {
        if (!ctl.signal.aborted) setState({ url, text: null, error: e instanceof ApiFailure ? e : null })
      })
    return () => ctl.abort()
  }, [url])
  return state.url === url ? state : { url, text: null, error: null }
}

/** credits.md 裡「說明欄範本」那個程式碼區塊的內容。 */
function descriptionBlock(markdown: string | null): string | null {
  const m = markdown ? /```\n([\s\S]*?)\n```/.exec(markdown) : null
  return m?.[1] ?? null
}

function CreditsPanel({ short }: { short: ShortDetail }) {
  const credits = fileOf(short.files, `${short.id}.credits.md`)
  const file = useTextFile(credits ? `${credits.url}?v=${credits.size_bytes}` : null)
  const description = descriptionBlock(file.text)
  const list = short.sources ? usedSources(short.sources) : []

  if (!short.sources) {
    return (
      <section className="card empty">
        <h3>還沒有素材清單</h3>
        <p className="muted">渲染（<code>vidpipe render</code> 或 <code>make</code>）之後，會記下每個場景實際用了哪個素材。</p>
      </section>
    )
  }
  return (
    <>
      <div className="pills panel-pills">
        <Pill>{short.credits?.assets ?? list.length} 個素材</Pill>
        <CreditsPills credits={short.credits} />
      </div>
      {(short.credits?.unclear ?? 0) > 0 && (
        <div className="banner warn" role="status">
          <div>
            <strong>有素材的授權要自己確認</strong>
            <p>標 ⚠ 的授權 vidgrab 沒辦法確定（或是本機檔案）。公開發佈前先到來源頁確認可以用。</p>
          </div>
        </div>
      )}
      <section className="card table-card">
        <table className="grid">
          <thead>
            <tr>
              <th scope="col">素材</th>
              <th scope="col">授權</th>
              <th scope="col">用在</th>
            </tr>
          </thead>
          <tbody>
            {list.map(({ credit, used }) => (
              <tr key={credit.asset_id ?? credit.path ?? credit.webpage_url}>
                <td>
                  {credit.webpage_url
                    ? <a href={credit.webpage_url} target="_blank" rel="noreferrer">{credit.title || credit.asset_id}</a>
                    : <span>{credit.title || credit.path?.split(/[\\/]/).pop() || credit.asset_id}</span>}
                  <div className="muted small">
                    {credit.author_url && credit.author
                      ? <a href={credit.author_url} target="_blank" rel="noreferrer">{credit.author}</a>
                      : credit.author}
                    {credit.asset_id && <>{credit.author ? '　' : ''}<code>{credit.asset_id}</code></>}
                  </div>
                </td>
                <td><LicensePill credit={credit} /></td>
                <td className="muted small">{used.join('、')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {description && (
        <section className="card">
          <div className="card-head">
            <div>
              <h3>說明欄範本</h3>
              <p className="muted small">貼進影片說明欄。要標註的授權一定要放。</p>
            </div>
            <CopyButton text={description} label="複製" />
          </div>
          <pre className="json">{description}</pre>
        </section>
      )}
      {credits && (
        <p className="muted small note-line">
          完整清單：<a href={credits.url} target="_blank" rel="noreferrer">{credits.name}</a>
        </p>
      )}
    </>
  )
}

/* ---------------------------------------------------------- 分頁：檔案、Manifest、工作 */

const OUT_NOTES: [string, string][] = [
  ['.mp4', '成片'], ['.cover.jpg', '封面'], ['.credits.md', '素材來源與授權'], ['.srt', '字幕檔'],
]
const WORK_NOTES: Record<string, string> = {
  'manifest.json': '送進來的 manifest',
  'timeline.json': '旁白時間軸（每句從幾秒到幾秒）',
  'sources.json': '每個場景實際用的素材',
  'narration.wav': '合成的整段旁白',
  'subs.srt': '字幕（SRT）',
  'subs.ass': '燒進畫面用的字幕（ASS）',
  'plan.json': 'plan 的挑選報告（備選、警告）',
}

function noteOf(file: ShortFile): string {
  if (file.area === 'work') return WORK_NOTES[file.name] ?? ''
  return OUT_NOTES.find(([suffix]) => file.name.endsWith(suffix))?.[1] ?? ''
}

function FilesPanel({ files }: { files: ShortFile[] }) {
  if (files.length === 0) return <section className="card empty"><p className="muted">還沒有任何檔案。</p></section>
  return (
    <section className="card table-card">
      <table className="grid">
        <thead>
          <tr>
            <th scope="col">檔案</th>
            <th scope="col">說明</th>
            <th scope="col" className="num">大小</th>
          </tr>
        </thead>
        <tbody>
          {files.map((f) => (
            <tr key={`${f.area}/${f.name}`}>
              <td>
                <a href={f.url} target="_blank" rel="noreferrer">{f.name}</a>
                <div className="muted small">{f.area === 'out' ? 'out 資料夾（成品）' : 'work 資料夾（中間檔）'}</div>
              </td>
              <td className="muted small">{noteOf(f)}</td>
              <td className="muted small num nowrap">{bytesText(f.size_bytes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function ManifestPanel({ manifest }: { manifest: ShortDetail['manifest'] }) {
  if (!manifest) return <section className="card empty"><p className="muted">沒有 manifest.json。</p></section>
  const text = JSON.stringify(manifest, null, 2)
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h3>manifest.json</h3>
          <p className="muted small">最後一次送進來的版本；要改就改這份再 <code>make</code>。</p>
        </div>
        <CopyButton text={text} />
      </div>
      <pre className="json">{text}</pre>
    </section>
  )
}

function JobsPanel({ jobs }: { jobs: JobBrief[] }) {
  useTick(jobs.some((j) => j.status === 'running'))
  if (jobs.length === 0) return <section className="card empty"><p className="muted">這支片還沒有工作紀錄。</p></section>
  return (
    <section className="card table-card">
      <table className="grid">
        <thead>
          <tr>
            <th scope="col">狀態</th>
            <th scope="col">工作</th>
            <th scope="col" className="col-start">開始</th>
            <th scope="col">耗時</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td className="status-cell"><JobDot status={job.status} /> <span className="status-text">{JOB_TEXT[job.status]}</span></td>
              <td>
                <a href={`#/jobs/${job.id}`} className="job-link">{kindText(job.kind)}</a>
                <div className="muted small">{job.stage ? stageText(job.stage) : ''}</div>
              </td>
              <td className="muted small col-start">{dateTimeIso(job.created_at)}</td>
              <td className="muted small nowrap">{elapsed(job.created_at, job.status === 'running' ? null : job.finished_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

/* ---------------------------------------------------------- 分頁：挑素材（plan 的報告） */

function Alternatives({ items, music }: { items: PlanCandidate[]; music?: boolean }) {
  if (items.length === 0) return null
  return (
    <details className="alts">
      <summary>備選 {items.length} 個</summary>
      {items.map((c) => <CandidateLine key={c.asset_id} cand={c} music={music} />)}
    </details>
  )
}

function PickBody({ pick, none, music }: { pick: PlanPick & { clip?: 'given' }; none: string; music?: boolean }) {
  return (
    <>
      {pick.clip === 'given' && <p className="muted small">這個場景原本就指定了素材，沒有動它。</p>}
      {pick.chosen && <CandidateLine cand={pick.chosen} music={music} />}
      {!pick.chosen && pick.clip !== 'given' && <p className="plan-none">{none}</p>}
      <Alternatives items={pick.alternatives ?? []} music={music} />
    </>
  )
}

function PlanPanel({ plan, planning, retry }: { plan: PlanReport | null; planning: boolean; retry: boolean }) {
  if (!plan) {
    return (
      <section className="card empty">
        <h3>{planning ? '正在搜尋、挑素材…' : '還沒有挑素材的結果'}</h3>
        <p className="muted">
          {planning ? '進度在上面；挑好之後這裡會列出每個場景選了什麼、還有哪些備選。'
            : retry ? '處理好上面的問題之後，按右上角的「重試挑素材」。'
              : '到「新增作品」貼一份腳本，或用 vidpipe plan。'}
        </p>
      </section>
    )
  }
  return (
    <>
      {plan.without_clip.length > 0 && (
        <div className="banner bad" role="alert">
          <div>
            <strong>{plan.without_clip.length} 個場景沒挑到素材：{plan.without_clip.join('、')}</strong>
            <p>這樣做不出成片。改一下那幾段的關鍵字（英文效果最好），刪掉這支片再貼一次。</p>
          </div>
        </div>
      )}
      {plan.warnings.length > 0 && (
        <div className="banner warn" role="status">
          <div>
            <strong>{plan.warnings.length} 則提醒，做成片之前看一下</strong>
            {plan.warnings.map((w) => <p key={w}>{w}</p>)}
          </div>
        </div>
      )}
      <p className="muted small note-line">
        旁白粗估 {durationText(plan.estimated_seconds)}（實際以合成後為準）。
        確認沒問題就按右上角的「做成片」。
      </p>
      {plan.scenes.map((scene, i) => (
        <section key={scene.id} className="card scene" aria-label={`場景 ${i + 1} 的素材`}>
          <header className="scene-head">
            <span className="scene-no">{i + 1}</span>
            <code>{scene.id}</code>
            <span className="muted small">約 {scene.estimated_seconds.toFixed(1)} 秒</span>
            {(scene.query ?? []).map((q) => <Pill key={q}>{q}</Pill>)}
          </header>
          <PickBody pick={scene} none="沒有可用的素材" />
          {scene.skipped_license ? (
            <p className="muted small">另有 {scene.skipped_license} 支候選因為授權不明沒有列入。</p>
          ) : null}
        </section>
      ))}
      {plan.music && (
        <section className="card scene" aria-label="背景音樂">
          <header className="scene-head">
            <span className="scene-no">♪</span>
            <strong>背景音樂</strong>
            {plan.music.query.map((q) => <Pill key={q}>{q}</Pill>)}
          </header>
          <PickBody pick={plan.music} music none="沒有可用的背景音樂，成片不會有音樂" />
        </section>
      )}
    </>
  )
}

/* ---------------------------------------------------------- 詳細頁 */

const TABS = [
  { id: 'scenes', label: '場景' },
  { id: 'plan', label: '挑素材' },
  { id: 'credits', label: '素材與授權' },
  { id: 'files', label: '檔案' },
  { id: 'manifest', label: 'Manifest' },
  { id: 'jobs', label: '工作' },
] as const

type TabId = (typeof TABS)[number]['id']

/** 網址沒指定分頁時：還沒旁白的片先看挑素材的結果，其餘看場景。 */
function tabOf(raw: string, short: ShortDetail | undefined): TabId {
  const named = TABS.find((t) => t.id === raw)?.id
  if (named) return named
  return short && (short.stage === 'planned' || short.manifest_source === 'draft') ? 'plan' : 'scenes'
}

function Placeholder({ stage, running }: { stage: ShortSummary['stage']; running: boolean }) {
  const text = {
    new: '還沒有旁白，也沒有成片。',
    planned: '素材挑好了，還沒有旁白與成片。',
    narrated: '旁白好了，還沒渲染。',
    rendered: '',
  }[stage]
  return (
    <div className="player placeholder">
      <p>{text}<br />{running ? '做完之後成片會出現在這裡。' : '按右上角的「做成片」。'}</p>
    </div>
  )
}

export function ShortDetailPage({ id, tab }: { id: string; tab: string }) {
  const [refresh, setRefresh] = useState(0)
  const detail = useApi<ShortDetailResponse>(`/api/short/${encodeURIComponent(id)}`, refresh)
  const short = detail.data?.short
  const job = short?.last_job ?? null
  const running = job?.status === 'running'
  useEffect(() => setRefresh(running ? 2000 : 0), [running])
  const action = useAction()
  const [confirming, setConfirming] = useState(false)
  const [editing, setEditing] = useState(false)
  const { reload } = detail

  const video = useRef<HTMLVideoElement>(null)
  const [active, setActive] = useState<number | null>(null)
  const sentences = useMemo(() => short?.timeline?.scenes.flatMap((s) => s.sentences) ?? [],
    [short?.timeline])
  const onTime = () => {
    const t = video.current?.currentTime ?? 0
    setActive(sentences.find((s) => t >= s.start && t < s.end)?.index ?? null)
  }
  const seek = (t: number) => {
    const v = video.current
    if (!v) return
    v.currentTime = t
    void v.play().catch(() => undefined)
  }

  const current = tabOf(tab, short)
  const mp4 = short ? fileOf(short.files, `${short.id}.mp4`) : undefined
  const canMake = !!short && !!short.manifest && short.manifest_source !== 'draft' && !running
  const canReplan = !!short && short.manifest_source === 'draft' && !!short.manifest && !running
  const canEdit = !!short && !!short.manifest && !!short.manifest_revision && !running
    && (short.manifest_source === 'manifest' || short.manifest_source === 'plan')
  const failed = job?.status === 'failed'
  const outdated = short?.outdated === true && !running
  const makeLabel = failed ? '重跑' : outdated ? '重新做（套用修改）'
    : short?.stage === 'rendered' ? '重新做一次' : '做成片'

  // 旁白、素材、已渲染的場景都在快取裡，所以「重跑」只會補做缺的那些
  const make = () => {
    if (!short?.manifest) return
    void action.run(() => postJson(`/api/make`, { manifest: short.manifest })).then((r) => {
      if (r) reload()
    })
  }
  const replan = () => {
    if (!short?.manifest) return
    void action.run(() => postJson(`/api/plan`, { manifest: short.manifest })).then((r) => {
      if (r) reload()
    })
  }
  const remove = () => {
    if (!short) return
    void action.run(() => deleteJson(`/api/short/${encodeURIComponent(short.id)}`)).then((r) => {
      if (r) window.location.hash = '#/shorts'
      else setConfirming(false)
    })
  }
  const cover = short ? fileOf(short.files, `${short.id}.cover.jpg`) : undefined
  const sentenceCount = sentences.length

  return (
    <>
      <p className="crumb"><a href="#/shorts">← 全部作品</a></p>
      {detail.error && <ErrorBanner error={detail.error} onRetry={detail.reload} />}
      {short && (
        <>
          <header className="page-head">
            <div>
              <h2>{short.title || short.id}</h2>
              <div className="pills">
                <code>{short.id}</code>
                <StagePill stage={short.stage} />
                <CreditsPills credits={short.credits} />
              </div>
            </div>
            <div className="head-actions">
              {canMake && (
                <button type="button"
                        className={short.stage === 'rendered' && !failed && !outdated ? undefined : 'primary'}
                        onClick={make} disabled={action.busy || editing}>
                  {makeLabel}
                </button>
              )}
              {canReplan && (
                <button type="button" className="primary" onClick={replan} disabled={action.busy}>
                  重試挑素材
                </button>
              )}
              {mp4 && <a className="button" href={mp4.url} download={mp4.name}>下載成片</a>}
              <button type="button" className="danger" onClick={() => setConfirming(true)}
                      disabled={running || action.busy}>
                刪除
              </button>
            </div>
          </header>

          {action.error && <ErrorBanner error={action.error} />}
          {confirming && (
            <div className="banner warn" role="alert">
              <div>
                <strong>確定要刪除「{short.title || short.id}」？</strong>
                <p>
                  會刪掉 work 裡這支片的中間檔，以及 out 裡的成片、封面、credits 和字幕。
                  旁白快取是各片共用的，不會動；刪掉就回不來。
                </p>
              </div>
              <button type="button" className="danger" onClick={remove} disabled={action.busy}>
                {action.busy ? '刪除中…' : '確定刪除'}
              </button>
              <button type="button" onClick={() => setConfirming(false)} disabled={action.busy}>不要</button>
            </div>
          )}
          {outdated && !editing && (
            <div className="banner" role="status">
              <div>
                <strong>manifest 改過了，{short.stage === 'rendered' ? '成片' : '旁白'}還是舊的</strong>
                <p>按「{makeLabel}」套用；旁白與場景有快取，只有改到的部分會重做。</p>
              </div>
              <button type="button" className="primary" onClick={make} disabled={action.busy}>{makeLabel}</button>
            </div>
          )}
          {running && job && <RunningJob job={job} onChanged={reload} />}
          {job?.status === 'failed' && <LastFailure key={job.id} job={job} />}

          <div className="short-layout">
            <div className="short-side">
              {mp4
                ? (
                  <video key={mp4.size_bytes} ref={video} className="player" controls playsInline
                         preload="metadata" poster={cover?.url} src={mp4.url}
                         aria-label="成片預覽" onTimeUpdate={onTime} onSeeked={onTime} />
                )
                : <Placeholder stage={short.stage} running={running} />}
              <dl className="meta">
                <div><dt>長度</dt><dd>{durationText(short.total)}</dd></div>
                <div><dt>場景</dt><dd>{short.scenes}</dd></div>
                {sentenceCount > 0 && <div><dt>旁白句數</dt><dd>{sentenceCount}</dd></div>}
                {short.timeline?.subtitles && (
                  <div><dt>字幕</dt><dd>{short.timeline.subtitles.burn_in ? '燒進畫面' : '另存 .srt'}</dd></div>
                )}
                <div><dt>更新</dt><dd>{dateTimeIso(short.updated_at)}</dd></div>
              </dl>
            </div>

            <div className="short-body">
              <nav className="tabs" aria-label="作品內容">
                {TABS.filter((t) => t.id !== 'plan' || short.plan || short.manifest_source === 'draft'
                  || job?.kind === 'plan').map((t) => (
                  <a key={t.id} href={`#/shorts/${encodeURIComponent(short.id)}/${t.id}`}
                     aria-current={current === t.id ? 'page' : undefined}>
                    {t.label}
                  </a>
                ))}
              </nav>
              {editing && (
                <div hidden={current !== 'scenes'}>
                  <SceneEditor short={short} locked={running}
                               onSaved={() => { setEditing(false); reload() }}
                               onLeave={() => { setEditing(false); reload() }} />
                </div>
              )}
              {current === 'scenes' && !editing && (
                <ScenesPanel short={short} active={active} onSeek={mp4 ? seek : undefined}
                             onEdit={canEdit ? () => setEditing(true) : undefined} />
              )}
              {current === 'plan' && <PlanPanel plan={short.plan} planning={running && job?.kind === 'plan'} retry={canReplan} />}
              {current === 'credits' && <CreditsPanel short={short} />}
              {current === 'files' && <FilesPanel files={short.files} />}
              {current === 'manifest' && <ManifestPanel manifest={short.manifest} />}
              {current === 'jobs' && <JobsPanel jobs={short.jobs} />}
            </div>
          </div>
        </>
      )}
    </>
  )
}
