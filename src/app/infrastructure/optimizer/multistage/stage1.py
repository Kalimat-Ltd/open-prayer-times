"""Stage 1 - Pure Astronomical Core optimizer.

This stage intentionally calibrates only the structural astronomical core.
Residual corrections and high-latitude regime switching are explicitly
excluded from optimization here.

Clock-shift normalization and stable-date prayer offsets are applied here
so later stages can focus only on unstable-date residual behavior.

Robust loss is used because reference datasets can include outlier days
(rounding artifacts, publication noise, occasional transcription issues).
The stage then flags problematic months for later stages instead of forcing
one yearly angle model to absorb high-latitude seasonal regimes.
"""

from __future__ import annotations

import datetime
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from src.app.domain.models import PipelineContext, Stage1Diagnostics
from src.app.infrastructure.optimizer.objective import (
    _compute_detailed_errors,
    _compute_offsets_direct,
)
from src.app.infrastructure.optimizer.shared import OFFSET_FIELDS

from .robust_loss import robust_loss
from .shared import (
    Stage1Config,
    circular_minutes_diff,
    km_to_degrees,
    local_time_str_to_utc_minutes,
    local_minutes_to_utc_minutes,
    parse_time_to_minutes,
    percentile,
    resolve_timezone_offset_hours,
)
from src.app.infrastructure.prayer_calculator import calculate_prayer_times


_STAGE1_TIMEZONE_NAME: Optional[str] = None
_STAGE1_RESIDUAL_CACHE: Dict[tuple, np.ndarray] = {}
_STAGE1_METHOD_LOSS_CACHE: Dict[tuple, float] = {}
_STAGE1_CACHE_MAX_ENTRIES = 4096


def _stage1_evict_if_needed(cache_dict: Dict[Any, Any]) -> None:
    if len(cache_dict) > _STAGE1_CACHE_MAX_ENTRIES:
        cache_dict.clear()


def _stage1_residual_cache_key(
    params: np.ndarray,
    reference_times: Dict[datetime.date, Dict[str, str]],
    dates: List[datetime.date],
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    isha_shafaq: Optional[str],
) -> tuple:
    params_arr = np.asarray(params, dtype=np.float64)
    return (
        params_arr.tobytes(),
        int(len(params_arr)),
        id(reference_times),
        tuple(dates),
        float(elevation),
        float(pressure),
        float(temperature),
        float(timezone),
        str(calculation_method),
        str(asr_madhab),
        str(isha_shafaq) if isha_shafaq is not None else None,
        _STAGE1_TIMEZONE_NAME,
    )


