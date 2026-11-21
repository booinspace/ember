import { expect, test } from "@playwright/test";

const memory = "11111111-1111-4111-8111-111111111111";
const entity = "22222222-2222-4222-8222-222222222222";
const next = "33333333-3333-4333-8333-333333333333";
const cursor = "1762000000:" + memory;
const firstPage = {
  nodes: [
    {
      id: memory,
      category: "memory",
      kind: "episode",
      label: "Visited the synthetic lab",
      modality: "FACTUAL_ASSERTION",
      recorded_at: "2026-10-21T09:00:00Z",
    },
    {
      id: entity,
      category: "entity",
      kind: "entity",
      label: "Synthetic lab",
      modality: null,
      recorded_at: null,
    },
  ],
  links: [{ source: memory, target: entity, kind: "entity" }],
  next_cursor: cursor,
  truncated: false,
};

test("browse, inspect, navigate and filter the memory graph", async ({
  page,
}) => {
  test.setTimeout(20_000);
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let payload: unknown = {};
    if (path === "/api/state") {
      payload = {
        binding: {
          character_id: memory,
          instance_id: memory,
          room: "synthetic",
          endpoint: "ws://127.0.0.1:7880",
        },
        context_id: memory,
        recall: {
          local_enabled: true,
          import_mode: "all",
          included_libraries: [],
          excluded_libraries: [],
          revision: 0,
        },
        capture: { enabled: false, revision: 0 },
        deep: "available",
      };
    } else if (path === "/api/memory-graph") {
      expect(route.request().method()).toBe("POST");
      const request = route.request().postDataJSON();
      if (request.text === "missing") {
        payload = { nodes: [], links: [], next_cursor: null, truncated: false };
      } else if (request.cursor === cursor) {
        payload = {
          nodes: [{ ...firstPage.nodes[0], id: next, label: "Older memory" }],
          links: [],
          next_cursor: null,
          truncated: false,
        };
      } else if (request.anchor === memory) {
        payload = { ...firstPage, next_cursor: null };
      } else {
        payload = firstPage;
      }
    }
    await route.fulfill({ json: payload });
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("http://127.0.0.1:4174/");
  await page.getByRole("link", { name: "Memory graph" }).click();
  await expect(page).toHaveURL(/\/memory-graph$/);
  await expect(
    page.getByRole("heading", { name: "Memory graph" }),
  ).toBeVisible();
  await expect(page.getByText("1 memory · 1 link")).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  const svg = page.locator("svg[aria-label='Memory and entity connections']");
  const original = await svg.getAttribute("viewBox");
  await page.getByRole("button", { name: "Zoom in" }).click();
  await expect(svg).not.toHaveAttribute("viewBox", original ?? "");
  await page.getByRole("button", { name: "Reset view" }).click();
  await expect(svg).toHaveAttribute("viewBox", original ?? "");

  await page.getByText("Nodes on this page").click();
  await page.getByRole("button", { name: /Visited the synthetic lab/ }).click();
  await expect(
    page.getByText("factual assertion", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Explore neighborhood" }).click();
  await expect(
    page.getByRole("button", { name: "Exit neighborhood" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Visible links" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Exit neighborhood" }).click();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(
    svg.locator("title").filter({ hasText: "episode: Older memory" }),
  ).toBeAttached();
  await page.getByRole("button", { name: "Previous" }).click();
  await expect(
    svg
      .locator("title")
      .filter({ hasText: "episode: Visited the synthetic lab" }),
  ).toBeAttached();

  await page.getByRole("textbox", { name: "Search memories" }).fill("missing");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByText("No memories match this view.")).toBeVisible();
});
