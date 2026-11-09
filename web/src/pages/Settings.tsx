import { useCallback, useEffect, useMemo, useState } from 'react'
import { ErrorBanner } from '../components/ErrorBanner'
import { Pill } from '../components/Pills'
import { SettingFieldRow } from '../components/SettingField'
import { TestPanel } from '../components/TestPanel'
import type { TestAction } from '../components/TestPanel'
import { ApiFailure, getJson, putJson, useApi } from '../lib/api'
import { clockIso } from '../lib/format'
import { leaveGuard } from '../lib/leaveGuard'
import {
  changesOf, idleReason, labelsOf, normalize, setEdit, testValuesOf, validate,
} from '../lib/settings'
import type { Edit, Edits } from '../lib/settings'
import { useAction } from '../lib/useAction'
import type { SettingField, SettingsInfo, SettingsSaved, VersionInfo } from '../types'

/** 每一組可以做的測試。 */
const TESTS: Record<string, TestAction[]> = {
  vidgrab: [{ id: 'vidgrab', label: '測試連線', target: 'vidgrab', title: '連 vidgrab，並確認 token 對不對' }],
  tts: [
    { id: 'tts', label: '測試連線', target: 'tts', title: '連 TTS，列出已載入的模型' },
    { id: 'speak', label: '試合成一句', target: 'tts', deep: true,
      title: '真的請 TTS 合成一句話（不留檔），確認模型與音色參考音檔配得上' },
  ],
  subtitles: [{ id: 'ffmpeg', label: '測試 ffmpeg 與字體', target: 'ffmpeg',
    title: '確認 ffmpeg 有 libass，並用 libass 真的畫一格字幕，看選到哪個字體' }],
  storage: [{ id: 'dirs', label: '檢查資料夾', target: 'dirs', title: '資料夾能不能寫、還剩多少空間（不會建立任何東西）' }],
}

interface Saved {
  at: number
  count: number
  dirsChanged: boolean
}

function without<T>(map: Record<string, T>, key: string): Record<string, T> {
  const { [key]: _drop, ...rest } = map
  return rest
}

/** 重新載入之後，保留還成立的修改（欄位被鎖了、或已經跟新的一樣的就丟掉）。 */
function rebase(info: SettingsInfo, edits: Edits): Edits {
  const out: Edits = {}
  for (const f of info.fields) {
    const kept = normalize(f, edits[f.name])
    if (kept) out[f.name] = kept
  }
  return out
}

function toFailure(e: unknown): ApiFailure {
  return e instanceof ApiFailure ? e : new ApiFailure(0, {
    code: 'internal', message: String(e), retryable: true, hint: '',
  })
}

function DockerHelp({ path }: { path: string }) {
  return (
    <details className="docker-help">
      <summary>在 Docker 裡怎麼讓設定留下來</summary>
      <p className="muted small">
        映像把設定檔放在 <code>/data/config/vidpipe.json</code>（環境變數 <code>VIDPIPE_CONFIG</code>），
        那個資料夾是 volume。要讓設定在容器重建後還在，把它掛到主機或具名 volume：
      </p>
      <pre className="json">{`docker run … -v "$PWD/config:/data/config" vidpipe serve …
# 容器裡是 uid 1000 在寫；主機的 config 資料夾要讓它寫得進去：
#   mkdir -p config && sudo chown 1000 config   # Linux
# 要掛的是資料夾，不是單一檔案（單檔 bind mount 在某些系統上沒辦法原子寫入）。`}</pre>
      {path && <p className="muted small">目前這份設定檔在：<code>{path}</code></p>}
    </details>
  )
}

