import { useId, useMemo, useRef, useState } from 'react'
import { ErrorBanner } from '../components/ErrorBanner'
import { CandidateLine, Pill } from '../components/Pills'
import { postJson, putJson } from '../lib/api'
import { useAction } from '../lib/useAction'
import type {
  Manifest, ManifestScene, PlanCandidate, PlanPick, PlanScene, SearchResponse, ShortDetail,
} from '../types'

const FIT_OPTIONS = [
  { value: '', label: '預設', hint: '模糊襯底：整個畫面留著，上下補模糊的背景。' },
  { value: 'blur_pad', label: '模糊襯底', hint: '整個畫面留著，上下補模糊的背景。' },
  { value: 'crop', label: '填滿裁切', hint: '裁掉兩側，畫面最滿；人物或字在邊邊時會被切到。' },
  { value: 'letterbox', label: '黑邊', hint: '整個畫面留著，上下補黑。' },
]

type Sentence = { text: string; en?: string }

/** 鍵的順序不算改動（刪掉再加回來的欄位會換到最後）。 */
function canon(value: unknown): string {
  return JSON.stringify(value, (_k, v: unknown) => (v && typeof v === 'object' && !Array.isArray(v)
    ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
    : v))
}

/** 深拷貝：編輯只改這一份，按儲存才送出去。 */
function cloneManifest(m: Manifest): Manifest {
  return JSON.parse(JSON.stringify(m)) as Manifest
}

function hasSentences(scene: ManifestScene): scene is ManifestScene & { sentences: Sentence[] } {
  return Array.isArray(scene.sentences)
}

function hasNarration(scene: ManifestScene): boolean {
  return hasSentences(scene) ? scene.sentences.length > 0 : !!scene.text?.trim()
}

/** 跟 manifest 的規則對齊：有問題的就不讓儲存，並說明是哪裡。 */
function problemsOf(draft: Manifest): string[] {
  const found: string[] = []
  draft.scenes.forEach((scene, i) => {
    const name = `場景 ${i + 1}`
    if (hasSentences(scene) && scene.sentences.some((s) => !s.text.trim())) {
      found.push(`${name} 有空白的旁白句：填字，或按「刪除這句」`)
    }
    if (!hasNarration(scene) && !(Number(scene.min_duration) > 0)) {
      found.push(`${name} 沒有旁白，要填最短秒數`)
    }
  })
  return found
}

function candidatesOf(pick: PlanPick | undefined, current: string | undefined): PlanCandidate[] {
  if (!pick) return []
  return [...(pick.chosen ? [pick.chosen] : []), ...(pick.alternatives ?? [])]
    .filter((c) => c.asset_id !== current)
}

function numberOrUndefined(raw: string): number | undefined {
  if (raw.trim() === '') return undefined
  const n = Number(raw)
  return Number.isFinite(n) ? n : undefined
}

