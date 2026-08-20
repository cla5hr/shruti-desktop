import { describe, expect, it } from "vitest";
import { msToClock } from "./time";

describe("msToClock", () => {
  it("formats sub-hour times as m:ss", () => {
    expect(msToClock(0)).toBe("0:00");
    expect(msToClock(65_000)).toBe("1:05");
    expect(msToClock(599_999)).toBe("9:59");
  });
  it("formats hour-plus times as h:mm:ss", () => {
    expect(msToClock(3_600_000)).toBe("1:00:00");
    expect(msToClock(3_725_000)).toBe("1:02:05");
  });
  it("clamps negatives to zero", () => {
    expect(msToClock(-500)).toBe("0:00");
  });
});
