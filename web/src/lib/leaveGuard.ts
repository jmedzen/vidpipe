/**
 * 有沒存的修改時，擋住「換頁」：App 用 hash 路由，換頁只是 `hashchange`，沒有 `beforeunload` 可用。
 * 設定頁回報有沒有未儲存的修改；App 在換頁前問這裡，被擋下的目標網址留著，
 * 設定頁顯示「要離開嗎？」，使用者選了才真的過去。
 */
type Listener = () => void

let dirty = false
let pending: string | null = null
const listeners = new Set<Listener>()

function emit(): void {
  listeners.forEach((fn) => fn())
}

export const leaveGuard = {
  setDirty(value: boolean): void {
    if (dirty === value) return
    dirty = value
    if (!value) pending = null
    emit()
  },
  isDirty: (): boolean => dirty,
  /** 被擋下的目標（`#/jobs` 之類）；沒有就是 null。 */
  pending: (): string | null => pending,
  block(target: string): void {
    pending = target
    emit()
  },
  /** 使用者決定留下。 */
  stay(): void {
    pending = null
    emit()
  },
  subscribe(fn: Listener): () => void {
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  },
}
