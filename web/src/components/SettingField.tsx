import { useId, useState } from 'react'
import { SOURCE_TEXT, defaultInput, shownInput } from '../lib/settings'
import type { Edit } from '../lib/settings'
import type { SettingField as Field } from '../types'
import { Pill } from './Pills'

interface Props {
  field: Field
  edit: Edit | undefined
  /** 這個項目現在不合格的原因（即時檢查或伺服器回的）；空字串 = 合格。 */
  error: string
  /** 用不到的原因（`depends_on`），空字串 = 用得到。 */
  idle: string
  /** 整頁不能寫（設定檔不可寫、或部署者設成唯讀）。 */
  frozen: boolean
  onEdit: (edit: Edit | undefined) => void
}

function decimals(step: number | null): number {
  const text = String(step ?? 1)
  return text.includes('.') ? text.split('.')[1]!.length : 0
}

function Stepper({ id, field, value, disabled, invalid, describedBy, onChange }: {
  id: string
  field: Field
  value: string
  disabled: boolean
  invalid: boolean
  describedBy: string
  onChange: (value: string) => void
}) {
  const step = field.step ?? 1
  const n = Number(value)
  const has = value.trim() !== '' && Number.isFinite(n)
  const nudge = (dir: 1 | -1) => {
    let next = (has ? n : Number(defaultInput(field))) + dir * step
    if (field.min !== null) next = Math.max(field.min, next)
    if (field.max !== null) next = Math.min(field.max, next)
    onChange(String(+next.toFixed(Math.max(decimals(step), 6))))
  }
  const hasRange = field.min !== null && field.max !== null
  return (
    <div className="num-control">
      <div className="num-row">
        <div className="stepper">
          <button type="button" aria-label={`${field.label}減少`} disabled={disabled || (has && field.min !== null && n <= field.min)}
                  onClick={() => nudge(-1)}>−</button>
          <input id={id} type="number" inputMode="decimal" value={value} disabled={disabled}
                 min={field.min ?? undefined} max={field.max ?? undefined} step={step}
                 aria-invalid={invalid} aria-describedby={describedBy}
                 onChange={(e) => onChange(e.target.value)} />
          <button type="button" aria-label={`${field.label}增加`} disabled={disabled || (has && field.max !== null && n >= field.max)}
                  onClick={() => nudge(1)}>＋</button>
        </div>
        {field.unit && <span className="unit">{field.unit}</span>}
        {hasRange && <span className="muted small">範圍 {field.min} 到 {field.max}</span>}
      </div>
      {hasRange && (
        <input type="range" className="slider" aria-label={`${field.label}（滑桿）`} disabled={disabled}
               min={field.min ?? 0} max={field.max ?? 100} step={step}
               value={has ? Math.min(Math.max(n, field.min ?? n), field.max ?? n) : (field.min ?? 0)}
               onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  )
}

function Secret({ field, edit, disabled, onEdit, describedBy }: {
  field: Field
  edit: Edit | undefined
  disabled: boolean
  onEdit: (edit: Edit | undefined) => void
  describedBy: string
}) {
  const id = useId()
  const [show, setShow] = useState(false)
  // 「更換」只是打開輸入框；字真的打了才算修改（空的不算，見 normalize）
  const [open, setOpen] = useState(false)
  if (open || edit?.kind === 'secret') {
    const text = edit?.kind === 'secret' ? edit.text : ''
    return (
      <div className="secret">
        <div className="swap-row">
          <input id={id} type={show ? 'text' : 'password'} value={text} autoComplete="new-password"
                 autoCapitalize="off" spellCheck={false} placeholder="貼上新的 token" autoFocus
                 aria-label={`${field.label}（新的值）`} aria-describedby={describedBy}
                 onChange={(e) => onEdit({ kind: 'secret', text: e.target.value })} />
          <button type="button" aria-pressed={show} onClick={() => setShow((v) => !v)}>{show ? '隱藏' : '顯示'}</button>
          <button type="button" className="ghost" onClick={() => { setOpen(false); setShow(false); onEdit(undefined) }}>取消</button>
        </div>
        <p className="muted small">按「儲存」才會寫進設定檔，之後畫面上不會再顯示它。</p>
      </div>
    )
  }
  if (edit?.kind === 'clear') {
    return (
      <div className="secret-row">
        <span className="pending">儲存時會清掉這個 token</span>
        <button type="button" className="ghost" onClick={() => { setOpen(false); onEdit(undefined) }}>撤銷</button>
      </div>
    )
  }
  return (
    <div className="secret-row">
      {field.is_set
        ? (
          <>
            <span className="dots" aria-hidden="true">••••••••••••</span>
            <Pill tone="ok">已設定</Pill>
          </>
        )
        : <span className="muted">未設定</span>}
      {!field.locked && (
        <>
          <button type="button" disabled={disabled} onClick={() => setOpen(true)}>
            {field.is_set ? '更換' : '設定'}
          </button>
          {field.is_set && field.in_file && (
            <button type="button" className="ghost" disabled={disabled} onClick={() => { setOpen(false); onEdit({ kind: 'clear' }) }}>清除</button>
          )}
        </>
      )}
    </div>
  )
}

/** 一個設定項目：標籤與來源、依型別的輸入、說明、錯誤、還原。所有型別共用同一種版面。 */
export function SettingFieldRow({ field, edit, error, idle, frozen, onEdit }: Props) {
  const id = useId()
  const helpId = `${id}-help`
  const errId = `${id}-err`
  const describedBy = error ? `${helpId} ${errId}` : helpId
  const locked = field.locked
  const disabled = locked || frozen
  const value = shownInput(field, edit)
  const dirty = edit !== undefined
  const pendingReset = edit?.kind === 'reset'
  const set = (v: string | boolean) => onEdit({ kind: 'set', value: v })
  const canReset = field.source === 'file' && !locked && !frozen && !pendingReset && field.kind !== 'secret'
  const cls = ['setting', dirty ? 'dirty' : '', locked ? 'locked' : '', idle ? 'idle' : '', frozen ? 'frozen' : '',
    error ? 'invalid' : ''].filter(Boolean).join(' ')
  const invalid = error !== ''
  const text = typeof value === 'boolean' ? '' : value

  let control: React.ReactNode
  switch (field.kind) {
    case 'bool':
      control = (
        <button type="button" role="switch" id={id} aria-checked={value === true} className="switch"
                aria-labelledby={`${id}-label`} aria-describedby={describedBy} disabled={disabled}
                onClick={() => set(!(value === true))}>
          <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
          <span className="switch-text">{value === true ? '開' : '關'}</span>
        </button>
      )
      break
    case 'choice': {
      const picked = field.choices.find((c) => c.value === text)
      control = (
        <div className="choice">
          <div className="segmented" role="radiogroup" aria-labelledby={`${id}-label`} aria-describedby={describedBy}>
            {field.choices.map((c) => (
              <label key={c.value} className={c.value === text ? 'seg on' : 'seg'}>
                <input type="radio" name={field.name} value={c.value} checked={c.value === text}
                       disabled={disabled} onChange={() => set(c.value)} />
                <span>{c.label}</span>
              </label>
            ))}
          </div>
          {picked?.hint && <p className="muted small">{picked.hint}</p>}
        </div>
      )
      break
    }
    case 'int':
    case 'float':
      control = (
        <Stepper id={id} field={field} value={text} disabled={disabled} invalid={invalid}
                 describedBy={describedBy} onChange={set} />
      )
      break
    case 'secret':
      control = <Secret field={field} edit={edit} disabled={disabled} onEdit={onEdit} describedBy={describedBy} />
      break
    case 'textarea':
      control = (
        <textarea id={id} rows={3} value={text} readOnly={disabled} spellCheck={false}
                  placeholder={field.placeholder} aria-invalid={invalid} aria-describedby={describedBy}
                  onChange={(e) => set(e.target.value)} />
      )
      break
    default:
      control = (
        <>
          <input id={id} type="text" value={text} readOnly={disabled} spellCheck={false} autoComplete="off"
                 inputMode={field.kind === 'url' ? 'url' : undefined}
                 className={field.kind === 'path' || field.kind === 'url' ? 'mono' : undefined}
                 placeholder={field.placeholder || (field.default === '' ? undefined : String(field.default))}
                 aria-invalid={invalid} aria-describedby={describedBy}
                 onChange={(e) => set(e.target.value)} />
          {field.suggestions.length > 0 && !disabled && (
            <div className="chips" role="group" aria-label={`${field.label}的常用選項`}>
              {field.suggestions.map((s) => (
                <button key={s} type="button" className="chip" aria-pressed={text.trim() === s} onClick={() => set(s)}>{s}</button>
              ))}
            </div>
          )}
        </>
      )
  }

  const shadow = locked && field.file_value !== null && field.file_value !== field.value
  const sourceTone = field.source === 'env' ? 'warn' : field.source === 'file' ? 'note' : 'plain'

  return (
    <div className={cls} data-field={field.name} data-source={field.source}>
      <div className="setting-head">
        <label id={`${id}-label`} htmlFor={field.kind === 'choice' || field.kind === 'secret' ? undefined : id}
               className="setting-label">{field.label}</label>
        <div className="setting-badges">
          {dirty && edit?.kind === 'set' && (
            <button type="button" className="link" onClick={() => onEdit(undefined)}>撤銷</button>
          )}
          {canReset && (
            <button type="button" className="link" title={`預設：${String(field.default) || '空'}`}
                    onClick={() => onEdit({ kind: 'reset' })}>
              ↺ 改回預設
            </button>
          )}
          {dirty && <Pill tone="note">未儲存</Pill>}
          <Pill tone={sourceTone}>{locked ? '🔒 ' : ''}{SOURCE_TEXT[field.source]}</Pill>
        </div>
      </div>

      <div className={field.kind === 'bool' ? 'control bool-control' : 'control'}>{control}</div>

      {pendingReset && (
        <p className="pending small">
          儲存時會改回預設值（<code>{String(field.default) || '空'}</code>）。
          <button type="button" className="link" onClick={() => onEdit(undefined)}>撤銷</button>
        </p>
      )}
      <p id={helpId} className="muted small help">
        {field.help}
        {idle && <span className="idle-note">　目前沒用到（{idle}）。</span>}
      </p>
      {error && <p id={errId} className="field-problem small" role="alert">{error}</p>}
      {!error && field.problem && (
        <p className="field-problem small" role="alert">目前的值不合格：{field.problem}</p>
      )}
      {locked && (
        <p className="lock-note small">
          由環境變數 <code>{field.env_var}</code> 指定，這裡改不了。要改請改啟動時的環境變數
          （Docker：compose 的 <code>environment</code> 或 <code>.env</code>），重啟後生效。
          {shadow && <> 設定檔裡另外存了 <code>{String(field.file_value) || '空'}</code>，目前被蓋住。</>}
          {field.kind === 'secret' && field.in_file && <> 設定檔裡也存了一份，目前被蓋住。</>}
        </p>
      )}
    </div>
  )
}