def compute_all_prayer_times(
    target_date: datetime.date,
    lat: float,
    lon: float,
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    fajr_angle: float,
    isha_angle: float,
    calculation_method: str,
    asr_madhab: str,
    isha_shafaq: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    """Compute UTC-minute prayer times for full-day prayers used in geo calibration."""
    calc_method = (
        "moonsighting" if calculation_method == "moonsighting" else "angle_based"
    )
    times, _method, _error = calculate_prayer_times(
        lat_dec=float(lat),
        lon_dec=float(lon),
        elevation=float(elevation),
        pressure=float(pressure),
        temp=float(temperature),
        tz_name=_STAGE1_TIMEZONE_NAME,
        tz_offset_hours=float(timezone),
        fajr_angle=float(fajr_angle),
        isha_angle=float(isha_angle),
        isha_minutes=0.0,
        target_date=target_date,
        rounding="off",
        calculation_method=calc_method,
        asr_madhab=0 if asr_madhab == "standard" else 1,
        isha_shafaq=(isha_shafaq or "general"),
    )
    prayers = ("fajr", "shurooq", "dhuhr", "asr", "maghrib", "isha")
    result: Dict[str, Optional[float]] = {p: None for p in prayers}
    for prayer in prayers:
        t = times.get(prayer)
        if t and t != "N/A":
            if _STAGE1_TIMEZONE_NAME:
                result[prayer] = local_time_str_to_utc_minutes(
                    t,
                    target_date,
                    timezone,
                    tz_name=_STAGE1_TIMEZONE_NAME,
                )
            else:
                result[prayer] = local_minutes_to_utc_minutes(
                    parse_time_to_minutes(t),
                    timezone,
                )
    return result


def _reference_utc_minutes_for_prayer(
    reference_times: Dict[datetime.date, Dict[str, str]],
    date_obj: datetime.date,
    prayer: str,
    timezone: float,
) -> Optional[float]:
    ref = reference_times.get(date_obj)
    if not ref:
        return None
    ref_text = ref.get(prayer)
    if not ref_text:
        return None
    try:
        return local_time_str_to_utc_minutes(
            ref_text,
            date_obj,
            timezone,
            tz_name=_STAGE1_TIMEZONE_NAME,
        )
    except (ValueError, TypeError):
        return None


def compute_stable_day_residuals(
    lat: float,
    lon: float,
    fajr_angle: float,
    isha_angle: float,
    dates: List[datetime.date],
    reference_times: Dict[datetime.date, Dict[str, str]],
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    isha_shafaq: Optional[str] = None,
) -> Dict[datetime.date, Dict[str, float]]:
    """Residuals for stable-day geo calibration prayers in UTC minutes."""
    target_prayers = ("shurooq", "dhuhr", "asr", "maghrib")
    by_day: Dict[datetime.date, Dict[str, float]] = {}
    for date_obj in dates:
        model = compute_all_prayer_times(
            target_date=date_obj,
            lat=lat,
            lon=lon,
            elevation=elevation,
            pressure=pressure,
            temperature=temperature,
            timezone=timezone,
            fajr_angle=fajr_angle,
            isha_angle=isha_angle,
            calculation_method=calculation_method,
            asr_madhab=asr_madhab,
            isha_shafaq=isha_shafaq,
        )
        per_day: Dict[str, float] = {}
        for prayer in target_prayers:
            model_utc = model.get(prayer)
            ref_utc = _reference_utc_minutes_for_prayer(
                reference_times=reference_times,
                date_obj=date_obj,
                prayer=prayer,
                timezone=timezone,
            )
            if model_utc is None or ref_utc is None:
                continue
            per_day[prayer] = circular_minutes_diff(model_utc, ref_utc)
        if per_day:
            by_day[date_obj] = per_day
    return by_day


def compute_common_mode_shift(
    residuals_by_day: Dict[datetime.date, Dict[str, float]],
) -> float:
    """Estimate global common-mode timing shift from non-dhuhr prayers."""
    per_day_shifts: List[float] = []
    for per_day in residuals_by_day.values():
        vals = [
            float(per_day[p]) for p in ("shurooq", "asr", "maghrib") if p in per_day
        ]
        if vals:
            per_day_shifts.append(float(np.median(np.asarray(vals, dtype=float))))
    if not per_day_shifts:
        return 0.0
    return float(np.median(np.asarray(per_day_shifts, dtype=float)))


def longitude_mae_objective(
    lon: float,
    fixed_lat: float,
    fixed_fajr_angle: float,
    fixed_isha_angle: float,
    dates: List[datetime.date],
    reference_times: Dict[datetime.date, Dict[str, str]],
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    isha_shafaq: Optional[str],
) -> float:
    """MAE objective for longitude via per-day common-mode shift."""
    residuals_by_day = compute_stable_day_residuals(
        lat=fixed_lat,
        lon=float(lon),
        fajr_angle=fixed_fajr_angle,
        isha_angle=fixed_isha_angle,
        dates=dates,
        reference_times=reference_times,
        elevation=elevation,
        pressure=pressure,
        temperature=temperature,
        timezone=timezone,
        calculation_method=calculation_method,
        asr_madhab=asr_madhab,
        isha_shafaq=isha_shafaq,
    )
    per_day_common: List[float] = []
    for per_day in residuals_by_day.values():
        vals = [
            float(per_day[p]) for p in ("shurooq", "asr", "maghrib") if p in per_day
        ]
        if vals:
            per_day_common.append(float(np.median(np.asarray(vals, dtype=float))))
    if not per_day_common:
        return float("inf")
    return float(np.mean(np.abs(np.asarray(per_day_common, dtype=float))))


def latitude_mae_objective(
    lat: float,
    fixed_lon: float,
    fixed_fajr_angle: float,
    fixed_isha_angle: float,
    dates: List[datetime.date],
    reference_times: Dict[datetime.date, Dict[str, str]],
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    isha_shafaq: Optional[str],
) -> float:
    """MAE objective for latitude after removing common-mode shift.

    Dhuhr is intentionally excluded so latitude captures differential shape only.
    """
    residuals_by_day = compute_stable_day_residuals(
        lat=float(lat),
        lon=fixed_lon,
        fajr_angle=fixed_fajr_angle,
        isha_angle=fixed_isha_angle,
        dates=dates,
        reference_times=reference_times,
        elevation=elevation,
        pressure=pressure,
        temperature=temperature,
        timezone=timezone,
        calculation_method=calculation_method,
        asr_madhab=asr_madhab,
        isha_shafaq=isha_shafaq,
    )
    common_shift = compute_common_mode_shift(residuals_by_day)
    centered: List[float] = []
    for per_day in residuals_by_day.values():
        for prayer in ("shurooq", "asr", "maghrib"):
            if prayer in per_day:
                centered.append(float(per_day[prayer]) - float(common_shift))
    if not centered:
        return float("inf")
    return float(np.mean(np.abs(np.asarray(centered, dtype=float))))


def _hybrid_1d_search(
    objective: Any,
    low: float,
    high: float,
    center: float,
    grid_points: int = 21,
) -> tuple[float, float, bool]:
    eval_cache: Dict[float, float] = {}

    def _eval(x: float) -> float:
        key = float(x)
        cached_val = eval_cache.get(key)
        if cached_val is not None:
            return float(cached_val)
        value = float(objective(key))
        eval_cache[key] = value
        return value

    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        val = _eval(float(center))
        return float(center), val, False

    n = max(7, int(grid_points))
    xs = np.linspace(float(low), float(high), n)
    ys = [_eval(float(x)) for x in xs]
    best_idx = int(np.argmin(np.asarray(ys, dtype=float)))
    best_x = float(xs[best_idx])
    best_y = float(ys[best_idx])

    left_idx = max(0, best_idx - 1)
    right_idx = min(n - 1, best_idx + 1)
    bracket_low = float(xs[left_idx])
    bracket_high = float(xs[right_idx])

    if bracket_high - bracket_low < 1e-9:
        return best_x, best_y, True

    refined = minimize_scalar(
        _eval,
        method="bounded",
        bounds=(bracket_low, bracket_high),
        options={"xatol": 1e-6, "maxiter": 80},
    )
    if bool(refined.success) and np.isfinite(float(refined.fun)):
        refined_x = float(refined.x)
        refined_y = float(refined.fun)
        if refined_y <= best_y:
            return refined_x, refined_y, True
    return best_x, best_y, bool(refined.success)


def optimize_geographic_calibration(
    params: np.ndarray,
    dates: List[datetime.date],
    reference_times: Dict[datetime.date, Dict[str, str]],
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    config: Stage1Config,
    isha_shafaq: Optional[str] = None,
) -> tuple[np.ndarray, Dict[str, Any]]:
    """Final geographic calibration using stable-day MAE, lon-first then lat."""
    summary: Dict[str, Any] = {
        "enabled": bool(config.enable_geographic_calibration),
        "skipped": True,
        "before_common_mode_mae": float("inf"),
        "after_common_mode_mae": float("inf"),
        "before_shape_mae": float("inf"),
        "after_shape_mae": float("inf"),
        "before_dhuhr_median_bias": float("inf"),
        "after_dhuhr_median_bias": float("inf"),
        "longitude_delta_deg": 0.0,
        "latitude_delta_deg": 0.0,
    }

    best = np.asarray(params, dtype=float).copy()
    if not config.enable_geographic_calibration or not dates:
        return best, summary

    geo_grid_points = max(7, int(config.geo_search_grid_points))

    radius_km = max(0.0, float(config.geo_search_radius_km))
    d_lat, d_lon = km_to_degrees(radius_km, float(best[0]))

    def _collect_metrics(lat: float, lon: float) -> tuple[float, float, float]:
        residuals_by_day = compute_stable_day_residuals(
            lat=lat,
            lon=lon,
            fajr_angle=float(best[2]),
            isha_angle=float(best[3]),
            dates=dates,
            reference_times=reference_times,
            elevation=elevation,
            pressure=pressure,
            temperature=temperature,
            timezone=timezone,
            calculation_method=calculation_method,
            asr_madhab=asr_madhab,
            isha_shafaq=isha_shafaq,
        )
        common_vals: List[float] = []
        for per_day in residuals_by_day.values():
            vals = [
                float(per_day[p]) for p in ("shurooq", "asr", "maghrib") if p in per_day
            ]
            if vals:
                common_vals.append(float(np.median(np.asarray(vals, dtype=float))))
        common_mae = (
            float(np.mean(np.abs(np.asarray(common_vals, dtype=float))))
            if common_vals
            else float("inf")
        )

        common_shift = compute_common_mode_shift(residuals_by_day)
        centered: List[float] = []
        dhuhr_vals: List[float] = []
        for per_day in residuals_by_day.values():
            for prayer in ("shurooq", "asr", "maghrib"):
                if prayer in per_day:
                    centered.append(float(per_day[prayer]) - float(common_shift))
            if "dhuhr" in per_day:
                dhuhr_vals.append(float(per_day["dhuhr"]))

        shape_mae = (
            float(np.mean(np.abs(np.asarray(centered, dtype=float))))
            if centered
            else float("inf")
        )
        dhuhr_bias = (
            float(np.median(np.asarray(dhuhr_vals, dtype=float)))
            if dhuhr_vals
            else float("inf")
        )
        return common_mae, shape_mae, dhuhr_bias

    before_common_mae, before_shape_mae, before_dhuhr = _collect_metrics(
        float(best[0]), float(best[1])
    )

    lon_low = float(best[1] - d_lon)
    lon_high = float(best[1] + d_lon)
    lon_obj = lambda x: longitude_mae_objective(
        lon=float(x),
        fixed_lat=float(best[0]),
        fixed_fajr_angle=float(best[2]),
        fixed_isha_angle=float(best[3]),
        dates=dates,
        reference_times=reference_times,
        elevation=elevation,
        pressure=pressure,
        temperature=temperature,
        timezone=timezone,
        calculation_method=calculation_method,
        asr_madhab=asr_madhab,
        isha_shafaq=isha_shafaq,
    )
    best_lon, _lon_val, lon_success = _hybrid_1d_search(
        objective=lon_obj,
        low=lon_low,
        high=lon_high,
        center=float(best[1]),
        grid_points=geo_grid_points,
    )
    best[1] = float(best_lon)

    lat_low = float(best[0] - d_lat)
    lat_high = float(best[0] + d_lat)
    lat_obj = lambda x: latitude_mae_objective(
        lat=float(x),
        fixed_lon=float(best[1]),
        fixed_fajr_angle=float(best[2]),
        fixed_isha_angle=float(best[3]),
        dates=dates,
        reference_times=reference_times,
        elevation=elevation,
        pressure=pressure,
        temperature=temperature,
        timezone=timezone,
        calculation_method=calculation_method,
        asr_madhab=asr_madhab,
        isha_shafaq=isha_shafaq,
    )
    best_lat, _lat_val, lat_success = _hybrid_1d_search(
        objective=lat_obj,
        low=lat_low,
        high=lat_high,
        center=float(best[0]),
        grid_points=geo_grid_points,
    )
    best[0] = float(best_lat)

    after_common_mae, after_shape_mae, after_dhuhr = _collect_metrics(
        float(best[0]), float(best[1])
    )

    summary.update(
        {
            "skipped": False,
            "before_common_mode_mae": float(before_common_mae),
            "after_common_mode_mae": float(after_common_mae),
            "before_shape_mae": float(before_shape_mae),
            "after_shape_mae": float(after_shape_mae),
            "before_dhuhr_median_bias": float(before_dhuhr),
            "after_dhuhr_median_bias": float(after_dhuhr),
            "longitude_delta_deg": float(best[1] - float(params[1])),
            "latitude_delta_deg": float(best[0] - float(params[0])),
            "longitude_success": bool(lon_success),
            "latitude_success": bool(lat_success),
        }
    )
    return best, summary


def _mean_abs_residual_for_prayers(
    residuals_by_day: Dict[datetime.date, Dict[str, float]],
    prayers: tuple[str, ...],
) -> float:
    vals: List[float] = []
    for per_day in residuals_by_day.values():
        for prayer in prayers:
            if prayer in per_day:
                vals.append(abs(float(per_day[prayer])))
    if not vals:
        return float("inf")
    return float(np.mean(np.asarray(vals, dtype=float)))


def _mean_signed_residual_for_prayer(
    prayer: str,
    lat: float,
    lon: float,
    fajr_angle: float,
    isha_angle: float,
    dates: List[datetime.date],
    reference_times: Dict[datetime.date, Dict[str, str]],
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    isha_shafaq: Optional[str],
) -> float:
    vals: List[float] = []
    for date_obj in dates:
        model = compute_all_prayer_times(
            target_date=date_obj,
            lat=lat,
            lon=lon,
            elevation=elevation,
            pressure=pressure,
            temperature=temperature,
            timezone=timezone,
            fajr_angle=fajr_angle,
            isha_angle=isha_angle,
            calculation_method=calculation_method,
            asr_madhab=asr_madhab,
            isha_shafaq=isha_shafaq,
        )
        model_utc = model.get(prayer)
        ref_utc = _reference_utc_minutes_for_prayer(
            reference_times=reference_times,
            date_obj=date_obj,
            prayer=prayer,
            timezone=timezone,
        )
        if model_utc is None or ref_utc is None:
            continue
        vals.append(float(circular_minutes_diff(model_utc, ref_utc)))
    if not vals:
        return 0.0
    return float(np.mean(np.asarray(vals, dtype=float)))


def _collect_signed_residuals_for_prayer(
    prayer: str,
    lat: float,
    lon: float,
    fajr_angle: float,
    isha_angle: float,
    dates: List[datetime.date],
    reference_times: Dict[datetime.date, Dict[str, str]],
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    isha_shafaq: Optional[str],
) -> List[float]:
    vals: List[float] = []
    for date_obj in dates:
        model = compute_all_prayer_times(
            target_date=date_obj,
            lat=lat,
            lon=lon,
            elevation=elevation,
            pressure=pressure,
            temperature=temperature,
            timezone=timezone,
            fajr_angle=fajr_angle,
            isha_angle=isha_angle,
            calculation_method=calculation_method,
            asr_madhab=asr_madhab,
            isha_shafaq=isha_shafaq,
        )
        model_utc = model.get(prayer)
        ref_utc = _reference_utc_minutes_for_prayer(
            reference_times=reference_times,
            date_obj=date_obj,
            prayer=prayer,
            timezone=timezone,
        )
        if model_utc is None or ref_utc is None:
            continue
        vals.append(float(circular_minutes_diff(model_utc, ref_utc)))
    return vals


def _detect_asr_madhab_phase1(
    params: np.ndarray,
    dates: List[datetime.date],
    reference_times: Dict[datetime.date, Dict[str, str]],
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    isha_shafaq: Optional[str],
    config: Stage1Config,
    current_asr_madhab: str,
) -> tuple[str, Dict[str, Any]]:
    diagnostics: Dict[str, Any] = {
        "enabled": bool(config.enable_asr_madhab_detection),
        "selected": str(current_asr_madhab),
        "reason": "disabled",
        "standard_mean_signed": float("inf"),
        "standard_median_signed": float("inf"),
        "standard_mae": float("inf"),
        "standard_samples": 0,
        "hanafi_mean_signed": float("inf"),
        "hanafi_median_signed": float("inf"),
        "hanafi_mae": float("inf"),
        "hanafi_samples": 0,
    }
    if not bool(config.enable_asr_madhab_detection):
        return str(current_asr_madhab), diagnostics

    if not dates:
        diagnostics["reason"] = "no_dates"
        return str(current_asr_madhab), diagnostics

    lat = float(params[0])
    lon = float(params[1])
    fajr_angle = float(params[2])
    isha_angle = float(params[3])

    standard_vals = _collect_signed_residuals_for_prayer(
        prayer="asr",
        lat=lat,
        lon=lon,
        fajr_angle=fajr_angle,
        isha_angle=isha_angle,
        dates=dates,
        reference_times=reference_times,
        elevation=elevation,
        pressure=pressure,
        temperature=temperature,
        timezone=timezone,
        calculation_method=calculation_method,
        asr_madhab="standard",
        isha_shafaq=isha_shafaq,
    )
    hanafi_vals = _collect_signed_residuals_for_prayer(
        prayer="asr",
        lat=lat,
        lon=lon,
        fajr_angle=fajr_angle,
        isha_angle=isha_angle,
        dates=dates,
        reference_times=reference_times,
        elevation=elevation,
        pressure=pressure,
        temperature=temperature,
        timezone=timezone,
        calculation_method=calculation_method,
        asr_madhab="hanafi",
        isha_shafaq=isha_shafaq,
    )

    diagnostics["standard_samples"] = int(len(standard_vals))
    diagnostics["hanafi_samples"] = int(len(hanafi_vals))
    if not standard_vals or not hanafi_vals:
        diagnostics["reason"] = "missing_asr_samples"
        return str(current_asr_madhab), diagnostics

    std_arr = np.asarray(standard_vals, dtype=float)
    han_arr = np.asarray(hanafi_vals, dtype=float)

    std_mean = float(np.mean(std_arr))
    std_median = float(np.median(std_arr))
    std_mae = float(np.mean(np.abs(std_arr)))
    han_mean = float(np.mean(han_arr))
    han_median = float(np.median(han_arr))
    han_mae = float(np.mean(np.abs(han_arr)))

    diagnostics.update(
        {
            "standard_mean_signed": std_mean,
            "standard_median_signed": std_median,
            "standard_mae": std_mae,
            "hanafi_mean_signed": han_mean,
            "hanafi_median_signed": han_median,
            "hanafi_mae": han_mae,
        }
    )

    high_error_threshold = float(config.asr_high_error_threshold_minutes)
    if std_mae < high_error_threshold:
        diagnostics["reason"] = "standard_asr_mae_not_high"
        return str(current_asr_madhab), diagnostics

    if han_mae < std_mae:
        diagnostics["selected"] = "hanafi"
        diagnostics["reason"] = "hanafi_reduces_asr_mae"
        return "hanafi", diagnostics

    diagnostics["reason"] = "hanafi_not_better"
    return str(current_asr_madhab), diagnostics


def optimize_environmental_calibration(
    params: np.ndarray,
    dates: List[datetime.date],
    reference_times: Dict[datetime.date, Dict[str, str]],
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    config: Optional[Stage1Config] = None,
    isha_shafaq: Optional[str] = None,
) -> tuple[float, float, float, Dict[str, Any]]:
    cfg = config or Stage1Config()
    env_grid_points = max(7, int(cfg.env_search_grid_points))
    summary: Dict[str, Any] = {
        "skipped": True,
        "before_shurooq_maghrib_mae": float("inf"),
        "after_shurooq_maghrib_mae": float("inf"),
        "before_shurooq_maghrib_asr_mae": float("inf"),
        "after_shurooq_maghrib_asr_mae": float("inf"),
        "elevation_delta": 0.0,
        "temperature_delta": 0.0,
        "pressure_delta": 0.0,
    }

    if not dates:
        return float(elevation), float(temperature), float(pressure), summary

    lat = float(params[0])
    lon = float(params[1])
    fajr_angle = float(params[2])
    isha_angle = float(params[3])

    base_elevation = float(elevation)
    base_temperature = float(temperature)
    base_pressure = float(pressure)

    base_residuals = compute_stable_day_residuals(
        lat=lat,
        lon=lon,
        fajr_angle=fajr_angle,
        isha_angle=isha_angle,
        dates=dates,
        reference_times=reference_times,
        elevation=base_elevation,
        pressure=base_pressure,
        temperature=base_temperature,
        timezone=timezone,
        calculation_method=calculation_method,
        asr_madhab=asr_madhab,
        isha_shafaq=isha_shafaq,
    )

    before_sm = _mean_abs_residual_for_prayers(base_residuals, ("shurooq", "maghrib"))
    before_sma = _mean_abs_residual_for_prayers(
        base_residuals, ("shurooq", "maghrib", "asr")
    )

    shurooq_vals = [
        float(per_day["shurooq"])
        for per_day in base_residuals.values()
        if "shurooq" in per_day
    ]
    maghrib_vals = [
        float(per_day["maghrib"])
        for per_day in base_residuals.values()
        if "maghrib" in per_day
    ]
    asr_vals = [
        float(per_day["asr"]) for per_day in base_residuals.values() if "asr" in per_day
    ]
    shurooq_mean = (
        float(np.mean(np.asarray(shurooq_vals, dtype=float))) if shurooq_vals else 0.0
    )
    maghrib_mean = (
        float(np.mean(np.asarray(maghrib_vals, dtype=float))) if maghrib_vals else 0.0
    )
    asr_mean = float(np.mean(np.asarray(asr_vals, dtype=float))) if asr_vals else 0.0
    isha_mean = _mean_signed_residual_for_prayer(
        prayer="isha",
        lat=lat,
        lon=lon,
        fajr_angle=fajr_angle,
        isha_angle=isha_angle,
        dates=dates,
        reference_times=reference_times,
        elevation=base_elevation,
        pressure=base_pressure,
        temperature=base_temperature,
        timezone=timezone,
        calculation_method=calculation_method,
        asr_madhab=asr_madhab,
        isha_shafaq=isha_shafaq,
    )

    def _regularization_penalty(
        elev_val: float,
        temp_val: float,
        pressure_val: float,
    ) -> float:
        # Keep environmental tuning physically plausible and avoid edge collapse
        # on weakly-identifiable datasets.
        elev_term = 0.0015 * abs(float(elev_val) - base_elevation)
        temp_term = 0.004 * abs(float(temp_val) - base_temperature)
        expected_pressure = (
            1013.25 * (1.0 - 2.25577e-5 * max(float(elev_val), 0.0)) ** 5.25588
        )
        pressure_term = 0.006 * abs(float(pressure_val) - float(expected_pressure))
        pressure_term += 0.001 * abs(float(pressure_val) - base_pressure)

        edge_temp = max(0.0, abs(float(temp_val)) - 28.0)
        edge_pressure = max(
            0.0, float(cfg.env_pressure_min_mbar) - float(pressure_val)
        ) + max(0.0, float(pressure_val) - float(cfg.env_pressure_max_mbar))
        edge_elevation = max(0.0, abs(float(elev_val)) - 3500.0)
        edge_term = 0.28 * edge_temp + 0.03 * edge_pressure + 0.002 * edge_elevation

        return float(elev_term + temp_term + pressure_term + edge_term)

    best_elevation = float(base_elevation)
    best_temperature = float(base_temperature)
    best_pressure = float(base_pressure)
    elev_success = False
    temp_success = False
    press_success = False

    temp_score = -shurooq_mean + maghrib_mean
    pressure_score = shurooq_mean - asr_mean - maghrib_mean
    elevation_score = -shurooq_mean + maghrib_mean + isha_mean

    temp_direction_shift = float(np.clip(2.5 * temp_score, -12.0, 12.0))
    pressure_direction_shift = float(np.clip(8.0 * pressure_score, -40.0, 40.0))
    elevation_direction_shift = float(np.clip(35.0 * elevation_score, -250.0, 250.0))

    env_residual_cache: Dict[
        tuple[float, float, float], Dict[datetime.date, Dict[str, float]]
    ] = {}

    def _env_residuals_cached(
        elev_val: float,
        temp_val: float,
        pressure_val: float,
    ) -> Dict[datetime.date, Dict[str, float]]:
        cache_key = (float(elev_val), float(temp_val), float(pressure_val))
        cached_residuals = env_residual_cache.get(cache_key)
        if cached_residuals is not None:
            return cached_residuals

        computed_residuals = compute_stable_day_residuals(
            lat=lat,
            lon=lon,
            fajr_angle=fajr_angle,
            isha_angle=isha_angle,
            dates=dates,
            reference_times=reference_times,
            elevation=float(elev_val),
            pressure=float(pressure_val),
            temperature=float(temp_val),
            timezone=timezone,
            calculation_method=calculation_method,
            asr_madhab=asr_madhab,
            isha_shafaq=isha_shafaq,
        )
        env_residual_cache[cache_key] = computed_residuals
        return computed_residuals

    for _ in range(3):
        prev_elevation = float(best_elevation)
        prev_temperature = float(best_temperature)
        prev_pressure = float(best_pressure)

        curr_pressure = float(best_pressure)
        curr_temperature = float(best_temperature)
        elev_center = float(
            min(
                max(
                    best_elevation + elevation_direction_shift,
                    float(cfg.env_elevation_min_m),
                ),
                float(cfg.env_elevation_max_m),
            )
        )
        elev_low = max(
            float(cfg.env_elevation_min_m),
            base_elevation - float(cfg.env_elevation_window_m),
        )
        elev_high = min(
            float(cfg.env_elevation_max_m),
            base_elevation + float(cfg.env_elevation_window_m),
        )
        best_elevation, _elev_val, elev_ok = _hybrid_1d_search(
            objective=lambda x, cp=curr_pressure, ct=curr_temperature: (
                _mean_abs_residual_for_prayers(
                    _env_residuals_cached(
                        elev_val=float(x),
                        temp_val=ct,
                        pressure_val=cp,
                    ),
                    ("shurooq", "maghrib"),
                )
                + _regularization_penalty(
                    float(x),
                    ct,
                    cp,
                )
            ),
            low=elev_low,
            high=elev_high,
            center=elev_center,
            grid_points=env_grid_points,
        )
        elev_success = bool(elev_success or elev_ok)

        temp_center = float(
            min(
                max(
                    best_temperature + temp_direction_shift,
                    float(cfg.env_temperature_min_c),
                ),
                float(cfg.env_temperature_max_c),
            )
        )
        temp_low = float(cfg.env_temperature_min_c)
        temp_high = float(cfg.env_temperature_max_c)
        curr_elevation = float(best_elevation)
        curr_pressure = float(best_pressure)
        best_temperature, _temp_val, temp_ok = _hybrid_1d_search(
            objective=lambda x, ce=curr_elevation, cp=curr_pressure: (
                _mean_abs_residual_for_prayers(
                    _env_residuals_cached(
                        elev_val=ce,
                        temp_val=float(x),
                        pressure_val=cp,
                    ),
                    ("shurooq", "maghrib"),
                )
                + _regularization_penalty(
                    ce,
                    float(x),
                    cp,
                )
            ),
            low=temp_low,
            high=temp_high,
            center=temp_center,
            grid_points=env_grid_points,
        )
        temp_success = bool(temp_success or temp_ok)

        pressure_center = float(
            min(
                max(
                    best_pressure + pressure_direction_shift,
                    float(cfg.env_pressure_min_mbar),
                ),
                float(cfg.env_pressure_max_mbar),
            )
        )
        pressure_low = float(cfg.env_pressure_min_mbar)
        pressure_high = float(cfg.env_pressure_max_mbar)
        curr_elevation = float(best_elevation)
        curr_temperature = float(best_temperature)
        best_pressure, _press_val, press_ok = _hybrid_1d_search(
            objective=lambda x, ce=curr_elevation, ct=curr_temperature: (
                _mean_abs_residual_for_prayers(
                    _env_residuals_cached(
                        elev_val=ce,
                        temp_val=ct,
                        pressure_val=float(x),
                    ),
                    ("shurooq", "maghrib", "asr"),
                )
                + _regularization_penalty(
                    ce,
                    ct,
                    float(x),
                )
            ),
            low=pressure_low,
            high=pressure_high,
            center=pressure_center,
            grid_points=env_grid_points,
        )
        press_success = bool(press_success or press_ok)

        if (
            abs(float(best_elevation) - prev_elevation) < 1e-9
            and abs(float(best_temperature) - prev_temperature) < 1e-9
            and abs(float(best_pressure) - prev_pressure) < 1e-9
        ):
            break

    after_residuals = compute_stable_day_residuals(
        lat=lat,
        lon=lon,
        fajr_angle=fajr_angle,
        isha_angle=isha_angle,
        dates=dates,
        reference_times=reference_times,
        elevation=float(best_elevation),
        pressure=float(best_pressure),
        temperature=float(best_temperature),
        timezone=timezone,
        calculation_method=calculation_method,
        asr_madhab=asr_madhab,
        isha_shafaq=isha_shafaq,
    )
    after_sm = _mean_abs_residual_for_prayers(after_residuals, ("shurooq", "maghrib"))
    after_sma = _mean_abs_residual_for_prayers(
        after_residuals, ("shurooq", "maghrib", "asr")
    )

    accepted = bool(np.isfinite(after_sma) and (after_sma <= before_sma - 1e-3))
    if not accepted:
        best_elevation = float(base_elevation)
        best_temperature = float(base_temperature)
        best_pressure = float(base_pressure)
        after_sm = float(before_sm)
        after_sma = float(before_sma)

    summary.update(
        {
            "skipped": False,
            "before_shurooq_maghrib_mae": float(before_sm),
            "after_shurooq_maghrib_mae": float(after_sm),
            "before_shurooq_maghrib_asr_mae": float(before_sma),
            "after_shurooq_maghrib_asr_mae": float(after_sma),
            "elevation_delta": float(best_elevation - base_elevation),
            "temperature_delta": float(best_temperature - base_temperature),
            "pressure_delta": float(best_pressure - base_pressure),
            "elevation_success": bool(elev_success),
            "temperature_success": bool(temp_success),
            "pressure_success": bool(press_success),
            "accepted": bool(accepted),
        }
    )
    return float(best_elevation), float(best_temperature), float(best_pressure), summary


def compute_prayer_times(
    target_date: datetime.date,
    lat: float,
    lon: float,
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    fajr_angle: float,
    isha_angle: float,
    calculation_method: str,
    asr_madhab: str,
    isha_shafaq: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    """Compute fajr/isha UTC minutes for one day using app calculator core."""
    calc_method = (
        "moonsighting" if calculation_method == "moonsighting" else "angle_based"
    )
    times, _method, _error = calculate_prayer_times(
        lat_dec=float(lat),
        lon_dec=float(lon),
        elevation=float(elevation),
        pressure=float(pressure),
        temp=float(temperature),
        tz_name=_STAGE1_TIMEZONE_NAME,
        tz_offset_hours=float(timezone),
        fajr_angle=float(fajr_angle),
        isha_angle=float(isha_angle),
        isha_minutes=0.0,
        target_date=target_date,
        rounding="off",
        calculation_method=calc_method,
        asr_madhab=0 if asr_madhab == "standard" else 1,
        isha_shafaq=(isha_shafaq or "general"),
    )
    result: Dict[str, Optional[float]] = {"fajr": None, "isha": None}
    for prayer in ("fajr", "isha"):
        t = times.get(prayer)
        if t and t != "N/A":
            if _STAGE1_TIMEZONE_NAME:
                result[prayer] = local_time_str_to_utc_minutes(
                    t,
                    target_date,
                    timezone,
                    tz_name=_STAGE1_TIMEZONE_NAME,
                )
            else:
                result[prayer] = local_minutes_to_utc_minutes(
                    parse_time_to_minutes(t),
                    timezone,
                )
    return result


def compute_residuals(
    params: np.ndarray,
    reference_times: Dict[datetime.date, Dict[str, str]],
    dates: List[datetime.date],
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    isha_shafaq: Optional[str] = None,
) -> np.ndarray:
    """Compute residual vector in UTC minutes for fajr/isha."""
    cache_key = _stage1_residual_cache_key(
        params=params,
        reference_times=reference_times,
        dates=dates,
        elevation=elevation,
        pressure=pressure,
        temperature=temperature,
        timezone=timezone,
        calculation_method=calculation_method,
        asr_madhab=asr_madhab,
        isha_shafaq=isha_shafaq,
    )
    cached = _STAGE1_RESIDUAL_CACHE.get(cache_key)
    if cached is not None:
        return cached.copy()

    lat, lon, fajr_angle, isha_angle = [float(x) for x in params]

    residuals: List[float] = []
    for date_obj in dates:
        ref = reference_times.get(date_obj)
        if not ref:
            continue
        model = compute_prayer_times(
            target_date=date_obj,
            lat=lat,
            lon=lon,
            elevation=elevation,
            pressure=pressure,
            temperature=temperature,
            timezone=timezone,
            fajr_angle=fajr_angle,
            isha_angle=isha_angle,
            calculation_method=calculation_method,
            asr_madhab=asr_madhab,
            isha_shafaq=isha_shafaq,
        )
        for prayer in ("fajr", "isha"):
            model_utc = model.get(prayer)
            ref_text = ref.get(prayer)
            if model_utc is None or not ref_text:
                continue
            try:
                ref_utc = local_time_str_to_utc_minutes(
                    ref_text,
                    date_obj,
                    timezone,
                    tz_name=_STAGE1_TIMEZONE_NAME,
                )
            except (ValueError, TypeError):
                continue
            residuals.append(circular_minutes_diff(model_utc, ref_utc))

    if not residuals:
        result = np.array([720.0], dtype=float)
        _STAGE1_RESIDUAL_CACHE[cache_key] = result
        _stage1_evict_if_needed(_STAGE1_RESIDUAL_CACHE)
        return result.copy()

    result = np.asarray(residuals, dtype=float)
    _STAGE1_RESIDUAL_CACHE[cache_key] = result
    _stage1_evict_if_needed(_STAGE1_RESIDUAL_CACHE)
    return result.copy()


def objective_function(
    params: np.ndarray,
    reference_times: Dict[datetime.date, Dict[str, str]],
    dates: List[datetime.date],
    base_lat: float,
    base_lon: float,
    config: Stage1Config,
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    isha_shafaq: Optional[str] = None,
) -> float:
    residuals = compute_residuals(
        params=params,
        reference_times=reference_times,
        dates=dates,
        elevation=elevation,
        pressure=pressure,
        temperature=temperature,
        timezone=timezone,
        calculation_method=calculation_method,
        asr_madhab=asr_madhab,
        isha_shafaq=isha_shafaq,
    )
    core = robust_loss(
        residuals,
        method=config.robust_loss_method,
        huber_delta=config.huber_delta,
        tukey_c=config.tukey_c,
    )

    d_lat = float(params[0] - base_lat)
    d_lon = float(params[1] - base_lon)
    reg_coord = config.lambda_coord_shift * (d_lat * d_lat + d_lon * d_lon)

    return float(core + reg_coord)


def optimize_continuous_parameters(
    initial_params: np.ndarray,
    bounds: List[tuple[float, float]],
    reference_times: Dict[datetime.date, Dict[str, str]],
    dates: List[datetime.date],
    base_lat: float,
    base_lon: float,
    config: Stage1Config,
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    isha_shafaq: Optional[str] = None,
) -> tuple[np.ndarray, float, bool]:
    """Run L-BFGS-B optimization for continuous parameters."""
    result = minimize(
        fun=objective_function,
        x0=np.asarray(initial_params, dtype=float),
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 300, "ftol": 1e-9},
        args=(
            reference_times,
            dates,
            base_lat,
            base_lon,
            config,
            elevation,
            pressure,
            temperature,
            timezone,
            calculation_method,
            asr_madhab,
            isha_shafaq,
        ),
    )
    return np.asarray(result.x, dtype=float), float(result.fun), bool(result.success)


def _compute_daily_error_map(
    params: np.ndarray,
    reference_times: Dict[datetime.date, Dict[str, str]],
    all_dates: List[datetime.date],
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    isha_shafaq: Optional[str] = None,
) -> Dict[datetime.date, float]:
    """Compute strict per-day max residual (minutes) for fajr/isha.

    A day is considered clean only when both fajr and isha residuals are
    present and small; therefore we use max(abs(fajr), abs(isha)).
    """
    lat, lon, fajr_angle, isha_angle = [float(x) for x in params]
    day_errors: Dict[datetime.date, float] = {}
    for date_obj in sorted(all_dates):
        ref = reference_times.get(date_obj)
        if not ref:
            continue

        model = compute_prayer_times(
            target_date=date_obj,
            lat=lat,
            lon=lon,
            elevation=elevation,
            pressure=pressure,
            temperature=temperature,
            timezone=timezone,
            fajr_angle=fajr_angle,
            isha_angle=isha_angle,
            calculation_method=calculation_method,
            asr_madhab=asr_madhab,
            isha_shafaq=isha_shafaq,
        )

        vals: List[float] = []
        for prayer in ("fajr", "isha"):
            model_utc = model.get(prayer)
            ref_text = ref.get(prayer)
            if model_utc is None or not ref_text:
                continue
            try:
                ref_utc = local_time_str_to_utc_minutes(
                    ref_text,
                    date_obj,
                    timezone,
                    tz_name=_STAGE1_TIMEZONE_NAME,
                )
            except (ValueError, TypeError):
                continue
            vals.append(abs(float(circular_minutes_diff(model_utc, ref_utc))))

        if len(vals) < 2:
            continue
        day_errors[date_obj] = float(max(vals))

    return day_errors


def _select_clean_core_dates(
    day_errors: Dict[datetime.date, float],
    threshold_minutes: float,
    min_days: int,
    lookahead_days: int,
) -> List[datetime.date]:
    """Select clean core dates and fallback to best days if too few remain."""
    ordered = sorted(day_errors.items())
    dates_only = [d for d, _ in ordered]
    err_map = {d: float(e) for d, e in ordered}

    clean: List[datetime.date] = []
    lookahead = max(0, int(lookahead_days))
    threshold = float(threshold_minutes)
    for idx, date_obj in enumerate(dates_only):
        if err_map.get(date_obj, float("inf")) > threshold:
            continue
        end_idx = min(len(dates_only), idx + 1 + lookahead)
        future_dates = dates_only[idx:end_idx]
        if all(err_map.get(d, float("inf")) <= threshold for d in future_dates):
            clean.append(date_obj)

    if len(clean) >= int(min_days):
        return clean

    ranked = sorted(day_errors.items(), key=lambda kv: float(kv[1]))
    keep_n = min(max(int(min_days), 1), len(ranked))
    return [d for d, _ in ranked[:keep_n]]


def _is_flat_shift_artifact_regime(
    day_errors: Dict[datetime.date, float],
    excluded_dates: List[datetime.date],
) -> bool:
    """Detect near-constant step-shift regimes (e.g. fixed +60 min blocks).

    These are typically clock/reference alignment artifacts rather than true
    seasonal instability. A regime is considered an artifact when:
    - it is long enough,
    - excluded-day errors are almost flat,
    - excluded-day level is far from kept-day level.
    """
    if not day_errors or not excluded_dates:
        return False

    excluded_set = set(excluded_dates)
    if len(excluded_set) < 60:
        return False

    excluded_vals = [
        float(day_errors[d])
        for d in sorted(excluded_set)
        if d in day_errors and np.isfinite(float(day_errors[d]))
    ]
    kept_vals = [
        float(v)
        for d, v in sorted(day_errors.items())
        if d not in excluded_set and np.isfinite(float(v))
    ]
    if len(excluded_vals) < 30 or len(kept_vals) < 30:
        return False

    excl_med = float(percentile(excluded_vals, 0.5))
    keep_med = float(percentile(kept_vals, 0.5))
    excl_p10 = float(percentile(excluded_vals, 0.10))
    excl_p90 = float(percentile(excluded_vals, 0.90))
    excl_spread = float(max(0.0, excl_p90 - excl_p10))

    # Large, flat, common-mode level shift => artifact-like block.
    return (excl_med - keep_med) >= 25.0 and excl_spread <= 4.0


def _contiguous_date_ranges(dates: List[datetime.date]) -> List[Dict[str, Any]]:
    """Build contiguous date ranges from a sorted list of dates."""
    if not dates:
        return []
    ordered = sorted(set(dates))
    ranges: List[Dict[str, Any]] = []
    start = ordered[0]
    end = ordered[0]
    for curr in ordered[1:]:
        if (curr - end).days == 1:
            end = curr
            continue
        ranges.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "days": int((end - start).days + 1),
            }
        )
        start = curr
        end = curr
    ranges.append(
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": int((end - start).days + 1),
        }
    )
    return ranges


