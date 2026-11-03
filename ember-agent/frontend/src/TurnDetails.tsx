import type { TurnDetails as Details } from "./contracts";

function valueText(value: unknown): string {
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  )
    return String(value);
  if (value && typeof value === "object") {
    if ("value" in value) {
      const unit =
        "unit" in value && typeof value.unit === "string"
          ? " " + value.unit
          : "";
      return valueText(value.value) + unit;
    }
    if ("entity_id" in value) return "Linked entity";
  }
  return "Unavailable value";
}

export function TurnDetailsPanel({ details }: { details: Details | null }) {
  if (!details) return null;
  const labels: Record<Details["stage"], string> = {
    preparing: "Recalling memories…",
    generating: "Preparing reply…",
    thinking: "Thinking…",
    answering: "Replying…",
    awaiting_delivery: "Delivering reply…",
    complete: "Reply details",
    interrupted: "Reply interrupted",
    failed: "Reply unavailable",
    invalidated: "Memory access changed; details are unavailable.",
  };
  return (
    <section className="turn-details" aria-label="Reply details">
      <p className="quiet" role="status">
        {labels[details.stage]}
      </p>
      {details.stage !== "invalidated" && (
        <>
          <details>
            <summary>
              Thinking{details.thinking_enabled ? "" : " (off)"}
            </summary>
            <div className="details-body">
              {details.thinking ? (
                <pre className="thinking-text">{details.thinking}</pre>
              ) : (
                <p className="quiet">
                  {details.thinking_enabled
                    ? "No thinking output yet."
                    : "Turn on Thinking for the next reply."}
                </p>
              )}
              {details.thinking_truncated && (
                <p className="quiet">Thinking display limit reached.</p>
              )}
            </div>
          </details>
          <details>
            <summary>Recalled memories ({details.memories.length})</summary>
            <div className="details-body memory-values">
              {details.memories.length === 0 && (
                <p className="quiet">
                  {details.stage === "preparing"
                    ? "Selecting memories…"
                    : "No recalled memory values were included."}
                </p>
              )}
              {details.memories.map(({ category, data }, index) => (
                <article className="memory-value" key={index}>
                  <span className="memory-category">
                    {category === "profile"
                      ? "Profile / pinned"
                      : category === "deep"
                        ? "Deep recall"
                        : "Recall"}
                  </span>
                  {typeof data.predicate === "string" && (
                    <p className="memory-predicate">
                      {data.predicate.replace(/[._]/g, " ")}
                    </p>
                  )}
                  <p>{valueText(data.value ?? data.goal ?? data.plan)}</p>
                  {data.polarity === "negative" && (
                    <p className="memory-qualifier">Negated statement</p>
                  )}
                  {typeof data.modality === "string" && (
                    <p className="memory-qualifier">
                      {data.modality.replaceAll("_", " ").toLowerCase()}
                    </p>
                  )}
                  {typeof data.state === "string" && (
                    <p className="memory-qualifier">{data.state}</p>
                  )}
                  {Array.isArray(data.steps) && (
                    <ol>
                      {data.steps.map((step, n) => (
                        <li key={n}>{valueText(step)}</li>
                      ))}
                    </ol>
                  )}
                  {data.time !== null &&
                    data.time !== undefined &&
                    typeof data.time === "object" &&
                    Object.entries(data.time).map(([key, value]) => (
                      <p className="memory-qualifier" key={key}>
                        {key.replaceAll("_", " ")}: {valueText(value)}
                      </p>
                    ))}
                </article>
              ))}
              {details.memories_truncated && (
                <p className="quiet">Some values exceeded the display limit.</p>
              )}
            </div>
          </details>
        </>
      )}
    </section>
  );
}
