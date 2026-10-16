import { z } from "zod";
import { type Investigation } from "./contracts";

const sourceReference = z.object({
  source_store_id: z.string().uuid(),
  record_kind: z.string(),
  record_id: z.string().uuid(),
  record_revision: z.number().int(),
  part_id: z.string().uuid().nullable().optional(),
});
const evidenceReference = z.object({
  evidence: z.object({
    source: sourceReference,
    span: z
      .object({
        start_codepoint: z.number().int().nonnegative(),
        end_codepoint: z.number().int().positive(),
        source_content_sha256: z.string().regex(/^[a-f0-9]{64}$/),
        quoted_span_sha256: z.string().regex(/^[a-f0-9]{64}$/),
      })
      .refine((span) => span.end_codepoint > span.start_codepoint),
  }),
});

function sourceKey(value: unknown): string | null {
  const evidence = evidenceReference.safeParse(value);
  if (!evidence.success) return null;
  const source = evidence.data.evidence.source;
  return [
    source.source_store_id,
    source.record_kind,
    source.record_id,
    source.record_revision,
    source.part_id ?? "",
    evidence.data.evidence.span.start_codepoint,
    evidence.data.evidence.span.end_codepoint,
    evidence.data.evidence.span.source_content_sha256,
    evidence.data.evidence.span.quoted_span_sha256,
  ].join(":");
}

function openDetails(id: string): void {
  const target = document.getElementById(id);
  if (target instanceof HTMLDetailsElement) target.open = true;
}

function eventStart(time: Record<string, unknown>): string | null {
  return typeof time.valid_from === "string" ? time.valid_from : null;
}

export function Inspector({
  result,
  answer,
  busy,
}: {
  result: Investigation;
  answer: () => void;
  busy: boolean;
}) {
  return (
    <section className="inspector">
      <div className="section-title">
        <h2>Recall details</h2>
        <span>{result.outcome.replaceAll("_", " ")}</span>
      </div>
      <p>
        {result.memories.length} accepted memories ·{" "}
        {result.observations.length} original observations ·
        {result.prompt_memories.length} memories selected for the answer
      </p>
      {result.reasons.length > 0 && (
        <p className="coverage">{result.reasons.join(" · ")}</p>
      )}
      {result.reasons.includes("read_budget_exceeded") && (
        <p>
          The investigation reached its search limit. Any results below
          completed validation; an empty result does not mean your memories are
          missing.
        </p>
      )}
      {Array.isArray(result.counters.limits_hit) &&
        result.counters.limits_hit.length > 0 && (
          <p>Limit reached: {result.counters.limits_hit.join(" · ")}</p>
        )}
      {result.scope && <pre>{JSON.stringify(result.scope, null, 2)}</pre>}
      {result.clusters.length > 0 && (
        <details>
          <summary>Evidence groups</summary>
          <p>Shared topics alone do not identify the same event.</p>
          {result.clusters.map((cluster) => (
            <p key={cluster.cluster_id}>
              {cluster.strength.replaceAll("_", " ")} · {cluster.members.length}{" "}
              memories
            </p>
          ))}
        </details>
      )}
      {result.memories.length > 0 && (
        <details>
          <summary>Dates and periods</summary>
          <p>
            Occurrence time is distinct from observation and recorded belief.
          </p>
          <ol>
            {[...result.memories]
              .sort((left, right) => {
                const a = eventStart(left.memory.temporal);
                const b = eventStart(right.memory.temporal);
                return a === null
                  ? b === null
                    ? 0
                    : 1
                  : b === null
                    ? -1
                    : a.localeCompare(b);
              })
              .map(({ memory }) => (
                <li key={memory.memory_id}>
                  <a
                    href={"#memory-" + memory.memory_id}
                    onClick={() => openDetails("memory-" + memory.memory_id)}
                  >
                    {eventStart(memory.temporal) ?? "Unknown event date"} ·
                    {memory.content.modality} ·{" "}
                    {String(memory.temporal.precision ?? "unknown")}
                  </a>
                </li>
              ))}
          </ol>
        </details>
      )}
      {result.memories.map(({ memory, score, ranking }) => (
        <details key={memory.memory_id} id={"memory-" + memory.memory_id}>
          <summary>
            {memory.content.kind} · confidence {memory.content.confidence} ·
            revision {memory.revision}
          </summary>
          <p>
            {memory.content.modality} · recall score {score} (not confidence)
          </p>
          {ranking.length > 0 && (
            <p>
              Included by:{" "}
              {ranking
                .map((item) => [item.method, ...item.reasons].join(" · "))
                .join("; ")}
            </p>
          )}
          <pre>{JSON.stringify(memory.content.payload, null, 2)}</pre>
          {result.observations.map((observation, index) => {
            const key = sourceKey(observation.source);
            return key !== null &&
              memory.content.evidence.some(
                (proof) => sourceKey(proof) === key,
              ) ? (
              <p key={index}>
                <a
                  href={"#observation-" + index}
                  onClick={() => openDetails("observation-" + index)}
                >
                  Original source observation {index + 1}
                </a>
              </p>
            ) : null;
          })}
          <p>Time and provenance</p>
          <pre>
            {JSON.stringify(
              {
                time: memory.temporal,
                origin: memory.origin,
                provenance: memory.content.provenance,
              },
              null,
              2,
            )}
          </pre>
        </details>
      ))}
      {result.observations.map((observation, index) => (
        <details key={index} id={"observation-" + index}>
          <summary>Original source observation {index + 1}</summary>
          <pre>{JSON.stringify(observation, null, 2)}</pre>
        </details>
      ))}
      <button
        disabled={
          busy ||
          result.prompt_memories.length + result.prompt_observations.length ===
            0 ||
          !["complete_within_budget", "partial"].includes(result.outcome)
        }
        onClick={answer}
      >
        Answer using this recall
      </button>
      <p>
        These results are bounded and may omit relevant or undated records. No
        matches do not establish that an event never happened.
      </p>
    </section>
  );
}