def _detect_unstable_regime_dates(
    day_errors: Dict[datetime.date, float],
) -> set[datetime.date]:
    """Detect a broad unstable season from day-level fajr/isha errors.

    Pattern-based detector (independent of monthly totals):
    - find a sustained high-error regime from a robust high threshold,
    - allow small gaps,
    - expand shoulders with a lower threshold,
    - require sufficient length and amplitude so clean cities are not flagged.
    """
    if len(day_errors) < 90:
        return set()

    ordered = sorted(day_errors.items())
    dates = [d for d, _ in ordered]
    vals = np.asarray([float(v) for _, v in ordered], dtype=float)

    p15 = float(np.percentile(vals, 15.0))
    p80 = float(np.percentile(vals, 80.0))
    span = max(0.0, p80 - p15)

    high_thr = p15 + 0.20 * span
    low_thr = p15 + 0.08 * span

    high_dates = [d for d, v in ordered if float(v) > high_thr]
    if not high_dates:
        return set()

    merged: List[tuple[datetime.date, datetime.date]] = []
    start = high_dates[0]
    end = high_dates[0]
    for curr in high_dates[1:]:
        if (curr - end).days <= 2:
            end = curr
            continue
        merged.append((start, end))
        start = curr
        end = curr
    merged.append((start, end))

    start, end = max(merged, key=lambda rg: (rg[1] - rg[0]).days)
    err_map = {d: float(v) for d, v in ordered}

    while True:
        prev = start - datetime.timedelta(days=1)
        if prev in err_map and err_map[prev] > low_thr:
            start = prev
            continue
        break
    while True:
        nxt = end + datetime.timedelta(days=1)
        if nxt in err_map and err_map[nxt] > low_thr:
            end = nxt
            continue
        break

    regime_dates = {d for d in dates if start <= d <= end}
    if not regime_dates:
        return set()

    regime_vals = [err_map[d] for d in sorted(regime_dates)]
    amplitude = float(max(regime_vals) - p15)
    length_days = int((end - start).days + 1)

    if length_days < 45:
        return set()
    if amplitude < 3.0:
        return set()

    return regime_dates


