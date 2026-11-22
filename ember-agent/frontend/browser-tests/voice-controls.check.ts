import { expect, test } from "@playwright/test";

const id = "11111111-1111-4111-8111-111111111111";

test("voice and speed controls apply confirmed settings and preserve failed edits", async ({
  page,
}) => {
  let selection = { voice: "default", speed: 1 };
  let reject = false;
  let requests = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const speech = () => ({
      model: "gradium/phonon-31m-en",
      voices: ["default", "Juniper"],
      selection,
    });
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
          speech: speech(),
        },
      });
    if (path === "/api/speech") {
      requests++;
      if (reject)
        return route.fulfill({
          status: 409,
          json: { error: "speech_settings_busy" },
        });
      const value: unknown = route.request().postDataJSON();
      if (
        typeof value !== "object" ||
        value === null ||
        !("voice" in value) ||
        !("speed" in value) ||
        typeof value.voice !== "string" ||
        typeof value.speed !== "number"
      )
        throw new Error("Invalid synthetic selection");
      selection = { voice: value.voice, speed: value.speed };
      return route.fulfill({ json: speech() });
    }
    return route.fulfill({ json: {} });
  });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("http://127.0.0.1:4174/#activate=synthetic");
  await page.getByRole("tab", { name: "Memory & Settings" }).click();
  await page.getByText("Voice and speaking speed", { exact: true }).click();
  await expect(page.getByLabel("Voice", { exact: true })).toHaveValue(
    "default",
  );
  await expect(
    page.getByRole("button", { name: "Apply voice" }),
  ).toBeDisabled();
  await page.getByLabel("Voice", { exact: true }).selectOption("Juniper");
  await page.getByLabel("Speaking speed:").fill("1.25");
  await page.getByRole("button", { name: "Apply voice" }).click();
  await expect(
    page.getByRole("button", { name: "Apply voice" }),
  ).toBeDisabled();
  expect(selection).toEqual({ voice: "Juniper", speed: 1.25 });
  reject = true;
  await page.getByLabel("Voice", { exact: true }).selectOption("default");
  await page.getByRole("button", { name: "Apply voice" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Voice settings were not changed",
  );
  expect(selection).toEqual({ voice: "Juniper", speed: 1.25 });
  expect(requests).toBe(2);
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(375);
});
