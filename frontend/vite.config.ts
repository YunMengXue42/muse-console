import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/muse2api/web",
    emptyOutDir: true,
    rollupOptions: { input: "dashboard.html" },
  },
  test: { environment: "jsdom", restoreMocks: true },
  server: { proxy: { "/admin": "http://127.0.0.1:18610" } },
});
