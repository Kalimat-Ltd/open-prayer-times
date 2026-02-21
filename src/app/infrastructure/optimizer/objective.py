# ruff: noqa: BLE001, F541, ARG002
# pylint: disable=broad-exception-caught
import math
import json
import inspect
import numpy as np
from typing import Any, Dict

from .shared import (
    MAE_OBJECTIVE_MAX_ERROR_WEIGHT,
    MAE_OBJECTIVE_RMSE_WEIGHT,
    OFFSET_FIELDS,
    PRAYER_NAMES,
    RMSE_GUARDRAIL_TOLERANCE,
    TAIL_GUARDRAIL_TOLERANCE,
    _calculate_prayer_times_dynamic,
    _time_diff_seconds,
)
from src.app.infrastructure.prayer_calculator import calculate_prayer_times


_AUTO_EXCLUDED_CALC_KEYS = {
    "lat_dec",
    "lon_dec",
    "elevation",
    "pressure",
    "temp",
    "tz_name",
    "tz_offset_hours",
    "fajr_angle",
    "isha_angle",
    "isha_minutes",
    "target_date",
    "rounding",
    *OFFSET_FIELDS,
}


def _get_supported_dynamic_calc_keys() -> set[str]:
    try:
        return {
            name
            for name in inspect.signature(calculate_prayer_times).parameters.keys()
            if name not in _AUTO_EXCLUDED_CALC_KEYS
        }
    except (ValueError, TypeError):
        return set()


def _to_settings_dict(settings_source: Any) -> Dict[str, Any]:
    if settings_source is None:
        return {}
    if isinstance(settings_source, dict):
        return dict(settings_source)
    try:
        return dict(vars(settings_source))
    except (TypeError, ValueError):
        return {}


def _merge_settings_sources(settings_source: Any) -> Dict[str, Any]:
    if settings_source is None:
        return {}
    if isinstance(settings_source, (list, tuple)):
        merged: Dict[str, Any] = {}
        for item in settings_source:
            for key, value in _to_settings_dict(item).items():
                if value is not None:
                    merged[key] = value
        return merged
    return _to_settings_dict(settings_source)


def _build_auto_extra_calc_kwargs(settings_source: Any) -> Dict[str, Any]:
    merged = _merge_settings_sources(settings_source)
    if not merged:
        return {}
    supported = _get_supported_dynamic_calc_keys()
    return {
        key: value
        for key, value in merged.items()
        if key in supported and value is not None
    }


def _get_clock_offset_for_date(clock_offsets_json: Any, target_date: Any) -> float:
    if not clock_offsets_json:
        return 0.0
    try:
        blocks = json.loads(str(clock_offsets_json))
    except (TypeError, ValueError):
        return 0.0

    month_day = (target_date.month, target_date.day)
    for block in blocks:
        try:
            start_text = str(block.get("start", ""))
            end_text = str(block.get("end", ""))
            start_parts = start_text.split("-")
            end_parts = end_text.split("-")
            if len(start_parts) != 2 or len(end_parts) != 2:
                continue
            start = (int(start_parts[0]), int(start_parts[1]))
            end = (int(end_parts[0]), int(end_parts[1]))
            offset = float(block.get("offset", 0.0) or 0.0)
        except (TypeError, ValueError, AttributeError):
            continue

        if start <= end:
            if start <= month_day <= end:
                return float(-offset)
        else:
            if month_day >= start or month_day <= end:
                return float(-offset)
    return 0.0


def _compute_mae_priority_objective(
    params_vector,
    available_dates,
    reference_times,
    elevation,
    timezone,
    tz_name,
    isha_minutes,
    prayer_weights,
    fixed_offsets,
    extra_calc_kwargs,
):
    """
    Compute MAE-priority objective with RMSE/tail guardrails.

    Score = weighted_MAE + (w_rmse * weighted_RMSE) + (w_tail * max_abs_error).
    Returns inf on failure or no usable samples.
    """
    fajr_angle, isha_angle, lat, lon, temp, pressure = params_vector

    total_weighted_abs = 0.0
    total_weighted_sq = 0.0
    total_weight = 0.0
    max_abs_err = 0.0

    for date_obj in available_dates:
        ref = reference_times.get(date_obj)
        if not ref:
            continue
        try:
            calc_kwargs: Dict[str, Any] = dict(
                lat_dec=lat,
                lon_dec=lon,
                elevation=elevation,
                pressure=pressure,
                temp=temp,
                tz_name=tz_name,
                tz_offset_hours=timezone,
                fajr_angle=fajr_angle,
                isha_angle=isha_angle,
                isha_minutes=isha_minutes,
                target_date=date_obj,
                rounding="off",
            )
            if fixed_offsets:
                calc_kwargs.update(fixed_offsets)
            if extra_calc_kwargs:
                calc_kwargs.update(extra_calc_kwargs)

            times, _, _ = _calculate_prayer_times_dynamic(calc_kwargs)

            for prayer in PRAYER_NAMES:
                calc_time = times.get(prayer, "N/A")
                ref_time = ref.get(prayer, "N/A")
                diff_s = _time_diff_seconds(calc_time, ref_time)
                if diff_s is not None:
                    diff_min = diff_s / 60.0
                    abs_diff = abs(diff_min)
                    w = prayer_weights.get(prayer, 1.0)
                    total_weighted_abs += w * abs_diff
                    total_weighted_sq += w * (diff_min**2)
                    total_weight += w
                    if abs_diff > max_abs_err:
                        max_abs_err = abs_diff
        except (ValueError, TypeError, KeyError, RuntimeError, OSError):
            continue

    if total_weight == 0:
        return float("inf")

    weighted_mae = total_weighted_abs / total_weight
    weighted_rmse = math.sqrt(total_weighted_sq / total_weight)
    return (
        weighted_mae
        + (MAE_OBJECTIVE_RMSE_WEIGHT * weighted_rmse)
        + (MAE_OBJECTIVE_MAX_ERROR_WEIGHT * max_abs_err)
    )


