"""Demand forecasting for terminals and aggregation to every tier.

The forecaster only sees information that is legitimately available *before*
the simulation starts: the terminal consumer-demand history and the known
calendar (weekday, planned promotion lift) for history and future.  It does
**not** read ``future_demand.csv``.

A robust multiplicative decomposition is used:

* weekday factors from the full history;
* the current *level* is anchored on the most recent 28 days (so the forecast
  tracks the demand level near the annual peak rather than extrapolating a
  trend fitted on the whole history);
* a gentle linear trend is estimated from the last 56 days;
* the known promotion lift from the calendar is applied directly.

The per-day forecast standard deviation is the root-mean-square one-step-ahead
error over the recent window and is used for safety-stock sizing.  Upper-tier
"demand" is the sum of the terminal forecasts that sit under that node.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from .data_loader import Dataset, future_date_at


@dataclass
class TerminalForecast:
    terminal_id: str
    mean_by_day: dict[int, float]   # 1-based simulation day -> expected demand
    std: float                      # per-day forecast std (units)
    level: float                    # recent deseasonalised level reference


def _least_squares(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Return (slope, intercept) of y = a + b*x via normal equations."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if sxx == 0:
        return 0.0, mean_y
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    return slope, intercept


def fit_terminal_forecast(
    dataset: Dataset,
    terminal_id: str,
    horizon_days: int,
) -> TerminalForecast:
    """Fit the decomposition on history and forecast the next ``horizon_days``.

    The returned ``mean_by_day`` is keyed by 1-based simulation day number.

    Approach — weekday-anchored recent average with trend:

        demand = level_by_weekday * (1 + trend)^t * (1 + promo) + noise

    * ``level_by_weekday`` is the average demand (with promo removed) for
      each weekday over the last 56 days.  This captures both the intra-week
      pattern and the current demand level in one step.
    * ``trend`` is the per-day growth factor from a log-linear regression of
      the deseasonalised series over the last 84 days.  This captures each
      terminal's recent trajectory (including its approach to the annual
      peak) without requiring an explicit annual model — the history only
      covers Feb–Aug so a cosine annual phase is not reliably identifiable.
    * The known promotion lift is applied directly from the calendar.
    """
    history: list[tuple[date, int, float, int]] = []
    for offset in range(dataset.history_days):
        day = dataset.history_start + timedelta(days=offset)
        entry = dataset.history_demand[(day.isoformat(), terminal_id)]
        cal = dataset.calendar[(day.isoformat(), terminal_id)]
        history.append((day, entry.demand_units, entry.planned_promotion_lift, cal.weekday_monday_zero))

    n = len(history)

    # 1. Remove the known promotion lift.
    base = [units / (1.0 + lift) if (1.0 + lift) > 0 else float(units)
            for _, units, lift, _ in history]

    # ---- Validation-based bias estimation ----
    # Hold out the last 28 days as a validation window.  Fit the model on the
    # preceding days, predict the validation window, and use the mean error as
    # the bias correction.  This captures the systematic error (e.g. under-
    # forecasting the September peak) that the in-sample residual misses.
    val_window = min(28, n)
    train_end = n - val_window
    if train_end < 14:
        train_end = n  # not enough data to hold out; use all history

    # 2. Weekday factors from the training window.
    train_base = base[:train_end]
    grand_mean_train = sum(train_base) / train_end
    ws = [0.0] * 7
    wc = [0] * 7
    for value, (_, _, _, weekday) in zip(train_base, history[:train_end]):
        ws[weekday] += value
        wc[weekday] += 1
    wf_train = [(ws[w] / wc[w]) / grand_mean_train if wc[w] else 1.0 for w in range(7)]

    # 3. Deseasonalised training series.
    ds_train = [train_base[i] / wf_train[history[i][3]] for i in range(train_end)]
    log_ds_train = [math.log(max(d, 1e-6)) for d in ds_train]

    # 4. Growth rate from log-linear regression over the last 84 days of training.
    tw = min(84, train_end)
    ts = train_end - tw
    tx = [float(i - ts) for i in range(ts, train_end)]
    ty = log_ds_train[ts:]
    dg_train, _ = _least_squares(tx, ty)

    # 5. Anchor on the last 56 days of training.
    lw = min(56, train_end)
    ls = train_end - lw
    rll_train = sum(log_ds_train[ls:]) / lw

    # 6. Predict the validation window and compute the bias.
    val_errors: list[float] = []
    for idx in range(train_end, n):
        day, units, lift, weekday = history[idx]
        t_offset = idx - train_end + 1  # 1-based for the validation window
        log_pred = rll_train + dg_train * t_offset
        pred = math.exp(log_pred) * wf_train[weekday] * (1.0 + lift)
        val_errors.append(units - pred)
    val_bias = sum(val_errors) / len(val_errors) if val_errors else 0.0
    val_std_raw = math.sqrt(sum(e * e for e in val_errors) / len(val_errors)) if val_errors else 0.0
    # Remove the bias from the std so the safety-stock calculation uses the
    # true residual variability (the bias is already added to the mean).
    val_std = math.sqrt(max(val_std_raw * val_std_raw - val_bias * val_bias, 0.0))

    # ---- Final model on full history ----
    # 7. Weekday factors from the full history (last 56 days for level).
    level_window = min(56, n)
    level_start = n - level_window
    weekday_sums = [0.0] * 7
    weekday_counts = [0] * 7
    for idx in range(level_start, n):
        weekday = history[idx][3]
        weekday_sums[weekday] += base[idx]
        weekday_counts[weekday] += 1
    weekday_level = [
        weekday_sums[w] / weekday_counts[w] if weekday_counts[w] else 0.0
        for w in range(7)
    ]
    grand_level = sum(weekday_level) / 7
    weekday_factors = [wl / grand_level if grand_level > 0 else 1.0 for wl in weekday_level]
    deseason = [base[idx] / weekday_factors[history[idx][3]] for idx in range(n)]
    log_deseason = [math.log(max(d, 1e-6)) for d in deseason]

    # 8. Growth rate from log-linear regression over the last 84 days.
    trend_window = min(84, n)
    trend_start = n - trend_window
    trend_xs = [float(i - trend_start) for i in range(trend_start, n)]
    trend_ys = log_deseason[trend_start:]
    daily_growth, _ = _least_squares(trend_xs, trend_ys)

    # 9. Anchor: average log deseason over the level window (last 56 days).
    recent_log_level = sum(log_deseason[level_start:]) / level_window

    # 10. In-sample residual std (for the forecast std estimate).
    recent_window = min(28, n)
    recent_start = n - recent_window
    errors: list[float] = []
    for idx in range(recent_start, n):
        day, units, lift, weekday = history[idx]
        t_offset = idx - recent_start
        log_pred = recent_log_level + daily_growth * t_offset
        pred = math.exp(log_pred) * weekday_factors[weekday] * (1.0 + lift)
        errors.append(units - pred)
    in_sample_std = math.sqrt(sum(e * e for e in errors) / len(errors)) if errors else 0.0

    # Use the validation std (out-of-sample) if available, else in-sample.
    std = val_std if val_std > 0 else in_sample_std

    # 11. Project forward, applying the validation bias correction.
    mean_by_day: dict[int, float] = {}
    for day_number in range(1, horizon_days + 1):
        future_day = future_date_at(dataset, day_number)
        cal = dataset.calendar[(future_day.isoformat(), terminal_id)]
        log_pred = recent_log_level + daily_growth * day_number
        mean = math.exp(log_pred) * weekday_factors[cal.weekday_monday_zero] * (1.0 + cal.planned_promotion_lift) + val_bias
        mean_by_day[day_number] = max(0.0, mean)

    return TerminalForecast(
        terminal_id=terminal_id,
        mean_by_day=mean_by_day,
        std=std,
        level=grand_level,
    )


def fit_all_terminal_forecasts(dataset: Dataset) -> dict[str, TerminalForecast]:
    return {
        terminal_id: fit_terminal_forecast(dataset, terminal_id, dataset.horizon_days)
        for terminal_id in dataset.terminal_ids
    }


def aggregate_forecast(
    forecasts: dict[str, TerminalForecast],
    terminal_ids: Iterable[str],
) -> dict[int, float]:
    """Sum the per-day mean forecasts of the given terminals."""
    result: dict[int, float] = {}
    for terminal_id in terminal_ids:
        for day_number, mean in forecasts[terminal_id].mean_by_day.items():
            result[day_number] = result.get(day_number, 0.0) + mean
    return result


def aggregate_std(forecasts: dict[str, TerminalForecast], terminal_ids: Iterable[str]) -> float:
    """Pooled per-day std assuming independence; upper bound for safety sizing."""
    variance = sum(forecasts[t].std ** 2 for t in terminal_ids)
    return math.sqrt(variance)
