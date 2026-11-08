import { useEffect, useState } from 'react'
import { ErrorBanner } from '../components/ErrorBanner'
import { JobDot } from '../components/JobDot'
import { ProgressBar } from '../components/ProgressBar'
import { ApiFailure, postJson, useApi } from '../lib/api'
import {
  JOB_LEVEL, JOB_TEXT, KIND_TEXT, clockIso, dateTimeIso, elapsed, stageText, summarize,
} from '../lib/format'
import type { Job, JobDetail, JobList } from '../types'

export function kindText(kind: string): string {
  return KIND_TEXT[kind] ?? kind
}

/** 進行中算到現在；結束的算到結束；「中斷」沒有結束時間，算到最後一次更新。 */
function jobElapsed(job: Job): string {
  if (job.status === 'running') return elapsed(job.started_at, null)
  return elapsed(job.started_at, job.finished_at ?? job.updated_at)
}

/** 每秒重畫一次，進行中工作的「已跑多久」才會動。 */
function useTick(active: boolean): void {
  const [, setN] = useState(0)
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => setN((n) => n + 1), 1000)
    return () => window.clearInterval(id)
  }, [active])
}

export function JobsPage() {
  const list = useApi<JobList>('/api/jobs?limit=100', 2000)
  const jobs = list.data?.jobs ?? []
  useTick(jobs.some((j) => j.status === 'running'))

  return (
    <>
      <header className="page-head">
        <div>
          <h2>工作</h2>
          <p className="muted">
            每個長指令（CLI、API、MCP 發起的都算）都會留下紀錄，這裡每 2 秒更新一次。
          </p>
        </div>
      </header>
      {list.error && <ErrorBanner error={list.error} onRetry={list.reload} />}
      {list.data && jobs.length === 0 && (
        <section className="card empty">
          <h3>還沒有工作紀錄</h3>
          <p className="muted">
            試試 <code>vidpipe normalize in.mp4 -o out.mp4</code>，它跑起來就會出現在這裡。
          </p>
        </section>
      )}
      {jobs.length > 0 && <JobTable jobs={jobs} />}
    </>
  )
}

