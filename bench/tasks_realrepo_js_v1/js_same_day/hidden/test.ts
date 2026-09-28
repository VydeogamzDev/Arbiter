import { describe, expect, it } from "vitest";
import { nextDay, nextMonday, previousDay, previousFriday, nextSunday } from "../index.js";

const d = (m: number, day: number) => new Date(2024, m, day);

describe("includeSameDay", () => {
  it("next_same", () => {
    expect(nextDay(d(0, 1), 1, { includeSameDay: true })).toEqual(d(0, 1));
    expect(nextDay(d(0, 1), 3, { includeSameDay: true })).toEqual(d(0, 3));
  });
  it("next_default", () => {
    expect(nextDay(d(0, 1), 1)).toEqual(d(0, 8));
  });
  it("previous_same", () => {
    expect(previousDay(d(0, 3), 3, { includeSameDay: true })).toEqual(d(0, 3));
    expect(previousDay(d(0, 3), 3)).toEqual(new Date(2023, 11, 27));
  });
  it("helpers", () => {
    expect(nextMonday(d(0, 1), { includeSameDay: true })).toEqual(d(0, 1));
    expect(previousFriday(d(0, 5), { includeSameDay: true })).toEqual(d(0, 5));
    expect(nextSunday(d(0, 7), { includeSameDay: true })).toEqual(d(0, 7));
  });
});
