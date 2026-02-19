"""Stage 2 - High latitude regime optimizer.

This stage calibrates high-latitude handling from Stage 1 outputs:
- high_lat_method is selected on problematic dates.
- isha_harag is selected on Stage 1 safe/core dates because it affects
    Isha globally and should not trade stable-day quality for unstable days.
- high_lat_start_date/high_lat_end_date are assigned from Stage 1 ranges.
"""

from __future__ import annotations

import datetime
import time
from typing import Any, Dict, List, Optional

import numpy as np

from src.app.domain.models import (
    PipelineContext,
    PrayerCalculationRequest,
    Stage2Diagnostics,
)
from src.app.infrastructure.optimizer.objective import _get_clock_offset_for_date
from src.app.infrastructure.optimizer.shared import OFFSET_FIELDS
from src.app.infrastructure.prayer_calculator import calculate_prayer_times

from .shared import (
    Stage2Config,
    circular_minutes_diff,
    local_time_str_to_utc_minutes,
    mean_abs,
    parse_time_to_minutes,
    resolve_timezone_offset_hours,
)


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


def _derive_high_lat_period(
    excluded_ranges: List[Dict[str, Any]],
) -> tuple[Optional[datetime.date], Optional[datetime.date]]:
    starts: List[datetime.date] = []
    ends: List[datetime.date] = []
    for item in excluded_ranges:
        start = _coerce_date(item.get("start"))
        end = _coerce_date(item.get("end"))
        if start is None or end is None:
            continue
        if end < start:
            start, end = end, start
        starts.append(start)
        ends.append(end)
    if not starts or not ends:
        return None, None
    return min(starts), max(ends)


def _compute_residuals_on_dates(
    *,
    dates: List[datetime.date],
    reference_times: Dict[datetime.date, Dict[str, str]],
    context: PipelineContext,
    timezone: float,
    tz_name: Optional[str],
    high_lat_method: int,
    isha_harag: int,
    custom_fajr_angle: Optional[float] = None,
    custom_isha_angle: Optional[float] = None,
    high_lat_fallback_method: Optional[int] = None,
) -> Dict[str, List[float]]:
    by_prayer: Dict[str, List[float]] = {"fajr": [], "isha": []}
    merged_offsets: Dict[str, float] = {
        f: float((context.offsets or {}).get(f, 0.0) or 0.0) for f in OFFSET_FIELDS
    }
    request_base_kwargs: Dict[str, Any] = {
        "lat_dec": float(context.lat),
        "lon_dec": float(context.lon),
        "elevation": float(context.elevation),
        "pressure": float(context.pressure),
        "temp": float(context.temp),
        "tz_name": tz_name or "",
        "tz_offset_hours": float(timezone),
        "fajr_angle": float(context.fajr_angle),
        "isha_angle": float(context.isha_angle),
        "isha_minutes": 0.0,
        "asr_madhab": int(context.asr_madhab),
        "rounding": "off",
        "calculation_method": context.calculation_method,
        "isha_shafaq": context.isha_shafaq or "general",
        "high_lat_method": int(high_lat_method),
        "high_lat_start_date": context.high_lat_start_date,
        "high_lat_end_date": context.high_lat_end_date,
        "isha_harag": int(isha_harag),
        "custom_fajr_angle": custom_fajr_angle,
        "custom_isha_angle": custom_isha_angle,
        "high_lat_fallback_method": high_lat_fallback_method,
    }

    for date_obj in dates:
        ref = reference_times.get(date_obj)
        if not ref:
            continue

        clock_shift = float(_get_clock_offset_for_date(context.clock_offsets, date_obj))
        effective_offsets = {
            f: float(merged_offsets.get(f, 0.0) + clock_shift) for f in OFFSET_FIELDS
        }

        request = PrayerCalculationRequest(
            **request_base_kwargs,
            target_date=date_obj,
            fajr_offset=float(effective_offsets.get("fajr_offset", 0.0)),
            shurooq_offset=float(effective_offsets.get("shurooq_offset", 0.0)),
            dhuhr_offset=float(effective_offsets.get("dhuhr_offset", 0.0)),
            asr_offset=float(effective_offsets.get("asr_offset", 0.0)),
            maghrib_offset=float(effective_offsets.get("maghrib_offset", 0.0)),
            isha_offset=float(effective_offsets.get("isha_offset", 0.0)),
        )
        times, _method, _error = calculate_prayer_times(
            **request.to_calculator_kwargs()
        )

        for prayer in ("fajr", "isha"):
            calc_text = times.get(prayer)
            ref_text = ref.get(prayer)
            if not calc_text or calc_text == "N/A" or not ref_text:
                continue
            try:
                if tz_name:
                    calc_utc = local_time_str_to_utc_minutes(
                        calc_text,
                        date_obj,
                        timezone,
                        tz_name=tz_name,
                    )
                    ref_utc = local_time_str_to_utc_minutes(
                        ref_text,
                        date_obj,
                        timezone,
                        tz_name=tz_name,
                    )
                else:
                    calc_utc = (
                        float(parse_time_to_minutes(calc_text)) - float(timezone) * 60.0
                    ) % 1440.0
                    ref_utc = (
                        float(parse_time_to_minutes(ref_text)) - float(timezone) * 60.0
                    ) % 1440.0
            except (ValueError, TypeError):
                continue

            by_prayer[prayer].append(circular_minutes_diff(calc_utc, ref_utc))

    return by_prayer


