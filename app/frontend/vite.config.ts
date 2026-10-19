import {defineConfig} from 'vite'
import {svelte} from '@sveltejs/vite-plugin-svelte'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [svelte()],
  test: {
    // The units under test are rune modules (*.svelte.ts). They need the
    // svelte plugin to compile $state/$derived, and the browser resolve
    // condition so svelte's client runtime is used rather than its SSR build.
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
  resolve: {
    conditions: ['browser'],
  },
})
