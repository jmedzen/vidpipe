// 把前端原始碼（src/、index.html、public/）的雜湊寫進 static/.build-stamp，
// 讓 `node stamp.mjs --check` 能發現「改了原始碼忘記 build」。
// 寫入與檢查用同一份程式，不會因為兩邊排序方式不同而對不起來。
import { createHash } from 'node:crypto'
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname, extname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const stampFile = join(here, '..', 'src', 'vidpipe', 'static', '.build-stamp')
const SOURCE_EXT = new Set(['.ts', '.tsx', '.css', '.js', '.html'])

function walk(dir) {
  if (!existsSync(dir)) return []
  const found = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) found.push(...walk(p))
    else if (SOURCE_EXT.has(extname(p))) found.push(p)
  }
  return found
}

function digest() {
  const files = [...walk(join(here, 'src')), ...walk(join(here, 'public')), join(here, 'index.html')]
    .map((p) => relative(here, p).split('\\').join('/'))
    .sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
  const hash = createHash('sha256')
  for (const rel of files) {
    hash.update(rel + '\0')
    hash.update(readFileSync(join(here, rel)))
    hash.update('\0')
  }
  return hash.digest('hex')
}

const current = digest()
if (process.argv.includes('--check')) {
  if (!existsSync(stampFile)) {
    console.error('✗ 找不到 static/.build-stamp —— 從沒 build 過？  修正：cd web && npm run build')
    process.exit(1)
  }
  if (readFileSync(stampFile, 'utf8').trim() !== current) {
    console.error('✗ 前端原始碼改了，但 src/vidpipe/static/ 裡的產物沒重新 build。')
    console.error('  修正：cd web && npm run build')
    process.exit(1)
  }
  console.log('✔ 前端產物與原始碼一致')
} else {
  writeFileSync(stampFile, current + '\n')
  console.log('stamped', current.slice(0, 12))
}
