import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server runs on :5173; the FastAPI backend is on :8000 (allow-listed in the
// backend's CORS config). We call the API by absolute URL from src/api.js, so no
// proxy is needed — CORS handles the cross-origin calls.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
