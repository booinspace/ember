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
  await page.getByRole("tab", { name: "Tasks", exact: true }).click();
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

test("Tasks show cancellable conversation-submitted work and deduplicate local submissions", async ({
  page,
}) => {
  let submitted = false;
  const fromChat = "22222222-2222-4222-8222-222222222222";
  const otherBuilds = Array.from(
    { length: 32 },
    (_, index) =>
      `${String(index + 1).padStart(8, "0")}-3333-4333-8333-333333333333`,
  );
  const cancelled = new Set<string>();
  const waiting = (taskId: string, operation: "build" | "delegate") => ({
    task_id: taskId,
    state: cancelled.has(taskId) ? "cancelled" : "waiting_for_realtime",
    cancellation_requested: cancelled.has(taskId),
    result: null,
    operation,
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
        json: waiting(id, "delegate"),
      });
    }
    if (path === "/api/task-notifications") {
      const tasks = [
        waiting(fromChat, "build"),
        ...otherBuilds.map((taskId) => waiting(taskId, "build")),
        ...(submitted ? [waiting(id, "delegate")] : []),
      ];
      return route.fulfill({
        json: {
          revision: 0,
          items: tasks.filter((item) => item.state === "cancelled"),
          active: tasks.filter((item) => item.state !== "cancelled"),
        },
      });
    }
    if (path === "/api/task-confirmations") return route.fulfill({ json: [] });
    if (path.startsWith("/api/tasks/") && path.endsWith("/cancel")) {
      cancelled.add(path.split("/")[3]!);
      return route.fulfill({ json: { accepted: true } });
    }
    if (path === "/api/tasks/" + id)
      return route.fulfill({ json: waiting(id, "delegate") });
    return route.fulfill({ json: {} });
  });
  await page.goto("http://127.0.0.1:4174/#activate=synthetic");
  await page.getByRole("tab", { name: "Tasks", exact: true }).click();
  await expect(page.locator('option[value="build"]')).toHaveCount(0);
  await expect(page.getByLabel("Local service port (optional)")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /^Cancel task: Build / }),
  ).toHaveCount(33);
  const fromChatRow = page.getByRole("group", { name: "Build task 22222222" });
  await expect(
    fromChatRow.getByText("Build: Waiting for inference access"),
  ).toBeVisible();
  await fromChatRow
    .getByRole("button", { name: "Cancel task: Build 22222222", exact: true })
    .click();
  await expect.poll(() => [...cancelled]).toEqual([fromChat]);
  await expect(
    page.getByRole("button", {
      name: "Cancel task: Build 00000032",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByLabel("Operation").selectOption("delegate");
  await page
    .getByLabel("Workspace inspection request")
    .fill("Find official search documentation");
  await page.getByRole("button", { name: "Queue task" }).click();
  await expect.poll(() => submitted).toBe(true);
  const cancel = page.getByRole("button", {
    name: "Cancel task: Workspace inspection 11111111",
    exact: true,
  });
  await expect(cancel).toHaveCount(1);
  await expect(
    page.getByText("Workspace inspection: Waiting for inference access"),
  ).toBeVisible();
  await cancel.click();
  await expect.poll(() => cancelled.has(id)).toBe(true);
  await expect(cancel).toHaveCount(0);
});

test("web research binds consent to the provider and revision and only links receipt citations", async ({
  page,
}) => {
  let available = false;
  let provider = "fixture-web";
  let label: string | null = null;
  const firstRevision = "a".repeat(64);
  const nextRevision = "b".repeat(64);
  let revision: string | null = firstRevision;
  let submitted = false;
  let invalid = false;
  const attempts: {
    idempotency_key: string;
    research_provider: string;
    research_revision: string;
  }[] = [];
  const result = {
    task_id: id,
    state: "succeeded",
    cancellation_requested: false,
    result:
      'Finding [S1]. <a href="https://example.invalid/invented">Invented</a>',
    operation: "research",
  };
  let showStaleTask = false;
  const staleTask = {
    ...result,
    task_id: "22222222-2222-4222-8222-222222222222",
    state: "failed",
    result: "research_provider_consent_required",
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
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
          transport: "http",
          text_available: true,
        },
      });
    if (path === "/api/text/connect")
      return route.fulfill({ json: { session_id: id } });
    if (path === "/api/capabilities")
      return route.fulfill({
        json: {
          capabilities: [
            {
              id: "harness.research",
              kind: "harness",
              name: "Public web research",
              provider,
              provider_label: label,
              binding_revision: revision,
              status: available ? "enabled" : "unavailable",
              operations: ["research"],
              reason: null,
              effect: "read",
            },
          ],
        },
      });
    if (path === "/api/tasks") {
      const payload = route.request().postDataJSON();
      expect(payload).toMatchObject({
        operation: "research",
        text: "Summarize public documentation.\nSearch: public documentation",
      });
      attempts.push(payload);
      if (
        payload.research_provider !== provider ||
        payload.research_revision !== revision
      )
        return route.fulfill({
          status: 409,
          json: { error: "research_provider_consent_required" },
        });
      if (attempts.length <= 3)
        return route.fulfill({ status: 409, json: { error: "test_retry" } });
      submitted = true;
      return route.fulfill({ json: { ...result, state: "queued" } });
    }
    if (path === `/api/tasks/${id}`) return route.fulfill({ json: result });
    if (path === "/api/task-notifications")
      return route.fulfill({
        json: {
          revision: showStaleTask ? 2 : 1,
          items: submitted
            ? showStaleTask
              ? [result, staleTask]
              : [result]
            : [],
        },
      });
    if (path === "/api/task-confirmations") return route.fulfill({ json: [] });
    if (path === `/api/tasks/${id}/receipt`)
      return route.fulfill({
        json: {
          task_id: id,
          state: "succeeded",
          receipt: {
            tool: "research",
            outcome: "succeeded",
            code: null,
            data: {
              sources: [
                {
                  id: "S1",
                  url: invalid
                    ? "javascript:alert(1)"
                    : "https://example.invalid/docs",
                  title: "Official docs",
                  provider: "fixture",
                  operation: "read",
                  retrieved_at: "2026-11-22T12:00:00Z",
                  truncated: true,
                },
                ...Array.from({ length: 11 }, (_, index) => ({
                  id: `S${index + 2}`,
                  url: "https://example.invalid/docs",
                  title: "📖".repeat(160),
                  provider: "fixture",
                  operation: "search",
                  retrieved_at: "2026-11-22T12:00:00Z",
                  truncated: false,
                })),
              ],
            },
          },
        },
      });
    return route.fulfill({ json: null });
  });
  await page.goto("http://127.0.0.1:4174/");
  const tasks = page.getByRole("tab", { name: "Tasks", exact: true });
  const chat = page.getByRole("tab", { name: "Conversation", exact: true });
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  const disconnect = page.getByRole("button", {
    name: "Disconnect",
    exact: true,
  });
  await expect(disconnect).toBeVisible();
  const openTasks = async () => {
    const capability = page.waitForResponse("**/api/capabilities");
    await tasks.click();
    await capability;
  };
  const researchOption = page.locator('option[value="research"]');
  label = "Fixture Search and Read";
  await openTasks();
  await expect(researchOption).toHaveCount(0);
  available = true;
  revision = null;
  await chat.click();
  await openTasks();
  await expect(researchOption).toHaveCount(0);
  revision = firstRevision;
  label = null;
  await chat.click();
  await openTasks();
  await expect(researchOption).toHaveCount(0);

  label = "Fixture Search and Read";
  await chat.click();
  await openTasks();
  await page.getByLabel("Operation").selectOption("research");
  const question = page.getByLabel("Public web research question");
  const text = "Summarize public documentation.\nSearch: public documentation";
  await question.fill(text);
  await expect(question).toHaveAccessibleDescription(
    /Use public information only\. The hosted service Fixture Search and Read receives your approved search queries and any public URLs read/,
  );
  const queue = page.getByRole("button", { name: "Queue task" });
  const submissionError = page.getByText("Task submission failed.");
  for (const count of [1, 2]) {
    await queue.click();
    await expect.poll(() => attempts.length).toBe(count);
    await expect(submissionError).toBeVisible();
  }
  expect(attempts[0]).toMatchObject({
    research_provider: "fixture-web",
    research_revision: firstRevision,
  });
  expect(attempts[1]).toEqual(attempts[0]);

  provider = "fixture-other";
  label = "Other Hosted Fixture";
  await chat.click();
  await expect(disconnect).toBeVisible();
  await openTasks();
  await expect(question).toHaveAccessibleDescription(/Other Hosted Fixture/);
  await queue.click();
  await expect.poll(() => attempts.length).toBe(3);
  await expect(submissionError).toBeVisible();
  expect(attempts[2]).toMatchObject({
    research_provider: "fixture-other",
    research_revision: firstRevision,
  });
  expect(attempts[2]?.idempotency_key).not.toBe(attempts[1]?.idempotency_key);

  revision = nextRevision;
  label = "Updated Hosted Fixture";
  const refreshed = page.waitForResponse("**/api/capabilities");
  await queue.click();
  await refreshed;
  await expect(page.getByRole("alert")).toContainText(
    "Review the current provider before choosing Queue task again.",
  );
  await expect(question).toHaveAccessibleDescription(/Updated Hosted Fixture/);
  await expect(question).toHaveValue(text);
  await expect(queue).toBeEnabled();
  expect(attempts).toHaveLength(4);
  expect(attempts[3]).toEqual(attempts[2]);

  await queue.click();
  await expect(page.getByText(result.result, { exact: true })).toBeVisible();
  expect(attempts).toHaveLength(5);
  expect(attempts[4]).toMatchObject({
    research_provider: "fixture-other",
    research_revision: nextRevision,
  });
  expect(attempts[4]?.idempotency_key).not.toBe(attempts[3]?.idempotency_key);
  await expect(page.getByRole("link", { name: "Invented" })).toHaveCount(0);
  const view = page.getByRole("button", { name: "View receipt" });
  await view.click();
  const receipt = page.getByRole("region", { name: "Web research receipt" });
  await expect(
    receipt.getByRole("link", { name: "Official docs" }),
  ).toHaveAttribute("href", "https://example.invalid/docs");
  await expect(
    receipt.getByRole("link", { name: "Official docs" }),
  ).toHaveAttribute("rel", /noreferrer/);
  await expect(receipt.getByRole("link")).toHaveCount(12);
  await expect(receipt).toContainText("[S1]");
  await expect(receipt).toContainText("[S12]");
  await expect(receipt).toContainText("Page excerpts · Truncated");
  invalid = true;
  await view.click();
  await expect(receipt).toContainText("No source data was retained");
  await expect(receipt.getByRole("link")).toHaveCount(0);

  showStaleTask = true;
  await expect(
    page.getByText(/Reopen Tasks to check the current provider/),
  ).toBeVisible();
  expect(attempts).toHaveLength(5);
});

