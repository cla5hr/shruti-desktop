import { describe, expect, it } from "vitest";
import { findActiveIndex } from "./segments";

describe("findActiveIndex", () => {
  const starts = [0, 4000, 9000, 14000];

  it("returns -1 before the first segment", () => {
    expect(findActiveIndex([1000, 2000], 500)).toBe(-1);
  });
  it("picks the segment containing t", () => {
    expect(findActiveIndex(starts, 0)).toBe(0);
    expect(findActiveIndex(starts, 3999)).toBe(0);
    expect(findActiveIndex(starts, 4000)).toBe(1);
    expect(findActiveIndex(starts, 13000)).toBe(2);
  });
  it("sticks to the last segment past the end", () => {
    expect(findActiveIndex(starts, 99999)).toBe(3);
  });
  it("handles empty input", () => {
    expect(findActiveIndex([], 100)).toBe(-1);
  });
});
