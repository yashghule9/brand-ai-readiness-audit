import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the API runs separately on :8000 (uvicorn braiaudit.server:app).
// In production FastAPI serves dist/ itself, so no proxy and no CORS.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