def _is_timezone_dst_transition(
    tz_name: str,
    date1: datetime.date,
    date2: datetime.date,
    detected_shift_minutes: float,
) -> bool:
    """Return True when the UTC offset for *tz_name* changes between *date1*
    and *date2* by approximately *detected_shift_minutes*.

    If so, the jump in reference data is explained by the IANA timezone
    (the prayer calculator already handles it), so it should NOT be
    treated as a reference-data artefact.
    """
    try:
        import pytz  # type: ignore

        tz = pytz.timezone(tz_name)
        # Use noon + is_dst=False to avoid ambiguous/nonexistent time errors at
        # DST boundaries (rare at noon, but guards against edge-case locales).
        dt1 = tz.localize(
            datetime.datetime(date1.year, date1.month, date1.day, 12), is_dst=False
        )
        dt2 = tz.localize(
            datetime.datetime(date2.year, date2.month, date2.day, 12), is_dst=False
        )
        off1 = dt1.utcoffset()
        off2 = dt2.utcoffset()
        if off1 is None or off2 is None:
            return False
        tz_shift = (off2.total_seconds() - off1.total_seconds()) / 60.0  # minutes
        return abs(tz_shift - detected_shift_minutes) < 15.0
    except (ImportError, KeyError, AttributeError):
        return False


