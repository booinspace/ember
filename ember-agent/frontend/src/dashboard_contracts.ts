import { z } from "zod";

export const activityEvent = z.object({
  id: z.string().uuid(),
  sequence: z.number().int().nonnegative(),
  started_at: z.string(),
  kind: z.enum(["tool", "memory", "task", "inference", "system"]),
  name: z.string().max(128),
  provider: z.string().max(64),
  status: z.enum([
    "running",
    "waiting_for_model",
    "waiting_for_realtime",
    "waiting_for_tool",
    "waiting_for_user",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "uncertain",
  ]),
  request_id: z.string().uuid().nullable(),
  task_id: z.string().uuid().nullable(),
  duration_ms: z.number().nullable(),
  count: z.number().int().nullable(),
  code: z
    .string()
    .regex(/^[a-z0-9_]{1,64}$/)
    .nullable()
    .default(null),
});
export type ActivityEvent = z.infer<typeof activityEvent>;
const number = z.number().finite().nullable();
export const requestHeader = z.object({
  request_id: z.string().uuid(),
  context_id: z.string().uuid(),
  model: z.string(),
  workload: z.string(),
  captured_at: z.string(),
  messages: z.number().int(),
  tools: z.number().int(),
  input_budget_units: z.number().int(),
  input_units: z.number().int().nullable().default(null),
  input_bytes: z.number().int(),
  prompt_tokens: number,
});
export const dashboardSnapshot = z.object({
  revision: z.number().int(),
  events: z.array(activityEvent).max(256),
  requests: z.array(requestHeader).max(8),
  system: z.object({
    cpu_percent: number,
    memory_percent: number,
    memory_used_bytes: number,
    memory_total_bytes: number,
    process_rss_bytes: number,
    gpu_name: z.string().nullable(),
    gpu_percent: number,
    gpu_used_mib: number,
    gpu_total_mib: number,
  }),
  gateway: z
    .object({
      status: z.string(),
      active_request_id: z.string().uuid().nullable(),
      realtime_sessions: z.array(z.string().uuid()),
      queued_p1: z.number().int(),
      queued_p2: z.number().int(),
      queued_p3: z.number().int(),
      queued_p4: z.number().int(),
    })
    .nullable(),
  model: z.string(),
  backend: z.string(),
  context_tokens: z.number().int(),
  usage: z
    .object({
      prompt_tokens: z.number().int(),
      output_tokens: z.number().int(),
      total_seconds: z.number(),
      first_visible_seconds: number,
      tokens_per_second: number,
    })
    .nullable(),
  uptime_seconds: z.number(),
  sample_seconds: z.number(),
  observation_failures: z.number().int().default(0),
});
export type DashboardSnapshot = z.infer<typeof dashboardSnapshot>;
export const promptInspection = z.object({
  request: z
    .object({
      request_id: z.string().uuid(),
      context_id: z.string().uuid(),
      model: z.string(),
      workload: z.string(),
      messages: z
        .array(
          z
            .object({ role: z.string(), content: z.string().max(262144) })
            .passthrough(),
        )
        .max(128),
      tools: z.array(z.record(z.string(), z.unknown())).max(64),
    })
    .passthrough(),
  captured_at: z.string(),
  input_budget_units: z.number(),
  input_units: z.number().nullable().default(null),
  model_context_tokens: z.number(),
  usage: z.record(z.string(), z.unknown()).nullable(),
});
export const capabilitySnapshot = z.object({
  capabilities: z
    .array(
      z.object({
        id: z.string(),
        kind: z.enum(["native", "mcp", "public_search", "harness"]),
        name: z.string(),
        provider: z.string(),
        status: z.enum(["enabled", "unconfigured", "unavailable", "disabled"]),
        operations: z.array(z.string()),
        reason: z.string().nullable(),
        effect: z.enum(["read", "write", "denied"]).nullable(),
      }),
    )
    .max(1038),
});
export type Capability = z.infer<
  typeof capabilitySnapshot
>["capabilities"][number];
export function mergeActivity(
  previous: ActivityEvent[],
  incoming: ActivityEvent[],
): ActivityEvent[] {
  const entries = new Map(previous.map((event) => [event.id, event]));
  for (const event of incoming) {
    const old = entries.get(event.id);
    if (!old || event.sequence > old.sequence) entries.set(event.id, event);
  }
  return [...entries.values()]
    .sort((a, b) => a.started_at.localeCompare(b.started_at))
    .slice(-256);
}
export function percent(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value)
    ? "—"
    : `${Math.round(value)}%`;
}
export function duration(milliseconds: number | null): string {
  return milliseconds == null
    ? "Live"
    : milliseconds < 1000
      ? `${Math.round(milliseconds)} ms`
      : `${(milliseconds / 1000).toFixed(1)} s`;
}
