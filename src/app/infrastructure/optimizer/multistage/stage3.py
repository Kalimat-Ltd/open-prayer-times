"""Stage 3 - Correction layers after Stage 1/2 core optimization.

Order of operations:
1) Read Stage 1 clock/offset outputs.
2) Fit residual Fourier corrections focused on unstable dates.
"""

from __future__ import annotations

import datetime
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.app.domain.models import PipelineContext, Stage3Diagnostics
from src.app.infrastructure.optimizer.objective import (
    _compute_detailed_errors,
)
from src.app.infrastructure.optimizer.shared import OFFSET_FIELDS
from src.app.infrastructure.residual_model import (
    PrayerResidualModel,
    _select_harmonics_bic,
    compute_city_residuals,
)
from .shared import Stage3Config, resolve_timezone_offset_hours


def _coerce_date(value: Any) -> Optional[datetime.date]:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.date.fromisoformat(text)
        except ValueError:
            return None
    return None


def _expand_excluded_date_ranges(
    excluded_ranges: List[Dict[str, Any]],
) -> List[datetime.date]:
    out: List[datetime.date] = []
    for item in excluded_ranges:
        start = _coerce_date(item.get("start"))
        end = _coerce_date(item.get("end"))
        if start is None or end is None:
            continue
        if end < start:
            start, end = end, start
        cursor = start
        while cursor <= end:
            out.append(cursor)
            cursor = cursor + datetime.timedelta(days=1)
    return sorted(set(out))


def _month_day_ranges_from_excluded_ranges(
    excluded_ranges: List[Dict[str, Any]],
) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for item in excluded_ranges:
        start = _coerce_date(item.get("start"))
        end = _coerce_date(item.get("end"))
        if start is None or end is None:
            continue
        out.append((start.strftime("%m-%d"), end.strftime("%m-%d")))
    return out


def _evaluate_mae(
    *,
    params: np.ndarray,
    dates: List[datetime.date],
    reference_times: Dict[datetime.date, Dict[str, str]],
    elevation: float,
    timezone: float,
    tz_name: Optional[str],
    offsets: Dict[str, float],
    extra_calc_kwargs: Dict[str, Any],
    residual_model: Optional[PrayerResidualModel] = None,
    residual_active_dates: Optional[set[datetime.date]] = None,
) -> tuple[float, Dict[str, float]]:
    _rmse, mae, _per_rmse, per_mae, _per_max, _per_signed = _compute_detailed_errors(
        params,
        available_dates=dates,
        reference_times=reference_times,
        elevation=elevation,
        timezone=timezone,
        tz_name=tz_name,
        isha_minutes=0.0,
        offsets=offsets,
        extra_calc_kwargs=extra_calc_kwargs,
        residual_model=residual_model,
        residual_active_dates=residual_active_dates,
    )
    return float(mae), {k: float(v) for k, v in per_mae.items()}