def _detect_reference_clock_shifts(
    reference_times: Dict[datetime.date, Dict[str, str]],
    all_dates: List[datetime.date],
    tz_name: Optional[str] = None,
) -> Dict[datetime.date, float]:
    """Detect likely clock-shift artifact dates and their median shift minutes.

    A date is flagged when most prayers shift by roughly +/-60 minutes
    compared to the previous day with low spread across prayers.

    If *tz_name* is provided and the detected jump matches a timezone DST
    transition on the same dates, the jump is considered natural (handled
    by the prayer calculator) and is NOT flagged.
    """
    ordered = sorted(d for d in all_dates if d in reference_times)
    if len(ordered) < 2:
        return {}

    flagged: Dict[datetime.date, float] = {}
    prayers = ("fajr", "shurooq", "dhuhr", "asr", "maghrib", "isha")
    for idx in range(1, len(ordered)):
        prev_d = ordered[idx - 1]
        curr_d = ordered[idx]
        prev_ref = reference_times.get(prev_d, {})
        curr_ref = reference_times.get(curr_d, {})
        diffs: List[float] = []
        for prayer in prayers:
            prev_txt = prev_ref.get(prayer)
            curr_txt = curr_ref.get(prayer)
            if not prev_txt or not curr_txt:
                continue
            try:
                prev_min = parse_time_to_minutes(prev_txt)
                curr_min = parse_time_to_minutes(curr_txt)
            except (ValueError, TypeError):
                continue
            diffs.append(circular_minutes_diff(curr_min, prev_min))

        if len(diffs) < 4:
            continue
        median_shift = float(percentile(diffs, 0.5))
        abs_dev = [abs(float(v) - median_shift) for v in diffs]
        mad = float(percentile(abs_dev, 0.5))

        if abs(abs(median_shift) - 60.0) <= 12.0 and mad <= 8.0:
            # Check if this jump is explained by a timezone DST transition.
            # If the calculator already accounts for DST via the IANA
            # timezone, storing a clock-offset would double-apply the shift.
            if tz_name and _is_timezone_dst_transition(
                tz_name, prev_d, curr_d, median_shift
            ):
                continue
            # The jump between prev_d -> curr_d is caused by the transition
            # occurring on prev_d (local civil clock change), so anchor the
            # artifact to prev_d to avoid a one-day late flag.
            flagged[prev_d] = float(median_shift)

    return flagged


def _detect_clock_shift_blocks(
    reference_times: Dict[datetime.date, Dict[str, str]],
    all_dates: List[datetime.date],
    tz_name: Optional[str] = None,
) -> List[tuple[datetime.date, datetime.date, int]]:
    shift_map = _detect_reference_clock_shifts(
        reference_times, all_dates, tz_name=tz_name
    )
    if not shift_map:
        return []

    flagged_dates = sorted(shift_map.keys())
    blocks: List[tuple[datetime.date, datetime.date, int]] = []
    block_start = flagged_dates[0]
    block_end = flagged_dates[0]
    shift_values = [float(shift_map[block_start])]

    for date_obj in flagged_dates[1:]:
        if (date_obj - block_end).days == 1:
            block_end = date_obj
            shift_values.append(float(shift_map[date_obj]))
            continue

        median_shift = int(round(float(np.median(np.array(shift_values, dtype=float)))))
        blocks.append((block_start, block_end, median_shift))
        block_start = date_obj
        block_end = date_obj
        shift_values = [float(shift_map[date_obj])]

    median_shift = int(round(float(np.median(np.array(shift_values, dtype=float)))))
    blocks.append((block_start, block_end, median_shift))
    return blocks


def _shift_hhmm(time_str: str, delta_minutes: int) -> str:
    text = str(time_str or "").strip()
    parts = text.split(":")
    if len(parts) < 2:
        return text
    try:
        hh = int(parts[0])
        mm = int(parts[1])
    except (ValueError, TypeError):
        return text
    total = (hh * 60 + mm + int(delta_minutes)) % 1440
    new_hh = total // 60
    new_mm = total % 60
    return f"{new_hh}:{new_mm:02d}"


def _normalize_reference_times_with_clock_blocks(
    reference_times: Dict[datetime.date, Dict[str, str]],
    blocks: List[tuple[datetime.date, datetime.date, int]],
) -> Dict[datetime.date, Dict[str, str]]:
    if not blocks:
        return dict(reference_times)

    shifted_date_offset: Dict[datetime.date, int] = {}
    for blk_start, blk_end, blk_offset in blocks:
        d = blk_start
        while d <= blk_end:
            shifted_date_offset[d] = int(blk_offset)
            d += datetime.timedelta(days=1)

    normalized: Dict[datetime.date, Dict[str, str]] = {}
    for date_obj, prayers in reference_times.items():
        offset_min = int(shifted_date_offset.get(date_obj, 0))
        if offset_min == 0:
            normalized[date_obj] = dict(prayers)
            continue
        adjusted: Dict[str, str] = {}
        for prayer, tstr in prayers.items():
            adjusted[prayer] = _shift_hhmm(tstr, -offset_min)
        normalized[date_obj] = adjusted
    return normalized


def _serialize_clock_blocks(
    blocks: List[tuple[datetime.date, datetime.date, int]],
) -> Optional[str]:
    if not blocks:
        return None
    payload = [
        {
            "start": start.strftime("%m-%d"),
            "end": end.strftime("%m-%d"),
            "offset": int(offset),
        }
        for start, end, offset in blocks
    ]
    return json.dumps(payload, separators=(",", ":"))


def _evaluate_offsets_mae(
    *,
    params: np.ndarray,
    dates: List[datetime.date],
    reference_times: Dict[datetime.date, Dict[str, str]],
    elevation: float,
    timezone: float,
    tz_name: Optional[str],
    offsets: Dict[str, float],
    extra_calc_kwargs: Dict[str, Any],
) -> float:
    _rmse, mae, _per_rmse, _per_mae, _per_max, _per_signed = _compute_detailed_errors(
        params,
        available_dates=dates,
        reference_times=reference_times,
        elevation=elevation,
        timezone=timezone,
        tz_name=tz_name,
        isha_minutes=0.0,
        offsets=offsets,
        extra_calc_kwargs=extra_calc_kwargs,
    )
    return float(mae)


def _detect_non_solar_dates(
    location_data: Dict[str, Any],
    dates: List[datetime.date],
    timezone: float,
    tz_name: Optional[str],
) -> set[datetime.date]:
    """Detect dates where sunrise/sunset require fallback or are unavailable.

    These days are excluded from stage-1 optimization to avoid biasing the
    model in extreme-latitude periods where the sun does not rise/set normally.
    """
    if not dates:
        return set()

    lat = float(location_data.get("optimized_lat") or location_data["latitude"])
    lon = float(location_data.get("optimized_lon") or location_data["longitude"])
    elevation = float(location_data.get("elevation", 0.0) or 0.0)
    pressure = float(location_data.get("pressure", 1010.0) or 1010.0)
    temperature = float(location_data.get("temp", 10.0) or 10.0)
    fajr_angle = float(location_data.get("fajr_angle", 18.0) or 18.0)
    isha_angle = float(location_data.get("isha_angle", 17.0) or 17.0)

    excluded: set[datetime.date] = set()
    for date_obj in sorted(dates):
        times, method_used, _error = calculate_prayer_times(
            lat_dec=lat,
            lon_dec=lon,
            elevation=elevation,
            pressure=pressure,
            temp=temperature,
            tz_name=tz_name,
            tz_offset_hours=float(timezone),
            fajr_angle=fajr_angle,
            isha_angle=isha_angle,
            isha_minutes=0.0,
            target_date=date_obj,
            rounding="off",
            calculation_method="angle_based",
            asr_madhab=0,
            isha_shafaq=(location_data.get("isha_shafaq") or "general"),
            skip_fallback=False,
            high_lat_method=0,
        )
        shurooq_txt = times.get("shurooq")
        maghrib_txt = times.get("maghrib")
        shurooq_fallback = int(method_used.get("shurooq", -1)) != -1
        maghrib_fallback = int(method_used.get("maghrib", -1)) != -1
        if (
            not shurooq_txt
            or shurooq_txt == "N/A"
            or not maghrib_txt
            or maghrib_txt == "N/A"
            or shurooq_fallback
            or maghrib_fallback
        ):
            excluded.add(date_obj)
    return excluded


