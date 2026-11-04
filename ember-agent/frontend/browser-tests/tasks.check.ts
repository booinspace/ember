import { expect, test } from "@playwright/test";

const id = "11111111-1111-4111-8111-111111111111";
test("draft approval shows exact text and binds the decision to its action hash", async ({
  page,
}) => {
  let submitted = false;
  let approved = false;
  const approval = {
    confirmation_id: id,
    action_sha256: "a".repeat(64),
    tool: "publish_draft",
    arguments: { draft: "Synthetic draft for review" },
    state: "pending",
  };
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
          tasks_available: true,
        },
      });
    if (path === "/api/tasks") {
      expect(route.request().postDataJSON()).toMatchObject({
        operation: "review",
        text: approval.arguments.draft,
      });
      submitted = true;
      return route.fulfill({
        json: {
          task_id: id,
          state: "waiting_for_user",
          cancellation_requested: false,
        },
      });
    }
    if (path === "/api/task-notifications")
      return route.fulfill({ json: { revision: 0, items: [] } });
    if (path === "/api/task-confirmations")
      return route.fulfill({ json: submitted && !approved ? [approval] : [] });
    if (path === "/api/task-confirmations/" + id) {
      expect(route.request().postDataJSON()).toEqual({
        action_sha256: approval.action_sha256,
        approve: true,
      });
      approved = true;
      return route.fulfill({ json: { ...approval, state: "approved" } });
    }
    if (path === "/api/tasks/" + id)
      return route.fulfill({
        json: {
          task_id: id,
          state: approved ? "succeeded" : "waiting_for_user",
          cancellation_requested: false,
          result: approved ? approval.arguments.draft : null,
          operation: "review",
        },
      });
    return route.fulfill({ json: {} });
  });
  await page.goto("http://127.0.0.1:4174/#activate=synthetic");
  await page.getByText("Tasks", { exact: true }).click();
  await page.getByLabel("Operation").selectOption("review");
  await page.getByLabel("Text to inspect").fill(approval.arguments.draft);
  await page.getByRole("button", { name: "Queue task" }).click();
  await expect(
    page.getByText('"draft": "Synthetic draft for review"', { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect.poll(() => approved).toBe(true);
  await expect(
    page.getByRole("button", { name: "Approve", exact: true }),
  ).toHaveCount(0);
});

test("Hermes research keeps the durable task handle and cancellation", async ({
  page,
}) => {
  let submitted = false;
  let cancelled = false;
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
            import_mode: "none",
            included_libraries: [],
            excluded_libraries: [],
            revision: 0,
          },
          capture: { enabled: false, revision: 0 },
          deep: "available",
          tasks_available: true,
          delegation_available: true,
        },
      });
    if (path === "/api/tasks") {
      expect(route.request().postDataJSON()).toMatchObject({
        operation: "delegate",
        text: "Find official search documentation",
      });
      submitted = true;
      return route.fulfill({
        json: {
          task_id: id,
          state: "waiting_for_inference",
          cancellation_requested: false,
        },
      });
    }
    if (path === "/api/task-notifications")
      return route.fulfill({ json: { revision: 0, items: [] } });
    if (path === "/api/task-confirmations") return route.fulfill({ json: [] });
    if (path === "/api/tasks/" + id + "/cancel") {
      cancelled = true;
      return route.fulfill({ json: { accepted: true } });
    }
    if (path === "/api/tasks/" + id)
      return route.fulfill({
        json: {
          task_id: id,
          state: cancelled ? "cancelled" : "waiting_for_inference",
          cancellation_requested: cancelled,
          result: null,
          operation: "delegate",
        },
      });
    return route.fulfill({ json: {} });
  });
  await page.goto("http://127.0.0.1:4174/#activate=synthetic");
  await page.getByText("Tasks", { exact: true }).click();
  await page.getByLabel("Operation").selectOption("delegate");
  await page
    .getByLabel("Research request")
    .fill("Find official search documentation");
  await page.getByRole("button", { name: "Queue task" }).click();
  await expect.poll(() => submitted).toBe(true);
  await expect(page.getByRole("button", { name: "Cancel task" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel task" }).click();
  await expect.poll(() => cancelled).toBe(true);
  await expect(page.getByRole("button", { name: "Cancel task" })).toHaveCount(
    0,
  );
});
