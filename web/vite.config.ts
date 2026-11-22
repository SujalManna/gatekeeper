import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Locally the API runs on :8000 (uvicorn server:app); on Vercel /api is routed to the API service.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
});