def search_discrete_configurations(
    location_data: Dict[str, Any],
    reference_times: Dict[datetime.date, Dict[str, str]],
    dates: List[datetime.date],
    config: Stage1Config,
    tz_name: Optional[str],
) -> Dict[str, Any]:
    """Optimize continuous core parameters with angle-based method only.

    Calculation method switching is intentionally deferred until after
    the absolute best angle-based core settings are found.
    """
    base_lat = float(location_data.get("optimized_lat") or location_data["latitude"])
    base_lon = float(location_data.get("optimized_lon") or location_data["longitude"])
    elevation = float(location_data.get("elevation", 0.0) or 0.0)
    pressure = float(location_data.get("pressure", 1010.0) or 1010.0)
    temperature = float(location_data.get("temp", 10.0) or 10.0)
    sample_date = dates[0] if dates else None
    timezone = resolve_timezone_offset_hours(
        location_data.get("timezone"),
        tz_name=tz_name,
        sample_date=sample_date,
    )
    isha_shafaq = location_data.get("isha_shafaq")
    isha_shafaq = location_data.get("isha_shafaq")

    d_lat, d_lon = km_to_degrees(5.0, base_lat)
    bounds = [
        (base_lat - d_lat, base_lat + d_lat),
        (base_lon - d_lon, base_lon + d_lon),
        (10.0, 25.0),
        (10.0, 25.0),
    ]
    initial = np.array(
        [
            base_lat,
            base_lon,
            float(location_data.get("fajr_angle", 18.0) or 18.0),
            float(location_data.get("isha_angle", 17.0) or 17.0),
        ],
        dtype=float,
    )

    def _best_angle_seed(asr_madhab_value: str) -> np.ndarray:
        best_seed = initial.copy()
        best_loss = float("inf")
        coarse_fajr = np.arange(14.0, 21.5, 0.5)
        coarse_isha = np.arange(14.0, 21.5, 0.5)
        for fajr_seed in coarse_fajr:
            for isha_seed in coarse_isha:
                trial = best_seed.copy()
                trial[2] = float(fajr_seed)
                trial[3] = float(isha_seed)
                trial_loss = objective_function(
                    trial,
                    reference_times=reference_times,
                    dates=dates,
                    base_lat=base_lat,
                    base_lon=base_lon,
                    config=config,
                    elevation=elevation,
                    pressure=pressure,
                    temperature=temperature,
                    timezone=timezone,
                    calculation_method="angles",
                    asr_madhab=asr_madhab_value,
                    isha_shafaq=isha_shafaq,
                )
                if trial_loss < best_loss:
                    best_loss = trial_loss
                    best_seed = trial
        return best_seed

    best: Optional[Dict[str, Any]] = None
    for asr_madhab in ("standard", "hanafi"):
        seeded_initial = _best_angle_seed(asr_madhab)
        x_opt, score, success = optimize_continuous_parameters(
            initial_params=seeded_initial,
            bounds=bounds,
            reference_times=reference_times,
            dates=dates,
            base_lat=base_lat,
            base_lon=base_lon,
            config=config,
            elevation=elevation,
            pressure=pressure,
            temperature=temperature,
            timezone=timezone,
            calculation_method="angles",
            asr_madhab=asr_madhab,
            isha_shafaq=isha_shafaq,
        )
        candidate = {
            "params": x_opt,
            "score": score,
            "success": success,
            "calculation_method": "angles",
            "asr_madhab": asr_madhab,
        }
        if best is None or score < float(best.get("score", float("inf"))):
            best = candidate

    if best is None:
        raise RuntimeError("Failed to evaluate stage 1 core optimization")
    return best


def _method_loss_for_fixed_core(
    params: np.ndarray,
    reference_times: Dict[datetime.date, Dict[str, str]],
    dates: List[datetime.date],
    config: Stage1Config,
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    isha_shafaq: Optional[str] = None,
) -> float:
    params_arr = np.asarray(params, dtype=np.float64)
    cache_key = (
        params_arr.tobytes(),
        int(len(params_arr)),
        id(reference_times),
        tuple(dates),
        float(elevation),
        float(pressure),
        float(temperature),
        float(timezone),
        str(calculation_method),
        str(asr_madhab),
        str(isha_shafaq) if isha_shafaq is not None else None,
        str(config.robust_loss_method),
        float(config.huber_delta),
        float(config.tukey_c),
        _STAGE1_TIMEZONE_NAME,
    )
    cached = _STAGE1_METHOD_LOSS_CACHE.get(cache_key)
    if cached is not None:
        return float(cached)

    residuals = compute_residuals(
        params=params,
        reference_times=reference_times,
        dates=dates,
        elevation=elevation,
        pressure=pressure,
        temperature=temperature,
        timezone=timezone,
        calculation_method=calculation_method,
        asr_madhab=asr_madhab,
        isha_shafaq=isha_shafaq,
    )
    loss = robust_loss(
        residuals,
        method=config.robust_loss_method,
        huber_delta=config.huber_delta,
        tukey_c=config.tukey_c,
    )
    _STAGE1_METHOD_LOSS_CACHE[cache_key] = float(loss)
    _stage1_evict_if_needed(_STAGE1_METHOD_LOSS_CACHE)
    return float(loss)


def _mae_local_angle_polish(
    params: np.ndarray,
    reference_times: Dict[datetime.date, Dict[str, str]],
    dates: List[datetime.date],
    elevation: float,
    pressure: float,
    temperature: float,
    timezone: float,
    calculation_method: str,
    asr_madhab: str,
    config: Stage1Config,
    isha_shafaq: Optional[str] = None,
) -> tuple[np.ndarray, Dict[str, float]]:
    """Final local angle polish using MAE on stable dates only."""
    if not dates:
        return params, {
            "before_mae": float("inf"),
            "after_mae": float("inf"),
            "before_fajr_mae": float("inf"),
            "after_fajr_mae": float("inf"),
        }

    base = np.asarray(params, dtype=float).copy()
    window = max(0.0, float(config.final_mae_angle_window_deg))
    step = max(1e-6, float(config.final_mae_angle_step_deg))

    before_res = compute_residuals(
        params=base,
        reference_times=reference_times,
        dates=dates,
        elevation=elevation,
        pressure=pressure,
        temperature=temperature,
        timezone=timezone,
        calculation_method=calculation_method,
        asr_madhab=asr_madhab,
        isha_shafaq=isha_shafaq,
    )
    before_abs = np.abs(before_res)
    before_mae = float(np.mean(before_abs)) if len(before_abs) else float("inf")
    before_fajr = np.abs(before_res[0::2])
    before_fajr_mae = float(np.mean(before_fajr)) if len(before_fajr) else float("inf")

    def _grid(center: float) -> List[float]:
        low = max(10.0, center - window)
        high = min(25.0, center + window)
        n_steps = int(round((high - low) / step))
        vals = [low + i * step for i in range(max(0, n_steps) + 1)]
        vals.append(center)
        return sorted({round(float(v), 4) for v in vals})

    fajr_vals = _grid(float(base[2]))
    isha_vals = _grid(float(base[3]))

    best = base.copy()
    best_mae = before_mae
    best_fajr_mae = before_fajr_mae
    best_delta = 0.0

    for fajr_angle in fajr_vals:
        for isha_angle in isha_vals:
            trial = base.copy()
            trial[2] = float(fajr_angle)
            trial[3] = float(isha_angle)
            trial_res = compute_residuals(
                params=trial,
                reference_times=reference_times,
                dates=dates,
                elevation=elevation,
                pressure=pressure,
                temperature=temperature,
                timezone=timezone,
                calculation_method=calculation_method,
                asr_madhab=asr_madhab,
                isha_shafaq=isha_shafaq,
            )
            trial_abs = np.abs(trial_res)
            if len(trial_abs) == 0:
                continue
            trial_mae = float(np.mean(trial_abs))
            trial_fajr = np.abs(trial_res[0::2])
            trial_fajr_mae = (
                float(np.mean(trial_fajr)) if len(trial_fajr) else float("inf")
            )
            delta = abs(float(fajr_angle) - float(base[2])) + abs(
                float(isha_angle) - float(base[3])
            )

            if trial_mae + 1e-9 < best_mae:
                best = trial
                best_mae = trial_mae
                best_fajr_mae = trial_fajr_mae
                best_delta = delta
                continue
            if (
                abs(trial_mae - best_mae) <= 1e-9
                and trial_fajr_mae + 1e-9 < best_fajr_mae
            ):
                best = trial
                best_fajr_mae = trial_fajr_mae
                best_delta = delta
                continue
            if (
                abs(trial_mae - best_mae) <= 1e-9
                and abs(trial_fajr_mae - best_fajr_mae) <= 1e-9
                and delta < best_delta
            ):
                best = trial
                best_delta = delta

    return best, {
        "before_mae": float(before_mae),
        "after_mae": float(best_mae),
        "before_fajr_mae": float(before_fajr_mae),
        "after_fajr_mae": float(best_fajr_mae),
    }


