import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` proxies the API to the Python server on 8742. Prod: the Python server serves dist/.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8742", "/shots": "http://127.0.0.1:8742" },
  },
});
