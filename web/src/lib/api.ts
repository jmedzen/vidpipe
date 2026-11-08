import { useCallback, useEffect, useRef, useState } from 'react'
import type { ApiErrorBody } from '../types'

/** 伺服器回的結構化錯誤（跟 CLI 同一套 code／message／retryable／hint）。 */
export class ApiFailure extends Error {
  readonly status: number
  readonly body: ApiErrorBody

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.name = 'ApiFailure'
    this.status = status
    this.body = body
  }
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(path, {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      signal,
    })
  } catch (err) {
    if (signal?.aborted) throw err
    throw new ApiFailure(0, {
      code: 'network',
      message: '連不到 vidpipe 伺服器',
      retryable: true,
      hint: '確認 `vidpipe serve` 還在跑，網址與 port 沒變',
    })
  }
  let data: unknown = null
  try {
    data = await resp.json()
  } catch {
    /* 不是 JSON；下面用狀態碼處理 */
  }
  if (!resp.ok) {
    const body = (data as { error?: ApiErrorBody } | null)?.error
    throw new ApiFailure(resp.status, body ?? {
      code: `http_${resp.status}`,
      message: `伺服器回了 HTTP ${resp.status}`,
      retryable: resp.status >= 500,
      hint: '',
    })
  }
  return data as T
}

export interface ApiState<T> {
  data: T | null
  error: ApiFailure | null
  loading: boolean
  /** 最後一次成功取得資料的時間（ms）。 */
  updatedAt: number | null
  reload: () => void
}

/** 取資料並（可選）定時更新。更新時保留舊資料，畫面不會閃成空白。 */
export function useApi<T>(path: string, refreshMs = 0): ApiState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiFailure | null>(null)
  const [loading, setLoading] = useState(true)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const ctl = useRef<AbortController | null>(null)

  const load = useCallback(() => {
    ctl.current?.abort()
    const c = new AbortController()
    ctl.current = c
    setLoading(true)
    getJson<T>(path, c.signal)
      .then((d) => {
        setData(d)
        setError(null)
        setUpdatedAt(Date.now())
        setLoading(false)
      })
      .catch((e: unknown) => {
        if (c.signal.aborted) return
        setError(e instanceof ApiFailure ? e : new ApiFailure(0, {
          code: 'internal', message: String(e), retryable: true, hint: '',
        }))
        setLoading(false)
      })
  }, [path])

  useEffect(() => {
    load()
    return () => ctl.current?.abort()
  }, [load])

  useEffect(() => {
    if (!refreshMs) return
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') load()
    }, refreshMs)
    return () => window.clearInterval(id)
  }, [load, refreshMs])

  return { data, error, loading, updatedAt, reload: load }
}
