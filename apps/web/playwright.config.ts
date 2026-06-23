import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testMatch: "**/*.spec.ts",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:3000",
    launchOptions: {
      executablePath: "/snap/bin/chromium",
      args: ["--no-sandbox", "--disable-gpu"],
    },
  },
  webServer: [
    {
      command:
        "cd ../api && API_HOST=127.0.0.1 API_PORT=8000 PYTHONPATH=src uv run python -m visit_agent.api.server",
      url: "http://127.0.0.1:8000/api/v1/integrations/health",
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: "pnpm exec next dev --hostname 127.0.0.1 --port 3000",
      url: "http://127.0.0.1:3000/dashboard",
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
