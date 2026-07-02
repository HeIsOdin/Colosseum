import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/auth": "http://localhost:5000",
      "/series": "http://localhost:5000",
      "/players": "http://localhost:5000",
      "/diagnostics": "http://localhost:5000",
    },
  },
});
