import { useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { ErrorBanner } from '../components/ErrorBanner'
import { Pill } from '../components/Pills'
import { TemplatePreview } from '../components/TemplatePreview'
import { deleteJson, putJson, useApi } from '../lib/api'
import {
  COLOR_LABEL, FIT_LABEL, OVERLAY_SIZE_LABEL, SUB_SIZE_LABEL, blankTemplate, cssColor,
} from '../lib/templates'
import type { OverlayStyle, Template, TemplateList } from '../lib/templates'
import { useAction } from '../lib/useAction'

const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,39}$/
const HEX_RE = /^#[0-9a-fA-F]{6}$/

function ColorField({ id, label, value, colors, onChange }: {
  id: string
  label: string
  value: string
  colors: Record<string, string>
  onChange: (v: string) => void
}) {
  const custom = !colors[value]
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="color-pick">
        <span className="swatch" style={{ background: cssColor(value, colors) }} aria-hidden="true" />
        <select id={id} value={custom ? 'custom' : value}
                onChange={(e) => onChange(e.target.value === 'custom' ? (custom ? value : '#ffffff') : e.target.value)}>
          {Object.keys(colors).map((c) => <option key={c} value={c}>{COLOR_LABEL[c] ?? c}</option>)}
          <option value="custom">自訂…</option>
        </select>
        {custom && (
          <input type="color" aria-label={`${label}（自訂）`} value={HEX_RE.test(value) ? value : '#ffffff'}
                 onChange={(e) => onChange(e.target.value)} />
        )}
      </div>
    </div>
  )
}

