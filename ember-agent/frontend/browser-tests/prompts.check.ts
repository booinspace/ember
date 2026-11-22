import { expect, test } from "@playwright/test";
import { z } from "zod";
const id = "11111111-1111-4111-8111-111111111111";
const edit = z.object({
  kind: z.enum(["system", "personality"]),
  markdown: z.string(),
  system_revision: z.string(),
  personality_revision: z.string(),
});
test("Markdown preview is safe and failed saves retain scoped drafts", async ({
  page,
}) => {
  let reads = 0;
  let rejection = "prompt_settings_busy";
  let revision = "a".repeat(64);
  const stored = {
    system: "Shared synthetic rules.",
    personality: "Synthetic character.",
  };
  const snapshot = (system: string, personality: string) => ({
    instructions: system + "\n" + personality,
    revision,
    cost: 640,
    limit: 4096,
  });
  const documents = () => ({
    character_id: id,
    instruction_limit: 4096,
    applied_revision: null,
    error: null,
    system: { markdown: stored.system, revision, error: null },
    personality: { markdown: stored.personality, revision, error: null },
    snapshot: snapshot(stored.system, stored.personality),
  });
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
          prompts_available: true,
        },
      });
    if (path === "/api/prompts") {
      reads++;
      return route.fulfill({ json: documents() });
    }
    if (path === "/api/prompts/preview") {
      const value = edit.parse(route.request().postDataJSON());
      const pair = { ...stored, [value.kind]: value.markdown };
      return route.fulfill({
        json: {
          ...documents(),
          snapshot: snapshot(pair.system, pair.personality),
        },
      });
    }
    if (path.startsWith("/api/prompts/")) {
      expect(route.request().method()).toBe("PUT");
      const value = edit.parse(route.request().postDataJSON());
      if (rejection)
        return route.fulfill({ status: 409, json: { error: rejection } });
      expect(value.system_revision).toBe(revision);
      expect(value.personality_revision).toBe(revision);
      stored[value.kind] = value.markdown;
      revision = "b".repeat(64);
      return route.fulfill({ json: documents() });
    }
    return route.fulfill({ json: {} });
  });
  await page.setViewportSize({ width: 320, height: 568 });
  await page.goto("http://127.0.0.1:4174/#activate=synthetic");
  await expect(
    page.getByRole("button", { name: "Connect", exact: true }),
  ).toBeEnabled();
  expect(reads).toBe(0);
  await page.getByRole("tab", { name: "Prompts", exact: true }).click();
  const source = page.getByLabel("This character Markdown");
  await expect(source).toHaveValue(stored.personality);
  const draft = "# Style\nLiteral <script>window.promptInjected=true</script>";
  await source.fill(draft);
  await page.getByRole("tab", { name: "Memory sources", exact: true }).click();
  await expect(source).toBeHidden();
  await page.getByRole("tab", { name: "Prompts", exact: true }).click();
  await expect(source).toHaveValue(draft);
  expect(reads).toBe(1);
  await expect(page.getByRole("tablist")).toHaveCount(1);
  await expect(page.getByRole("tabpanel")).toHaveCount(1);
  await page.getByRole("button", { name: "Preview draft" }).click();
  await page
    .getByText("Effective system instruction (preview)", { exact: true })
    .click();
  await expect(page.locator(".prompt-editor pre")).toContainText(draft);
  expect(stored.personality).toBe("Synthetic character.");
  expect(await page.evaluate(() => "promptInjected" in window)).toBe(false);
  await page.getByRole("button", { name: "Save prompt" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Wait for the current reply",
  );
  await expect(source).toHaveValue(draft);
  rejection = "prompt_revision_conflict";
  await page.getByRole("button", { name: "Save prompt" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "A saved document changed",
  );
  await page.getByRole("button", { name: "Reload saved revisions" }).click();
  await expect(page.getByRole("button", { name: "Save prompt" })).toBeEnabled();
  await expect(source).toHaveValue(draft);
  rejection = "";
  await page.getByRole("button", { name: "Save prompt" }).click();
  await expect(
    page.getByText("Saved. This version will be loaded for the next message."),
  ).toBeVisible();
  expect(stored.personality).toBe(draft);
  await page.getByLabel("Prompt scope").selectOption("system");
  await expect(
    page.getByText("Affects all characters using this shared system file."),
  ).toBeVisible();
  await page.getByLabel("Shared system Markdown").fill("Unsaved shared draft");
  await page.getByRole("button", { name: "Discard draft" }).click();
  await expect(page.getByLabel("Shared system Markdown")).toHaveValue(
    stored.system,
  );
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(320);
});
