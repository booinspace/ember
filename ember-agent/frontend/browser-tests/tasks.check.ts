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

test("Delegated research keeps the durable task handle and cancellation", async ({
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
  await expect(page.locator('option[value="build"]')).toHaveCount(0);
  await expect(page.getByLabel("Local service port (optional)")).toHaveCount(0);
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

test("Build has a separate request, ZIP, receipt, and independently stoppable preview", async ({
  page,
}) => {
  const url = `http://build-${"a".repeat(32)}.localhost:49152/`;
  const modelText = '<a href="https://model.example.test/">Draft page</a>';
  const attempts: { idempotency_key: string; service_port?: number }[] = [];
  let completed = false;
  let dismissed = false;
  let stopped = false;
  let previewUrl = "https://model.example.test/";
  const status = () => ({
    task_id: id,
    state: completed ? "succeeded" : "running",
    operation: "build",
    build_artifact: completed,
    cancellation_requested: false,
    result: completed ? modelText : null,
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    switch (path) {
      case "/api/state":
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
            delegation_provider: "OpenCode",
            build_available: true,
          },
        });
      case "/api/tasks": {
        const payload = route.request().postDataJSON();
        expect(payload).toMatchObject({
          operation: "build",
          text: "Create a synthetic page",
        });
        attempts.push(payload);
        if (attempts.length < 3)
          return route.fulfill({ status: 409, json: { error: "test_retry" } });
        return route.fulfill({ status: 202, json: status() });
      }
      case `/api/tasks/${id}`:
        return route.fulfill({ json: status() });
      case "/api/task-notifications":
        return route.fulfill({
          json: {
            revision: 0,
            items: completed && !dismissed ? [status()] : [],
          },
        });
      case "/api/task-confirmations":
        return route.fulfill({ json: [] });
      case `/api/tasks/${id}/receipt`:
        return route.fulfill({
          json: {
            task_id: id,
            state: "succeeded",
            receipt: {
              tool: "opencode_build",
              outcome: "applied",
              code: null,
              data: { summary: modelText },
            },
          },
        });
      case `/api/tasks/${id}/build-artifact`:
        return route.fulfill({
          contentType: "application/zip",
          headers: {
            "Content-Disposition": "attachment; filename=ember-build.zip",
          },
          body: Buffer.from(
            "504b0506000000000000000000000000000000000000",
            "hex",
          ),
        });
      case `/api/tasks/${id}/acknowledge`:
        dismissed = true;
        return route.fulfill({ json: { accepted: true } });
      case "/api/tasks/build-service":
        return route.fulfill({
          json:
            completed && !stopped
              ? {
                  task_id: id,
                  url: previewUrl,
                  expires_at: Date.now() / 1000 + 120,
                }
              : null,
        });
      case `/api/tasks/${id}/stop-service`:
        expect(route.request().method()).toBe("POST");
        stopped = true;
        return route.fulfill({ json: { accepted: true } });
      default:
        return route.fulfill({ json: {} });
    }
  });
  await page.goto("http://127.0.0.1:4174/");
  await page.getByText("Tasks", { exact: true }).click();
  const operation = page.getByLabel("Operation");
  const port = page.getByLabel("Local service port (optional)");
  const queue = page.getByRole("button", { name: "Queue task" });
  await operation.selectOption("delegate");
  await expect(port).toHaveCount(0);
  await operation.selectOption("build");
  await page.getByLabel("Build request").fill("Create a synthetic page");
  await queue.click();
  await expect(page.getByText("Task submission failed.")).toBeVisible();
  await queue.click();
  await expect.poll(() => attempts.length).toBe(2);
  await expect(page.getByText("Task submission failed.")).toBeVisible();
  await port.fill("1023");
  await expect(queue).toBeDisabled();
  await port.fill("65536");
  await expect(queue).toBeDisabled();
  await port.fill("3210");
  await queue.click();
  await expect(page.getByText("Build: running")).toBeVisible();
  expect(attempts[0]).not.toHaveProperty("service_port");
  expect(attempts[1]).toEqual(attempts[0]);
  expect(attempts[2]?.idempotency_key).not.toBe(attempts[0]?.idempotency_key);
  expect(attempts[2]?.service_port).toBe(3210);

  completed = true;
  await expect(
    page.getByRole("heading", { name: "Build: succeeded" }),
  ).toBeVisible();
  await expect(page.getByText(modelText, { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Draft page" })).toHaveCount(0);
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download ZIP" }).click();
  expect((await download).suggestedFilename()).toBe("ember-build.zip");
  await page.getByRole("button", { name: "View receipt" }).click();
  await expect(
    page.getByRole("region", { name: "Build receipt" }),
  ).toContainText("opencode_build");

  const service = page.getByRole("region", { name: "Active Build service" });
  await expect(
    service.getByText("Build preview URL unavailable."),
  ).toBeVisible();
  await expect(service.getByRole("link")).toHaveCount(0);
  previewUrl = url;
  await expect(service.getByText(/only .* Ember host/)).toBeVisible();
  await expect(service.getByRole("link")).toHaveAttribute("href", url);
  await expect(service.getByRole("link")).toHaveAttribute("rel", /noreferrer/);
  await page.getByRole("button", { name: "Dismiss" }).click();
  await expect(
    page.getByRole("heading", { name: "Build: succeeded" }),
  ).toHaveCount(0);
  await expect(service).toBeVisible();
  await service.getByRole("button", { name: "Stop service" }).click();
  await expect(service).toHaveCount(0);
  expect(stopped).toBe(true);
});