def optimize_pure_astronomical_core(
    location_data: Dict[str, Any],
    reference_times: Dict[datetime.date, Dict[str, str]],
    available_dates: List[datetime.date],
    tz_name: Optional[str] = None,
    config: Optional[Stage1Config] = None,
) -> Tuple[PipelineContext, Stage1Diagnostics]:
    """Run Stage 1 and return core astronomical calibration output."""
    started_at = time.perf_counter()
    step_timings: Dict[str, float] = {}

    step_started_at = time.perf_counter()
    cfg = config or Stage1Config()
    all_dates_sorted = sorted(available_dates)
    clock_blocks: List[tuple[datetime.date, datetime.date, int]] = []
    artifact_dates: set[datetime.date] = set()
    if bool(cfg.detect_clock_offsets):
        # Pass tz_name so DST transitions already handled by the calculator
        # are not misidentified as reference-data clock-shift artefacts.
        # Fall back to location_data["timezone"] if tz_name was not provided
        # (e.g. DST checkbox unchecked) but the timezone is an IANA name.
        _clock_tz = tz_name
        if not _clock_tz:
            _tz_val = location_data.get("timezone")
            if isinstance(_tz_val, str):
                _tz_txt = _tz_val.strip()
                try:
                    float(_tz_txt)
                except (ValueError, TypeError):
                    if _tz_txt:
                        _clock_tz = _tz_txt
        clock_blocks = _detect_clock_shift_blocks(
            reference_times,
            all_dates_sorted,
            tz_name=_clock_tz,
        )
        for block_start, block_end, _block_offset in clock_blocks:
            cursor = block_start
            while cursor <= block_end:
                artifact_dates.add(cursor)
                cursor += datetime.timedelta(days=1)

    candidate_dates = [d for d in all_dates_sorted if d not in artifact_dates]
    step_timings["detect_clock_artifacts"] = float(
        time.perf_counter() - step_started_at
    )

    step_started_at = time.perf_counter()
    sample_date = candidate_dates[0] if candidate_dates else None
    timezone = resolve_timezone_offset_hours(
        location_data.get("timezone"),
        tz_name=tz_name,
        sample_date=sample_date,
    )
    timezone_name = tz_name
    non_solar_dates: set[datetime.date] = set()
    dates_for_core: List[datetime.date] = sorted(candidate_dates)
    if not timezone_name:
        tz_value = location_data.get("timezone")
        if isinstance(tz_value, str):
            tz_text = tz_value.strip()
            if tz_text:
                try:
                    float(tz_text)
                except (ValueError, TypeError):
                    timezone_name = tz_text

    non_solar_dates = _detect_non_solar_dates(
        location_data=location_data,
        dates=sorted(candidate_dates),
        timezone=timezone,
        tz_name=timezone_name,
    )
    candidate_dates = [d for d in candidate_dates if d not in non_solar_dates]
    dates_for_core = sorted(candidate_dates)
    step_timings["prepare_dates_and_timezone"] = float(
        time.perf_counter() - step_started_at
    )

    # Auto-select evaluation mode: use tz_name only when it clearly matches
    # the reference-time regime better than fixed-offset conversion.
    step_started_at = time.perf_counter()
    selected_timezone_name: Optional[str] = None
    if timezone_name and candidate_dates:
        base_lat = float(
            location_data.get("optimized_lat") or location_data["latitude"]
        )
        base_lon = float(
            location_data.get("optimized_lon") or location_data["longitude"]
        )
        init_fajr = float(location_data.get("fajr_angle", 18.0) or 18.0)
        init_isha = float(location_data.get("isha_angle", 17.0) or 17.0)
        init_params = np.array([base_lat, base_lon, init_fajr, init_isha], dtype=float)

        asr_raw = location_data.get("asr_madhab", 0)
        asr_probe = "hanafi" if int(asr_raw or 0) == 1 else "standard"
        calc_raw = (
            str(location_data.get("calculation_method") or "angle_based")
            .strip()
            .lower()
        )
        calc_probe = "moonsighting" if calc_raw == "moonsighting" else "angles"

        prev_tz_name = globals().get("_STAGE1_TIMEZONE_NAME")
        try:
            globals()["_STAGE1_TIMEZONE_NAME"] = None
            fixed_res = compute_residuals(
                params=init_params,
                reference_times=reference_times,
                dates=dates_for_core,
                elevation=float(location_data.get("elevation", 0.0) or 0.0),
                pressure=float(location_data.get("pressure", 1010.0) or 1010.0),
                temperature=float(location_data.get("temp", 10.0) or 10.0),
                timezone=timezone,
                calculation_method=calc_probe,
                asr_madhab=asr_probe,
                isha_shafaq=location_data.get("isha_shafaq"),
            )
            fixed_mae = float(np.mean(np.abs(fixed_res)))

            globals()["_STAGE1_TIMEZONE_NAME"] = timezone_name
            tz_res = compute_residuals(
                params=init_params,
                reference_times=reference_times,
                dates=dates_for_core,
                elevation=float(location_data.get("elevation", 0.0) or 0.0),
                pressure=float(location_data.get("pressure", 1010.0) or 1010.0),
                temperature=float(location_data.get("temp", 10.0) or 10.0),
                timezone=timezone,
                calculation_method=calc_probe,
                asr_madhab=asr_probe,
                isha_shafaq=location_data.get("isha_shafaq"),
            )
            tz_mae = float(np.mean(np.abs(tz_res)))

            if tz_mae + 1e-6 < fixed_mae:
                selected_timezone_name = timezone_name
        finally:
            globals()["_STAGE1_TIMEZONE_NAME"] = prev_tz_name
    step_timings["timezone_mode_probe"] = float(time.perf_counter() - step_started_at)

    globals()["_STAGE1_TIMEZONE_NAME"] = selected_timezone_name
    stage1_elevation = float(location_data.get("elevation", 0.0) or 0.0)
    stage1_temperature = float(location_data.get("temp", 10.0) or 10.0)
    stage1_pressure = float(location_data.get("pressure", 1010.0) or 1010.0)
    isha_shafaq = location_data.get("isha_shafaq")
    dates_for_environment = sorted(candidate_dates)

    daily_error_cache: Dict[tuple, Dict[datetime.date, float]] = {}

    def _daily_error_map_cached(
        params_for_cache: np.ndarray,
        dates_for_cache: List[datetime.date],
        calculation_method_for_cache: str,
        asr_madhab_for_cache: str,
    ) -> Dict[datetime.date, float]:
        params_arr = np.asarray(params_for_cache, dtype=np.float64)
        cache_key = (
            params_arr.tobytes(),
            int(len(params_arr)),
            tuple(dates_for_cache),
            float(stage1_elevation),
            float(stage1_pressure),
            float(stage1_temperature),
            float(timezone),
            str(calculation_method_for_cache),
            str(asr_madhab_for_cache),
            str(isha_shafaq) if isha_shafaq is not None else None,
        )
        cached_map = daily_error_cache.get(cache_key)
        if cached_map is not None:
            return cached_map

        computed = _compute_daily_error_map(
            params=params_arr,
            reference_times=reference_times,
            all_dates=dates_for_cache,
            elevation=stage1_elevation,
            pressure=stage1_pressure,
            temperature=stage1_temperature,
            timezone=timezone,
            calculation_method=calculation_method_for_cache,
            asr_madhab=asr_madhab_for_cache,
            isha_shafaq=isha_shafaq,
        )
        daily_error_cache[cache_key] = computed
        return computed

    # Pre-detect seasonal unstable regime using edge-season anchored angles.
    # This keeps instability detection separate from DST artifacts and from
    # compromise-all-year angle fits.
    step_started_at = time.perf_counter()
    edge_unstable_dates: set[datetime.date] = set()
    if len(candidate_dates) >= max(90, int(cfg.min_clean_core_days)):
        edge_span = min(90, max(45, len(candidate_dates) // 5))
        edge_dates = sorted(candidate_dates[:edge_span] + candidate_dates[-edge_span:])
        edge_candidate = search_discrete_configurations(
            location_data=location_data,
            reference_times=reference_times,
            dates=edge_dates,
            config=cfg,
            tz_name=tz_name,
        )
        edge_day_error_map = _daily_error_map_cached(
            params_for_cache=edge_candidate["params"],
            dates_for_cache=sorted(candidate_dates),
            calculation_method_for_cache=edge_candidate["calculation_method"],
            asr_madhab_for_cache=edge_candidate["asr_madhab"],
        )
        edge_unstable_dates = _detect_unstable_regime_dates(edge_day_error_map)
        if edge_unstable_dates:
            prefiltered_dates = sorted(
                d for d in candidate_dates if d not in edge_unstable_dates
            )
            if len(prefiltered_dates) >= max(60, int(cfg.min_clean_core_days)):
                dates_for_core = prefiltered_dates
    step_timings["edge_unstable_prefilter"] = float(
        time.perf_counter() - step_started_at
    )

    best_solution: Optional[Dict[str, Any]] = None
    daily_error_map: Dict[datetime.date, float] = {}
    flat_shift_artifact_detected = False

    step_started_at = time.perf_counter()
    for _ in range(max(1, int(cfg.max_refinement_iterations))):
        candidate = search_discrete_configurations(
            location_data=location_data,
            reference_times=reference_times,
            dates=dates_for_core,
            config=cfg,
            tz_name=tz_name,
        )
        params = candidate["params"]
        excluded_dates_candidate: List[datetime.date] = []

        if edge_unstable_dates:
            daily_error_map = _daily_error_map_cached(
                params_for_cache=params,
                dates_for_cache=sorted(candidate_dates),
                calculation_method_for_cache=candidate["calculation_method"],
                asr_madhab_for_cache=candidate["asr_madhab"],
            )
            best_solution = candidate
            break

        daily_error_map = _daily_error_map_cached(
            params_for_cache=params,
            dates_for_cache=sorted(candidate_dates),
            calculation_method_for_cache=candidate["calculation_method"],
            asr_madhab_for_cache=candidate["asr_madhab"],
        )
        new_dates_for_core = _select_clean_core_dates(
            day_errors=daily_error_map,
            threshold_minutes=cfg.clean_day_threshold_minutes,
            min_days=cfg.min_clean_core_days,
            lookahead_days=cfg.clean_day_lookahead_days,
        )

        if daily_error_map:
            clean_dates = set(new_dates_for_core)
            excluded_dates_candidate = [
                d
                for d in sorted(candidate_dates)
                if d in daily_error_map and d not in clean_dates
            ]
            if _is_flat_shift_artifact_regime(
                day_errors=daily_error_map,
                excluded_dates=excluded_dates_candidate,
            ):
                flat_shift_artifact_detected = True
                excluded_dates_candidate = []
                dates_for_core = sorted(candidate_dates)
                new_dates_for_core = sorted(candidate_dates)

        best_solution = candidate
        if not excluded_dates_candidate or new_dates_for_core == dates_for_core:
            break
        if not new_dates_for_core:
            break
        dates_for_core = new_dates_for_core
    step_timings["iterative_core_refinement"] = float(
        time.perf_counter() - step_started_at
    )

    if best_solution is None:
        raise RuntimeError("Stage 1 optimizer failed to produce a solution")

    best_params = best_solution["params"]
    # Phase 1 Asr madhab detection from direct Asr residuals.
    asr_choice = str(best_solution["asr_madhab"])
    step_started_at = time.perf_counter()
    asr_madhab_detection = {
        "enabled": False,
        "selected": asr_choice,
        "reason": "not_run",
    }
    asr_choice, asr_madhab_detection = _detect_asr_madhab_phase1(
        params=np.asarray(best_solution["params"], dtype=float),
        dates=dates_for_core,
        reference_times=reference_times,
        elevation=stage1_elevation,
        pressure=stage1_pressure,
        temperature=stage1_temperature,
        timezone=timezone,
        calculation_method="angles",
        isha_shafaq=isha_shafaq,
        config=cfg,
        current_asr_madhab=asr_choice,
    )
    step_timings["asr_madhab_detection"] = float(time.perf_counter() - step_started_at)

    # Post-core method check: only after angle-based optimization is complete.
    # Refit per method so comparison is fair and not biased by angle-based-only params.
    base_lat = float(location_data.get("optimized_lat") or location_data["latitude"])
    d_lat, d_lon = km_to_degrees(5.0, base_lat)
    base_lon = float(location_data.get("optimized_lon") or location_data["longitude"])
    method_bounds = [
        (base_lat - d_lat, base_lat + d_lat),
        (base_lon - d_lon, base_lon + d_lon),
        (10.0, 25.0),
        (10.0, 25.0),
    ]
    dates_for_method_selection = (
        sorted(dates_for_core) if dates_for_core else sorted(candidate_dates)
    )
    step_started_at = time.perf_counter()
    method_records: List[Dict[str, Any]] = []
    method_candidates: List[tuple[str, Optional[str]]] = [("angles", None)]
    for shafaq in ("general", "ahmer", "abyad"):
        method_candidates.append(("moonsighting", shafaq))

    def _evaluate_method_candidate(
        idx: int, method_name: str, shafaq_name: Optional[str]
    ):
        method_params, method_loss, method_success = optimize_continuous_parameters(
            initial_params=np.asarray(best_params, dtype=float),
            bounds=method_bounds,
            reference_times=reference_times,
            dates=dates_for_method_selection,
            base_lat=base_lat,
            base_lon=base_lon,
            config=cfg,
            elevation=stage1_elevation,
            pressure=stage1_pressure,
            temperature=stage1_temperature,
            timezone=timezone,
            calculation_method=method_name,
            asr_madhab=asr_choice,
            isha_shafaq=shafaq_name,
        )
        return {
            "order": int(idx),
            "method": method_name,
            "isha_shafaq": shafaq_name,
            "params": method_params,
            "loss": float(method_loss),
            "success": bool(method_success),
        }

    with ThreadPoolExecutor(max_workers=min(4, len(method_candidates))) as executor:
        futures = [
            executor.submit(_evaluate_method_candidate, idx, method_name, shafaq_name)
            for idx, (method_name, shafaq_name) in enumerate(method_candidates)
        ]
        method_records = [f.result() for f in futures]

    method_records.sort(key=lambda item: int(item.get("order", 0)))
    step_timings["method_refit_selection"] = float(
        time.perf_counter() - step_started_at
    )

    best_record = min(
        method_records,
        key=lambda item: (
            float(item.get("loss", float("inf"))),
            int(item.get("order", 0)),
        ),
    )
    selected_method = str(best_record["method"])
    selected_isha_shafaq = best_record.get("isha_shafaq")
    best_params = np.asarray(best_record["params"], dtype=float)
    if flat_shift_artifact_detected:
        selected_method = "angles"
        selected_isha_shafaq = None

    angle_loss = min(
        float(item["loss"]) for item in method_records if item["method"] == "angles"
    )
    moon_loss = min(
        float(item["loss"])
        for item in method_records
        if item["method"] == "moonsighting"
    )

    _mae_polish_info = {
        "before_mae": float("inf"),
        "after_mae": float("inf"),
        "before_fajr_mae": float("inf"),
        "after_fajr_mae": float("inf"),
    }
    geographic_calibration = {
        "enabled": bool(cfg.enable_geographic_calibration),
        "skipped": True,
    }
    _environmental_calibration = {"skipped": True}
    _final_angle_retest = {
        "before_mae": float("inf"),
        "after_mae": float("inf"),
        "before_fajr_mae": float("inf"),
        "after_fajr_mae": float("inf"),
    }
    if bool(cfg.enable_final_mae_angle_polish):
        step_started_at = time.perf_counter()
        best_params, _mae_polish_info = _mae_local_angle_polish(
            params=best_params,
            reference_times=reference_times,
            dates=dates_for_core,
            elevation=stage1_elevation,
            pressure=stage1_pressure,
            temperature=stage1_temperature,
            timezone=timezone,
            calculation_method=selected_method,
            asr_madhab=asr_choice,
            config=cfg,
            isha_shafaq=selected_isha_shafaq,
        )

        angle_loss = _method_loss_for_fixed_core(
            params=best_params,
            reference_times=reference_times,
            dates=dates_for_method_selection,
            config=cfg,
            elevation=stage1_elevation,
            pressure=stage1_pressure,
            temperature=stage1_temperature,
            timezone=timezone,
            calculation_method="angles",
            asr_madhab=asr_choice,
            isha_shafaq=None,
        )
        shafaq_candidates = ["general", "ahmer", "abyad"]

        def _moon_loss_eval(idx: int, shafaq_name: str):
            return (
                int(idx),
                shafaq_name,
                _method_loss_for_fixed_core(
                    params=best_params,
                    reference_times=reference_times,
                    dates=dates_for_method_selection,
                    config=cfg,
                    elevation=stage1_elevation,
                    pressure=stage1_pressure,
                    temperature=stage1_temperature,
                    timezone=timezone,
                    calculation_method="moonsighting",
                    asr_madhab=asr_choice,
                    isha_shafaq=shafaq_name,
                ),
            )

        with ThreadPoolExecutor(max_workers=min(3, len(shafaq_candidates))) as executor:
            moon_losses = [
                f.result()
                for f in [
                    executor.submit(_moon_loss_eval, idx, shafaq_name)
                    for idx, shafaq_name in enumerate(shafaq_candidates)
                ]
            ]
        moon_losses.sort(key=lambda item: int(item[0]))
        _best_idx, selected_isha_shafaq, moon_loss = min(
            moon_losses,
            key=lambda item: (float(item[2]), int(item[0])),
        )
        if flat_shift_artifact_detected:
            selected_method = "angles"
        else:
            selected_method = "moonsighting" if moon_loss < angle_loss else "angles"
        if selected_method == "angles":
            selected_isha_shafaq = None
        step_timings["post_method_mae_polish"] = float(
            time.perf_counter() - step_started_at
        )

    step_started_at = time.perf_counter()
    best_params, geographic_calibration = optimize_geographic_calibration(
        params=best_params,
        dates=dates_for_core,
        reference_times=reference_times,
        elevation=stage1_elevation,
        pressure=stage1_pressure,
        temperature=stage1_temperature,
        timezone=timezone,
        calculation_method=selected_method,
        asr_madhab=asr_choice,
        config=cfg,
        isha_shafaq=selected_isha_shafaq,
    )
    step_timings["geographic_calibration"] = float(
        time.perf_counter() - step_started_at
    )

    step_started_at = time.perf_counter()
    (
        stage1_elevation,
        stage1_temperature,
        stage1_pressure,
        _environmental_calibration,
    ) = optimize_environmental_calibration(
        params=best_params,
        dates=dates_for_environment,
        reference_times=reference_times,
        elevation=stage1_elevation,
        pressure=stage1_pressure,
        temperature=stage1_temperature,
        timezone=timezone,
        calculation_method=selected_method,
        asr_madhab=asr_choice,
        config=cfg,
        isha_shafaq=selected_isha_shafaq,
    )
    step_timings["environmental_calibration"] = float(
        time.perf_counter() - step_started_at
    )

    step_started_at = time.perf_counter()
    best_params, _final_angle_retest = _mae_local_angle_polish(
        params=best_params,
        reference_times=reference_times,
        dates=dates_for_core,
        elevation=stage1_elevation,
        pressure=stage1_pressure,
        temperature=stage1_temperature,
        timezone=timezone,
        calculation_method=selected_method,
        asr_madhab=asr_choice,
        config=cfg,
        isha_shafaq=selected_isha_shafaq,
    )
    step_timings["final_angle_retest"] = float(time.perf_counter() - step_started_at)

    step_started_at = time.perf_counter()
    angle_loss = _method_loss_for_fixed_core(
        params=best_params,
        reference_times=reference_times,
        dates=dates_for_method_selection,
        config=cfg,
        elevation=stage1_elevation,
        pressure=stage1_pressure,
        temperature=stage1_temperature,
        timezone=timezone,
        calculation_method="angles",
        asr_madhab=asr_choice,
        isha_shafaq=None,
    )
    shafaq_candidates = ["general", "ahmer", "abyad"]

    def _moon_loss_eval(idx: int, shafaq_name: str):
        return (
            int(idx),
            shafaq_name,
            _method_loss_for_fixed_core(
                params=best_params,
                reference_times=reference_times,
                dates=dates_for_method_selection,
                config=cfg,
                elevation=stage1_elevation,
                pressure=stage1_pressure,
                temperature=stage1_temperature,
                timezone=timezone,
                calculation_method="moonsighting",
                asr_madhab=asr_choice,
                isha_shafaq=shafaq_name,
            ),
        )

    with ThreadPoolExecutor(max_workers=min(3, len(shafaq_candidates))) as executor:
        moon_losses = [
            f.result()
            for f in [
                executor.submit(_moon_loss_eval, idx, shafaq_name)
                for idx, shafaq_name in enumerate(shafaq_candidates)
            ]
        ]
    moon_losses.sort(key=lambda item: int(item[0]))
    _best_idx, selected_isha_shafaq, moon_loss = min(
        moon_losses,
        key=lambda item: (float(item[2]), int(item[0])),
    )
    if flat_shift_artifact_detected:
        selected_method = "angles"
    else:
        selected_method = "moonsighting" if moon_loss < angle_loss else "angles"
    if selected_method == "angles":
        selected_isha_shafaq = None
    step_timings["final_method_retest"] = float(time.perf_counter() - step_started_at)

    step_started_at = time.perf_counter()
    reference_times_for_corrections = reference_times
    clock_offsets_json: Optional[str] = None
    clock_blocks_count = 0
    if bool(cfg.detect_clock_offsets) and clock_blocks:
        reference_times_for_corrections = _normalize_reference_times_with_clock_blocks(
            reference_times,
            clock_blocks,
        )
        clock_offsets_json = _serialize_clock_blocks(clock_blocks)
        clock_blocks_count = int(len(clock_blocks))
    step_timings["clock_offsets_normalization"] = float(
        time.perf_counter() - step_started_at
    )

    step_started_at = time.perf_counter()
    zero_offsets = {field: 0.0 for field in OFFSET_FIELDS}
    selected_asr_madhab = 0 if asr_choice == "standard" else 1
    extra_calc_kwargs = {
        "calculation_method": (
            "angle_based" if selected_method == "angles" else selected_method
        ),
        "asr_madhab": selected_asr_madhab,
    }
    if selected_isha_shafaq is not None:
        extra_calc_kwargs["isha_shafaq"] = str(selected_isha_shafaq)

    offsets = dict(zero_offsets)
    offsets_accepted = False
    stable_mae_before_offsets = float("inf")
    stable_mae_after_offsets = float("inf")
    if dates_for_core:
        stable_mae_before_offsets = _evaluate_offsets_mae(
            params=np.array(
                [
                    float(best_params[2]),
                    float(best_params[3]),
                    float(best_params[0]),
                    float(best_params[1]),
                    float(stage1_temperature),
                    float(stage1_pressure),
                ],
                dtype=float,
            ),
            dates=dates_for_core,
            reference_times=reference_times_for_corrections,
            elevation=stage1_elevation,
            timezone=timezone,
            tz_name=tz_name,
            offsets=zero_offsets,
            extra_calc_kwargs=extra_calc_kwargs,
        )
        stable_mae_after_offsets = stable_mae_before_offsets

    if bool(cfg.optimize_prayer_offsets) and len(dates_for_core) >= int(
        cfg.min_stable_dates_for_offsets
    ):
        candidate_offsets = _compute_offsets_direct(
            np.array(
                [
                    float(best_params[2]),
                    float(best_params[3]),
                    float(best_params[0]),
                    float(best_params[1]),
                    float(stage1_temperature),
                    float(stage1_pressure),
                ],
                dtype=float,
            ),
            available_dates=dates_for_core,
            reference_times=reference_times_for_corrections,
            elevation=stage1_elevation,
            timezone=timezone,
            tz_name=tz_name,
            isha_minutes=0.0,
            extra_calc_kwargs=extra_calc_kwargs,
            max_dhuhr_asr_offset_minutes=float(cfg.max_dhuhr_asr_offset_minutes),
            max_other_offset_minutes=float(cfg.max_other_prayer_offset_minutes),
        )
        offsets = {
            field: float(candidate_offsets.get(field, 0.0) or 0.0)
            for field in OFFSET_FIELDS
        }
        stable_mae_after_offsets = _evaluate_offsets_mae(
            params=np.array(
                [
                    float(best_params[2]),
                    float(best_params[3]),
                    float(best_params[0]),
                    float(best_params[1]),
                    float(stage1_temperature),
                    float(stage1_pressure),
                ],
                dtype=float,
            ),
            dates=dates_for_core,
            reference_times=reference_times_for_corrections,
            elevation=stage1_elevation,
            timezone=timezone,
            tz_name=tz_name,
            offsets=offsets,
            extra_calc_kwargs=extra_calc_kwargs,
        )
        offsets_accepted = True
    step_timings["stable_offsets_fit"] = float(time.perf_counter() - step_started_at)

    step_started_at = time.perf_counter()
    excluded_dates = sorted(set(candidate_dates) - set(dates_for_core))
    excluded_date_ranges = (
        [] if flat_shift_artifact_detected else _contiguous_date_ranges(excluded_dates)
    )
    step_timings["final_residual_eval"] = float(time.perf_counter() - step_started_at)
    step_timings["total"] = float(time.perf_counter() - started_at)

    calc_method = "angle_based" if selected_method == "angles" else str(selected_method)
    asr_int = 0 if asr_choice == "standard" else 1

    context = PipelineContext(
        lat=float(best_params[0]),
        lon=float(best_params[1]),
        fajr_angle=float(best_params[2]),
        isha_angle=float(best_params[3]),
        calculation_method=calc_method,
        isha_shafaq=selected_isha_shafaq,
        asr_madhab=asr_int,
        elevation=float(stage1_elevation),
        temp=float(stage1_temperature),
        pressure=float(stage1_pressure),
        offsets=offsets,
        offsets_accepted=bool(offsets_accepted),
        clock_offsets=clock_offsets_json,
        clock_blocks_count=int(clock_blocks_count),
        excluded_date_ranges=excluded_date_ranges,
        artifact_ignored_dates=[d.isoformat() for d in sorted(artifact_dates)],
        dates_used_for_core=dates_for_core,
        reference_times_for_corrections=reference_times_for_corrections,
        stable_mae_before_offsets=float(stable_mae_before_offsets),
        stable_mae_after_offsets=float(stable_mae_after_offsets),
    )

    diagnostics = Stage1Diagnostics(
        loss=float(moon_loss if selected_method == "moonsighting" else angle_loss),
        geographic_calibration=geographic_calibration,
        asr_madhab_detection=asr_madhab_detection,
        method_comparison={
            "angles_loss": float(angle_loss),
            "moonsighting_loss": float(moon_loss),
        },
        step_timings=step_timings,
    )

    return context, diagnostics
