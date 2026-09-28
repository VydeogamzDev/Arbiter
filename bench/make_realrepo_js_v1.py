"""Generate realrepo_js_v1: five 3-prompt sessions on date-fns 4.1.0 (TypeScript, vitest, 1,727 files).

Written before Arbiter was ever run on this repo, so nothing here was tuned against it. Set up once:

    git clone --depth 1 --branch v4.1.0 https://github.com/date-fns/date-fns.git D:/ArbiterBench/repos/date-fns-4.1.0
    cd D:/ArbiterBench/deps/date-fns && npm install vitest@^1.3.1 sinon@^7.4.1 @date-fns/tz@^1.0.2 \
        @date-fns/utc@^1.2.0 lodash js-fns fp-ts typescript@^5.4.5
    python -m bench.make_realrepo_js_v1

Workspaces link node_modules to the shared install. Hidden tests are one vitest file per session,
placed at src/__hidden__/test.ts (the repo's vitest config collects src/**/test.ts); each test's
title is its requirement's test id.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import yaml

OUT = Path(__file__).resolve().parent / "tasks_realrepo_js_v1"
REPO = Path("D:/ArbiterBench/repos/date-fns-4.1.0")
DEPS = "D:/ArbiterBench/deps/date-fns/node_modules"


def edit(rel: str, *reps: tuple[str, str]) -> tuple[str, str]:
    text = (REPO / rel).read_text("utf-8")
    for a, b in reps:
        assert text.count(a) == 1, (rel, a)
        text = text.replace(a, b)
    return rel, text


def new(rel: str, text: str) -> tuple[str, str]:
    return rel, textwrap.dedent(text).lstrip("\n")


def hidden(body: str) -> str:
    return 'import { describe, expect, it } from "vitest";\n' + textwrap.dedent(body).lstrip("\n")


TASKS = []

# ------------------------------------------------------------------ 1. weekend days
TASKS.append({
    "id": "js_weekend_days",
    "prompts": [
        "Let `isWeekend` take a `weekendDays` option: the days of the week that count as the weekend (0 = Sunday "
        "... 6 = Saturday). The default stays Saturday and Sunday; this is so Friday-Saturday weekends can be used.",
        "`eachWeekendOfInterval` should honor the same `weekendDays` option.",
        "And `differenceInBusinessDays` too: days listed in `weekendDays` are not business days.",
    ],
    "requirements": {"weekend_default": "weekend_default", "weekend_option": "weekend_option",
                     "each_weekend_option": "each_weekend_option", "each_weekend_default": "each_weekend_default",
                     "business_days_option": "business_days_option", "business_days_default": "business_days_default"},
    "hidden": hidden('''
        import { differenceInBusinessDays, eachWeekendOfInterval, isWeekend } from "../index.js";

        const d = (day: number) => new Date(2024, 0, day);

        describe("weekendDays", () => {
          it("weekend_default", () => {
            expect(isWeekend(d(6))).toBe(true);
            expect(isWeekend(d(5))).toBe(false);
          });
          it("weekend_option", () => {
            expect(isWeekend(d(5), { weekendDays: [5, 6] })).toBe(true);
            expect(isWeekend(d(7), { weekendDays: [5, 6] })).toBe(false);
          });
          it("each_weekend_option", () => {
            const r = eachWeekendOfInterval({ start: d(1), end: d(14) }, { weekendDays: [5, 6] });
            expect(r.map((x) => x.getDate())).toEqual([5, 6, 12, 13]);
          });
          it("each_weekend_default", () => {
            const r = eachWeekendOfInterval({ start: d(1), end: d(14) });
            expect(r.map((x) => x.getDate())).toEqual([6, 7, 13, 14]);
          });
          it("business_days_option", () => {
            expect(differenceInBusinessDays(d(6), d(1), { weekendDays: [5, 6] })).toBe(4);
            expect(differenceInBusinessDays(d(22), d(1), { weekendDays: [5, 6] })).toBe(15);
          });
          it("business_days_default", () => {
            expect(differenceInBusinessDays(d(6), d(1))).toBe(5);
            expect(differenceInBusinessDays(d(22), d(1))).toBe(15);
          });
        });
        '''),
    "solution": [
        edit("src/isWeekend/index.ts",
             ("export interface IsWeekendOptions extends ContextOptions<Date> {}",
              "export interface IsWeekendOptions extends ContextOptions<Date> {\n"
              "  /** The days of the week that are the weekend (0 = Sunday ... 6 = Saturday). Default: [0, 6] */\n"
              "  weekendDays?: readonly number[];\n}"),
             ("  return day === 0 || day === 6;", "  return (options?.weekendDays ?? [0, 6]).includes(day);")),
        edit("src/eachWeekendOfInterval/index.ts",
             ("  extends ContextOptions<DateType> {}",
              "  extends ContextOptions<DateType> {\n  /** The days of the week that are the weekend. Default: [0, 6] */\n"
              "  weekendDays?: readonly number[];\n}"),
             ("    if (isWeekend(date)) weekends.push(constructFrom(start, date));",
              "    if (isWeekend(date, { weekendDays: options?.weekendDays }))\n"
              "      weekends.push(constructFrom(start, date));")),
        edit("src/differenceInBusinessDays/index.ts",
             ("export interface DifferenceInBusinessDaysOptions extends ContextOptions<Date> {}",
              "export interface DifferenceInBusinessDaysOptions extends ContextOptions<Date> {\n"
              "  /** The days of the week that are the weekend. Default: [0, 6] */\n"
              "  weekendDays?: readonly number[];\n}"),
             ("  let result = weeks * 5;",
              "  const businessDaysPerWeek = 7 - new Set(options?.weekendDays ?? [0, 6]).size;\n"
              "  let result = weeks * businessDaysPerWeek;")),
    ],
})

# ------------------------------------------------------------------ 2. roundToNearestSeconds
TASKS.append({
    "id": "js_round_seconds",
    "prompts": [
        "Add `roundToNearestSeconds(date, options)` next to `roundToNearestMinutes`, with the same `nearestTo` and "
        "`roundingMethod` options (it rounds to seconds; milliseconds are what gets rounded away). Export it from "
        "the package index like the other functions.",
        "roundToNearestSeconds should return an Invalid Date when `nearestTo` is below 1 or above 30, the way "
        "roundToNearestMinutes does.",
        "Change of plan: for roundToNearestSeconds, allow any `nearestTo` from 1 to 59. roundToNearestMinutes "
        "keeps its 1-30 limit.",
    ],
    "requirements": {"rounds": "rounds", "rounding_method": "rounding_method", "nearest_to": "nearest_to",
                     "invalid_range": "invalid_range", "wide_range": "wide_range", "minutes_unchanged": "minutes_unchanged"},
    "hidden": hidden('''
        import { roundToNearestMinutes, roundToNearestSeconds } from "../index.js";

        const at = (s: number, ms: number) => new Date(2024, 0, 1, 12, 0, s, ms);

        describe("roundToNearestSeconds", () => {
          it("rounds", () => {
            expect(roundToNearestSeconds(at(10, 600))).toEqual(at(11, 0));
            expect(roundToNearestSeconds(at(10, 400))).toEqual(at(10, 0));
          });
          it("rounding_method", () => {
            expect(roundToNearestSeconds(at(10, 600), { roundingMethod: "floor" })).toEqual(at(10, 0));
            expect(roundToNearestSeconds(at(10, 100), { roundingMethod: "ceil" })).toEqual(at(11, 0));
          });
          it("nearest_to", () => {
            expect(roundToNearestSeconds(at(10, 600), { nearestTo: 15 })).toEqual(at(15, 0));
          });
          it("invalid_range", () => {
            expect(isNaN(+roundToNearestSeconds(at(10, 0), { nearestTo: 0 }))).toBe(true);
            expect(isNaN(+roundToNearestSeconds(at(10, 0), { nearestTo: 60 } as never))).toBe(true);
          });
          it("wide_range", () => {
            expect(roundToNearestSeconds(at(40, 0), { nearestTo: 45 } as never)).toEqual(at(45, 0));
          });
          it("minutes_unchanged", () => {
            expect(isNaN(+roundToNearestMinutes(at(0, 0), { nearestTo: 45 } as never))).toBe(true);
          });
        });
        '''),
    "solution": [
        new("src/roundToNearestSeconds/index.ts", '''
            import { getRoundingMethod } from "../_lib/getRoundingMethod/index.js";
            import { constructFrom } from "../constructFrom/index.js";
            import { toDate } from "../toDate/index.js";
            import type { ContextOptions, DateArg, RoundingOptions } from "../types.js";

            /**
             * The {@link roundToNearestSeconds} function options.
             */
            export interface RoundToNearestSecondsOptions<DateType extends Date = Date>
              extends RoundingOptions,
                ContextOptions<DateType> {
              /** The nearest number of seconds to round to (1-59). Default: 1 */
              nearestTo?: number;
            }

            /**
             * @name roundToNearestSeconds
             * @category Second Helpers
             * @summary Rounds the given date to the nearest second
             */
            export function roundToNearestSeconds<
              DateType extends Date,
              ResultDate extends Date = DateType,
            >(
              date: DateArg<DateType>,
              options?: RoundToNearestSecondsOptions<ResultDate>,
            ): ResultDate {
              const nearestTo = options?.nearestTo ?? 1;
              if (nearestTo < 1 || nearestTo > 59) return constructFrom(date, NaN);
              const date_ = toDate(date, options?.in);
              const seconds = date_.getSeconds() + date_.getMilliseconds() / 1000;
              const roundingMethod = getRoundingMethod(options?.roundingMethod ?? "round");
              date_.setSeconds(roundingMethod(seconds / nearestTo) * nearestTo, 0);
              return date_ as ResultDate;
            }
            '''),
        edit("src/index.ts", ('export * from "./roundToNearestMinutes/index.js";',
                              'export * from "./roundToNearestMinutes/index.js";\n'
                              'export * from "./roundToNearestSeconds/index.js";')),
    ],
})

# ------------------------------------------------------------------ 3. closest date direction
TASKS.append({
    "id": "js_closest_direction",
    "prompts": [
        "Give `closestIndexTo` an options argument with a `direction` option: 'any' (the default), 'before' or "
        "'after'. With 'before' only dates at or before the given date count; with 'after', only dates at or "
        "after it.",
        "`closestTo` should accept the same `direction` option.",
        "When no date qualifies for the direction, both should return undefined.",
    ],
    "requirements": {"index_default": "index_default", "index_before": "index_before", "index_after": "index_after",
                     "closest_direction": "closest_direction", "none_qualifies": "none_qualifies"},
    "hidden": hidden('''
        import { closestIndexTo, closestTo } from "../index.js";

        const d = (day: number) => new Date(2024, 0, day);
        const dates = [d(5), d(12), d(20)];

        describe("direction", () => {
          it("index_default", () => {
            expect(closestIndexTo(d(10), dates)).toBe(1);
          });
          it("index_before", () => {
            expect(closestIndexTo(d(10), dates, { direction: "before" })).toBe(0);
            expect(closestIndexTo(d(12), dates, { direction: "before" })).toBe(1);
          });
          it("index_after", () => {
            expect(closestIndexTo(d(13), dates, { direction: "after" })).toBe(2);
          });
          it("closest_direction", () => {
            expect(closestTo(d(10), dates, { direction: "before" })).toEqual(d(5));
            expect(closestTo(d(13), dates, { direction: "after" })).toEqual(d(20));
            expect(closestTo(d(13), dates)).toEqual(d(12));
          });
          it("none_qualifies", () => {
            expect(closestIndexTo(d(1), dates, { direction: "before" })).toBeUndefined();
            expect(closestTo(d(25), dates, { direction: "after" })).toBeUndefined();
          });
        });
        '''),
    "solution": [
        edit("src/closestIndexTo/index.ts",
             ("export function closestIndexTo(\n  dateToCompare: DateArg<Date> & {},\n  dates: Array<DateArg<Date> & {}>,\n): number | undefined {",
              "export interface ClosestIndexToOptions {\n  /** Only count dates before ('before') or after ('after') it. Default: 'any' */\n"
              "  direction?: \"any\" | \"before\" | \"after\";\n}\n\n"
              "export function closestIndexTo(\n  dateToCompare: DateArg<Date> & {},\n  dates: Array<DateArg<Date> & {}>,\n"
              "  options?: ClosestIndexToOptions,\n): number | undefined {\n  const direction = options?.direction ?? \"any\";"),
             ("    const distance = Math.abs(timeToCompare - +date_);",
              "    if (direction === \"before\" && +date_ > timeToCompare) return;\n"
              "    if (direction === \"after\" && +date_ < timeToCompare) return;\n"
              "    const distance = Math.abs(timeToCompare - +date_);")),
        edit("src/closestTo/index.ts",
             ("export interface ClosestToOptions<DateType extends Date = Date>\n  extends ContextOptions<DateType> {}",
              "export interface ClosestToOptions<DateType extends Date = Date>\n  extends ContextOptions<DateType> {\n"
              "  /** Only count dates before ('before') or after ('after') it. Default: 'any' */\n"
              "  direction?: \"any\" | \"before\" | \"after\";\n}"),
             ("  const index = closestIndexTo(dateToCompare_, dates_);",
              "  const index = closestIndexTo(dateToCompare_, dates_, { direction: options?.direction });")),
    ],
})

# ------------------------------------------------------------------ 4. includeSameDay
TASKS.append({
    "id": "js_same_day",
    "prompts": [
        "Give `nextDay` an `includeSameDay` option: when the date already falls on the requested weekday, return "
        "that date instead of the one a week later.",
        "Do the same for `previousDay`.",
        "The weekday helpers (nextMonday ... nextSunday and previousMonday ... previousSunday) should accept "
        "`includeSameDay` too.",
    ],
    "requirements": {"next_same": "next_same", "next_default": "next_default", "previous_same": "previous_same",
                     "helpers": "helpers"},
    "hidden": hidden('''
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
            expect(previousDay(d(0, 3), 3)).toEqual(d(11, 27));
          });
          it("helpers", () => {
            expect(nextMonday(d(0, 1), { includeSameDay: true })).toEqual(d(0, 1));
            expect(previousFriday(d(0, 5), { includeSameDay: true })).toEqual(d(0, 5));
            expect(nextSunday(d(0, 7), { includeSameDay: true })).toEqual(d(0, 7));
          });
        });
        ''').replace("d(11, 27)", "new Date(2023, 11, 27)"),
    "solution": [
        edit("src/nextDay/index.ts",
             ("export interface NextDayOptions<DateType extends Date = Date>\n  extends ContextOptions<DateType> {}",
              "export interface NextDayOptions<DateType extends Date = Date>\n  extends ContextOptions<DateType> {\n"
              "  /** Return the date itself when it already falls on that day */\n  includeSameDay?: boolean;\n}"),
             ("  if (delta <= 0) delta += 7;", "  if (delta < 0 || (delta === 0 && !options?.includeSameDay)) delta += 7;")),
        edit("src/previousDay/index.ts",
             ("  extends ContextOptions<DateType> {}",
              "  extends ContextOptions<DateType> {\n  /** Return the date itself when it already falls on that day */\n"
              "  includeSameDay?: boolean;\n}"),
             ("  if (delta <= 0) delta += 7;", "  if (delta < 0 || (delta === 0 && !options?.includeSameDay)) delta += 7;")),
    ] + [edit(f"src/{fn}/index.ts", ("  extends ContextOptions<DateType> {}",
                                     "  extends ContextOptions<DateType> {\n  includeSameDay?: boolean;\n}"))
         for fn in [f"{w}{day}" for w in ("next", "previous")
                    for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")]],
})

# ------------------------------------------------------------------ 5. formatDuration options
TASKS.append({
    "id": "js_duration_units",
    "prompts": [
        "Add a `maxUnits` option to `formatDuration`: only the N largest units that would appear in the output are "
        "written (default: no limit).",
        "Also add a `lastDelimiter` option to formatDuration, used between the last two units instead of "
        "`delimiter` (so you can get \"2 years, 9 months and 3 days\").",
        "And a `zeroText` option: what to return when nothing would be written (default: the empty string, as now).",
    ],
    "requirements": {"max_units": "max_units", "max_units_zero": "max_units_zero", "last_delimiter": "last_delimiter",
                     "single_unit": "single_unit", "zero_text": "zero_text", "default_unchanged": "default_unchanged"},
    "hidden": hidden('''
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
        '''),
    "solution": [edit(
        "src/formatDuration/index.ts",
        ('''  /** The delimiter string to use */
  delimiter?: string;
}''', '''  /** The delimiter string to use */
  delimiter?: string;
  /** Write at most this many units (the largest ones) */
  maxUnits?: number;
  /** The delimiter between the last two units (default: `delimiter`) */
  lastDelimiter?: string;
  /** Returned when nothing would be written (default: "") */
  zeroText?: string;
}'''),
        ('''    }, [] as string[])
    .join(delimiter);

  return result;''', '''    }, [] as string[])
    .slice(0, options?.maxUnits ?? undefined);

  if (!result.length) return options?.zeroText ?? "";
  const last = options?.lastDelimiter;
  if (last === undefined || result.length < 2) return result.join(delimiter);
  return result.slice(0, -1).join(delimiter) + last + result[result.length - 1];'''),
    )],
})


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for t in TASKS:
        d = OUT / t["id"]
        (d / "hidden").mkdir(parents=True)
        (d / "hidden" / "test.ts").write_text(t["hidden"], encoding="utf-8", newline="\n")
        for rel, text in t["solution"]:
            p = d / "solution" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")
        meta = {"id": t["id"], "category": "multi_turn", "prompts": t["prompts"], "requirements": t["requirements"],
                "repo_src": REPO.as_posix(), "link": {"node_modules": DEPS}, "hidden_runner": "vitest",
                "integrity": "modified", "pre_index": True,
                "notes": "date-fns 4.1.0 (1,727 files, TypeScript, vitest); written before any Arbiter run on it"}
        (d / "task.yaml").write_text(yaml.safe_dump(meta, sort_keys=False, width=110), encoding="utf-8")
    print(f"wrote {len(TASKS)} tasks to {OUT}")


if __name__ == "__main__":
    main()
