import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  reporter: process.env.CI ? 'github' : 'line',
  use: { baseURL: process.env.BASE_URL ?? '{{WEB_URL}}', trace: 'retain-on-failure' },
  webServer: [
    {
      // {{API_SERVER_NOTE}}
      command: '{{API_SERVER_CMD}}',
      url: process.env.API_URL ?? '{{API_HEALTH_URL}}',
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
    },
    {
      command: '{{WEB_SERVER_CMD}}',
      url: process.env.BASE_URL ?? '{{WEB_URL}}',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
