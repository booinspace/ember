import { describe, expect, it, vi } from "vitest";
import { createRequestId, enableLanIds } from "./ids";

describe("HTTP LAN request identifiers", () => {
  it("uses cryptographic bytes without depending on the secure-context randomUUID API", () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => bytes.fill(0xab));
    vi.stubGlobal("crypto", { getRandomValues });
    try {
      expect(createRequestId()).toBe("abababab-abab-4bab-abab-abababababab");
      expect(getRandomValues).toHaveBeenCalledOnce();
      enableLanIds();
      expect(globalThis.crypto.randomUUID()).toBe(
        "abababab-abab-4bab-abab-abababababab",
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
