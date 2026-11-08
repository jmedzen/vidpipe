import { ErrorBanner } from '../components/ErrorBanner'
import { StatusDot } from '../components/StatusDot'
import { useApi } from '../lib/api'
import { LEVEL_TEXT, clock, levelOf, worst } from '../lib/format'
import type { Check, ConfigInfo, Doctor, Level } from '../types'

const LABELS: Record<string, string> = {
  ffmpeg: 'ffmpeg',
  ffprobe: 'ffprobe',
  ffmpeg_subtitles: '字幕濾鏡（libass）',
  cjk_font: 'CJK 字型',
  dirs: 'work／out 目錄',
  vidgrab: 'vidgrab 連線',
  tts: 'TTS 服務',
  voice_ref: '音色參考音檔',
}

interface Group {
  id: string
  title: string
  note: string
  names: string[]
  url?: string
}

function groupsFor(config: ConfigInfo | null): Group[] {
  const c = config?.config ?? {}
  return [
    { id: 'render', title: '渲染環境', note: 'ffmpeg 與字型，以及中間產物、成品的資料夾',
      names: ['ffmpeg', 'ffprobe', 'ffmpeg_subtitles', 'cjk_font', 'dirs'] },
    { id: 'vidgrab', title: '素材來源 · vidgrab', note: '搜尋與下載素材、授權資訊',
      names: ['vidgrab'], url: typeof c.vidgrab_url === 'string' ? c.vidgrab_url : undefined },
    { id: 'tts', title: '旁白 · TTS', note: '逐句合成旁白，量秒數當字幕時間軸',
      names: ['tts', 'voice_ref'], url: typeof c.tts_base_url === 'string' ? c.tts_base_url : undefined },
  ]
}

/** `ffmpeg version 6.1.1 Copyright …` 這種整行版權字樣太吵，只留版本。 */
function tidy(detail: string): string {
  const m = /^(ffmpeg|ffprobe) version (\S+)/.exec(detail)
  return m ? `${m[1]} ${m[2]}` : detail
}

function CheckRow({ check }: { check: Check }) {
  const level = levelOf(check)
  return (
    <li className={`check check-${level}`}>
      <StatusDot level={level} />
      <div>
        <div className="check-name">
          {LABELS[check.name] ?? check.name}
          {!check.required && <span className="tag">選用</span>}
        </div>
        {check.detail && <div className="detail">{tidy(check.detail)}</div>}
        {!check.ok && check.hint && <div className="hint">{check.hint}</div>}
      </div>
    </li>
  )
}

function Summary({ level, doctor, bad, warn }: { level: Level; doctor: Doctor; bad: number; warn: number }) {
  const text = level === 'ok'
    ? '全部就緒，可以開始做片'
    : level === 'warn'
      ? `必要項目都通過，有 ${warn} 項警告`
      : `有 ${bad} 項必要檢查沒通過，先處理這些`
  return (
    <div className={`banner ${level}`} role="status">
      <StatusDot level={level} />
      <div>
        <strong>{text}</strong>
        {doctor.skipped.length > 0 && <p className="muted small">略過：{doctor.skipped.join('、')}</p>}
      </div>
    </div>
  )
}

export function Overview() {
  const doctor = useApi<Doctor>('/api/doctor', 30_000)
  const config = useApi<ConfigInfo>('/api/config')

  const checks = doctor.data?.checks ?? []
  const levels = checks.map(levelOf)
  const bad = levels.filter((l) => l === 'bad').length
  const warn = levels.filter((l) => l === 'warn').length
  const byName = new Map(checks.map((c) => [c.name, c]))
  const groups = groupsFor(config.data)
  const known = new Set(groups.flatMap((g) => g.names))
  const extra = checks.filter((c) => !known.has(c.name))

  return (
    <>
      <header className="page-head">
        <div>
          <h2>總覽</h2>
          <p className="muted">三個服務現在的狀態；每 30 秒自動重新檢查。</p>
        </div>
        <div className="head-actions">
          <span className="muted small">上次檢查 {clock(doctor.updatedAt)}</span>
          <button type="button" onClick={doctor.reload} disabled={doctor.loading}>
            {doctor.loading ? '檢查中…' : '重新檢查'}
          </button>
        </div>
      </header>

      {doctor.error && <ErrorBanner error={doctor.error} onRetry={doctor.reload} />}
      {!doctor.data && !doctor.error && <p className="muted">第一次檢查中…</p>}
      {doctor.data && <Summary level={worst(levels)} doctor={doctor.data} bad={bad} warn={warn} />}

      {doctor.data && (
        <div className="cards">
          {groups.map((g) => {
            const rows = g.names.map((n) => byName.get(n)).filter((c): c is Check => !!c)
            if (rows.length === 0) return null
            const level = worst(rows.map(levelOf))
            return (
              <section key={g.id} className={`card card-${level}`} aria-labelledby={`g-${g.id}`}>
                <div className="card-head">
                  <StatusDot level={level} />
                  <div>
                    <h3 id={`g-${g.id}`}>{g.title}</h3>
                    <p className="muted small">{g.url ?? g.note}</p>
                  </div>
                  <span className={`level level-${level}`}>{LEVEL_TEXT[level]}</span>
                </div>
                <ul className="checks">{rows.map((c) => <CheckRow key={c.name} check={c} />)}</ul>
              </section>
            )
          })}
          {extra.length > 0 && (
            <section className="card" aria-label="其他檢查">
              <div className="card-head"><div><h3>其他</h3></div></div>
              <ul className="checks">{extra.map((c) => <CheckRow key={c.name} check={c} />)}</ul>
            </section>
          )}
        </div>
      )}
    </>
  )
}
