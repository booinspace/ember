import { expect, test } from "@playwright/test";

const id = "11111111-1111-4111-8111-111111111111";
test("MCP requests retain progress, cancellation, safe output and idempotent retries", async ({
  page,
}) => {
  let submissionKey = "";
  let rejectOnce = true;
  let cancelled = false;
  let submitted = false;
  const tool = {
    id: "mcp_fixture_echo_abc",
    server_id: "fixture",
    name: "echo",
    description: '<img src=x onerror="window.injected=true">',
    input_schema: { type: "object" },
    requirements: [],
    effect: "read",
    available: true,
    reason: null,
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
    if (path === "/api/tools")
      return route.fulfill({
        json: {
          servers: [
            { id: "fixture", status: "ready", tools: 1 },
            { id: "offline", status: "unavailable", tools: 0 },
          ],
          tools: [tool],
        },
      });
    if (path === "/api/tasks") {
      const body = route.request().postDataJSON() as {
        idempotency_key: string;
        operation: string;
        tool: unknown;
      };
      expect(body.operation).toBe("tool");
      expect(body.tool).toEqual({
        tool_id: tool.id,
        arguments: { text: "Synthetic" },
      });
      if (rejectOnce) {
        submissionKey = body.idempotency_key;
        rejectOnce = false;
        return route.abort("failed");
      }
      expect(body.idempotency_key).toBe(submissionKey);
      submitted = true;
      return route.fulfill({
        json: {
          task_id: id,
          state: "waiting_for_tool",
          cancellation_requested: false,
        },
      });
    }
    if (path === "/api/tasks/" + id + "/cancel") {
      cancelled = true;
      return route.fulfill({ json: { accepted: true } });
    }
    if (path === "/api/tasks/" + id)
      return route.fulfill({
        json: {
          task_id: id,
          state: cancelled ? "uncertain" : "waiting_for_tool",
          cancellation_requested: cancelled,
        },
      });
    if (path === "/api/task-notifications")
      return route.fulfill({
        json: {
          revision: 0,
          items:
            submitted && cancelled
              ? [
                  {
                    task_id: id,
                    state: "uncertain",
                    result: null,
                    operation: "tool",
                    cancellation_requested: true,
                  },
                ]
              : [],
        },
      });
    if (path === "/api/task-confirmations") return route.fulfill({ json: [] });
    if (path === "/api/tasks/" + id + "/receipt")
      return route.fulfill({
        json: { task_id: id, state: "uncertain", receipt: null },
      });
    return route.fulfill({ json: {} });
  });
  await page.goto("http://127.0.0.1:4174/#activate=synthetic");
  await page.getByText("Run an MCP tool", { exact: true }).click();
  await expect(
    page.getByText("offline: unavailable (0 tools)", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("Tool", { exact: true }).selectOption(tool.id);
  await expect(page.getByText(tool.description, { exact: true })).toBeVisible();
  await expect(page.locator("img")).toHaveCount(0);
  await page.getByLabel("Arguments (JSON)").fill('{"text":"Synthetic"}');
  await page.getByRole("button", { name: "Queue tool" }).click();
  await expect(
    page.getByText("Tool request failed.", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Queue tool" }).click();
  await expect(page.getByRole("button", { name: "Cancel tool" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel tool" }).click();
  await expect.poll(() => cancelled).toBe(true);
  await page.getByText("Tasks", { exact: true }).click();
  await page.getByRole("button", { name: "View receipt" }).click();
  await expect(
    page.getByText("Outcome: uncertain", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("No result data was retained for this outcome.", {
      exact: true,
    }),
  ).toBeVisible();
});
