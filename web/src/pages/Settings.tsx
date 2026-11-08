import { ErrorBanner } from '../components/ErrorBanner'
import { useApi } from '../lib/api'
import type { ConfigInfo, VersionInfo } from '../types'

const LABELS: Record<string, string> = {
  vidgrab_url: 'vidgrab 位址',
  vidgrab_token: 'vidgrab token',
  tts_base_url: 'TTS 位址',
  tts_token: 'TTS token',
  tts_model: 'TTS 模型',
  voice_ref: '音色參考音檔',
  reference_text: '參考音檔逐字稿',
  work_dir: 'work 目錄（中間產物）',
  out_dir: 'out 目錄（成品）',
  subtitle_langs: '字幕語言',
  burn_subtitles: '燒進影片',
}

function show(value: string | boolean) {
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (value === '') return <span className="muted">（未設定）</span>
  return value
}

export function Settings() {
  const config = useApi<ConfigInfo>('/api/config')
  const version = useApi<VersionInfo>('/api/version')

  return (
    <>
      <header className="page-head">
        <div>
          <h2>設定</h2>
          <p className="muted">目前生效的設定，唯讀。優先序：環境變數 <code>VIDPIPE_&lt;名稱&gt;</code> ＞ 設定檔 ＞ 預設。</p>
        </div>
      </header>

      {config.error && <ErrorBanner error={config.error} onRetry={config.reload} />}
      {config.data && (
        <section className="card">
          <div className="card-head">
            <div>
              <h3>設定項目</h3>
              <p className="muted small">設定檔：{config.data.path}</p>
            </div>
          </div>
          <table className="kv">
            <tbody>
              {Object.entries(config.data.config).map(([key, value]) => (
                <tr key={key}>
                  <th scope="row">{LABELS[key] ?? key}<code>{key}</code></th>
                  <td>{show(value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small note">token 一律遮蔽成 ***；祕密建議只放環境變數。</p>
        </section>
      )}

      {version.data && (
        <section className="card">
          <div className="card-head"><div><h3>版本與存取</h3></div></div>
          <table className="kv">
            <tbody>
              <tr><th scope="row">版本</th><td>{version.data.version}</td></tr>
              <tr><th scope="row">build</th><td>{version.data.build}</td></tr>
              <tr><th scope="row">API token</th><td>{version.data.auth_required ? '已啟用' : '未啟用（只綁本機時才建議）'}</td></tr>
            </tbody>
          </table>
        </section>
      )}
    </>
  )
}
