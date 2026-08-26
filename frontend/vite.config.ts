import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Node environment, not jsdom: the only tests here target the API layer
  // as plain modules with fetch stubbed. No component rendering, so no DOM.
  test: {
    environment: 'node',
  },
})
