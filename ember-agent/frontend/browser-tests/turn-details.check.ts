import { expect, test, type Page } from "@playwright/test";
import { resolve } from "node:path";

const id = "11111111-1111-4111-8111-111111111111";

async function fixture(page: Page, replyDelay = 0) {
  const sent: string[] = [];
  let thinking = false;
  let current: object | null = null;
  const state = {
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
    transport: "http",
    text_available: true,
    thinking_available: true,
    thinking_enabled: false,
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let result: unknown = {};
    if (path === "/api/bootstrap") result = { ready: true };
    else if (path === "/api/state") result = state;
    else if (path === "/api/thinking") {
      thinking = (route.request().postDataJSON() as { enabled: boolean })
        .enabled;
      result = { enabled: thinking };
    } else if (path === "/api/turn-details") result = { turn: current };
    else if (path === "/api/text/connect") result = { session_id: id };
    else if (path === "/api/text/turn") {
      const body = route.request().postDataJSON() as {
        request_id: string;
        text: string;
      };
      sent.push(body.text);
      current = {
        request_id: body.request_id,
        context_id: id,
        stage: "thinking",
        thinking_enabled: thinking,
        thinking: thinking
          ? "SYNTHETIC <script>window.injected=true</script>"
          : "",
        thinking_truncated: false,
        memories_truncated: false,
        memories: [
          {
            category: "recall",
            data: { value: "Milo is a synthetic plushie." },
          },
        ],
      };
      if (replyDelay) await new Promise((done) => setTimeout(done, replyDelay));
      current = { ...current, stage: "complete" };
      result = {
        request_id: body.request_id,
        text: "A synthetic reply. ".repeat(50),
        retained_memories: 1,
      };
    } else if (path === "/api/text/acknowledge") result = { delivered: true };
    else result = { connected: path.endsWith("heartbeat") };
    await route.fulfill({ status: 200, json: result });
  });
  await page.goto("http://127.0.0.1:4174/#activate=synthetic");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.locator(".status")).toHaveText("Connected");
  return { sent, thinking: () => thinking };
}

test("Enter sends once, Shift+Enter adds a newline, IME/repeat do not submit", async ({
  page,
}) => {
  const observed = await fixture(page, 200);
  const prompt = page.getByRole("textbox", { name: "Message Ember" });
  await prompt.fill("Line one");
  await prompt.press("Shift+Enter");
  await prompt.pressSequentially("Line two");
  expect(await prompt.inputValue()).toBe("Line one\nLine two");
  await prompt.dispatchEvent("keydown", {
    key: "Enter",
    code: "Enter",
    isComposing: true,
    keyCode: 229,
  });
  await prompt.dispatchEvent("keydown", {
    key: "Enter",
    code: "Enter",
    repeat: true,
  });
  expect(observed.sent).toHaveLength(0);
  await prompt.press("Enter");
  await expect.poll(() => observed.sent.length).toBe(1);
  expect(observed.sent[0]).toBe("Line one\nLine two");
  await expect(prompt).toHaveValue("");
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
});

test("thinking and exact recalled values expand safely while the reply is running", async ({
  page,
}) => {
  const observed = await fixture(page, 1000);
  await page.getByRole("checkbox", { name: "Thinking", exact: true }).check();
  await expect.poll(observed.thinking).toBe(true);
  await page
    .getByRole("textbox", { name: "Message Ember" })
    .fill("Tell me about Milo");
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeEnabled();
  await page.getByRole("textbox", { name: "Message Ember" }).press("Enter");
  await expect(page.getByText("Thinking…", { exact: true })).toBeVisible();
  await page
    .locator("summary")
    .filter({ hasText: /^Thinking/ })
    .click();
  await expect(page.locator(".thinking-text")).toContainText(
    "SYNTHETIC <script>",
  );
  await page.getByText("Recalled memories (1)", { exact: true }).click();
  await expect(page.locator(".memory-value")).toHaveText(
    /Milo is a synthetic plushie/,
  );
  expect(await page.evaluate(() => "injected" in window)).toBe(false);
  await expect(page.getByText("Reply details", { exact: true })).toBeVisible();
  await page.screenshot({
    path: resolve("../../.local/browser-ui-turn-details.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  await expect(page.getByRole("region", { name: "Reply details" })).toHaveCount(
    0,
  );
});

test("each sent message scrolls down even after reading older messages", async ({
  page,
}) => {
  await fixture(page);
  const prompt = page.getByRole("textbox", { name: "Message Ember" });
  for (let index = 0; index < 6; index += 1) {
    await prompt.fill("Synthetic message " + index);
    await prompt.press("Enter");
    await expect(page.locator(".turn.assistant")).toHaveCount(index + 1);
  }
  const messages = page.locator(".messages");
  await messages.evaluate((element) => {
    element.scrollTop = 0;
    element.dispatchEvent(new Event("scroll"));
  });
  await prompt.fill("Bring the latest message into view");
  await prompt.press("Enter");
  await expect(page.locator(".turn.assistant")).toHaveCount(7);
  await expect
    .poll(() =>
      messages.evaluate(
        (element) =>
          element.scrollHeight - element.scrollTop - element.clientHeight,
      ),
    )
    .toBeLessThan(3);
  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
});
