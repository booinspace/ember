import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

const fixture = process.env.EMBER_VOICE_FIXTURE_DIR;
const isolatedBuild = process.env.EMBER_ISOLATED_FRONTEND === "1";
export default defineConfig({
  testDir: "browser-tests",
  testMatch: "*.check.ts",
  timeout: 180000,
  workers: 1,
  retries: 0,
  outputDir: "../../.local/browser-test-results",
  webServer: {
    command:
      "pnpm exec vite preview --host 127.0.0.1 --port 4174 --strictPort" +
      (isolatedBuild ? " --outDir ../../.local/mobile-ui-dist" : ""),
    url: "http://127.0.0.1:4174",
    reuseExistingServer: false,
    timeout: 20000,
  },
  use: {
    headless: true,
    channel: process.platform === "win32" ? "msedge" : undefined,
    launchOptions: {
      args: fixture
        ? [
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--use-file-for-fake-audio-capture=" +
              resolve(fixture, "question.wav") +
              "%noloop",
          ]
        : [],
    },
    trace: "off",
    screenshot: "off",
    video: "off",
  },
});
