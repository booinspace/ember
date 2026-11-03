import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { turnDetails } from "./contracts";
import { TurnDetailsPanel } from "./TurnDetails";

const id = "11111111-1111-4111-8111-111111111111";

describe("current turn details", () => {
  it("renders escaped channel text and qualified selected values in closed expansions", () => {
    const details = turnDetails.parse({
      request_id: id,
      context_id: id,
      stage: "thinking",
      thinking_enabled: true,
      thinking: "SYNTHETIC <script>alert(1)</script>",
      thinking_truncated: false,
      memories_truncated: false,
      memories: [
        {
          category: "recall",
          data: {
            predicate: "hardware.owns_gpu",
            value: { type: "string", value: "SYNTHETIC_GPU" },
            polarity: "negative",
            modality: "NEGATED_ASSERTION",
            time: { observed_at: "2026-01-01T00:00:00Z" },
          },
        },
      ],
    });
    const html = renderToStaticMarkup(<TurnDetailsPanel details={details} />);
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).toContain("Recalled memories (1)");
    expect(html).toContain("SYNTHETIC_GPU");
    expect(html).toContain("Negated statement");
    expect(html).toContain("2026-01-01");
    expect(html).not.toContain(" open=");
  });

  it("does not display revoked details or pretend unavailable thinking exists", () => {
    const base = {
      request_id: id,
      context_id: id,
      stage: "complete",
      thinking_enabled: false,
      thinking: "",
      thinking_truncated: false,
      memories: [],
      memories_truncated: false,
    };
    const disabled = renderToStaticMarkup(
      <TurnDetailsPanel details={turnDetails.parse(base)} />,
    );
    expect(disabled).toContain("Thinking (off)");
    expect(disabled).toContain("No recalled memory values were included.");
    const revoked = renderToStaticMarkup(
      <TurnDetailsPanel
        details={turnDetails.parse({
          ...base,
          stage: "invalidated",
          thinking: "SHOULD_NOT_RENDER",
        })}
      />,
    );
    expect(revoked).toContain("Memory access changed");
    expect(revoked).not.toContain("SHOULD_NOT_RENDER");
    expect(revoked).not.toContain("<details");
  });
});
