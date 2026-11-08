import type { ApiFailure } from '../lib/api'

/** API 出錯時的橫幅：401 特別講怎麼登入，其他照伺服器給的 message／hint。 */
export function ErrorBanner({ error, onRetry }: { error: ApiFailure; onRetry?: () => void }) {
  const needToken = error.status === 401
  return (
    <div className="banner bad" role="alert">
      <div>
        <strong>{needToken ? '需要 API token' : error.body.message}</strong>
        <p>
          {needToken
            ? <>這個伺服器有設 token。用 <code>/?token=你的token</code> 開一次網址登入，之後就不用再帶。</>
            : error.body.hint}
        </p>
        {error.body.code && <p className="muted small">錯誤碼：{error.body.code}</p>}
      </div>
      {onRetry && <button type="button" onClick={onRetry}>重試</button>}
    </div>
  )
}