export function Settings() {
  const [info, setInfo] = useState<SettingsInfo | null>(null)
  const [loadError, setLoadError] = useState<ApiFailure | null>(null)
  const [edits, setEdits] = useState<Edits>({})
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState<Saved | null>(null)
  const [pendingLeave, setPendingLeave] = useState<string | null>(leaveGuard.pending())
  const [confirmRebuild, setConfirmRebuild] = useState(false)
  const version = useApi<VersionInfo>('/api/version')
  const save = useAction()

  const load = useCallback((keepEdits: boolean) => {
    getJson<SettingsInfo>('/api/settings')
      .then((next) => {
        setInfo(next)
        setLoadError(null)
        setEdits((prev) => (keepEdits ? rebase(next, prev) : {}))
      })
      .catch((e: unknown) => setLoadError(toFailure(e)))
  }, [])
  useEffect(() => load(false), [load])

  useEffect(() => leaveGuard.subscribe(() => setPendingLeave(leaveGuard.pending())), [])
  useEffect(() => { setServerErrors(save.error?.body.fields ?? {}) }, [save.error])

  const dirtyNames = useMemo(() => Object.keys(edits), [edits])
  const dirty = dirtyNames.length > 0

  // 有沒存的修改：關分頁、重新整理、點側欄換頁，都先問
  useEffect(() => {
    leaveGuard.setDirty(dirty)
    if (!dirty) return
    const onUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', onUnload)
    return () => window.removeEventListener('beforeunload', onUnload)
  }, [dirty])
  useEffect(() => () => leaveGuard.setDirty(false), [])

  const errors = useMemo(() => {
    const out: Record<string, string> = {}
    for (const f of info?.fields ?? []) {
      const e = edits[f.name]
      const msg = (e?.kind === 'set' ? validate(f, e.value) : '') || serverErrors[f.name] || ''
      if (msg) out[f.name] = msg
    }
    return out
  }, [info, edits, serverErrors])

  const frozen = !info || !info.writable
  const stale = save.error?.body.code === 'stale_config'
  const fieldErrorCount = Object.keys(errors).length
  const canSave = !frozen && dirty && fieldErrorCount === 0 && !stale

  const edit = (f: SettingField, next: Edit | undefined) => {
    setEdits((prev) => setEdit(prev, f, next))
    setServerErrors((prev) => without(prev, f.name))
    setSaved(null)
  }

  const submit = useCallback((replaceInvalid = false) => {
    if (!info || (!replaceInvalid && !canSave)) return
    void save.run(() => putJson<SettingsSaved>('/api/settings', {
      revision: info.revision,
      changes: changesOf(info, edits),
      replace_invalid: replaceInvalid || undefined,
    })).then((r) => {
      if (!r) return
      setInfo(r)
      setEdits({})
      setServerErrors({})
      setConfirmRebuild(false)
      setSaved({ at: Date.now(), count: r.changed.length,
        dirsChanged: r.changed.includes('work_dir') || r.changed.includes('out_dir') })
    })
  }, [info, edits, canSave, save])

  // 伺服器說哪個欄位不合格：把游標帶到第一個
  useEffect(() => {
    const first = Object.keys(save.error?.body.fields ?? {})[0]
    if (first) document.querySelector<HTMLElement>(`[data-field="${first}"] input, [data-field="${first}"] textarea, [data-field="${first}"] button`)?.focus()
  }, [save.error])

  // Ctrl／⌘ + S
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        submit()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [submit])

  const discard = () => {
    setEdits({})
    setServerErrors({})
    save.clear()
  }
  const leaveNow = () => {
    const target = leaveGuard.pending()
    discard()
    leaveGuard.setDirty(false)
    if (target) window.location.hash = target
  }

  const problems = info?.problems ?? []
  const fileBroken = problems.some((p) => p.code === 'file_invalid')
  const unknownKeys = problems.find((p) => p.code === 'unknown_keys')
  const names = dirtyNames.length > 0 && info ? labelsOf(info, dirtyNames) : []
  const nameLine = names.length > 3 ? `${names.slice(0, 3).join('、')} 等 ${names.length} 項` : names.join('、')

  let status: React.ReactNode
  if (!info) status = '讀取設定中…'
  else if (info.readonly) status = '這個部署把設定頁設成唯讀'
  else if (!info.writable) status = '設定檔不能寫入，這頁只能看'
  else if (dirty) status = nameLine
  else if (saved) {
    status = (
      <>
        <span className="ok-text">✓ 已儲存</span> {clockIso(new Date(saved.at).toISOString())}
        {saved.count > 0 ? `（改了 ${saved.count} 項）` : '（沒有需要改的）'}。新的工作會用新設定，進行中的不受影響。
      </>
    )
  } else status = '改好按「儲存」（Ctrl／⌘ + S）。新的工作會用新設定。'

  const modelField = info?.fields.find((f) => f.name === 'tts_model')

  return (
    <form noValidate onSubmit={(e) => { e.preventDefault(); submit() }}>
      <header className="page-head">
        <div>
          <h2>設定</h2>
          <p className="muted">
            優先序：環境變數 ＞ 設定檔 ＞ 預設。被環境變數指定的項目會上鎖，其餘都能在這裡改，
            按「儲存」寫回設定檔。
          </p>
        </div>
      </header>

      {loadError && <ErrorBanner error={loadError} onRetry={() => load(true)} />}
      {!info && !loadError && <p className="muted" role="status">讀取中…</p>}

      {info && (
        <>
          {pendingLeave && (
            <div className="banner warn" role="alertdialog" aria-label="還有沒儲存的修改">
              <div>
                <strong>還有 {dirtyNames.length} 項沒儲存，要離開嗎？</strong>
                <p>離開之後這些修改就沒有了。</p>
              </div>
              <button type="button" className="primary" onClick={() => leaveGuard.stay()}>留在這裡</button>
              <button type="button" className="danger" onClick={leaveNow}>放棄修改並離開</button>
            </div>
          )}
          {info.readonly && (
            <div className="banner warn" role="status">
              <div>
                <strong>設定頁是唯讀的</strong>
                <p>部署者設了 <code>VIDPIPE_SETTINGS_READONLY</code>。可以看、也可以測試現有的設定，不能改；要改請直接編輯設定檔或環境變數。</p>
              </div>
            </div>
          )}
          {!info.readonly && !info.writable && (
            <div className="banner warn" role="status">
              <div>
                <strong>設定檔寫不進去</strong>
                <p>{info.write_problem || `${info.path} 不可寫`}。換一個可寫的位置（<code>VIDPIPE_CONFIG</code>），或調整權限；在 Docker 裡見最下面「設定檔」。</p>
              </div>
            </div>
          )}
          {fileBroken && (
            <div className="banner bad" role="alert">
              <div>
                <strong>設定檔壞了，vidpipe 現在讀不了它</strong>
                <p>{problems.find((p) => p.code === 'file_invalid')?.message}</p>
                {confirmRebuild
                  ? <p>會先把壞掉的檔案備份成 <code>{info.path}.bak</code>，再寫入一份新的（你在下面改的也會一起存）。</p>
                  : <p>下面顯示的是預設值與環境變數。可以自己修好檔案，或讓 vidpipe 用預設重建一份。</p>}
              </div>
              {confirmRebuild
                ? (
                  <>
                    <button type="button" className="primary" disabled={save.busy || !info.writable} onClick={() => submit(true)}>確定重建</button>
                    <button type="button" onClick={() => setConfirmRebuild(false)}>先不要</button>
                  </>
                )
                : <button type="button" disabled={!info.writable} onClick={() => setConfirmRebuild(true)}>用預設重建</button>}
            </div>
          )}
          {unknownKeys && (
            <div className="banner warn" role="status">
              <div>
                <strong>設定檔有不認得的項目：{unknownKeys.keys?.join('、')}</strong>
                <p>vidpipe 會拒絕載入這樣的設定檔。下次儲存時這些項目會被移除，舊檔先備份成 <code>{info.path}.bak</code>。</p>
              </div>
            </div>
          )}
          {!fileBroken && info.load_error && (
            <div className="banner bad" role="alert">
              <div>
                <strong>目前的設定載入失敗，指令列也會報同樣的錯</strong>
                <p>{info.load_error}</p>
              </div>
            </div>
          )}
          {saved?.dirsChanged && (
            <div className="banner ok" role="status">
              <div>
                <strong>work／out 目錄換了</strong>
                <p>舊目錄裡的作品不會跟著搬，列表只看新的 work 目錄；想接著用舊作品，把設定改回去，或自己把資料夾搬過去。</p>
              </div>
            </div>
          )}
          {stale && (
            <div className="banner warn" role="alert">
              <div>
                <strong>設定檔在你編輯的時候被改過了</strong>
                <p>可能是別的視窗、指令列或別人改的。為了不蓋掉對方，這次沒有儲存。</p>
              </div>
              <button type="button" className="primary" onClick={() => { save.clear(); load(true) }}>
                載入最新版，保留我的修改
              </button>
              <button type="button" onClick={() => { discard(); load(false) }}>放棄我的修改</button>
            </div>
          )}
          {save.error && !stale && (
            fieldErrorCount > 0 && save.error.body.fields
              ? (
                <div className="banner bad" role="alert">
                  <div>
                    <strong>{save.error.body.message}</strong>
                    <p>標紅的項目修好再存；已經存好的不受影響。</p>
                  </div>
                </div>
              )
              : <ErrorBanner error={save.error} />
          )}

          <div className={dirty ? 'editor-bar settings-bar dirty' : 'editor-bar settings-bar'} role="region" aria-label="儲存設定">
            <div>
              <strong>{dirty ? `有 ${dirtyNames.length} 項還沒儲存` : '沒有未儲存的修改'}</strong>
              <span className="muted small" role="status">　{status}</span>
            </div>
            <button type="submit" className="primary" disabled={!canSave || save.busy}>
              {save.busy ? '儲存中…' : '儲存'}
            </button>
            <button type="button" onClick={discard} disabled={!dirty || save.busy}>放棄修改</button>
          </div>

          {dirty && fieldErrorCount > 0 && (
            <p className="field-problem small bar-note" role="status">
              有 {fieldErrorCount} 項不合格式：{labelsOf(info, Object.keys(errors)).join('、')}。修好才能儲存。
            </p>
          )}

          {info.groups.map((g) => {
            const fields = info.fields.filter((f) => f.group === g.id)
            const groupErrors = fields.some((f) => errors[f.name] && edits[f.name])
            const modelPick = (model: string) => modelField && edit(modelField, { kind: 'set', value: model })
            return (
              <section key={g.id} className="card setting-group" aria-labelledby={`g-${g.id}`}>
                <div className="card-head">
                  <div>
                    <h3 id={`g-${g.id}`}>{g.title}</h3>
                    <p className="muted small">{g.note}</p>
                  </div>
                  {fields.some((f) => edits[f.name]) && <Pill tone="note">有未儲存的修改</Pill>}
                </div>
                {fields.map((f) => (
                  <SettingFieldRow key={f.name} field={f} edit={edits[f.name]} error={errors[f.name] ?? ''}
                                   idle={idleReason(info, edits, f)} frozen={!info.writable}
                                   onEdit={(next) => edit(f, next)} />
                ))}
                {TESTS[g.id] && (
                  <TestPanel actions={TESTS[g.id]!} values={testValuesOf(info, edits, errors)}
                             blocked={groupErrors ? '先修正上面標紅的項目再測試' : ''}
                             extra={g.id === 'tts' && modelField
                               ? (r) => (r.models.length > 0
                                 ? (
                                   <div className="models">
                                     <p className="muted small">已載入的模型{modelField.locked ? '' : '（點一下填進「TTS 模型」）'}</p>
                                     <div className="chips">
                                       {r.models.map((m) => (
                                         <button key={m} type="button" className="chip" disabled={modelField.locked || !info.writable}
                                                 aria-pressed={String(edits.tts_model?.kind === 'set' ? edits.tts_model.value : modelField.value) === m}
                                                 onClick={() => modelPick(m)}>{m}</button>
                                       ))}
                                     </div>
                                   </div>
                                 )
                                 : null)
                               : undefined} />
                )}
              </section>
            )
          })}

          <section className="card" aria-labelledby="g-access">
            <div className="card-head"><div><h3 id="g-access">設定檔與存取</h3></div></div>
            <table className="kv">
              <tbody>
                <tr>
                  <th scope="row">設定檔</th>
                  <td>
                    <code>{info.path}</code>
                    <p className="muted small">
                      {info.path_from_env ? '由環境變數 VIDPIPE_CONFIG 指定。' : '預設是啟動 vidpipe 時所在資料夾的 vidpipe.json。'}
                      {info.exists ? '' : '（還沒有這個檔案，第一次儲存時才會建立。）'}
                      指令列要讀同一份，請在同一個資料夾執行，或同樣設 VIDPIPE_CONFIG。
                    </p>
                  </td>
                </tr>
                <tr>
                  <th scope="row">寫入</th>
                  <td>
                    {info.readonly ? '唯讀（VIDPIPE_SETTINGS_READONLY）' : info.writable ? '可寫' : `不可寫：${info.write_problem}`}
                    <p className="muted small">token 存進設定檔是明文，檔案權限 600，而且 <code>vidpipe.json</code> 不進版控；有環境變數可用時，祕密放環境變數更安全。</p>
                  </td>
                </tr>
                {version.data && (
                  <>
                    <tr><th scope="row">版本</th><td>{version.data.version}</td></tr>
                    <tr><th scope="row">build</th><td>{version.data.build}</td></tr>
                    <tr><th scope="row">API token</th><td>{version.data.auth_required ? '已啟用' : '未啟用（只綁本機時才建議）'}</td></tr>
                  </>
                )}
              </tbody>
            </table>
            <DockerHelp path={info.path} />
          </section>
        </>
      )}
    </form>
  )
}
