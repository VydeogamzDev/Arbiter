import { describe, expect, it } from "vitest";
import { formatDuration } from "../index.js";

describe("formatDuration options", () => {
  it("max_units", () => {
    expect(formatDuration({ years: 2, months: 9, weeks: 1, days: 7 }, { maxUnits: 2 })).toBe("2 years 9 months");
  });
  it("max_units_zero", () => {
    expect(formatDuration({ years: 0, months: 1, days: 3 },
      { format: ["years", "months", "days"], zero: true, maxUnits: 2 })).toBe("0 years 1 month");
  });
  it("last_delimiter", () => {
    expect(formatDuration({ years: 2, months: 9, days: 3 }, { lastDelimiter: " and " }))
      .toBe("2 years 9 months and 3 days");
    expect(formatDuration({ years: 2, months: 9, days: 3 }, { delimiter: ", ", lastDelimiter: " and " }))
      .toBe("2 years, 9 months and 3 days");
  });
  it("single_unit", () => {
    expect(formatDuration({ days: 3 }, { lastDelimiter: " and " })).toBe("3 days");
  });
  it("zero_text", () => {
    expect(formatDuration({}, { zeroText: "now" })).toBe("now");
    expect(formatDuration({ seconds: 0 }, { zeroText: "now" })).toBe("now");
  });
  it("default_unchanged", () => {
    expect(formatDuration({})).toBe("");
    expect(formatDuration({ years: 2, days: 3 })).toBe("2 years 3 days");
  });
});
