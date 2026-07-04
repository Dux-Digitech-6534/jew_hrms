import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  base: "/assets/jew_hrms_mobile/frontend/",
  build: {
    manifest: true,
    outDir: "../jew_hrms_mobile/public/frontend",
    emptyOutDir: true,
    sourcemap: false
  }
});
