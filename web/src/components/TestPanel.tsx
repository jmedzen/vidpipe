import { useState } from 'react'
import { postJson } from '../lib/api'
import { LEVEL_TEXT, levelOf, worst } from '../lib/format'
import { useAction } from '../lib/useAction'
import type { SettingsTest } from '../types'
import { ErrorBanner } from './ErrorBanner'
import { StatusDot } from './StatusDot'

const CHECK_LABELS: Record<string, string> = {
  ffmpeg: 'ffmpeg',
  ffprobe: 'ffprobe',
  ffmpeg_subtitles: '字幕濾鏡（libass）',
  cjk_font: '系統有 CJK 字型',
  subtitle_font: '字幕字體',
  vidgrab: 'vidgrab 連線',
  tts: 'TTS 服務',
  voice_ref: '音色參考音檔',
  tts_speak: '試合成一句',
  dir_work: 'work 目錄',
  dir_out: 'out 目錄',
  voices: '參考音檔資料夾',
}

export interface TestAction {
  id: string
  label: string
  target: SettingsTest['target']
  /** TTS：真的合成一句（不留檔）。 */
  deep?: boolean
  title?: string
}

interface Props {
  actions: TestAction[]
  /** 目前表單裡還沒儲存的草稿（只含合格的）；測試用它，不寫任何設定。 */
  values: Record<string, unknown>
  /** 有原因就不能測（例如這一組有欄位標紅）。 */
  blocked: string
  /** 結果下面接的東西（例如「已載入的模型」）。 */
  extra?: (result: SettingsTest) => React.ReactNode
}

interface Shown {
  data: SettingsTest
  action: TestAction
  /** 測的當下的草稿；之後又改了就標「舊結果」。 */
  key: string
}

/** 「測試連線」：用表單裡的草稿去測，所以不必先儲存；結果逐項列出原因與 hint。 */
export function TestPanel({ actions, values, blocked, extra }: Props) {
  const [shown, setShown] = useState<Shown | null>(null)
  const [running, setRunning] = useState<string | null>(null)
  const action = useAction()
  const key = JSON.stringify(values)

  const run = (a: TestAction) => {
    setRunning(a.id)
    void action.run(() => postJson<SettingsTest>('/api/settings/test', {
      target: a.target, values, deep: a.deep === true,
    })).then((data) => {
      setRunning(null)
      if (data) setShown({ data, action: a, key })
    })
  }

  const level = shown ? worst(shown.data.checks.map(levelOf)) : 'ok'
  const stale = shown !== null && shown.key !== key

  return (
    <div className="test-area">
      <div className="test-actions">
        {actions.map((a) => (
          <button key={a.id} type="button" disabled={action.busy || blocked !== ''} title={a.title}
                  onClick={() => run(a)}>
            {running === a.id ? '測試中…' : a.label}
          </button>
        ))}
        <span className="muted small">
          {blocked !== '' ? blocked : '用上面表單裡的值去測，不用先儲存；不會改任何設定。'}
        </span>
      </div>
      {action.error && <ErrorBanner error={action.error} />}
      <div aria-live="polite">
        {shown && (
          <div className={stale ? 'test-result stale' : 'test-result'} role="group"
               aria-label={`${shown.action.label}的結果`}>
            <div className="test-summary">
              <StatusDot level={level} />
              <strong>{shown.action.label}：{LEVEL_TEXT[level]}</strong>
              <span className="muted small">{shown.data.elapsed_ms} ms</span>
              {stale && <span className="stale-note small">改了設定，這是舊的結果，再測一次</span>}
            </div>
            <ul className="checks">
              {shown.data.checks.map((c) => {
                const lv = levelOf(c)
                return (
                  <li key={c.name} className={`check check-${lv}`}>
                    <StatusDot level={lv} />
                    <div>
                      <div className="check-name">
                        {CHECK_LABELS[c.name] ?? c.name.replace(/^voice:/, '')}
                        {!c.required && <span className="tag">選用</span>}
                      </div>
                      {c.detail && <div className="detail">{c.detail}</div>}
                      {!c.ok && c.hint && <div className="hint">{c.hint}</div>}
                    </div>
                  </li>
                )
              })}
            </ul>
            {extra?.(shown.data)}
          </div>
        )}
      </div>
    </div>
  )
}
