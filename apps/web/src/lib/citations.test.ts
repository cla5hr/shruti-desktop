import { describe, expect, it } from "vitest";
import { splitCitations } from "./citations";

describe("splitCitations", () => {
  it("passes plain text through", () => {
    expect(splitCitations("no citations here")).toEqual([
      { type: "text", value: "no citations here" },
    ]);
  });

  it("extracts m:ss citations with correct ms", () => {
    const chunks = splitCitations("Priority is bench test [0:28] and budget [1:05].");
    expect(chunks).toEqual([
      { type: "text", value: "Priority is bench test " },
      { type: "cite", label: "[0:28]", ms: 28_000 },
      { type: "text", value: " and budget " },
      { type: "cite", label: "[1:05]", ms: 65_000 },
      { type: "text", value: "." },
    ]);
  });

  it("handles h:mm:ss", () => {
    const chunks = splitCitations("[1:02:05]");
    expect(chunks).toEqual([{ type: "cite", label: "[1:02:05]", ms: 3_725_000 }]);
  });

  it("ignores bracketed non-times", () => {
    expect(splitCitations("[TODO] fix")).toEqual([{ type: "text", value: "[TODO] fix" }]);
  });
});
