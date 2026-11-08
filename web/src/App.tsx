import { useCallback, useEffect, useState } from 'react'
import { Sidebar } from './components/Sidebar'
import type { NavItem } from './components/Sidebar'
import { useApi } from './lib/api'
import { applyTheme, initialTheme } from './lib/theme'
import { Overview } from './pages/Overview'
import { Settings } from './pages/Settings'
import type { VersionInfo } from './types'

const NAV: NavItem[] = [
  { id: 'overview', label: '總覽', icon: '◎' },
  { id: 'settings', label: '設定', icon: '⚙' },
]

/** hash 路由：`#/` → overview，`#/settings` → settings。不認得的一律回總覽。 */
function routeFromHash(): string {
  const id = window.location.hash.replace(/^#\/?/, '').split('/')[0] ?? ''
  return NAV.some((n) => n.id === id) ? id : 'overview'
}

export default function App() {
  const [route, setRoute] = useState(routeFromHash)
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
    document.title = `${NAV.find((n) => n.id === route)?.label ?? ''} · vidpipe`
  }, [route])

  return (
    <div className="app">
      <Sidebar items={NAV} route={route} version={version.data} theme={theme} onTheme={changeTheme} />
      <main>
        {route === 'settings' ? <Settings /> : <Overview />}
      </main>
    </div>
  )
}