def _max_finite_error(metric_map: Dict[str, float]) -> float:
    vals = [v for v in metric_map.values() if math.isfinite(v)] if metric_map else []
    return max(vals) if vals else float("inf")


def _is_mae_priority_improvement(
    baseline_mae: float,
    candidate_mae: float,
    baseline_rmse: float,
    candidate_rmse: float,
    baseline_per_prayer_max: Dict[str, float],
    candidate_per_prayer_max: Dict[str, float],
    eps: float = 1e-6,
) -> bool:
    mae_improved = candidate_mae < baseline_mae - eps
    rmse_guardrail_ok = candidate_rmse <= baseline_rmse + RMSE_GUARDRAIL_TOLERANCE
    baseline_tail = _max_finite_error(baseline_per_prayer_max)
    candidate_tail = _max_finite_error(candidate_per_prayer_max)
    tail_guardrail_ok = candidate_tail <= baseline_tail + TAIL_GUARDRAIL_TOLERANCE
    return mae_improved and rmse_guardrail_ok and tail_guardrail_ok


def _compute_detailed_errors(
    params_vector,
    available_dates,
    reference_times,
    elevation,
    timezone,
    tz_name,
    isha_minutes,
    offsets,
    extra_calc_kwargs=None,
    residual_model=None,
    residual_active_dates=None,
    settings_source=None,
    clock_offsets_json=None,
    rounding="off",
):
    """
    Compute per-prayer error statistics using the final optimized parameters.

    If *residual_model* is provided, its per-date corrections are added to
    the offsets for each date, matching runtime behaviour.

    Returns: (rmse_total, mae_total, per_prayer_rmse, per_prayer_mae,
              per_prayer_max_error, per_prayer_signed_mean)
    """
    fajr_angle, isha_angle, lat, lon, temp, pressure = params_vector

    errors_by_prayer = {p: [] for p in PRAYER_NAMES}

    auto_extra_calc_kwargs = _build_auto_extra_calc_kwargs(settings_source)

    for date_obj in available_dates:
        ref = reference_times.get(date_obj)
        if not ref:
            continue
        try:
            calc_kwargs: Dict[str, Any] = dict(
                lat_dec=lat,
                lon_dec=lon,
                elevation=elevation,
                pressure=pressure,
                temp=temp,
                tz_name=tz_name,
                tz_offset_hours=timezone,
                fajr_angle=fajr_angle,
                isha_angle=isha_angle,
                isha_minutes=isha_minutes,
                target_date=date_obj,
                rounding=rounding,
            )
            if offsets:
                calc_kwargs.update(offsets)
            clock_shift = _get_clock_offset_for_date(clock_offsets_json, date_obj)
            if abs(clock_shift) > 1e-9:
                for prayer in PRAYER_NAMES:
                    offset_key = f"{prayer}_offset"
                    current_offset_raw = calc_kwargs.get(offset_key, 0.0)
                    try:
                        current_offset = float(current_offset_raw or 0.0)
                    except (TypeError, ValueError):
                        current_offset = 0.0
                    calc_kwargs[offset_key] = current_offset + float(clock_shift)
            if auto_extra_calc_kwargs:
                calc_kwargs.update(auto_extra_calc_kwargs)
            if extra_calc_kwargs:
                calc_kwargs.update(extra_calc_kwargs)

            # Apply per-date residual corrections (same as runtime)
            residual_allowed = True
            if residual_active_dates is not None:
                residual_allowed = date_obj in residual_active_dates

            if residual_model is not None and residual_allowed:
                rc = residual_model.predict_all(date_obj)
                for prayer in PRAYER_NAMES:
                    offset_key = f"{prayer}_offset"
                    current_offset_raw = calc_kwargs.get(offset_key, 0.0)
                    try:
                        current_offset = float(current_offset_raw or 0.0)
                    except (TypeError, ValueError):
                        current_offset = 0.0
                    calc_kwargs[offset_key] = current_offset + float(
                        rc.get(prayer, 0.0) or 0.0
                    )

            times, _, _ = _calculate_prayer_times_dynamic(calc_kwargs)

            for prayer in PRAYER_NAMES:
                calc_time = times.get(prayer, "N/A")
                ref_time = ref.get(prayer, "N/A")
                diff_s = _time_diff_seconds(calc_time, ref_time)
                if diff_s is not None:
                    errors_by_prayer[prayer].append(diff_s / 60.0)
        except (ValueError, TypeError, KeyError, RuntimeError, OSError):
            continue

    per_prayer_rmse = {}
    per_prayer_mae = {}
    per_prayer_max_error = {}
    per_prayer_signed_mean = {}

    for p in PRAYER_NAMES:
        errs = errors_by_prayer[p]
        if errs:
            arr = np.array(errs)
            per_prayer_rmse[p] = float(np.sqrt(np.mean(arr**2)))
            per_prayer_mae[p] = float(np.mean(np.abs(arr)))
            per_prayer_max_error[p] = float(np.max(np.abs(arr)))
            per_prayer_signed_mean[p] = float(np.mean(arr))
        else:
            per_prayer_rmse[p] = float("inf")
            per_prayer_mae[p] = float("inf")
            per_prayer_max_error[p] = float("inf")
            per_prayer_signed_mean[p] = 0.0

    all_errs = []
    for errs in errors_by_prayer.values():
        all_errs.extend(errs)
    if all_errs:
        arr = np.array(all_errs)
        rmse_total = float(np.sqrt(np.mean(arr**2)))
        mae_total = float(np.mean(np.abs(arr)))
    else:
        rmse_total = float("inf")
        mae_total = float("inf")

    return (
        rmse_total,
        mae_total,
        per_prayer_rmse,
        per_prayer_mae,
        per_prayer_max_error,
        per_prayer_signed_mean,
    )


