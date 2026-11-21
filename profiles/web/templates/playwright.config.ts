import { defineConfig } from "@playwright/test";

const port = Number(process.env.PORT ?? "3000");

export default defineConfig({
  testDir: "tests/e2e",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
  },
  webServer: {
    command: "bun run dev",
    reuseExistingServer: true,
    url: `http://127.0.0.1:${port}`,
  },
});