/** 換素材：向 vidgrab 搜尋（縮圖、授權、直式優先），或用 plan 挑過的備選，或直接輸入素材 id。 */
function SwapClip({ shortId, target, initialQuery, currentId, candidates, onPick, label }: {
  shortId: string
  /** `scene:<id>` 或 `music`；沒有（場景沒有 id）就不能搜尋。 */
  target: string | null
  initialQuery: string
  currentId: string | undefined
  candidates: PlanCandidate[]
  onPick: (assetId: string) => void
  label: string
}) {
  const [query, setQuery] = useState(initialQuery)
  const [allowUnknown, setAllowUnknown] = useState(false)
  const [found, setFound] = useState<SearchResponse | null>(null)
  const search = useAction()
  const [typed, setTyped] = useState('')
  const ids = useId()
  const isMusic = target === 'music'

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!target || !query.trim()) return
    void search.run(() => postJson<SearchResponse>(`/api/short/${encodeURIComponent(shortId)}/search`, {
      query, target, allow_unknown_license: allowUnknown,
    })).then((r) => {
      if (r) setFound(r)
    })
  }
  const action = (c: PlanCandidate) => (c.asset_id === currentId
    ? <Pill tone="ok">目前使用</Pill>
    : (
      <button type="button" onClick={() => onPick(c.asset_id)} aria-label={`用「${c.title || c.asset_id}」`}>
        用這個
      </button>
    ))

  return (
    <details className="swap">
      <summary>換{label}</summary>
      {target && (
        <form className="swap-search" onSubmit={submit} role="search" aria-label={`搜尋${label}`}>
          <label htmlFor={`${ids}-q`} className="small">搜尋{label}（關鍵字用逗號分開，英文效果最好）</label>
          <div className="swap-row">
            <input id={`${ids}-q`} type="text" value={query} autoComplete="off" spellCheck={false}
                   placeholder={isMusic ? 'calm piano' : 'night market, steaming bowl'}
                   onChange={(e) => setQuery(e.target.value)} />
            <button type="submit" disabled={search.busy || !query.trim()}>
              {search.busy ? '搜尋中…' : '搜尋'}
            </button>
          </div>
          <div className="field tick">
            <input id={`${ids}-unk`} type="checkbox" checked={allowUnknown}
                   onChange={(e) => setAllowUnknown(e.target.checked)} />
            <label htmlFor={`${ids}-unk`} className="small">也列出授權不明、要自己確認的</label>
          </div>
        </form>
      )}
      {search.busy && <p className="muted small" role="status">向 vidgrab 搜尋中，通常幾秒；不會下載任何影片。</p>}
      {search.error && <ErrorBanner error={search.error} />}
      {found && (
        <div className="swap-results" aria-label="搜尋結果">
          <p className="muted small note-line">
            找到 {found.total} 支，列出 {found.candidates.length} 支：授權明確的在前面，
            {isMusic ? '夠長的優先' : '夠長、直式的優先'}；需要約 {found.need.toFixed(1)} 秒。
            {found.skipped_license > 0 && `另有 ${found.skipped_license} 支授權不明或要確認，沒有列入。`}
          </p>
          {found.candidates.length === 0 && <p className="plan-none">沒有可用的結果，換個關鍵字試試。</p>}
          {found.candidates.map((c) => (
            <CandidateLine key={c.asset_id} cand={c} action={action(c)} music={isMusic} badges={(
              <>
                {c.used_in.length > 0 && <Pill>已用在 {c.used_in.join('、')}</Pill>}
                {!c.enough && <Pill tone="warn">比需要的短，會循環補長</Pill>}
              </>
            )} />
          ))}
        </div>
      )}
      {candidates.length > 0 && (
        <div className="swap-plan">
          <p className="muted small">plan 挑過的備選</p>
          {candidates.map((c) => <CandidateLine key={c.asset_id} cand={c} action={action(c)} music={isMusic} />)}
        </div>
      )}
      <div className="swap-manual">
        <label htmlFor={`${ids}-id`} className="small">或輸入素材 id（vidgrab 的，例如 pixabay:123）</label>
        <div className="swap-row">
          <input id={`${ids}-id`} type="text" value={typed} placeholder="pixabay:123" autoComplete="off"
                 spellCheck={false} onChange={(e) => setTyped(e.target.value.trim())} />
          <button type="button" disabled={!typed.includes(':')}
                  onClick={() => { onPick(typed); setTyped('') }}>
            套用
          </button>
        </div>
      </div>
    </details>
  )
}

