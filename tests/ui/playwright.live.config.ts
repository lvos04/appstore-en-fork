import { defineConfig } from '@playwright/test';

// Ontwerpreview tegen de draaiende dienst (statuspagina.live.ts). Alleen via review_live.py: dat script start de
// dienst, brengt hem per toestand in de juiste stand en start dan deze configuratie (één test, één worker).
export default defineConfig({
  testDir: '.',
  testMatch: /statuspagina\.live\.ts$/,
  workers: 1,
  retries: 0,
  timeout: 600_000,
  reporter: [['list']],
  use: {
    browserName: 'chromium',
    headless: true,
    locale: 'nl-NL',
    timezoneId: 'UTC',
    // DNS-rebinding nabootsen (contract 1.3 §3.7 punt 4): een publieke naam die naar de dienst wijst
    launchOptions: { args: ['--host-resolver-rules=MAP nas.fritz.box 127.0.0.1, MAP nas.local 127.0.0.1'] },
  },
});
