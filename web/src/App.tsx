import { useCallback, useEffect, useState } from 'react'
import { Sidebar } from './components/Sidebar'
import type { NavItem } from './components/Sidebar'
import { useApi } from './lib/api'
import { applyTheme, initialTheme } from './lib/theme'
import { JobDetailPage, JobsPage } from './pages/Jobs'
import { NewShortPage } from './pages/NewShort'
import { Overview } from './pages/Overview'
import { Settings } from './pages/Settings'
import { ShortDetailPage, ShortsPage } from './pages/Shorts'
import type { VersionInfo } from './types'

const NAV: NavItem[] = [
  { id: 'overview', label: '總覽', icon: '◎' },
  { id: 'shorts', label: '作品', icon: '▶' },
  { id: 'jobs', label: '工作', icon: '▤' },
  { id: 'settings', label: '設定', icon: '⚙' },
]

/** 不在側欄上、但有自己網址的頁（`#/new`）。 */
const HIDDEN_PAGES = ['new']

interface Route {
  page: string
  param: string
  sub: string
}

function safeDecode(text: string): string {
  try {
    return decodeURIComponent(text)
  } catch {
    return text
  }
}

/**
 * hash 路由：`#/` → overview，`#/jobs`、`#/jobs/<id>`、`#/shorts`、`#/shorts/<id>`、
 * `#/shorts/<id>/<分頁>`、`#/settings`。不認得的一律回總覽。
 */
function routeFromHash(): Route {
  const [id = '', param = '', sub = ''] = window.location.hash.replace(/^#\/?/, '').split('/')
  return NAV.some((n) => n.id === id) || HIDDEN_PAGES.includes(id)
    ? { page: id, param: safeDecode(param), sub }
    : { page: 'overview', param: '', sub: '' }
}

export default function App() {
  const [route, setRoute] = useState<Route>(routeFromHash)
  const [theme, setTheme] = useState(initialTheme)
  const version = useApi<VersionInfo>('/api/version')

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const changeTheme = useCallback((id: string) => {
    applyTheme(id)
    setTheme(id)
  }, [])

  useEffect(() => {
    document.title = `${route.page === 'new' ? '新增作品' : NAV.find((n) => n.id === route.page)?.label ?? ''} · vidpipe`
  }, [route.page])

  return (
    <div className="app">
      <Sidebar items={NAV} route={route.page === 'new' ? 'shorts' : route.page} version={version.data} theme={theme} onTheme={changeTheme} />
      <main>
        {route.page === 'settings' && <Settings />}
        {route.page === 'new' && <NewShortPage />}
        {route.page === 'shorts' && (route.param
          ? <ShortDetailPage key={route.param} id={route.param} tab={route.sub} />
          : <ShortsPage />)}
        {route.page === 'jobs' && (route.param ? <JobDetailPage key={route.param} id={route.param} /> : <JobsPage />)}
        {route.page === 'overview' && <Overview />}
      </main>
    </div>
  )
}
