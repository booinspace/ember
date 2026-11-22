import { expect, test, type Page } from "@playwright/test";
import { resolve } from "node:path";
const id = "11111111-1111-4111-8111-111111111111";
const toolId = "22222222-2222-4222-8222-222222222222";
const request = {
  request_id: id,
  context_id: id,
  model: "local-synthetic-27b",
  workload: "P0",
  messages: [
    { role: "system", content: "SYNTHETIC_SYSTEM_RULES" },
    { role: "user", content: "SYNTHETIC_MEMORY_VALUE" },
    {
      role: "assistant",
      content: "",
      tool_calls: [
        {
          call_id: "call1",
          name: "search",
          arguments: { query: "Synthetic public query" },
        },
      ],
    },
    { role: "tool", content: "SYNTHETIC_MCP_RESULT", tool_call_id: "call1" },
    { role: "user", content: "Synthetic question" },
  ],
  tools: [
    {
      type: "function",
      function: { name: "inspect_text", parameters: { type: "object" } },
    },
  ],
  max_output_tokens: 512,
};
async function mock(page: Page) {
  let revoked = false;
  let promptReads = 0;
  const event = {
    id: toolId,
    sequence: 2,
    started_at: "2026-11-04T12:00:00Z",
    kind: "tool",
    name: "web_search",
    provider: "fixture",
    status: "succeeded",
    request_id: id,
    task_id: null,
    duration_ms: 182,
    count: 3,
  };
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    let body: unknown = {};
    if (url.pathname === "/api/bootstrap") body = { ready: true };
    else if (url.pathname === "/api/state")
      body = {
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
        summary_tasks_available: true,
        recall_tasks_available: true,
        delegation_available: true,
        delegation_provider: "Fixture harness",
        transport: "http",
        text_available: true,
      };
    else if (url.pathname === "/api/dashboard")
      body = {
        revision: event.sequence,
        events: [event],
        requests: [
          {
            request_id: id,
            context_id: id,
            model: request.model,
            workload: "P0",
            captured_at: event.started_at,
            messages: 5,
            tools: 1,
            input_budget_units: 24576,
            input_bytes: 1820,
            prompt_tokens: 820,
          },
        ],
        system: {
          cpu_percent: 14,
          memory_percent: 38,
          memory_used_bytes: 12000000000,
          memory_total_bytes: 32000000000,
          process_rss_bytes: 400000000,
          gpu_name: "NVIDIA RTX 3090",
          gpu_percent: 62,
          gpu_used_mib: 15400,
          gpu_total_mib: 24576,
        },
        gateway: {
          status: "ready",
          active_request_id: null,
          realtime_sessions: [],
          queued_p1: 0,
          queued_p2: 0,
          queued_p3: 0,
          queued_p4: 0,
        },
        model: request.model,
        backend: "ollama",
        context_tokens: 131072,
        usage: {
          prompt_tokens: 820,
          output_tokens: 72,
          total_seconds: 2.1,
          first_visible_seconds: 0.22,
          tokens_per_second: 41.3,
        },
        uptime_seconds: 320,
        sample_seconds: 3,
      };
    else if (url.pathname === "/api/capabilities")
      body = {
        capabilities: [
          {
            id: "native.inspect",
            kind: "native",
            name: "Inspect text",
            provider: "ember",
            status: "enabled",
            operations: ["inspect"],
            reason: null,
            effect: "read",
          },
          {
            id: "public_search",
            kind: "public_search",
            name: "Public web search",
            provider: "fixture",
            status: "enabled",
            operations: ["web_search"],
            reason: null,
            effect: "read",
          },
          {
            id: "harness",
            kind: "harness",
            name: "Agentic delegation",
            provider: "Fixture harness",
            status: "enabled",
            operations: ["delegate"],
            reason: null,
            effect: null,
          },
          {
            id: "mcp",
            kind: "mcp",
            name: "MCP services",
            provider: "mcp",
            status: "unconfigured",
            operations: [],
            reason: "No MCP servers configured.",
            effect: null,
          },
        ],
      };
    else if (url.pathname === "/api/inference/requests/" + id) {
      if (url.searchParams.has("validate_only")) {
        if (revoked)
          return route.fulfill({
            status: 409,
            json: { error: "context_invalidated" },
          });
        body = { valid: true };
      } else {
        promptReads++;
        body = {
          request,
          captured_at: event.started_at,
          input_budget_units: 24576,
          model_context_tokens: 131072,
          usage: null,
        };
      }
    } else if (
      url.pathname === "/api/tools" ||
      url.pathname === "/api/tools/refresh"
    )
      body = { servers: [], tools: [] };
    else if (url.pathname === "/api/text/connect") body = { session_id: id };
    else if (url.pathname === "/api/text/turn") {
      const submitted = route.request().postDataJSON() as {
        request_id: string;
      };
      body = {
        request_id: submitted.request_id,
        text: "Synthetic reply with a public reference.",
        retained_memories: 1,
      };
    } else if (url.pathname === "/api/turn-details") body = { turn: null };
    else if (url.pathname === "/api/task-notifications")
      body = { revision: 0, items: [] };
    else if (url.pathname === "/api/task-confirmations") body = [];
    else body = { connected: true, delivered: true };
    await route.fulfill({ json: body });
  });
  return {
    revoke: () => {
      revoked = true;
    },
    reads: () => promptReads,
    setEventRequest: (identity: string) => {
      event.request_id = identity;
      event.sequence++;
    },
  };
}
test("delayed prior-turn metadata cannot move a tool card to the next user message", async ({
  page,
}) => {
  const fixture = await mock(page);
  const submitted: string[] = [];
  await page.route("**/api/text/turn", async (route) => {
    const value = route.request().postDataJSON() as { request_id: string };
    submitted.push(value.request_id);
    fixture.setEventRequest(submitted[0] ?? value.request_id);
    await route.fulfill({
      json: {
        request_id: value.request_id,
        text: "Synthetic answer",
        retained_memories: 0,
      },
    });
  });
  await page.route("**/api/turn-details?**", async (route) => {
    await route.fulfill({
      json: {
        turn:
          submitted.length < 2
            ? null
            : {
                request_id: submitted[0],
                context_id: id,
                stage: "complete",
                thinking_enabled: false,
                thinking: "",
                thinking_truncated: false,
                memories: [],
                memories_truncated: false,
              },
      },
    });
  });
  await page.goto("http://127.0.0.1:4174/#activate=synthetic");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  const message = page.getByRole("textbox", { name: "Message Ember" });
  for (const text of [
    "First synthetic question",
    "Second synthetic question",
  ]) {
    await message.fill(text);
    await expect(
      page.getByRole("button", { name: "Send", exact: true }),
    ).toBeEnabled();
    await message.press("Enter");
  }
  await expect.poll(() => submitted.length).toBe(2);
  const first = page.locator(".turn.user").first();
  await expect(first.locator("xpath=following-sibling::*[1]")).toHaveClass(
    /activity-card/,
  );
  await expect(first.locator("xpath=following-sibling::*[1]")).toContainText(
    "Web search",
  );
  await expect(
    page.locator(".turn.user").last().locator("xpath=following-sibling::*[1]"),
  ).not.toHaveClass(/activity-card/);
});
test("dashboard exposes transactions and lazily inspects exact input with revocation", async ({
  page,
}) => {
  const fixture = await mock(page);
  await page.goto("http://127.0.0.1:4174/#activate=synthetic");
  await expect(
    page.getByRole("region", { name: "System monitoring" }),
  ).toContainText("41.3");
  await expect(
    page.getByRole("region", { name: "Capabilities" }),
  ).toContainText("Agentic delegation");
  await expect(
    page.getByRole("region", { name: "Capabilities" }),
  ).toContainText("unconfigured");
  expect(fixture.reads()).toBe(0);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  const input = page.getByRole("textbox", { name: "Message Ember" });
  await expect(input).toHaveAttribute("placeholder", "Message Ember…");
  await input.fill("Search for a synthetic source");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator(".activity-card.tool")).toHaveCount(1);
  await page.locator(".activity-card.tool summary").click();
  await expect(page.locator(".activity-body")).toContainText("fixture");
  await page
    .getByRole("button", { name: "Inspect request", exact: true })
    .click();
  await expect(page.locator(".prompt-inspector")).toContainText(
    "SYNTHETIC_SYSTEM_RULES",
  );
  await expect(page.locator(".prompt-inspector")).toContainText(
    "SYNTHETIC_MEMORY_VALUE",
  );
  await expect(page.locator(".prompt-inspector")).toContainText(
    "SYNTHETIC_MCP_RESULT",
  );
  expect(fixture.reads()).toBe(1);
  await page
    .locator(".prompt-message")
    .filter({ hasText: "Tool definitions" })
    .locator("summary")
    .click();
  await expect(page.locator(".prompt-inspector")).toContainText("inspect_text");
  fixture.revoke();
  await expect(page.locator(".prompt-inspector")).toContainText(
    "no longer available",
    { timeout: 7000 },
  );
  await expect(
    page.getByText("SYNTHETIC_MEMORY_VALUE", { exact: true }),
  ).toHaveCount(0);
});
for (const width of [375, 1280, 1680])
  test(`dashboard fits ${width}px and shows meaningful unavailable states`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await mock(page);
    await page.goto("http://127.0.0.1:4174/#activate=synthetic");
    await expect(
      page.getByRole("region", { name: "System monitoring" }),
    ).toContainText("ready");
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(width);
    await expect(
      page.getByRole("textbox", { name: "Message Ember" }),
    ).not.toHaveAttribute("placeholder", /NAS|Milo|summer/);
    await page.screenshot({
      path: resolve(`../../.local/dashboard-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
  });
