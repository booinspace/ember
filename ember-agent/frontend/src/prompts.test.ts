import { afterEach, describe, expect, it, vi } from "vitest";
import { control, promptDocuments } from "./contracts";
const revision = "a".repeat(64);
const documents = {
  character_id: "11111111-1111-4111-8111-111111111111",
  system: { markdown: "Shared", revision, error: null },
  personality: { markdown: "Style", revision, error: null },
  snapshot: null,
  error: "prompt_instruction_budget_exceeded",
  instruction_limit: 1750,
  applied_revision: null,
};
afterEach(() => vi.unstubAllGlobals());
describe("prompt editor contract", () => {
  it("accepts repairable documents with invalid combined instructions", () => {
    expect(promptDocuments.parse(documents).snapshot).toBeNull();
    expect(() =>
      promptDocuments.parse({
        ...documents,
        system: { ...documents.system, revision: "not a revision" },
      }),
    ).toThrow();
  });
  it("uses PUT and preserves actionable save conflicts", async () => {
    const fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "prompt_revision_conflict" }), {
          status: 409,
        }),
    );
    vi.stubGlobal("fetch", fetch);
    const body = {
      kind: "personality",
      markdown: "Unsaved draft",
      system_revision: revision,
      personality_revision: revision,
    };
    await expect(
      control("prompts/personality", promptDocuments, body, undefined, "PUT"),
    ).rejects.toThrow("prompt_revision_conflict");
    expect(fetch).toHaveBeenCalledWith(
      "/api/prompts/personality",
      expect.objectContaining({
        method: "PUT",
        credentials: "same-origin",
        body: JSON.stringify(body),
      }),
    );
  });
});
