import { describe, expect, it } from "vitest";
import { buildPreviewUrl, libraryIds, recallSelection } from "./contracts";

describe("Build preview links", () => {
  const host = "build-" + "a".repeat(32) + ".localhost";
  const url = `http://${host}:49152/`;
  it("accepts only the isolated, runtime-issued preview URL format", () => {
    expect(buildPreviewUrl.parse(url)).toBe(url);
    for (const value of [
      "javascript:void(0)",
      "http://127.0.0.1:49152/",
      `http://${host}.example.test:49152/`,
      `http://name@${host}:49152/`,
      `${url}?unexpected=1`,
      `http://${host}:81/`,
      `https://${host}:49152/`,
    ]) {
      expect(buildPreviewUrl.safeParse(value).success).toBe(false);
    }
  });
});

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
