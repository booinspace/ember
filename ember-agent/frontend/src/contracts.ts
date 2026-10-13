import { z } from "zod";

export const recallSelection = z.object({
  local_enabled: z.boolean(),
  import_mode: z.enum(["all", "selected", "none"]),
  included_libraries: z.array(z.string().uuid()).max(128),
  excluded_libraries: z.array(z.string().uuid()).max(128),
  revision: z.number().int().nonnegative(),
});
export type RecallSelection = z.infer<typeof recallSelection>;

export const captureControl = z.object({
  enabled: z.boolean(),
  revision: z.number().int().nonnegative(),
});
export type CaptureControl = z.infer<typeof captureControl>;

export const runtimeState = z.object({
  binding: z.object({
    character_id: z.string().uuid(),
    room: z.string(),
    endpoint: z.string().url(),
    instance_id: z.string().uuid(),
  }),
  context_id: z.string().uuid(),
  libraries: z
    .array(
      z.object({
        library_id: z.string().uuid(),
        namespace: z.string().max(1000),
        available: z.boolean(),
      }),
    )
    .max(128)
    .default([]),
  recall: recallSelection,
  capture: captureControl,
  processing: z
    .object({
      counts: z.record(z.string(), z.number().int().nonnegative()),
      failure_codes: z.array(z.string()).max(8),
      index_status: z.string(),
    })
    .default({ counts: {}, failure_codes: [], index_status: "unknown" }),
  deep: z.string(),
  voice: z.boolean().default(false),
  voice_status: z.string().default("disabled"),
});
export type RuntimeState = z.infer<typeof runtimeState>;

export const roomAccess = z.object({
  token: z.string(),
  endpoint: z.string().url(),
});
export const turnReceipt = z.object({
  request_id: z.string().uuid(),
  outcome: z.enum(["complete", "error", "clarification"]),
  retained_memories: z.number().int().nonnegative().default(0),
  code: z.string().nullable().optional(),
  message: z.string().nullable().optional(),
});

export const investigation = z.object({
  request_id: z.string().uuid(),
  outcome: z.enum([
    "complete_within_budget",
    "partial",
    "unavailable",
    "cancelled",
    "deferred",
  ]),
  reasons: z.array(z.string()).max(64),
  scope: z.record(z.string(), z.unknown()).nullable(),
  memories: z
    .array(
      z.object({
        memory: z.object({
          memory_id: z.string().uuid(),
          revision: z.number().int(),
          origin: z.record(z.string(), z.unknown()).nullable(),
          content: z.object({
            kind: z.string(),
            confidence: z.number(),
            modality: z.string(),
            evidence: z.array(z.record(z.string(), z.unknown())).default([]),
            payload: z.record(z.string(), z.unknown()),
            provenance: z.array(z.unknown()),
          }),
          temporal: z.record(z.string(), z.unknown()),
        }),
        score: z.number(),
        ranking: z
          .array(
            z.object({
              method: z.string(),
              score: z.number(),
              reasons: z.array(z.string()),
            }),
          )
          .default([]),
      }),
    )
    .max(24),
  observations: z.array(z.record(z.string(), z.unknown())).max(24),
  associations: z.array(z.record(z.string(), z.unknown())).max(256),
  clusters: z
    .array(
      z.object({
        cluster_id: z.string().uuid(),
        members: z.array(
          z.object({
            memory_id: z.string().uuid(),
            revision: z.number().int(),
          }),
        ),
        strength: z.string(),
      }),
    )
    .max(24)
    .default([]),
  counters: z.record(z.string(), z.unknown()),
  prompt_memories: z.array(
    z.object({ memory_id: z.string().uuid(), revision: z.number().int() }),
  ),
  prompt_observations: z.array(z.number().int()),
});
export type Investigation = z.infer<typeof investigation>;
export const inspection = z.object({
  status: z.string(),
  result: investigation.nullable(),
});

export async function control<T>(
  path: string,
  schema: z.ZodType<T>,
  body?: unknown,
): Promise<T> {
  const response = await fetch("/api/" + path, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    ...(body === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
  const text = await response.text();
  if (text.length > 524288)
    throw new Error("Control response exceeds its bound.");
  const value: unknown = JSON.parse(text);
  if (!response.ok) {
    const error = z
      .object({ error: z.string().optional(), detail: z.string().optional() })
      .safeParse(value);
    throw new Error(
      error.success
        ? (error.data.error ?? error.data.detail ?? "Control unavailable")
        : "Control unavailable",
    );
  }
  return schema.parse(value);
}

export function libraryIds(value: string): string[] {
  const ids = value.split(/[\s,]+/).filter(Boolean);
  return z
    .array(z.string().uuid())
    .max(128)
    .parse([...new Set(ids)]);
}

export async function activate(): Promise<RuntimeState> {
  const token = new URLSearchParams(location.hash.slice(1)).get("activate");
  history.replaceState(null, "", location.pathname);
  if (token)
    await control("bootstrap", z.object({ ready: z.boolean() }), { token });
  return control("state", runtimeState);
}
