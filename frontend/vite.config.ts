import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/muse_console/web",
    emptyOutDir: true,
    rollupOptions: { input: "dashboard.html" },
  },
  test: {
    environment: "jsdom",
    restoreMocks: true,
    setupFiles: ["./src/test-setup.ts"],
  },
  server: { proxy: { "/admin": "http://127.0.0.1:18610" } },
});
