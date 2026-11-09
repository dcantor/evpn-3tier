import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built into dist/ and served by Django under /static/app/ (STATICFILES_DIRS "app"); index.html by workflows/spa.py.
// `npm run dev`: Vite on :5173, the API proxied to the running app on :8120 (same-origin cookies, CSRF).
export default defineConfig({
  plugins: [react()],
  base: "/static/app/",
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
  server: { proxy: { "/api": { target: "http://127.0.0.1:8120", changeOrigin: false } } },
});
