import { expect, test } from "vitest";
import { skillCatalog, skillDocument } from "./Skills";

const summary = {
  id: "shared/brief",
  name: "brief",
  description: "Brief",
  version: "1.0.0",
  source: "shared",
  content_sha256: "a".repeat(64),
  enabled: false,
  requirements: ["web.read_page"],
  unavailable: ["tool_required:web.read_page"],
  diagnostics: [],
};
test("skill contracts retain explicit readiness, original metadata and complete instructions", () => {
  const document = skillDocument.parse({
    skill: summary,
    markdown: "---\nname: brief\n---\nComplete procedure.",
    metadata: { metadata: { openclaw: { unknown: true } } },
    extensions: { openclaw: { unknown: true } },
    resources: [],
  });
  expect(document.extensions).toEqual({ openclaw: { unknown: true } });
  expect(document.markdown).toContain("Complete procedure.");
  expect(document.skill.unavailable).toEqual(["tool_required:web.read_page"]);
});
test("malformed metadata cannot masquerade as an enabled available catalog", () => {
  expect(
    skillCatalog.safeParse({
      provider: "fixture",
      revision: "one",
      skills: [{ ...summary, enabled: "true" }],
      unavailable: null,
    }).success,
  ).toBe(false);
  expect(
    skillCatalog.safeParse({
      provider: "fixture",
      revision: "one",
      skills: [{ ...summary, content_sha256: "invalid" }],
      unavailable: null,
    }).success,
  ).toBe(false);
});