def _mae_all_prayers(by_prayer: Dict[str, List[float]]) -> float:
    vals: List[float] = []
    vals.extend(by_prayer.get("fajr", []))
    vals.extend(by_prayer.get("isha", []))
    if not vals:
        return float("inf")
    return float(mean_abs(vals))


def _mae_isha_only(by_prayer: Dict[str, List[float]]) -> float:
    vals = by_prayer.get("isha", [])
    if not vals:
        return float("inf")
    return float(mean_abs(vals))


def optimize_high_latitude_parameters(
    *,
    context: PipelineContext,
    location_data: Dict[str, Any],
    reference_times: Dict[datetime.date, Dict[str, str]],
    available_dates: List[datetime.date],
    tz_name: Optional[str] = None,
    config: Optional[Stage2Config] = None,
) -> Stage2Diagnostics:
    """Run Stage 2 high-latitude optimization.

    Reads base parameters from *context* (populated by Stage 1).
    On success, updates *context* with the selected high-lat settings.
    Returns lightweight diagnostics.
    """
    started_at = time.perf_counter()
    step_timings: Dict[str, float] = {}
    cfg = config or Stage2Config()

    # ── Prepare dates ─────────────────────────────────────────────────────
    step_started_at = time.perf_counter()
    excluded_ranges = list(context.excluded_date_ranges or [])
    problematic_dates = _expand_excluded_date_ranges(excluded_ranges)
    available_set = set(available_dates)
    problematic_dates = sorted([d for d in problematic_dates if d in available_set])

    high_lat_start_date, high_lat_end_date = _derive_high_lat_period(excluded_ranges)
    # Always set derived period on context (even on early return)
    context.high_lat_start_date = high_lat_start_date
    context.high_lat_end_date = high_lat_end_date

    safe_dates = sorted(
        {
            d
            for d in (_coerce_date(x) for x in context.dates_used_for_core)
            if d is not None and d in available_set
        }
    )
    step_timings["prepare_dates"] = float(time.perf_counter() - step_started_at)

    n_problematic = len(problematic_dates)
    n_safe = len(safe_dates)

    if n_problematic < int(cfg.min_problematic_days):
        step_timings["total"] = float(time.perf_counter() - started_at)
        return Stage2Diagnostics(
            reason="insufficient_problematic_days",
            problematic_dates_count=n_problematic,
            safe_dates_count=n_safe,
            step_timings=step_timings,
        )
    if high_lat_start_date is None or high_lat_end_date is None:
        step_timings["total"] = float(time.perf_counter() - started_at)
        return Stage2Diagnostics(
            reason="missing_problematic_date_range",
            problematic_dates_count=n_problematic,
            safe_dates_count=n_safe,
            step_timings=step_timings,
        )
    if not safe_dates:
        step_timings["total"] = float(time.perf_counter() - started_at)
        return Stage2Diagnostics(
            reason="missing_safe_dates",
            problematic_dates_count=n_problematic,
            safe_dates_count=n_safe,
            step_timings=step_timings,
        )

    # ── Resolve timezone ──────────────────────────────────────────────────
    step_started_at = time.perf_counter()
    sample_date = problematic_dates[0] if problematic_dates else None
    timezone = resolve_timezone_offset_hours(
        location_data.get("timezone"),
        tz_name=tz_name,
        sample_date=sample_date,
    )
    step_timings["resolve_timezone"] = float(time.perf_counter() - step_started_at)

    # ── Baselines (from context, which was initialised by the pipeline) ──
    baseline_method = context.high_lat_method
    baseline_harag = context.isha_harag
    baseline_custom_fajr = context.custom_fajr_angle
    baseline_custom_isha = context.custom_isha_angle
    baseline_fallback_method = context.high_lat_fallback_method

    # ── Baseline evaluation ───────────────────────────────────────────────
    step_started_at = time.perf_counter()
    baseline_problematic_residuals = _compute_residuals_on_dates(
        dates=problematic_dates,
        reference_times=reference_times,
        context=context,
        timezone=timezone,
        tz_name=tz_name,
        high_lat_method=baseline_method,
        isha_harag=baseline_harag,
        custom_fajr_angle=baseline_custom_fajr,
        custom_isha_angle=baseline_custom_isha,
        high_lat_fallback_method=baseline_fallback_method,
    )
    baseline_problematic_mae = _mae_all_prayers(baseline_problematic_residuals)
    step_timings["baseline_problematic_mae"] = float(
        time.perf_counter() - step_started_at
    )

    step_started_at = time.perf_counter()
    baseline_safe_residuals = _compute_residuals_on_dates(
        dates=safe_dates,
        reference_times=reference_times,
        context=context,
        timezone=timezone,
        tz_name=tz_name,
        high_lat_method=baseline_method,
        isha_harag=baseline_harag,
        custom_fajr_angle=baseline_custom_fajr,
        custom_isha_angle=baseline_custom_isha,
        high_lat_fallback_method=baseline_fallback_method,
    )
    baseline_safe_isha_mae = _mae_isha_only(baseline_safe_residuals)
    step_timings["baseline_safe_isha_mae"] = float(
        time.perf_counter() - step_started_at
    )

    # ── Method sweep ──────────────────────────────────────────────────────
    best_problematic_mae = float("inf")
    best_method = baseline_method
    best_harag = baseline_harag
    best_safe_isha_mae = float("inf")
    best_non_angle_method: Optional[int] = None
    best_non_angle_mae = float("inf")
    best_custom_fajr_angle: Optional[float] = None
    best_custom_isha_angle: Optional[float] = None
    best_custom_with_fallback_mae = float("inf")

    candidate_methods = [
        int(m) for m in cfg.candidate_methods if int(m) in (0, 1, 2, 3)
    ]
    candidate_harag_values = [
        int(h) for h in cfg.candidate_harag_values if int(h) in (0, 1, 2, 3)
    ]
    if not candidate_methods:
        candidate_methods = [0, 1, 2, 3]
    if not candidate_harag_values:
        candidate_harag_values = [0, 1, 2, 3]

    step_started_at = time.perf_counter()
    for method in candidate_methods:
        residuals = _compute_residuals_on_dates(
            dates=problematic_dates,
            reference_times=reference_times,
            context=context,
            timezone=timezone,
            tz_name=tz_name,
            high_lat_method=method,
            isha_harag=baseline_harag,
        )
        mae = _mae_all_prayers(residuals)
        if mae + 1e-9 < best_problematic_mae:
            best_problematic_mae = float(mae)
            best_method = int(method)
        if int(method) != 0 and mae + 1e-9 < best_non_angle_mae:
            best_non_angle_mae = float(mae)
            best_non_angle_method = int(method)

    # ── Custom angle grid search ──────────────────────────────────────────
    custom_accepted = False
    if (
        bool(cfg.optimize_custom_angles)
        and 0 in candidate_methods
        and best_non_angle_method is not None
        and np.isfinite(best_non_angle_mae)
    ):
        angle_values = np.linspace(
            float(cfg.custom_angle_min_deg),
            float(cfg.custom_angle_max_deg),
            int(max(3, cfg.custom_angle_grid_points)),
        )
        for candidate_fajr in angle_values:
            for candidate_isha in angle_values:
                residuals = _compute_residuals_on_dates(
                    dates=problematic_dates,
                    reference_times=reference_times,
                    context=context,
                    timezone=timezone,
                    tz_name=tz_name,
                    high_lat_method=0,
                    isha_harag=baseline_harag,
                    custom_fajr_angle=float(candidate_fajr),
                    custom_isha_angle=float(candidate_isha),
                    high_lat_fallback_method=int(best_non_angle_method),
                )
                mae = _mae_all_prayers(residuals)
                if mae + 1e-9 < best_custom_with_fallback_mae:
                    best_custom_with_fallback_mae = float(mae)
                    best_custom_fajr_angle = float(candidate_fajr)
                    best_custom_isha_angle = float(candidate_isha)

        custom_accepted = bool(
            np.isfinite(best_custom_with_fallback_mae)
            and best_custom_with_fallback_mae
            <= best_non_angle_mae - float(cfg.custom_angle_improvement_threshold)
        )
        if custom_accepted:
            best_problematic_mae = float(best_custom_with_fallback_mae)
            best_method = 0
        else:
            best_custom_fajr_angle = None
            best_custom_isha_angle = None

    step_timings["method_sweep"] = float(time.perf_counter() - step_started_at)

    if not np.isfinite(best_problematic_mae):
        step_timings["total"] = float(time.perf_counter() - started_at)
        return Stage2Diagnostics(
            reason="no_valid_stage2_evaluation",
            problematic_dates_count=n_problematic,
            safe_dates_count=n_safe,
            problematic_mae_before=float(baseline_problematic_mae),
            problematic_mae_after=float(baseline_problematic_mae),
            step_timings=step_timings,
        )

    # ── Harag sweep ───────────────────────────────────────────────────────
    step_started_at = time.perf_counter()
    selected_custom_fajr = best_custom_fajr_angle if custom_accepted else None
    selected_custom_isha = best_custom_isha_angle if custom_accepted else None
    selected_fallback_method = (
        int(best_non_angle_method)
        if custom_accepted and best_non_angle_method is not None
        else None
    )
    for harag in candidate_harag_values:
        safe_residuals = _compute_residuals_on_dates(
            dates=safe_dates,
            reference_times=reference_times,
            context=context,
            timezone=timezone,
            tz_name=tz_name,
            high_lat_method=best_method,
            isha_harag=harag,
            custom_fajr_angle=selected_custom_fajr,
            custom_isha_angle=selected_custom_isha,
            high_lat_fallback_method=selected_fallback_method,
        )
        safe_isha_mae = _mae_isha_only(safe_residuals)
        if safe_isha_mae + 1e-9 < best_safe_isha_mae:
            best_safe_isha_mae = float(safe_isha_mae)
            best_harag = int(harag)
    step_timings["harag_sweep"] = float(time.perf_counter() - step_started_at)

    if not np.isfinite(best_safe_isha_mae):
        best_safe_isha_mae = float(baseline_safe_isha_mae)
        best_harag = int(baseline_harag)

    accepted = True
    if bool(cfg.require_mae_improvement):
        accepted = bool(best_problematic_mae <= baseline_problematic_mae - 1e-6)

    # ── Apply to context if accepted ──────────────────────────────────────
    if accepted:
        context.high_lat_method = int(best_method)
        context.isha_harag = int(best_harag)
        context.custom_fajr_angle = (
            float(selected_custom_fajr)
            if selected_custom_fajr is not None
            else baseline_custom_fajr
        )
        context.custom_isha_angle = (
            float(selected_custom_isha)
            if selected_custom_isha is not None
            else baseline_custom_isha
        )
        context.high_lat_fallback_method = (
            int(selected_fallback_method)
            if selected_fallback_method is not None
            else baseline_fallback_method
        )

    step_timings["total"] = float(time.perf_counter() - started_at)
    return Stage2Diagnostics(
        ran=True,
        accepted=bool(accepted),
        reason="ok" if accepted else "no_stage2_improvement",
        problematic_dates_count=n_problematic,
        safe_dates_count=n_safe,
        problematic_mae_before=float(baseline_problematic_mae),
        problematic_mae_after=float(
            best_problematic_mae if accepted else baseline_problematic_mae
        ),
        step_timings=step_timings,
    )
