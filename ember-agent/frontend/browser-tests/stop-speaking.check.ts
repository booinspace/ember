import { expect, test } from "@playwright/test";

test("Stop speaking remains usable during a reply and preserves chat and microphone", async ({
  page,
}) => {
  const id = "11111111-1111-4111-8111-111111111111";
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/bootstrap")
      return route.fulfill({ json: { ready: true } });
    if (path === "/api/state")
      return route.fulfill({
        json: {
          binding: {
            character_id: id,
            instance_id: id,
            room: "synthetic",
            endpoint: "ws://127.0.0.1:7880",
          },
          context_id: id,
          recall: {
            local_enabled: true,
            import_mode: "all",
            included_libraries: [],
            excluded_libraries: [],
            revision: 0,
          },
          capture: { enabled: false, revision: 0 },
          deep: "available",
          voice: true,
        },
      });
    if (path === "/api/turn-details")
      return route.fulfill({ json: { turn: null } });
    return route.fulfill({ json: {} });
  });
  // The UI is real; only the room transport is synthetic. Socket/audio ownership
  // and exact RPC targets are covered by the connection tests.
  await page.route("**/assets/connection-*.js", (route) =>
    route.fulfill({
      contentType: "application/javascript",
      body: `export class CharacterConnection {
      constructor(text, failure, disconnected, changed) {
        this.text = text; this.changed = changed;
        window.syntheticVoice = { microphone: false, stops: 0, disconnects: 0 };
        window.addEventListener("synthetic-task-notice", () => {
          this.notice = true;
          this.changed("33333333-3333-4333-8333-333333333333");
        });
      }
      async connect() {}
      async microphone(value) { window.syntheticVoice.microphone = value; }
      async ask() {
        this.changed("22222222-2222-4222-8222-222222222222");
        this.text("The full visible answer stays here.", "reply", "assistant");
        await new Promise(resolve => this.done = resolve);
        return 0;
      }
      async stopSpeaking() {
        window.syntheticVoice.stops++;
        await new Promise(resolve => setTimeout(resolve, 100));
        this.changed(null); if (!this.notice) this.done();
      }
      async close() { window.syntheticVoice.disconnects++; }
    }`,
    }),
  );
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("http://127.0.0.1:4174/#activate=synthetic");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await page
    .getByRole("button", { name: "Use microphone", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Message Ember" })
    .fill("Keep my original question.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeDisabled();
  const stop = page.getByRole("button", { name: "Stop speaking", exact: true });
  await expect(stop).toBeEnabled();
  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Mute microphone", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(stop).toBeEnabled();
  await stop.click();
  const stopping = page.getByRole("button", {
    name: "Stopping speech…",
    exact: true,
  });
  await expect(stopping).toBeDisabled();
  await expect(stopping).toHaveCount(0);
  await expect(stop).toHaveCount(0);
  await page.getByRole("tab", { name: "Conversation", exact: true }).click();
  await expect(page.locator(".turn.user")).toContainText(
    "Keep my original question.",
  );
  await expect(page.locator(".turn.assistant")).toContainText(
    "The full visible answer stays here.",
  );
  await expect(page.locator("header .status")).toHaveText("Connected");
  await expect(
    page.getByRole("button", { name: "Mute microphone", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(
    await page.evaluate(
      () => (window as unknown as { syntheticVoice: object }).syntheticVoice,
    ),
  ).toEqual({ microphone: true, stops: 1, disconnects: 0 });
  await page.evaluate(() =>
    window.dispatchEvent(new Event("synthetic-task-notice")),
  );
  await expect(stop).toBeEnabled();
  await stop.click();
  await expect(stop).toBeDisabled();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (
            window as unknown as {
              syntheticVoice: { stops: number };
            }
          ).syntheticVoice.stops,
      ),
    )
    .toBe(2);
  await expect(page.locator(".turn.assistant")).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Mute microphone", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(375);
  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
});
