import { describe, expect, it } from "vitest";
import {
  activityEvent,
  mergeActivity,
  percent,
  promptInspection,
} from "./dashboard_contracts";
const event = activityEvent.parse({
  id: "11111111-1111-4111-8111-111111111111",
  sequence: 1,
  started_at: "2026-11-04T12:00:00Z",
  kind: "tool",
  name: "web_search",
  provider: "hermes",
  status: "running",
  request_id: null,
  task_id: null,
  duration_ms: null,
  count: null,
});
describe("live dashboard contracts", () => {
  it("updates a transaction without duplicates or regressing to an old status", () => {
    const completed = {
      ...event,
      sequence: 2,
      status: "succeeded" as const,
      duration_ms: 12,
    };
    expect(mergeActivity([completed], [event, completed])).toEqual([completed]);
  });
  it("bounds retained metadata and keeps chronological start order", () => {
    const events = Array.from({ length: 300 }, (_, index) => ({
      ...event,
      id: `event-${index}`,
      sequence: index,
      started_at: new Date(index * 1000).toISOString(),
    }));
    const merged = mergeActivity([], events);
    expect(merged).toHaveLength(256);
    expect(merged[0]?.id).toBe("event-44");
  });
  it("distinguishes unknown load from an actual idle sample", () => {
    expect(percent(null)).toBe("—");
    expect(percent(0)).toBe("0%");
    expect(percent(Number.NaN)).toBe("—");
  });
  it("accepts actual visible tool history without allowing unbounded inspector data", () => {
    const value = {
      request: {
        request_id: event.id,
        context_id: event.id,
        model: "synthetic",
        workload: "P0",
        messages: [
          { role: "system", content: "EXACT_SYSTEM" },
          { role: "tool", content: "EXACT_TOOL_RESULT", tool_call_id: "call1" },
        ],
        tools: [],
      },
      captured_at: event.started_at,
      input_budget_units: 16384,
      model_context_tokens: 131072,
      usage: null,
    };
    expect(promptInspection.parse(value).request.messages[1]?.content).toBe(
      "EXACT_TOOL_RESULT",
    );
    expect(() =>
      promptInspection.parse({
        ...value,
        request: {
          ...value.request,
          messages: Array(129).fill(value.request.messages[0]),
        },
      }),
    ).toThrow();
  });
});
