import { ORIENT_TEXT, licenseTone } from '../lib/format'
import type { PlanCandidate } from '../types'

export function Pill({ tone = 'plain', children }: { tone?: 'plain' | 'ok' | 'note' | 'warn'; children: React.ReactNode }) {
  return <span className={`pill pill-${tone}`}>{children}</span>
}

export function LicenseBadge({ kind, label }: { kind: string | undefined; label: string | undefined }) {
  const tone = licenseTone(kind)
  return <Pill tone={tone}>{tone === 'warn' ? '⚠ ' : ''}{label || '授權未明'}</Pill>
}

/** 一支候選素材：標題（連到來源頁）、授權、方向、長度、作者、id。plan 與編輯換素材共用。 */
export function CandidateLine({ cand, action }: { cand: PlanCandidate; action?: React.ReactNode }) {
  return (
    <div className="cand">
      <div className="cand-head">
        {cand.webpage_url
          ? <a href={cand.webpage_url} target="_blank" rel="noreferrer">{cand.title || cand.asset_id}</a>
          : <span>{cand.title || cand.asset_id}</span>}
        {action}
      </div>
      <div className="pills">
        <LicenseBadge kind={cand.license.kind} label={cand.license.label} />
        <Pill>{ORIENT_TEXT[cand.orientation] ?? cand.orientation}</Pill>
        {cand.duration ? <Pill>{cand.duration} 秒</Pill> : null}
        {cand.author && <span className="muted small">{cand.author}</span>}
        <code>{cand.asset_id}</code>
      </div>
      {cand.why && <p className="muted small">{cand.why}</p>}
    </div>
  )
}
