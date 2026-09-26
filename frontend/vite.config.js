// STEP 1: Import Vite's configuration helper and React plugin
// - defineConfig provides TypeScript type hints and auto-completion
// - @vitejs/plugin-react enables JSX transformation and Fast Refresh (HMR)
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// STEP 2: Export the Vite configuration object
export default defineConfig({
  // WHY THIS STEP: Enables the React plugin so Vite understands JSX syntax and updates UI instantly on code edits
  plugins: [react()],

  server: {
    // WHY THIS STEP: Bind to 127.0.0.1 (localhost) to ensure consistent local testing
    host: '127.0.0.1',

    // WHY THIS STEP: Set the port to 5173 (standard Vite port), or an environment override
    port: Number(process.env.VITE_PORT) || 5173,

    // WHY THIS STEP: If 5173 is occupied, fail immediately instead of switching ports silently
    strictPort: true,

    // WHY THIS STEP: Reverse Proxy Configuration
    // The frontend runs on port 5173, but the Python backend runs on port 8000.
    // The proxy forwards any '/api' and '/uploads' requests to FastAPI, avoiding CORS errors in development!
    proxy: {
      // Forward all /api requests to FastAPI backend (e.g. /api/health -> http://127.0.0.1:8000/api/health)
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true, // Changes the Origin header to match the backend host
      },
      // Forward uploaded media files (e.g. profile photos) to backend uploads directory
      '/uploads': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})