function Select({ id, label, value, options, onChange, note }: {
  id: string
  label: string
  value: string
  options: Record<string, string>
  onChange: (v: string) => void
  note?: ReactNode
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {Object.entries(options).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      {note && <p className="muted small">{note}</p>}
    </div>
  )
}

function OverlayFields({ prefix, label, value, sizes, colors, onChange }: {
  prefix: string
  label: string
  value: OverlayStyle
  sizes: string[]
  colors: Record<string, string>
  onChange: (v: OverlayStyle) => void
}) {
  return (
    <fieldset className="tpl-group">
      <legend>{label}</legend>
      <div className="field tick">
        <input id={`${prefix}-show`} type="checkbox" checked={value.show}
               onChange={(e) => onChange({ ...value, show: e.target.checked })} />
        <label htmlFor={`${prefix}-show`}>顯示</label>
      </div>
      <div className="field-row three">
        <Select id={`${prefix}-pos`} label="位置" value={value.position}
                options={{ top: '上方', bottom: '下方' }}
                onChange={(v) => onChange({ ...value, position: v as OverlayStyle['position'] })} />
        <Select id={`${prefix}-size`} label="大小" value={value.size}
                options={Object.fromEntries(sizes.map((s) => [s, OVERLAY_SIZE_LABEL[s] ?? s]))}
                onChange={(v) => onChange({ ...value, size: v })} />
        <ColorField id={`${prefix}-color`} label="顏色" value={value.color} colors={colors}
                    onChange={(v) => onChange({ ...value, color: v })} />
      </div>
    </fieldset>
  )
}

/** 建立或修改一個自訂模板；右邊即時示意。 */
function Editor({ start, isNew, colors, taken, onDone, onCancel }: {
  start: Template
  isNew: boolean
  colors: Record<string, string>
  taken: string[]
  onDone: () => void
  onCancel: () => void
}) {
  const [tpl, setTpl] = useState<Template>(start)
  const action = useAction()
  const set = <K extends keyof Template>(key: K, value: Template[K]) => setTpl({ ...tpl, [key]: value })
  const sub = (patch: Partial<Template['subtitles']>) => set('subtitles', { ...tpl.subtitles, ...patch })
  const nameProblem = !NAME_RE.test(tpl.name)
    ? '小寫英文、數字、底線與連字號，開頭要是英文或數字，最長 40 字'
    : isNew && taken.includes(tpl.name) ? '已經有同名的模板了（內建的不能覆蓋；自訂的請直接編輯）' : null

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (nameProblem) return
    void action.run(() => putJson(`/api/templates/${encodeURIComponent(tpl.name)}`, { template: tpl }))
      .then((r) => { if (r) onDone() })
  }

  return (
    <form className="card form tpl-editor" onSubmit={submit} aria-label="編輯模板">
      {action.error && <ErrorBanner error={action.error} />}
      <div className="tpl-editor-body">
        <div className="form">
          <div className="field-row">
            <div className="field">
              <label htmlFor="tpl-name">名稱（檔名）</label>
              <input id="tpl-name" type="text" value={tpl.name} disabled={!isNew} spellCheck={false}
                     autoComplete="off" aria-invalid={nameProblem !== null}
                     onChange={(e) => set('name', e.target.value.trim())} />
              <p className={nameProblem ? 'field-problem small' : 'muted small'}>
                {nameProblem ?? `指令列用 --template ${tpl.name || '名稱'} 選它`}
              </p>
            </div>
            <div className="field">
              <label htmlFor="tpl-label">顯示名稱</label>
              <input id="tpl-label" type="text" value={tpl.label} maxLength={40} autoComplete="off"
                     placeholder={tpl.name} onChange={(e) => set('label', e.target.value)} />
            </div>
          </div>
          <div className="field">
            <label htmlFor="tpl-desc">說明（選填）</label>
            <input id="tpl-desc" type="text" value={tpl.description} maxLength={200} autoComplete="off"
                   onChange={(e) => set('description', e.target.value)} />
          </div>
          <div className="field-row">
            <Select id="tpl-aspect" label="畫面比例" value={tpl.aspect}
                    options={{ '9:16': '9:16 直式（Shorts）', '1:1': '1:1 方形', '16:9': '16:9 橫式' }}
                    onChange={(v) => set('aspect', v as Template['aspect'])} />
            <Select id="tpl-fit" label="素材怎麼放進畫面" value={tpl.fit} options={FIT_LABEL}
                    onChange={(v) => set('fit', v as Template['fit'])} />
          </div>

          <fieldset className="tpl-group">
            <legend>字幕</legend>
            <div className="field-row three">
              <Select id="tpl-sub-size" label="字級" value={tpl.subtitles.size} options={SUB_SIZE_LABEL}
                      onChange={(v) => sub({ size: v as Template['subtitles']['size'] })} />
              <ColorField id="tpl-sub-color" label="顏色" value={tpl.subtitles.color} colors={colors}
                          onChange={(v) => sub({ color: v })} />
              <ColorField id="tpl-sub-en" label="英文顏色（雙語）" value={tpl.subtitles.en_color}
                          colors={colors} onChange={(v) => sub({ en_color: v })} />
            </div>
            <div className="field-row">
              <div className="field">
                <label htmlFor="tpl-margin">離底邊 {Math.round((tpl.subtitles.margin ?? 0.2) * 100)}%</label>
                <input id="tpl-margin" type="range" min={0.04} max={0.45} step={0.01}
                       value={tpl.subtitles.margin ?? 0.2}
                       onChange={(e) => sub({ margin: Number(e.target.value) })} />
                <p className="muted small">
                  {tpl.subtitles.margin === null ? '預設：直式 20%、橫式 8%（避開 Shorts 的介面）。' : (
                    <button type="button" className="link" onClick={() => sub({ margin: null })}>回到預設</button>
                  )}
                </p>
              </div>
              <div className="field">
                <label htmlFor="tpl-font">字體（選填）</label>
                <input id="tpl-font" type="text" value={tpl.subtitles.font} autoComplete="off"
                       placeholder="空白＝用設定的字幕字體" onChange={(e) => sub({ font: e.target.value })} />
              </div>
            </div>
            <div className="field tick">
              <input id="tpl-box" type="checkbox" checked={tpl.subtitles.box}
                     onChange={(e) => sub({ box: e.target.checked })} />
              <label htmlFor="tpl-box">
                半透明黑底框<span className="muted small">　取代描邊，素材畫面很雜時比較清楚。</span>
              </label>
            </div>
          </fieldset>

          <OverlayFields prefix="tpl-title" label="常駐標題（文案的標題）" value={tpl.title}
                         sizes={['auto', 'xlarge', 'large', 'medium', 'small']} colors={colors}
                         onChange={(v) => set('title', v)} />
          <OverlayFields prefix="tpl-series" label="系列名稱" value={tpl.series}
                         sizes={['xlarge', 'large', 'medium', 'small']} colors={colors}
                         onChange={(v) => set('series', v)} />

          {tpl.title.show && tpl.series.show && tpl.title.position === tpl.series.position && (
            <p className="field-problem small">標題和系列名稱放在同一邊會疊在一起，建議一個放上方、一個放下方。</p>
          )}
          <div className="field">
            <label htmlFor="tpl-volume">背景音樂音量 {tpl.music.volume.toFixed(2)}</label>
            <input id="tpl-volume" type="range" min={0} max={1} step={0.01} value={tpl.music.volume}
                   onChange={(e) => set('music', { volume: Number(e.target.value) })} />
            <p className="muted small">0.22 左右不搶旁白；0 就不放音樂。</p>
          </div>
        </div>
        <div className="tpl-editor-preview">
          <TemplatePreview tpl={tpl} colors={colors} width={tpl.aspect === '16:9' ? 300 : 220} />
          <p className="muted small">示意圖，字型與折行以實際成片為準。</p>
        </div>
      </div>
      <div className="form-actions">
        <button type="submit" className="primary" disabled={nameProblem !== null || action.busy}>
          {action.busy ? '儲存中…' : '儲存模板'}
        </button>
        <button type="button" onClick={onCancel}>取消</button>
      </div>
    </form>
  )
}

