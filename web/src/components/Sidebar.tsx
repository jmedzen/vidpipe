import type { VersionInfo } from '../types'
import { ThemePicker } from './ThemePicker'

export interface NavItem {
  id: string
  label: string
  icon: string
}

interface Props {
  items: NavItem[]
  route: string
  version: VersionInfo | null
  theme: string
  onTheme: (id: string) => void
}

export function Sidebar({ items, route, version, theme, onTheme }: Props) {
  return (
    <aside>
      <div className="brand">
        <span className="logo" aria-hidden="true">▶</span>
        <div>
          <h1>vidpipe</h1>
          <p>Shorts 流水線</p>
        </div>
      </div>
      <nav aria-label="主選單">
        {items.map((item) => (
          <a key={item.id} href={`#/${item.id === 'overview' ? '' : item.id}`}
             className={route === item.id ? 'nav active' : 'nav'}
             aria-current={route === item.id ? 'page' : undefined}>
            <span aria-hidden="true">{item.icon}</span> {item.label}
          </a>
        ))}
      </nav>
      <div className="side-foot">
        <ThemePicker theme={theme} onChange={onTheme} />
        <p className="muted small" title={version ? `build ${version.build}` : undefined}>
          {version ? `v${version.full}` : '…'}
        </p>
      </div>
    </aside>
  )
}