test("Build has a separate request, ZIP, receipt, and independently stoppable preview", async ({
  page,
}) => {
  const url = `http://build-${"a".repeat(32)}.localhost:49152/`;
  const modelText = '<a href="https://model.example.test/">Draft page</a>';
  const researchWaiting = {
    task_id: "22222222-2222-4222-8222-222222222222",
    operation: "research",
    state: "waiting_for_realtime",
    cancellation_requested: false,
    result: null,
  };
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
            summary_tasks_available: true,
            delegation_available: true,
            delegation_provider: "OpenCode",
            build_available: true,
            transport: "http",
            text_available: true,
          },
        });
      case "/api/text/connect":
        return route.fulfill({ json: { session_id: id } });
      case "/api/capabilities":
        return route.fulfill({
          json: {
            capabilities: [
              {
                id: "harness.research",
                kind: "harness",
                name: "Public web research",
                provider: "fixture-web",
                provider_label: "Fixture Search and Read",
                binding_revision: "a".repeat(64),
                status: "enabled",
                operations: ["research"],
                reason: null,
                effect: "read",
              },
            ],
          },
        });
      case "/api/tasks": {
        const payload = route.request().postDataJSON();
        expect(payload).toMatchObject({
          operation: "build",
          text: "Create a synthetic page",
        });
        expect(payload).not.toHaveProperty("research_provider");
        expect(payload).not.toHaveProperty("research_revision");
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
            active: [researchWaiting],
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
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Disconnect", exact: true }),
  ).toBeEnabled();
  await page.getByRole("tab", { name: "Tasks", exact: true }).click();
  const operation = page.getByLabel("Operation");
  const port = page.getByLabel("Local service port (optional)");
  const queue = page.getByRole("button", { name: "Queue task" });
  await expect(operation.locator('option[value="research"]')).toBeEnabled();
  await expect(
    page.getByRole("group", { name: "Web research task 22222222" }),
  ).toContainText("Waiting for inference access");
  const sharedInference = page.getByText(
    "with foreground requests taking priority between steps.",
    { exact: false },
  );
  for (const modelTask of ["research", "summarize", "delegate", "build"]) {
    await operation.selectOption(modelTask);
    await expect(sharedInference).toBeVisible();
    if (modelTask !== "build") await expect(port).toHaveCount(0);
  }
  await expect(
    page.getByText(/disconnect.*(?:to run|starts after)/i),
  ).toHaveCount(0);
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