def _compute_offsets_direct(
    params_vector,
    available_dates,
    reference_times,
    elevation,
    timezone,
    tz_name,
    isha_minutes,
    extra_calc_kwargs,
    max_dhuhr_asr_offset_minutes: float = 5.0,
    max_other_offset_minutes: float = 20.0,
):
    """
    Compute per-prayer minute offsets using ALL data (maximum overfitting).

    For each prayer, the offset is the negated mean signed error across all
    reference dates.  This directly zeroes out systematic bias.

    Returns dict of offset field -> float value.
    """
    fajr_angle, isha_angle, lat, lon, temp, pressure = params_vector

    errors_by_prayer = {p: [] for p in PRAYER_NAMES}
    for date_obj in available_dates:
        ref = reference_times.get(date_obj)
        if not ref:
            continue
        try:
            calc_kwargs: Dict[str, Any] = dict(
                lat_dec=lat,
                lon_dec=lon,
                elevation=elevation,
                pressure=pressure,
                temp=temp,
                tz_name=tz_name,
                tz_offset_hours=timezone,
                fajr_angle=fajr_angle,
                isha_angle=isha_angle,
                isha_minutes=isha_minutes,
                target_date=date_obj,
                rounding="off",
            )
            if extra_calc_kwargs:
                calc_kwargs.update(extra_calc_kwargs)
            times, _, _ = _calculate_prayer_times_dynamic(calc_kwargs)
            for prayer in PRAYER_NAMES:
                calc_time = times.get(prayer, "N/A")
                ref_time = ref.get(prayer, "N/A")
                diff_s = _time_diff_seconds(calc_time, ref_time)
                if diff_s is not None:
                    errors_by_prayer[prayer].append(diff_s / 60.0)
        except (ValueError, TypeError, KeyError, RuntimeError, OSError):
            continue

    offsets = {}
    for p, f in zip(PRAYER_NAMES, OFFSET_FIELDS):
        errs = errors_by_prayer[p]
        if errs:
            arr = np.array(errs, dtype=float)
            if arr.size >= 8:
                q1, q3 = np.percentile(arr, [25, 75])
                iqr = max(float(q3 - q1), 1e-6)
                lo = q1 - 1.5 * iqr
                hi = q3 + 1.5 * iqr
                trimmed = arr[(arr >= lo) & (arr <= hi)]
                if trimmed.size > 0:
                    arr = trimmed

            offset_minutes = -float(np.median(arr))
            max_abs_offset = (
                float(max_dhuhr_asr_offset_minutes)
                if p in ("dhuhr", "asr")
                else float(max_other_offset_minutes)
            )
            offset_minutes = max(-max_abs_offset, min(max_abs_offset, offset_minutes))
            offsets[f] = round(offset_minutes, 2)
        else:
            offsets[f] = 0.0
    return offsets
