import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { ignitePlugin } from "./server/ignite-api.js";

export default defineConfig({
  plugins: [react(), ignitePlugin()],
  server: { port: 5174, host: true },
});
