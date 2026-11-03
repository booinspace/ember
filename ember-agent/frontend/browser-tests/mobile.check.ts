import { expect, test, type Page } from "@playwright/test";
import { resolve } from "node:path";

const id = "11111111-1111-4111-8111-111111111111";

async function mockRuntime(page: Page): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let result: unknown = {};
    if (path === "/api/bootstrap") result = { ready: true };
    else if (path === "/api/state") {
      result = {
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
        capture: { enabled: true, revision: 0 },
        deep: "available",
        lan_available: true,
        tasks_available: true,
        transport: "http",
        text_available: true,
      };
    } else if (path === "/api/text/connect") result = { session_id: id };
    else if (path === "/api/text/turn") {
      const body = route.request().postDataJSON() as { request_id: string };
      result = {
        request_id: body.request_id,
        text: "Synthetic reply " + "long-unbroken-content".repeat(30),
        retained_memories: 0,
      };
    } else if (path === "/api/text/acknowledge") result = { delivered: true };
    else if (path === "/api/turn-details") result = { turn: null };
    else if (path === "/api/lan-link") {
      result = { url: "https://example.invalid/#activate=" + "x".repeat(96) };
    } else result = { connected: path.endsWith("heartbeat") };
    await route.fulfill({ status: 200, json: result });
  });
}

for (const { width, height } of [
  { width: 320, height: 568 },
  { width: 375, height: 812 },
  { width: 568, height: 320 },
  { width: 768, height: 700 },
  { width: 1280, height: 700 },
]) {
  test(`conversation and controls remain usable at ${width}x${height}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height });
    await mockRuntime(page);
    await page.goto("http://127.0.0.1:4174/#activate=synthetic");
    const connect = page.getByRole("button", { name: "Connect", exact: true });
    await expect(connect).toBeEnabled();
    await connect.click();
    await expect(page.locator("header .status")).toHaveText("Connected");
    const prompt = page.getByRole("textbox", { name: "Message Ember" });
    await prompt.fill("Synthetic mobile message");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.locator(".turn.assistant")).toContainText(
      "Synthetic reply",
    );
    await expect(
      page.getByRole("complementary", { name: "Memory controls" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Use microphone" }),
    ).toHaveAttribute("aria-pressed", "false");
    await page.getByRole("button", { name: "Copy LAN link" }).click();
    await expect(
      page.getByRole("textbox", { name: "Private LAN access link" }),
    ).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(width);
    if (width <= 520) {
      const button = await page
        .getByRole("button", { name: "Send", exact: true })
        .boundingBox();
      expect(button?.height).toBeGreaterThanOrEqual(44);
      const composer = await page.locator(".composer").boundingBox();
      expect(composer?.width).toBeLessThanOrEqual(width);
    }
    if (width === 375) {
      await page.screenshot({
        path: resolve("../../.local/browser-ui-mobile.png"),
        fullPage: true,
      });
    }
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  });
}
