import { defineConfig, loadEnv, type Plugin } from 'vite'
import path from 'path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Use relative imports here. The '@' alias is configured in resolve.alias
// below and only takes effect during bundling — Node cannot resolve it when
// loading vite.config.ts. Bun resolves tsconfig paths natively, masking the
// issue, but Node does not.
import { normalizeApiPrefix, normalizeWebuiPrefix } from './src/lib/pathPrefix'

/**
 * Inject `<script>window.__LIGHTRAG_CONFIG__ = ...</script>` into index.html.
 *
 * This mirrors what the FastAPI server does at request time in production
 * (see `SmartStaticFiles._inject_runtime_config` in
 * `magi_core/api/lightrag_server.py`). Doing it in dev too means the SPA
 * always reads its prefix the same way, so behaviour matches between
 * `bun run dev` and a production deploy.
 *
 * Only `VITE_DEV_API_PREFIX` is read. The WebUI is mounted at the site root,
 * so the injected `webuiPrefix` is `/` without an API prefix and
 * `apiPrefix + "/"` when simulating a reverse-proxied deployment.
 */
function lightragRuntimeConfigPlugin(env: Record<string, string>): Plugin {
  const apiPrefix = normalizeApiPrefix(env.VITE_DEV_API_PREFIX)
  const webuiPrefix = normalizeWebuiPrefix(apiPrefix)
  const payload = JSON.stringify({ apiPrefix, webuiPrefix }).replace(
    /<\//g,
    '<\\/'
  )
  const snippet = `<script>window.__LIGHTRAG_CONFIG__ = ${payload};</script>`

  return {
    name: 'lightrag-dev-runtime-config',
    apply: 'serve',
    transformIndexHtml(html: string) {
      return html.replace('<!-- __LIGHTRAG_RUNTIME_CONFIG__ -->', snippet)
    }
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  // Dev-only: prefix every proxied endpoint with the simulated site
  // prefix so e.g. `/site01/documents/...` is forwarded to the backend
  // running with LIGHTRAG_API_PREFIX=/site01.
  const devApiPrefix = normalizeApiPrefix(env.VITE_DEV_API_PREFIX)

  return {
    plugins: [react(), tailwindcss(), lightragRuntimeConfigPlugin(env)],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src')
      },
      // Force all modules to use the same katex instance
      // This ensures mhchem extension registered in main.tsx is available to rehype-katex
      dedupe: ['katex']
    },
    // Relative base: asset URLs in index.html become `./assets/...` so the
    // built bundle works under any reverse-proxy site root. The browser
    // resolves them against the current document URL, which always ends in
    // `/` for both the default root and a configured API prefix.
    base: './',
    build: {
      outDir: path.resolve(__dirname, '../magi_core/api/webui'),
      emptyOutDir: true,
      chunkSizeWarningLimit: 3800,
      rollupOptions: {
        // Let Vite handle chunking automatically to avoid circular dependency issues
        output: {
          // Ensure consistent chunk naming format
          chunkFileNames: 'assets/[name]-[hash].js',
          // Entry file naming format
          entryFileNames: 'assets/[name]-[hash].js',
          // Asset file naming format
          assetFileNames: 'assets/[name]-[hash].[ext]'
        }
      }
    },
    server: {
      // The integrated MAGI server owns 3491. Vite is only a hot-reload
      // development surface and proxies API calls to that server.
      port: 5173,
      strictPort: true,
      proxy: env.VITE_API_PROXY === 'true' && env.VITE_API_ENDPOINTS ?
        Object.fromEntries(
          env.VITE_API_ENDPOINTS.split(',').map(endpoint => [
            devApiPrefix + endpoint,
            {
              target: env.VITE_BACKEND_URL || 'http://localhost:3491',
              changeOrigin: true
              // No rewrite: the backend already understands its own prefix
              // via FastAPI's root_path, so forward the path verbatim.
            }
          ])
        ) : {}
    },
    preview: {
      port: 4173,
      strictPort: true
    }
  }
})
