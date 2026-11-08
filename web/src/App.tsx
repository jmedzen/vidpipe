import { useCallback, useEffect, useState } from 'react'
import { Sidebar } from './components/Sidebar'
import type { NavItem } from './components/Sidebar'
import { useApi } from './lib/api'
import { applyTheme, initialTheme } from './lib/theme'
import { JobDetailPage, JobsPage } from './pages/Jobs'
import { Overview } from './pages/Overview'
import { Settings } from './pages/Settings'
import type { VersionInfo } from './types'

const NAV: NavItem[] = [
  { id: 'overview', label: '總覽', icon: '◎' },
  { id: 'jobs', label: '工作', icon: '▤' },
  { id: 'settings', label: '設定', icon: '⚙' },
]

interface Route {
  page: string
  param: string
}

/** hash 路由：`#/` → overview，`#/jobs`、`#/jobs/<id>`、`#/settings`。不認得的一律回總覽。 */
function routeFromHash(): Route {
  const [id = '', param = ''] = window.location.hash.replace(/^#\/?/, '').split('/')
  return NAV.some((n) => n.id === id) ? { page: id, param } : { page: 'overview', param: '' }
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
    document.title = `${NAV.find((n) => n.id === route.page)?.label ?? ''} · vidpipe`
  }, [route.page])

  return (
    <div className="app">
      <Sidebar items={NAV} route={route.page} version={version.data} theme={theme} onTheme={changeTheme} />
      <main>
        {route.page === 'settings' && <Settings />}
        {route.page === 'jobs' && (route.param ? <JobDetailPage key={route.param} id={route.param} /> : <JobsPage />)}
        {route.page === 'overview' && <Overview />}
      </main>
    </div>
  )
}
