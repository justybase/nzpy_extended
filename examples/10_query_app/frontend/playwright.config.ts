import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.NZ_E2E_BASE_URL || 'http://127.0.0.1:8480';
const python = process.env.NZ_E2E_PYTHON || 'python3';

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  timeout: 60_000,
  fullyParallel: false,
  reporter: process.env.CI ? [['line'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL,
    ...devices['Desktop Chrome'],
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: process.env.NZ_E2E_START_SERVER ? {
    command: `${python} ../server.py`,
    url: `${baseURL}/`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  } : undefined,
});
