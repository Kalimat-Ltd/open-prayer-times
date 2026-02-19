"""Shared constants and helpers for multi-stage optimization."""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import pytz


@dataclass(frozen=True)
class Stage1Config:
    robust_loss_method: str = "huber"
    huber_delta: float = 30.0
    tukey_c: float = 120.0
    clean_day_threshold_minutes: float = 4.8
    clean_day_lookahead_days: int = 5
    min_clean_core_days: int = 120
    enable_final_mae_angle_polish: bool = True
    final_mae_angle_window_deg: float = 0.4
    final_mae_angle_step_deg: float = 0.1
    max_refinement_iterations: int = 3
    lambda_coord_shift: float = 0.01
    enable_geographic_calibration: bool = True
    geo_search_radius_km: float = 10.0
    enable_asr_madhab_detection: bool = True
    asr_high_error_threshold_minutes: float = 10.0
    detect_clock_offsets: bool = True
    optimize_prayer_offsets: bool = True
    min_stable_dates_for_offsets: int = 30
    max_dhuhr_asr_offset_minutes: float = 10.0
    max_other_prayer_offset_minutes: float = 20.0
    geo_search_grid_points: int = 33
    env_search_grid_points: int = 25
    env_elevation_min_m: float = 0
    env_elevation_max_m: float = 5100.0
    env_elevation_window_m: float = 5100.0
    env_temperature_min_c: float = -60.0
    env_temperature_max_c: float = 60.0
    env_pressure_min_mbar: float = 840.0
    env_pressure_max_mbar: float = 1100.0


@dataclass(frozen=True)
class Stage2Config:
    candidate_methods: tuple[int, ...] = (0, 1, 2, 3)
    candidate_harag_values: tuple[int, ...] = (0, 1, 2, 3)
    min_problematic_days: int = 20
    require_mae_improvement: bool = True
    optimize_custom_angles: bool = True
    custom_angle_min_deg: float = 8.0
    custom_angle_max_deg: float = 22.0
    custom_angle_grid_points: int = 17
    custom_angle_improvement_threshold: float = 0.0


@dataclass(frozen=True)
class Stage3Config:
    fit_residual_corrections: bool = True
    max_harmonics: int = 6
    min_unstable_dates_for_residuals: int = 35
    min_residual_mae_gain: float = 0.02
    min_unstable_per_prayer_gain: float = 0.0
    max_unstable_per_prayer_worsen: float = 0.0


def km_to_degrees(km: float, latitude: float) -> tuple[float, float]:
    cos_lat = math.cos(math.radians(float(latitude)))
    if abs(cos_lat) < 1e-9:
        cos_lat = 1e-9
    delta_lat = float(km) / 111.0
    delta_lon = float(km) / (111.0 * cos_lat)
    return delta_lat, delta_lon


def parse_time_to_minutes(time_str: str) -> float:
    parts = (time_str or "").strip().split(":")
    if len(parts) < 2:
        raise ValueError(f"Invalid time string: {time_str}")
    hh = int(parts[0])
    mm = int(parts[1])
    ss = int(parts[2]) if len(parts) >= 3 else 0
    return float(hh * 60 + mm + ss / 60.0)


def local_minutes_to_utc_minutes(minutes_local: float, timezone_hours: float) -> float:
    return float(minutes_local - float(timezone_hours) * 60.0) % 1440.0


def local_time_str_to_utc_minutes(
    time_str: str,
    date_obj: datetime.date,
    timezone_hours: float,
    tz_name: Optional[str] = None,
) -> float:
    local_minutes = parse_time_to_minutes(time_str)
    if tz_name:
        try:
            tz = pytz.timezone(tz_name)
            hh = int(local_minutes // 60)
            mm_float = local_minutes - hh * 60
            mm = int(mm_float)
            ss = int(round((mm_float - mm) * 60.0))
            if ss >= 60:
                ss = 59
            local_dt = datetime.datetime(
                date_obj.year,
                date_obj.month,
                date_obj.day,
                hh,
                mm,
                ss,
            )
            localized = tz.localize(local_dt)
            utc_dt = localized.astimezone(datetime.timezone.utc)
            return float(utc_dt.hour * 60 + utc_dt.minute + utc_dt.second / 60.0)
        except (pytz.UnknownTimeZoneError, ValueError, TypeError, OverflowError):
            pass
    return local_minutes_to_utc_minutes(local_minutes, timezone_hours)


def circular_minutes_diff(model_utc_min: float, reference_utc_min: float) -> float:
    diff = float(model_utc_min - reference_utc_min)
    while diff <= -720.0:
        diff += 1440.0
    while diff > 720.0:
        diff -= 1440.0
    return diff


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return float("inf")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    idx = (len(ordered) - 1) * max(0.0, min(1.0, q))
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return ordered[lo]
    w = idx - lo
    return ordered[lo] * (1.0 - w) + ordered[hi] * w


def mean_abs(values: Iterable[float]) -> float:
    vals = [abs(float(v)) for v in values]
    if not vals:
        return float("inf")
    return float(sum(vals) / len(vals))


def resolve_timezone_offset_hours(
    timezone_value,
    tz_name: str | None = None,
    sample_date: datetime.date | None = None,
) -> float:
    """Resolve timezone input to numeric UTC offset hours.

    Supports numeric offsets and IANA timezone names.
    """
    if sample_date is None:
        sample_date = datetime.date.today()

    try:
        return float(timezone_value)
    except (TypeError, ValueError):
        pass

    name = (tz_name or str(timezone_value or "")).strip()
    if not name:
        return 0.0

    try:
        tz = pytz.timezone(name)
        noon = datetime.datetime(
            sample_date.year,
            sample_date.month,
            sample_date.day,
            12,
            0,
            0,
        )
        offset = tz.utcoffset(noon)
        if offset is None:
            return 0.0
        return float(offset.total_seconds() / 3600.0)
    except (pytz.UnknownTimeZoneError, ValueError, TypeError, OverflowError):
        return 0.0
