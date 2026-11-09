import { defineConfig } from '@playwright/test';

// Statuspagina-UI-tests (ONTWERPTAAL §12/§13.2). Draait alleen Chromium; de pagina komt uit de echte app
// (genereer_toestanden.py) en wordt met dezelfde headers (CSP) aan de browser gegeven.
export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: true,
  workers: 2, // geheugen is de grens op de bouwmachine
  retries: 0,
  timeout: 180_000,
  reporter: [['list']],
  use: { browserName: 'chromium', headless: true, locale: 'nl-NL', timezoneId: 'UTC' },
});