def optimize_correction_layers(
    *,
    context: PipelineContext,
    location_data: Dict[str, Any],
    reference_times: Dict[datetime.date, Dict[str, str]],
    available_dates: List[datetime.date],
    tz_name: Optional[str] = None,
    config: Optional[Stage3Config] = None,
) -> Stage3Diagnostics:
    """Run Stage 3 correction layers.

    Reads parameters from *context* (already updated by Stage 1 + 2).
    Updates *context* with refined offsets / residual corrections.
    Returns lightweight diagnostics.
    """
    started_at = time.perf_counter()
    step_timings: Dict[str, float] = {}
    cfg = config or Stage3Config()

    step_started_at = time.perf_counter()
    params = np.array(
        [
            context.fajr_angle,
            context.isha_angle,
            context.lat,
            context.lon,
            context.temp,
            context.pressure,
        ],
        dtype=float,
    )
    extra_calc_kwargs = context.to_extra_calc_kwargs()

    available = sorted(set(available_dates))
    available_set = set(available)

    stable_dates = sorted(
        {
            d
            for d in (_coerce_date(v) for v in context.dates_used_for_core)
            if d is not None and d in available_set
        }
    )
    if not stable_dates:
        stable_dates = available

    unstable_dates = [
        d
        for d in _expand_excluded_date_ranges(list(context.excluded_date_ranges or []))
        if d in available_set
    ]
    unstable_dates_set = set(unstable_dates)

    sample_date = available[0] if available else None
    timezone = resolve_timezone_offset_hours(
        location_data.get("timezone"),
        tz_name=tz_name,
        sample_date=sample_date,
    )
    step_timings["prepare_inputs"] = float(time.perf_counter() - step_started_at)

    best_offsets = {
        f: float((context.offsets or {}).get(f, 0.0) or 0.0) for f in OFFSET_FIELDS
    }

    working_reference_times = context.reference_times_for_corrections or reference_times

    # ── Offset evaluation ─────────────────────────────────────────────────
    all_dates_for_offsets = available if available else stable_dates
    step_started_at = time.perf_counter()
    all_mae_no_offsets, _ = _evaluate_mae(
        params=params,
        dates=all_dates_for_offsets,
        reference_times=working_reference_times,
        elevation=context.elevation,
        timezone=timezone,
        tz_name=tz_name,
        offsets={f: 0.0 for f in OFFSET_FIELDS},
        extra_calc_kwargs=extra_calc_kwargs,
    )
    all_mae_with_offsets, _ = _evaluate_mae(
        params=params,
        dates=all_dates_for_offsets,
        reference_times=working_reference_times,
        elevation=context.elevation,
        timezone=timezone,
        tz_name=tz_name,
        offsets=best_offsets,
        extra_calc_kwargs=extra_calc_kwargs,
    )
    step_timings["all_dates_offset_eval"] = float(time.perf_counter() - step_started_at)

    stable_mae_before_residual = float(context.stable_mae_after_offsets)
    stable_mae_after_residual = float(context.stable_mae_after_offsets)
    unstable_mae_before_residual = float("inf")
    unstable_mae_after_residual = float("inf")

    # ── Residual fitting ──────────────────────────────────────────────────
    if cfg.fit_residual_corrections and len(unstable_dates) >= int(
        cfg.min_unstable_dates_for_residuals
    ):
        residual_source_dates = unstable_dates
        step_started_at = time.perf_counter()
        city_residuals = compute_city_residuals(
            optimized_params=params,
            offsets=best_offsets,
            reference_times=working_reference_times,
            available_dates=residual_source_dates,
            elevation=context.elevation,
            timezone_val=timezone,
            tz_name=tz_name,
            isha_minutes=0.0,
            extra_calc_kwargs=extra_calc_kwargs,
        )
        step_timings["residual_series_compute"] = float(
            time.perf_counter() - step_started_at
        )

        if city_residuals:
            model = PrayerResidualModel()
            prayers_fitted = 0
            step_started_at = time.perf_counter()
            for prayer, (days_arr, residuals_arr) in city_residuals.items():
                if len(days_arr) < 10:
                    continue
                best_h, _coeffs = _select_harmonics_bic(
                    np.asarray(days_arr, dtype=float),
                    np.asarray(residuals_arr, dtype=float),
                    max_harmonics=max(1, int(cfg.max_harmonics)),
                    alpha=0.1,
                )
                model.fit_prayer(
                    prayer,
                    np.asarray(days_arr, dtype=float),
                    np.asarray(residuals_arr, dtype=float),
                    int(best_h),
                    alpha=0.1,
                )
                prayers_fitted += 1
            step_timings["residual_model_fit"] = float(
                time.perf_counter() - step_started_at
            )

            if prayers_fitted > 0:
                model.fitted = True
                model.n_cities_validated = 1
                model.set_active_month_day_ranges(
                    _month_day_ranges_from_excluded_ranges(
                        list(context.excluded_date_ranges or [])
                    )
                )

                step_started_at = time.perf_counter()
                stable_before_residual, _ = _evaluate_mae(
                    params=params,
                    dates=stable_dates,
                    reference_times=working_reference_times,
                    elevation=context.elevation,
                    timezone=timezone,
                    tz_name=tz_name,
                    offsets=best_offsets,
                    extra_calc_kwargs=extra_calc_kwargs,
                    residual_model=None,
                )
                unstable_before_residual, unstable_per_before = _evaluate_mae(
                    params=params,
                    dates=unstable_dates,
                    reference_times=working_reference_times,
                    elevation=context.elevation,
                    timezone=timezone,
                    tz_name=tz_name,
                    offsets=best_offsets,
                    extra_calc_kwargs=extra_calc_kwargs,
                    residual_model=None,
                )
                stable_after_residual, _ = _evaluate_mae(
                    params=params,
                    dates=stable_dates,
                    reference_times=working_reference_times,
                    elevation=context.elevation,
                    timezone=timezone,
                    tz_name=tz_name,
                    offsets=best_offsets,
                    extra_calc_kwargs=extra_calc_kwargs,
                    residual_model=model,
                    residual_active_dates=unstable_dates_set,
                )
                unstable_after_residual, unstable_per_after = _evaluate_mae(
                    params=params,
                    dates=unstable_dates,
                    reference_times=working_reference_times,
                    elevation=context.elevation,
                    timezone=timezone,
                    tz_name=tz_name,
                    offsets=best_offsets,
                    extra_calc_kwargs=extra_calc_kwargs,
                    residual_model=model,
                    residual_active_dates=unstable_dates_set,
                )
                step_timings["residual_guardrail_eval"] = float(
                    time.perf_counter() - step_started_at
                )

                unstable_gain = float(
                    unstable_before_residual - unstable_after_residual
                )

                prayers_to_keep = []
                for prayer in list(model.prayer_models.keys()):
                    before_val = float(unstable_per_before.get(prayer, float("inf")))
                    after_val = float(unstable_per_after.get(prayer, float("inf")))
                    delta = after_val - before_val
                    gain = before_val - after_val
                    if delta > float(cfg.max_unstable_per_prayer_worsen):
                        continue
                    if gain < float(cfg.min_unstable_per_prayer_gain):
                        continue
                    prayers_to_keep.append(prayer)

                if prayers_to_keep and len(prayers_to_keep) < len(model.prayer_models):
                    model.prayer_models = {
                        prayer: model.prayer_models[prayer]
                        for prayer in prayers_to_keep
                    }
                    step_started_at = time.perf_counter()
                    unstable_after_residual, _ = _evaluate_mae(
                        params=params,
                        dates=unstable_dates,
                        reference_times=working_reference_times,
                        elevation=context.elevation,
                        timezone=timezone,
                        tz_name=tz_name,
                        offsets=best_offsets,
                        extra_calc_kwargs=extra_calc_kwargs,
                        residual_model=model,
                        residual_active_dates=unstable_dates_set,
                    )
                    unstable_gain = float(
                        unstable_before_residual - unstable_after_residual
                    )
                    step_timings["residual_post_filter_eval"] = float(
                        time.perf_counter() - step_started_at
                    )

                residual_ok = bool(model.prayer_models) and unstable_gain >= float(
                    cfg.min_residual_mae_gain
                )

                stable_mae_before_residual = float(stable_before_residual)
                stable_mae_after_residual = float(
                    stable_after_residual if residual_ok else stable_before_residual
                )
                unstable_mae_before_residual = float(unstable_before_residual)
                unstable_mae_after_residual = float(
                    unstable_after_residual if residual_ok else unstable_before_residual
                )

                if residual_ok:
                    model.validation_rmse_improvement = max(0.0, unstable_gain * 100.0)
                    context.residuals_accepted = True
                    context.residual_corrections = model.to_json()

    # ── Update context ────────────────────────────────────────────────────
    context.reference_times_for_evaluation = working_reference_times
    context.residual_active_dates = unstable_dates

    step_timings["total"] = float(time.perf_counter() - started_at)
    return Stage3Diagnostics(
        stable_dates_count=len(stable_dates),
        unstable_dates_count=len(unstable_dates),
        all_dates_mae_before_offsets=float(all_mae_no_offsets),
        all_dates_mae_after_offsets=float(all_mae_with_offsets),
        stable_mae_before_residual=float(stable_mae_before_residual),
        stable_mae_after_residual=float(stable_mae_after_residual),
        unstable_mae_before_residual=float(unstable_mae_before_residual),
        unstable_mae_after_residual=float(unstable_mae_after_residual),
        step_timings=step_timings,
    )
