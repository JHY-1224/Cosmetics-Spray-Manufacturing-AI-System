import {defineConfig} from '@playwright/test';

const advanced = process.env.QUALITY_BROWSER_SUITE === 'advanced';
const learning = process.env.QUALITY_BROWSER_SUITE === 'learning';
const delivery = process.env.QUALITY_BROWSER_SUITE === 'delivery';
const reaudit = process.env.QUALITY_BROWSER_SUITE === 'reaudit';
const model = process.env.QUALITY_BROWSER_SUITE === 'model';

export default defineConfig({
  testDir: 'e2e',
  testMatch: model ? '**/model.spec.ts' : reaudit ? '**/industrial.spec.ts' : learning ? '**/learning.spec.ts' : delivery ? '**/delivery.spec.ts' : advanced ? '**/advanced.spec.ts' : '**/workflow.spec.ts',
  timeout: 90000,
  workers: 1,
  reporter: [
    ['list'],
    ['json', {outputFile: process.env.QUALITY_BROWSER_REPORT ?? (learning ? '../reports/browser-learning.json' : delivery ? '../reports/browser-delivery.json' : advanced ? '../reports/browser-advanced.json' : '../reports/browser-tests.json')}],
  ],
  use: {
    baseURL: advanced ? undefined : process.env.QUALITY_E2E_URL ?? 'http://127.0.0.1:8000',
    viewport: {width: 1440, height: advanced ? 1100 : 1000},
    timezoneId: advanced ? undefined : 'Asia/Shanghai',
    trace: advanced ? undefined : 'off',
    actionTimeout: 10000,
    launchOptions: {executablePath: process.env.QUALITY_BROWSER_PATH},
  },
  outputDir: process.env.QUALITY_BROWSER_OUTPUT ?? (advanced ? '../var/advanced-browser-results' : '../var/browser-results'),
});
