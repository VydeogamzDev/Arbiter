import { expect, it } from "vitest";
import { intervalToDuration } from "../index.js";

it("years_months_days", () => {
  expect(intervalToDuration({ start: new Date(2020, 0, 10), end: new Date(2023, 3, 15) }))
    .toEqual({ years: 3, months: 3, days: 5 });
  expect(intervalToDuration({ start: new Date(2021, 5, 1, 8), end: new Date(2022, 7, 20, 10, 30) }))
    .toEqual({ years: 1, months: 2, days: 19, hours: 2, minutes: 30 });
});
