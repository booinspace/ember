import { expect, test, type Page } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { z } from "zod";
import { runtimeState } from "../src/contracts";

const directory = process.env.EMBER_VOICE_FIXTURE_DIR;
const fixtureSchema = z.object({
  version: z.literal("ember.synthetic-voice-test@0.1.0"),
  character_id: z.string().uuid(),
  expected_answer: z.literal("cobalt"),
});

type ObservedWindow = Window & { emberPeers: RTCPeerConnection[] };

async function openFixture(page: Page) {
  if (!directory)
    throw new Error("Explicit synthetic fixture directory is required.");
  const fixture = fixtureSchema.parse(
    JSON.parse(await readFile(resolve(directory, "fixture.json"), "utf8")),
  );
  const activation = z
    .object({ url: z.string().url() })
    .parse(
      JSON.parse(
        await readFile(
          resolve(directory, "runtime/browser-activation.json"),
          "utf8",
        ),
      ),
    );
  await page.addInitScript(() => {
    const peers: RTCPeerConnection[] = [];
    const original = window.RTCPeerConnection;
    window.RTCPeerConnection = class extends original {
      constructor(...args: ConstructorParameters<typeof RTCPeerConnection>) {
        super(...args);
        peers.push(this);
      }
    };
    (window as unknown as ObservedWindow).emberPeers = peers;
  });
  await page.goto(activation.url);
  await expect(
    page.getByRole("button", { name: "Connect", exact: true }),
  ).toBeEnabled();
  const response = await page.request.get(
    new URL("/api/state", activation.url).href,
  );
  const state = runtimeState.parse(await response.json());
  // Fail before sending anything if this is a real/private character or wrong fixture.
  expect(state.binding.character_id).toBe(fixture.character_id);
  expect(state.libraries).toHaveLength(0);
  expect(state.voice).toBe(true);
  const origin = new URL(activation.url).origin;
  const capture = await page.request.post(origin + "/api/capture", {
    data: { enabled: false, revision: state.capture.revision },
    headers: { origin },
  });
  expect(capture.ok()).toBe(true);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.locator(".status")).toHaveText("Connected", {
    timeout: 45000,
  });
  return fixture;
}

async function audioBytes(page: Page): Promise<number> {
  return page.evaluate(async () => {
    let bytes = 0;
    for (const peer of (window as unknown as ObservedWindow).emberPeers) {
      const stats = await peer.getStats();
      stats.forEach((value: unknown) => {
        if (
          typeof value === "object" &&
          value !== null &&
          "type" in value &&
          value.type === "inbound-rtp" &&
          "kind" in value &&
          value.kind === "audio" &&
          "bytesReceived" in value &&
          typeof value.bytesReceived === "number"
        ) {
          bytes += value.bytesReceived;
        }
      });
    }
    return bytes;
  });
}

async function receipt(kind: string, elapsed: number, bytes: number) {
  if (!directory) throw new Error("Fixture disappeared.");
  await writeFile(
    resolve(directory, kind + "-result.json"),
    JSON.stringify(
      {
        scenario: kind,
        corpus: "synthetic_one_accepted_memory",
        browser: "installed_browser",
        local_providers: true,
        visible_answer_ms: Math.round(elapsed),
        inbound_audio_bytes: bytes,
        supported_answer: true,
        physical_microphone_tested: false,
      },
      null,
      2,
    ) + "\n",
  );
}

test.describe.serial("installed speech and browser recall", () => {
  test.skip(
    !directory,
    "Requires an explicitly prepared synthetic voice fixture and running runtime.",
  );
  test("text connects, recalls the accepted memory, and receives spoken output", async ({
    page,
  }) => {
    const fixture = await openFixture(page);
    const before = await audioBytes(page);
    await page.locator("#prompt").fill("What is my favorite color?");
    await expect(
      page.getByRole("button", { name: "Send", exact: true }),
    ).toBeEnabled();
    const started = performance.now();
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.locator(".turn.assistant p")).toContainText(
      fixture.expected_answer,
      { timeout: 150000 },
    );
    const visible = performance.now() - started;
    await expect
      .poll(() => audioBytes(page), { timeout: 30000 })
      .toBeGreaterThan(before + 500);
    await expect(
      page.getByRole("button", { name: "Disconnect", exact: true }),
    ).toBeEnabled({ timeout: 30000 });
    await receipt(
      "text_recall_speech",
      visible,
      (await audioBytes(page)) - before,
    );
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  });
  test("microphone speech becomes a memory-backed Qwen answer and streamed audio", async ({
    page,
  }) => {
    const fixture = await openFixture(page);
    const before = await audioBytes(page);
    await expect(
      page.getByRole("button", { name: "Use microphone", exact: true }),
    ).toBeEnabled();
    const started = performance.now();
    await page
      .getByRole("button", { name: "Use microphone", exact: true })
      .click();
    await expect(page.locator(".turn.user p")).toContainText("color", {
      timeout: 60000,
    });
    await expect(page.locator(".turn.assistant p")).toContainText(
      fixture.expected_answer,
      { timeout: 150000 },
    );
    const visible = performance.now() - started;
    await expect
      .poll(() => audioBytes(page), { timeout: 30000 })
      .toBeGreaterThan(before + 500);
    await page
      .getByRole("button", { name: "Mute microphone", exact: true })
      .click();
    await receipt(
      "voice_recall_speech",
      visible,
      (await audioBytes(page)) - before,
    );
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  });
});
