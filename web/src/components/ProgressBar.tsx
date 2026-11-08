/** 進度條。ratio 為 null（還不知道進度）時顯示不確定的流動樣式。 */
export function ProgressBar({ ratio, label }: { ratio: number | null; label?: string }) {
  const pct = ratio === null ? null : Math.round(Math.max(0, Math.min(1, ratio)) * 100)
  return (
    <div className="bar-wrap">
      <div className={pct === null ? 'bar indeterminate' : 'bar'} role="progressbar"
           aria-label={label ?? '進度'} aria-valuemin={0} aria-valuemax={100}
           aria-valuenow={pct ?? undefined}>
        <div className="bar-fill" style={pct === null ? undefined : { width: `${pct}%` }} />
      </div>
      <span className="bar-text">{pct === null ? '…' : `${pct}%`}</span>
    </div>
  )
}
