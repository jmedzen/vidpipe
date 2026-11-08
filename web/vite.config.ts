import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Python 只掛 /static/，資源路徑要跟著帶前綴
  base: '/static/',
  build: {
    // 產物直接輸出到 Python 套件裡，pip 與 Docker 安裝就帶著走
    outDir: '../src/vidpipe/static',
    emptyOutDir: true,
    assetsDir: 'assets',
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
})
