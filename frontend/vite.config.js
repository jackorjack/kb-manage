import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Relative base so the built bundle works under any mount path
  // ("/" locally, "/kb-manage/" behind an nginx sub-path proxy).
  base: "./",
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