/** 排版模板：內建＋自訂，每個附示意圖；可以以任一個為底建立新的、編輯或刪除自訂的。 */
export function TemplatesPage() {
  const list = useApi<TemplateList>('/api/templates')
  const [editing, setEditing] = useState<{ tpl: Template; isNew: boolean } | null>(null)
  const [confirming, setConfirming] = useState('')
  const action = useAction()
  const data = list.data
  const colors = data?.colors ?? {}

  const copyOf = (tpl: Template) => {
    const taken = new Set(data?.items.map((i) => i.name))
    let n = 1
    while (taken.has(`${tpl.name}-${n}`)) n += 1
    setEditing({ tpl: { ...tpl, name: `${tpl.name}-${n}`, label: `${tpl.label}（複本）` }, isNew: true })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
  const remove = (name: string) => {
    void action.run(() => deleteJson(`/api/templates/${encodeURIComponent(name)}`)).then((r) => {
      setConfirming('')
      if (r) list.reload()
    })
  }
  const makeDefault = (name: string) => {
    void action.run(() => putJson('/api/settings', { changes: { default_template: name } })).then((r) => {
      if (r) list.reload()
    })
  }

  return (
    <>
      <header className="page-head">
        <div>
          <h2>排版模板</h2>
          <p className="muted">
            標題、字幕、系列名稱的樣式存成模板，文案直出時選一個用。內建的不能改，按「以此為底」複製一份再調。
          </p>
        </div>
        <div className="head-actions">
          <button type="button" className="primary"
                  onClick={() => setEditing({ tpl: blankTemplate(''), isNew: true })}>新增模板</button>
        </div>
      </header>
      {list.error && <ErrorBanner error={list.error} />}
      {action.error && <ErrorBanner error={action.error} />}
      {editing && (
        <Editor key={`${editing.tpl.name}-${editing.isNew}`} start={editing.tpl} isNew={editing.isNew}
                colors={colors} taken={data?.items.map((i) => i.name) ?? []}
                onCancel={() => setEditing(null)}
                onDone={() => { setEditing(null); list.reload() }} />
      )}
      {data && (
        <div className="tpl-grid">
          {data.items.map((item) => (
            <article key={item.name} className="card tpl-card">
              {item.template
                ? <TemplatePreview tpl={item.template} colors={colors} width={item.template.aspect === '16:9' ? 200 : 132} />
                : <div className="tpl-broken" aria-hidden="true">⚠</div>}
              <div className="tpl-info">
                <h3>{item.template?.label ?? item.name}</h3>
                <div className="pills">
                  <Pill>{item.builtin ? '內建' : '自訂'}</Pill>
                  {data.default === item.name && <Pill tone="ok">預設</Pill>}
                  <code className="small">{item.name}</code>
                </div>
                {item.template?.description && <p className="muted small">{item.template.description}</p>}
                {item.error && <p className="field-problem small">{item.error}</p>}
                <div className="tpl-actions">
                  {item.template && (
                    <button type="button" onClick={() => copyOf(item.template!)}>以此為底</button>
                  )}
                  {!item.builtin && item.template && (
                    <button type="button" onClick={() => {
                      setEditing({ tpl: item.template!, isNew: false })
                      window.scrollTo({ top: 0, behavior: 'smooth' })
                    }}>編輯</button>
                  )}
                  {item.template && data.default !== item.name && (
                    <button type="button" disabled={action.busy} onClick={() => makeDefault(item.name)}>設為預設</button>
                  )}
                  {!item.builtin && confirming !== item.name && (
                    <button type="button" className="danger" onClick={() => setConfirming(item.name)}>刪除</button>
                  )}
                  {confirming === item.name && (
                    <>
                      <button type="button" className="danger" disabled={action.busy}
                              onClick={() => remove(item.name)}>{action.busy ? '刪除中…' : '確定刪除'}</button>
                      <button type="button" onClick={() => setConfirming('')}>取消</button>
                    </>
                  )}
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
      {data && (
        <p className="muted small">
          自訂模板存在 <code>{data.dir}</code>（一個模板一個 .json）。指令列：<code>vidpipe templates</code> 列出、
          <code>vidpipe quick 文案.txt --template 名稱</code> 套用。
        </p>
      )}
    </>
  )
}
