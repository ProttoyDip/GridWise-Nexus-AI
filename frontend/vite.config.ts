import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "client", "src"),
      "@shared": path.resolve(import.meta.dirname, "shared"),
    },
  },
  envDir: path.resolve(import.meta.dirname),
  root: path.resolve(import.meta.dirname, "client"),
  build: {
    outDir: path.resolve(import.meta.dirname, "dist/public"),
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // Long-lived vendor chunks cache across deploys; charts load only with the results view.
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (/recharts|d3-|victory-vendor|decimal\.js-light|internmap/.test(id)) return "vendor-charts";
          if (/framer-motion|motion-dom|motion-utils/.test(id)) return "vendor-motion";
          if (/react-dom|[\/]react[\/]|scheduler/.test(id)) return "vendor-react";
          return undefined;
        },
      },
    },
  },
  server: {
    port: 3000,
    host: true,
  },
});
