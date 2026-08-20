import { defineConfig } from "@playwright/test";

// E2E against the running dev stack (api + worker + `npm run dev` — see README).
// Run: npm run test:e2e
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
