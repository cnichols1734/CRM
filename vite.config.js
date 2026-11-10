import { defineConfig } from "vite";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  publicDir: false,
  base: "/static/dist/",
  build: {
    outDir: path.resolve(__dirname, "static/dist"),
    emptyOutDir: true,
    minify: "esbuild",
    cssMinify: true,
    rollupOptions: {
      input: { app: path.resolve(__dirname, "frontend/main.js"), "home-facts": path.resolve(__dirname, "frontend/home-facts.js") },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames: (assetInfo) => {
          if (assetInfo.name && assetInfo.name.endsWith(".css")) {
            return "app.css";
          }
          return "assets/[name]-[hash][extname]";
        }
      }
    }
  }
});
