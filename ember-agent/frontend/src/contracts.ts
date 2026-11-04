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

export const speechSelection = z.object({
  voice: z.string().min(1).max(128),
  speed: z.number().min(0.75).max(1.5),
});
export type SpeechSelection = z.infer<typeof speechSelection>;
export const speechOptions = z.object({
  model: z.string().min(1).max(128),
  voices: z.array(z.string().min(1).max(128)).min(1).max(256),
  selection: speechSelection,
});
export type SpeechOptions = z.infer<typeof speechOptions>;

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
  lan_available: z.boolean().default(false),
  tasks_available: z.boolean().default(false),
  summary_tasks_available: z.boolean().default(false),
  recall_tasks_available: z.boolean().default(false),
  delegation_available: z.boolean().default(false),
  voice: z.boolean().default(false),
  voice_status: z.string().default("disabled"),
  speech: speechOptions.nullable().default(null),
  prompts_available: z.boolean().default(false),
  transport: z.enum(["livekit", "http"]).default("livekit"),
  text_available: z.boolean().default(true),
  thinking_available: z.boolean().default(false),
  thinking_enabled: z.boolean().default(false),
});
export type RuntimeState = z.infer<typeof runtimeState>;

export const turnDetails = z.object({
  request_id: z.string().uuid(),
  context_id: z.string().uuid(),
  stage: z.enum([
    "preparing",
    "generating",
    "thinking",
    "answering",
    "awaiting_delivery",
    "complete",
    "interrupted",
    "failed",
    "invalidated",
  ]),
  thinking_enabled: z.boolean(),
  thinking: z.string().max(32768),
  thinking_truncated: z.boolean(),
  memories: z
    .array(
      z.object({
        category: z.enum(["profile", "recall", "deep"]),
        data: z.record(z.string(), z.unknown()),
      }),
    )
    .max(100),
  memories_truncated: z.boolean(),
});
export type TurnDetails = z.infer<typeof turnDetails>;
export const turnInspection = z.object({ turn: turnDetails.nullable() });

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
  query: z.string().max(8192).default(""),
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
  counters: z.object({
    sql_statements: z.number().int().nonnegative().default(0),
    rows_reported: z.number().int().nonnegative().default(0),
    materialized_bytes: z.number().int().nonnegative().default(0),
    elapsed_ms: z.number().int().nonnegative().default(0),
    limits_hit: z.array(z.string()).max(8).default([]),
  }),
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
  signal?: AbortSignal,
  method?: "PUT",
): Promise<T> {
  const response = await fetch("/api/" + path, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    credentials: "same-origin",
    ...(signal ? { signal } : {}),
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

const promptDocument = z.object({
  markdown: z.string().max(8192),
  revision: z.string().regex(/^[0-9a-f]{64}$/),
  error: z.string().nullable(),
});
export const promptDocuments = z.object({
  character_id: z.string().uuid(),
  system: promptDocument,
  personality: promptDocument,
  snapshot: z
    .object({
      instructions: z.string(),
      revision: z.string(),
      cost: z.number(),
      limit: z.number(),
    })
    .nullable(),
  error: z.string().nullable(),
  applied_revision: z.string().nullable(),
  instruction_limit: z.number(),
});
export type PromptDocuments = z.infer<typeof promptDocuments>;
