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
