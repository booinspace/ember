import { expect, test } from "@playwright/test";

test("shared catalog inspects raw Markdown and binds enablement to exact package/revision", async ({
  page,
}) => {
  const id = "11111111-1111-4111-8111-111111111111";
  let enabled = false;
  let revision = "before";
  let reject = true;
  const summary = () => ({
    id: "shared/text-brief",
    name: "text-brief",
    description: "Create a faithful brief.",
    version: "1.0.0",
    source: "shared",
    content_sha256: "a".repeat(64),
    enabled,
    requirements: [],
    unavailable: [],
    diagnostics: [],
  });
  const catalog = () => ({
    provider: "fixture",
    revision,
    skills: [summary()],
    unavailable: null,
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/bootstrap")
      return route.fulfill({ json: { ready: true } });
    if (url.pathname === "/api/state")
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
        },
      });
    if (
      url.pathname === "/api/skills" ||
      url.pathname === "/api/skills/refresh"
    )
      return route.fulfill({ json: catalog() });
    if (url.pathname === "/api/skills/document") {
      expect(url.searchParams.get("identity")).toBe("shared/text-brief");
      return route.fulfill({
        json: {
          skill: summary(),
          markdown:
            "---\nname: text-brief\n---\n<script>window.injected=true</script>\nComplete procedure.",
          metadata: { metadata: { openclaw: { unknown: true } } },
          extensions: { openclaw: { unknown: true } },
          resources: [
            { path: "SKILL.md", sha256: "b".repeat(64), size_bytes: 100 },
          ],
        },
      });
    }
    if (url.pathname === "/api/skills/select") {
      expect(route.request().postDataJSON()).toEqual({
        id: "shared/text-brief",
        content_sha256: "a".repeat(64),
        enabled: !enabled,
        revision,
      });
      if (reject) {
        reject = false;
        return route.fulfill({
          status: 409,
          json: { error: "skills_revision_conflict" },
        });
      }
      enabled = !enabled;
      revision = "after";
      return route.fulfill({ json: catalog() });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto("http://127.0.0.1:4174/#activate=synthetic");
  await page.locator("#skills > summary").click();
  await page.getByLabel("Find skills", { exact: true }).fill("text-brief");
  await page.getByRole("button", { name: /text-brief/ }).click();
  await expect(
    page.getByRole("region", { name: "Selected skill" }).locator("pre").first(),
  ).toContainText("Complete procedure.");
  await expect(page.locator("#skills script")).toHaveCount(0);
  await page.getByRole("button", { name: "Enable skill", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Selection failed");
  await page
    .getByRole("button", { name: "Refresh skills", exact: true })
    .click();
  await page.getByRole("button", { name: /text-brief/ }).click();
  await page.getByRole("button", { name: "Enable skill", exact: true }).click();
  await page.getByRole("button", { name: /text-brief/ }).click();
  await expect(
    page.getByRole("button", { name: "Disable skill", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Use in conversation", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Message Ember", exact: true }),
  ).toHaveValue("Use shared/text-brief to ");
  await page
    .getByRole("button", { name: "Disable skill", exact: true })
    .click();
  await expect.poll(() => enabled).toBe(false);
});