function SceneFields({ scene, index, shortId, plan, onChange }: {
  scene: ManifestScene
  index: number
  shortId: string
  plan: PlanScene | undefined
  onChange: (next: ManifestScene) => void
}) {
  const uid = useId()
  const set = (patch: Partial<ManifestScene>) => {
    const next: ManifestScene = { ...scene, ...patch }
    for (const key of Object.keys(patch)) {
      if (next[key] === undefined) delete next[key]
    }
    onChange(next)
  }
  const setSentences = (sentences: Sentence[]) => set({ sentences })
  const setSentence = (i: number, patch: Partial<Sentence>) => {
    if (!hasSentences(scene)) return
    setSentences(scene.sentences.map((s, j) => {
      if (j !== i) return s
      const next = { ...s, ...patch }
      if (!next.en) delete next.en
      return next
    }))
  }
  const setText = (text: string) => {
    // 旁白清空：沒有旁白的場景要有最短秒數，先給一個
    if (!text.trim()) set({ text: undefined, min_duration: scene.min_duration ?? 2 })
    else set({ text })
  }
  const clipName = scene.clip?.asset_id ?? scene.clip?.path
  const candidates = candidatesOf(plan, scene.clip?.asset_id)
  const narrated = hasNarration(scene)

  return (
    <>
      <div className="field">
        <span className="field-label" id={`${uid}-narr`}>旁白</span>
        {hasSentences(scene)
          ? (
            <div className="sentence-edit" role="group" aria-labelledby={`${uid}-narr`}>
              {scene.sentences.map((s, i) => (
                <div key={i} className="sentence-row">
                  <textarea rows={2} value={s.text} aria-label={`場景 ${index + 1} 第 ${i + 1} 句`}
                            aria-invalid={!s.text.trim()}
                            onChange={(e) => setSentence(i, { text: e.target.value })} />
                  <input type="text" value={s.en ?? ''} placeholder="English（選填，雙語字幕用）"
                         aria-label={`場景 ${index + 1} 第 ${i + 1} 句的英文`}
                         onChange={(e) => setSentence(i, { en: e.target.value })} />
                  <button type="button" className="ghost"
                          onClick={() => setSentences(scene.sentences.filter((_, j) => j !== i))}>
                    刪除這句
                  </button>
                </div>
              ))}
              <button type="button" className="ghost add"
                      onClick={() => setSentences([...scene.sentences, { text: '' }])}>
                ＋ 新增一句
              </button>
            </div>
          )
          : (
            <textarea rows={3} value={scene.text ?? ''} aria-labelledby={`${uid}-narr`}
                      onChange={(e) => setText(e.target.value)}
                      placeholder="這個場景沒有旁白；下面填最短秒數，或在這裡寫字" />
          )}
        {!hasSentences(scene) && narrated && (
          <p className="muted small">自動分句：每個句號、問號、驚嘆號各是一句；沒改到的句子不會重新合成。</p>
        )}
      </div>

      <div className="field-row three">
        <div className="field">
          <label htmlFor={`${uid}-fit`}>畫面適配</label>
          <select id={`${uid}-fit`} value={scene.fit ?? ''}
                  onChange={(e) => set({ fit: e.target.value || undefined })}>
            {FIT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <p className="muted small">{FIT_OPTIONS.find((o) => o.value === (scene.fit ?? ''))?.hint}</p>
        </div>
        {scene.clip && (
          <div className="field">
            <label htmlFor={`${uid}-start`}>素材從第幾秒開始</label>
            <input id={`${uid}-start`} type="number" min={0} step={0.5} value={scene.clip.start ?? ''}
                   placeholder="0"
                   onChange={(e) => {
                     const start = numberOrUndefined(e.target.value)
                     const { start: _old, ...rest } = scene.clip ?? {}
                     set({ clip: start === undefined ? rest : { ...rest, start } })
                   }} />
          </div>
        )}
        {(!narrated || scene.min_duration !== undefined) && (
          <div className="field">
            <label htmlFor={`${uid}-min`}>{narrated ? '最短秒數（選填）' : '最短秒數'}</label>
            <input id={`${uid}-min`} type="number" min={0} step={0.5} value={scene.min_duration ?? ''}
                   aria-invalid={!narrated && !(Number(scene.min_duration) > 0)}
                   onChange={(e) => set({ min_duration: numberOrUndefined(e.target.value) })} />
          </div>
        )}
      </div>

      <div className="field">
        <span className="field-label">素材</span>
        <div className="scene-source">
          {clipName
            ? <code>{clipName}</code>
            : <span className="muted small">這個場景沒有指定素材。</span>}
        </div>
        <SwapClip shortId={shortId} target={scene.id ? `scene:${scene.id}` : null}
                  initialQuery={(plan?.query ?? []).join(', ')} currentId={scene.clip?.asset_id}
                  candidates={candidates} label="素材"
                  onPick={(assetId) => set({ clip: { asset_id: assetId } })} />
      </div>
    </>
  )
}

export function SceneEditor({ short, locked, onSaved, onLeave }: {
  short: ShortDetail
  /** 有工作在跑：改了也存不進去，先把草稿留著。 */
  locked: boolean
  /** 儲存成功：呼叫端重新載入並離開編輯。 */
  onSaved: () => void
  /** 不改了、或放棄修改。 */
  onLeave: () => void
}) {
  // 草稿與 revision 都在開始編輯那一刻定下來：中途別的視窗改了，儲存才會 409，不會被悄悄蓋掉
  const base = useRef({ manifest: short.manifest as Manifest, revision: short.manifest_revision })
  const [draft, setDraft] = useState<Manifest>(() => cloneManifest(base.current.manifest))
  const action = useAction()
  const dirty = useMemo(() => canon(draft) !== canon(base.current.manifest), [draft])
  const problems = useMemo(() => problemsOf(draft), [draft])
  const titleId = useId()
  const volumeId = useId()

  const setScene = (i: number, next: ManifestScene) =>
    setDraft((d) => ({ ...d, scenes: d.scenes.map((s, j) => (j === i ? next : s)) }))
  const setMusic = (patch: Partial<NonNullable<Manifest['music']>>) =>
    setDraft((d) => {
      const music = { ...d.music, ...patch }
      for (const key of Object.keys(patch) as (keyof typeof music)[]) {
        if (music[key] === undefined) delete music[key]
      }
      return { ...d, music }
    })

  const save = () => {
    if (!base.current.revision) return
    void action.run(() => putJson(`/api/short/${encodeURIComponent(short.id)}/manifest`, {
      manifest: draft, revision: base.current.revision,
    })).then((r) => {
      if (r) onSaved()
    })
  }

  const stale = action.error?.body.code === 'stale_manifest'
  const planScenes = new Map((short.plan?.scenes ?? []).map((s) => [s.id, s]))

  return (
    <>
      <div className="editor-bar" role="region" aria-label="編輯">
        <div>
          <strong>{dirty ? '有還沒儲存的修改' : '編輯中'}</strong>
          <span className="muted small">
            {locked ? '　有工作在跑，等它結束才能儲存（草稿會留著）。'
              : dirty ? '　儲存之後要按「重新做」才會套用到成片。'
                : '　改好按儲存；只有改到的旁白與場景會重做。'}
          </span>
        </div>
        <button type="button" className="primary" onClick={save}
                disabled={!dirty || problems.length > 0 || action.busy || locked || stale}>
          {action.busy ? '儲存中…' : '儲存'}
        </button>
        <button type="button" onClick={onLeave} disabled={action.busy}>
          {dirty ? '放棄修改' : '不改了'}
        </button>
      </div>
      {action.error && !stale && <ErrorBanner error={action.error} />}
      {stale && (
        <div className="banner warn" role="alert">
          <div>
            <strong>這份 manifest 在你編輯的時候被改過了</strong>
            <p>可能是別的視窗、agent，或重新挑素材。為了不蓋掉對方的修改，這次沒有儲存。</p>
          </div>
          <button type="button" onClick={onLeave}>載入最新版（放棄我的修改）</button>
        </div>
      )}
      {problems.length > 0 && (
        <div className="banner warn" role="status">
          <div>
            <strong>先處理這些才能儲存</strong>
            {problems.map((p) => <p key={p}>{p}</p>)}
          </div>
        </div>
      )}

      <section className="card edit-scene" aria-label="作品設定">
        <div className="field">
          <label htmlFor={titleId}>標題</label>
          <input id={titleId} type="text" value={draft.title ?? ''} autoComplete="off"
                 onChange={(e) => setDraft((d) => {
                   const { title: _old, ...rest } = d
                   return e.target.value ? { ...rest, title: e.target.value } : rest
                 })} />
        </div>
        {draft.music && (
          <>
            <div className="field">
              <label htmlFor={volumeId}>背景音樂音量（0–1）</label>
              <input id={volumeId} type="number" min={0} max={1} step={0.05}
                     value={draft.music.volume ?? ''} placeholder="0.3（預設）"
                     onChange={(e) => setMusic({ volume: numberOrUndefined(e.target.value) })} />
            </div>
            <div className="field tick">
              <input id={`${volumeId}-duck`} type="checkbox"
                     checked={draft.music.duck_under_voice ?? true}
                     onChange={(e) => setMusic({ duck_under_voice: e.target.checked })} />
              <label htmlFor={`${volumeId}-duck`}>有人說話時壓低音樂</label>
            </div>
            <div className="scene-source">
              <span className="muted small">目前的音樂：</span>
              <code>{draft.music.asset_id ?? draft.music.path ?? '—'}</code>
            </div>
            <SwapClip shortId={short.id} target="music" label="音樂" currentId={draft.music.asset_id}
                      initialQuery={(short.plan?.music?.query ?? []).join(', ')}
                      candidates={candidatesOf(short.plan?.music ?? undefined, draft.music.asset_id)}
                      onPick={(assetId) => setMusic({ asset_id: assetId, path: undefined, start: undefined })} />
          </>
        )}
      </section>

      {draft.scenes.map((scene, i) => (
        <section key={scene.id ?? i} className="card scene edit-scene" aria-label={`編輯場景 ${i + 1}`}>
          <header className="scene-head">
            <span className="scene-no">{i + 1}</span>
            <code>{scene.id}</code>
          </header>
          <SceneFields scene={scene} index={i} shortId={short.id}
                       plan={scene.id ? planScenes.get(scene.id) : undefined}
                       onChange={(next) => setScene(i, next)} />
        </section>
      ))}
    </>
  )
}