export function JobTable({ jobs, compact = false }: { jobs: Job[]; compact?: boolean }) {
  return (
    <section className="card table-card">
      <table className="jobs">
        <thead>
          <tr>
            <th scope="col">狀態</th>
            <th scope="col">工作</th>
            <th scope="col">進度</th>
            {!compact && <th scope="col" className="col-start">開始</th>}
            <th scope="col">耗時</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id} className={`job-row job-${JOB_LEVEL[job.status]}`}>
              <td className="status-cell"><JobDot status={job.status} /> <span className="status-text">{JOB_TEXT[job.status]}</span></td>
              <td>
                <a href={`#/jobs/${job.id}`} className="job-link">{kindText(job.kind)}</a>
                <div className="muted small clip">{summarize(job.args)}</div>
              </td>
              <td className="progress-cell">
                {job.status === 'running'
                  ? <ProgressBar ratio={job.progress} label={`${kindText(job.kind)} 進度`} />
                  : <span className="muted small">{job.stage ? stageText(job.stage) : '—'}</span>}
              </td>
              {!compact && <td className="muted small col-start">{dateTimeIso(job.started_at)}</td>}
              <td className="muted small nowrap">{jobElapsed(job)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function KeyValues({ data }: { data: Record<string, unknown> }) {
  const rows = Object.entries(data).filter(([, v]) => v !== null && v !== '' && v !== undefined)
  if (rows.length === 0) return <p className="muted small">（沒有）</p>
  return (
    <table className="kv">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <th scope="row"><code>{k}</code></th>
            <td>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function CancelButton({ id, onDone }: { id: string; onDone: () => void }) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiFailure | null>(null)

  const cancel = () => {
    setBusy(true)
    postJson(`/api/job/${id}/cancel`)
      .then(() => { setConfirming(false); setError(null) })
      .catch((e: unknown) => setError(e instanceof ApiFailure ? e : null))
      .finally(() => { setBusy(false); onDone() })
  }

  return (
    <div className="cancel">
      {!confirming
        ? <button type="button" className="danger" onClick={() => setConfirming(true)}>取消這個工作</button>
        : (
          <span className="confirm">
            確定要取消？進行到一半的輸出會被丟掉。
            <button type="button" className="danger" onClick={cancel} disabled={busy}>
              {busy ? '取消中…' : '確定取消'}
            </button>
            <button type="button" onClick={() => setConfirming(false)} disabled={busy}>不要</button>
          </span>
        )}
      {error && <ErrorBanner error={error} />}
    </div>
  )
}

export function JobDetailPage({ id }: { id: string }) {
  const detail = useApi<JobDetail>(`/api/job/${id}`)
  const job = detail.data?.job
  const running = job?.status === 'running'
  const { reload } = detail
  useTick(!!running)
  useEffect(() => {
    if (!running) return
    const t = window.setInterval(reload, 1000)
    return () => window.clearInterval(t)
  }, [running, reload])

  return (
    <>
      <p className="crumb"><a href="#/jobs">← 全部工作</a></p>
      {detail.error && <ErrorBanner error={detail.error} onRetry={detail.reload} />}
      {job && detail.data && (
        <>
          <header className="page-head">
            <div>
              <h2>{kindText(job.kind)}</h2>
              <p className="muted small"><code>{job.id}</code></p>
            </div>
            <div className="head-actions">
              <JobDot status={job.status} />
              <strong>{JOB_TEXT[job.status]}</strong>
              <span className="muted small">耗時 {jobElapsed(job)}</span>
            </div>
          </header>

          {running && (
            <section className="card">
              <ProgressBar ratio={job.progress} label="工作進度" />
              <p className="muted small">
                目前階段：{job.stage ? stageText(job.stage) : '準備中'}
                {job.pid ? `　行程 ${job.pid}` : ''}
              </p>
              <CancelButton id={job.id} onDone={detail.reload} />
            </section>
          )}

          {job.error && (
            <div className={`banner ${job.status === 'failed' ? 'bad' : 'warn'}`} role="alert">
              <div>
                <strong>{job.error.message}</strong>
                {job.error.hint && <p>{job.error.hint}</p>}
                <p className="muted small">錯誤碼：{job.error.code}{job.error.retryable ? '（可以重試）' : ''}</p>
              </div>
            </div>
          )}
          {job.status === 'interrupted' && (
            <div className="banner warn" role="status">
              <div>
                <strong>執行的行程已經不在了</strong>
                <p>可能被強制結束、或機器重開。輸出檔可能不完整，重跑一次即可。</p>
              </div>
            </div>
          )}

          <div className="two-col">
            <section className="card">
              <div className="card-head"><div><h3>階段</h3></div></div>
              {job.stages.length === 0
                ? <p className="muted small">（還沒開始）</p>
                : (
                  <ol className="stages">
                    {job.stages.map((s, i) => (
                      <li key={`${s.name}-${i}`}>
                        <JobDot status={s.status} />
                        <div>
                          <div>{stageText(s.name)}</div>
                          {s.detail && <div className="muted small clip">{s.detail}</div>}
                        </div>
                        <span className="muted small">{elapsed(s.started_at, s.finished_at ?? (running ? null : job.updated_at))}</span>
                      </li>
                    ))}
                  </ol>
                )}
            </section>
            <section className="card">
              <div className="card-head"><div><h3>參數</h3></div></div>
              <KeyValues data={job.args} />
            </section>
          </div>

          {job.result && (
            <section className="card">
              <div className="card-head"><div><h3>結果</h3></div></div>
              <KeyValues data={job.result} />
            </section>
          )}

          <section className="card">
            <div className="card-head"><div><h3>事件記錄</h3></div></div>
            <ol className="events">
              {detail.data.events.map((e) => (
                <li key={e.seq} className={`ev ev-${e.level ?? 'info'}`}>
                  <span className="muted">{clockIso(new Date(e.t * 1000).toISOString())}</span>
                  <span className="ev-kind">{e.kind}</span>
                  <span>{e.message}{e.hint ? `（${e.hint}）` : ''}</span>
                </li>
              ))}
            </ol>
          </section>
        </>
      )}
    </>
  )
}
