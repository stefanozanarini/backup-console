import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In sviluppo /api viene inoltrato al backend (uvicorn locale o stack compose).
const target = process.env.BC_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": { target, changeOrigin: true, secure: false } },
  },
  build: { sourcemap: false },
});
