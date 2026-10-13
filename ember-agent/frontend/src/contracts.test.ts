import { describe, expect, it } from "vitest";
import { libraryIds, recallSelection } from "./contracts";

describe("source controls", () => {
  it("deduplicates selected logical stores without accepting malformed IDs", () => {
    const id = "11111111-1111-4111-8111-111111111111";
    expect(libraryIds(id + ", " + id)).toEqual([id]);
    expect(() => libraryIds("a path or namespace")).toThrow();
  });
  it("keeps import and local selection independent and bounded", () => {
    const selection = {
      local_enabled: false,
      import_mode: "all",
      included_libraries: [],
      excluded_libraries: [],
      revision: 0,
    };
    expect(recallSelection.parse(selection).import_mode).toBe("all");
    expect(() =>
      recallSelection.parse({ ...selection, revision: -1 }),
    ).toThrow();
  });
});
