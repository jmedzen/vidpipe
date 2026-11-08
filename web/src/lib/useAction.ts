import { useCallback, useState } from 'react'
import { ApiFailure } from './api'

/** 按鈕送出寫入類請求：進行中禁用、失敗留下結構化錯誤給 ErrorBanner。 */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiFailure | null>(null)

  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true)
    setError(null)
    try {
      return await fn()
    } catch (e) {
      setError(e instanceof ApiFailure ? e : new ApiFailure(0, {
        code: 'internal', message: String(e), retryable: true, hint: '',
      }))
      return undefined
    } finally {
      setBusy(false)
    }
  }, [])

  const clear = useCallback(() => setError(null), [])
  return { busy, error, run, clear }
}
