import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

const fixture = process.env.EMBER_VOICE_FIXTURE_DIR;
export default defineConfig({
  testDir: "browser-tests",
  testMatch: "*.check.ts",
  timeout: 180000,
  workers: 1,
  retries: 0,
  outputDir: "../../.local/browser-test-results",
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
