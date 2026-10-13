import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Browser regressions intercept API traffic. This server has no backend proxy,
// including during browser teardown when pending requests lose their mocks.
export default defineConfig({
  plugins: [react()],
  server: { strictPort: true },
});
