import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { investigation } from "./contracts";
import { Inspector } from "./Inspector";

describe("authorized recall inspection", () => {
  it("renders time, modality, inclusion reasons and links to existing exact source data", () => {
    const id = "11111111-1111-4111-8111-111111111111";
    const source = {
      source_store_id: "22222222-2222-4222-8222-222222222222",
      record_kind: "message",
      record_id: "33333333-3333-4333-8333-333333333333",
      record_revision: 1,
      part_id: "44444444-4444-4444-8444-444444444444",
    };
    const proof = {
      evidence: {
        source,
        span: {
          start_codepoint: 0,
          end_codepoint: 29,
          source_content_sha256: "1".repeat(64),
          quoted_span_sha256: "2".repeat(64),
        },
      },
    };
    const result = investigation.parse({
      request_id: "55555555-5555-4555-8555-555555555555",
      outcome: "partial",
      reasons: ["undated_records_excluded"],
      scope: null,
      memories: [
        {
          memory: {
            memory_id: id,
            revision: 1,
            origin: null,
            content: {
              kind: "episode",
              modality: "FACTUAL_ASSERTION",
              confidence: 0.95,
              payload: { event: "SYNTHETIC <script>alert(1)</script>" },
              evidence: [proof],
              provenance: [],
            },
            temporal: {
              valid_from: "2025-07-01T00:00:00.000000Z",
              precision: "month",
            },
          },
          score: 0,
          ranking: [
            {
              method: "bounded_related",
              score: 0,
              reasons: ["authorized_related_scope"],
            },
          ],
        },
      ],
      observations: [
        {
          source: {
            evidence: proof.evidence,
            quote: "Synthetic authorized original.",
          },
          role: "user",
        },
        {
          source: {
            evidence: {
              source,
              span: {
                ...proof.evidence.span,
                start_codepoint: 30,
                end_codepoint: 60,
                quoted_span_sha256: "3".repeat(64),
              },
            },
            quote: "Different passage in the same source.",
          },
          role: "user",
        },
      ],
      associations: [],
      clusters: [],
      counters: {},
      prompt_memories: [{ memory_id: id, revision: 1 }],
      prompt_observations: [0],
    });
    const html = renderToStaticMarkup(
      <Inspector result={result} answer={() => undefined} busy={false} />,
    );
    expect(html).toContain("Dates and periods");
    expect(html).toContain("FACTUAL_ASSERTION");
    expect(html).toContain("authorized_related_scope");
    expect(html).toContain("recall score 0 (not confidence)");
    expect(html).toContain('href="#observation-0"');
    expect(html).not.toContain('href="#observation-1"');
    expect(html).toContain('id="observation-0"');
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
  });
});
