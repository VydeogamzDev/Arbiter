import { expect, it } from "vitest";
import { getWeekOfMonth } from "../index.js";

it("first_is_week_one", () => {
  for (let m = 0; m < 24; m++)
    for (const ws of [0, 1, 6] as const)
      expect(getWeekOfMonth(new Date(2024, m, 1), { weekStartsOn: ws })).toBe(1);
  expect(getWeekOfMonth(new Date(2024, 8, 8), { weekStartsOn: 0 })).toBe(2);
});
