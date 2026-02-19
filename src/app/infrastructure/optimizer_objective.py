import math
from typing import Dict, List, Optional

from src.app.domain.constants import PRAYER_NAMES
from src.app.domain.models import PrayerCalculationRequest
from src.app.domain.prayer_times import calculate_prayer_times
from src.app.domain.time_utils import time_diff_seconds


def compute_rmse_objective(
    params_vector,
    available_dates: List,
    reference_times: Dict,
    elevation: float,
    timezone: float,
    tz_name: Optional[str],
    isha_minutes: float,
    prayer_weights: Dict[str, float],
    fixed_offsets: Optional[Dict[str, float]],
    extra_calc_kwargs: Optional[Dict],
) -> float:
    fajr_angle, isha_angle, lat, lon, temp, pressure = params_vector

    total_weighted_sq = 0.0
    total_weight = 0.0

    for date_obj in available_dates:
        ref = reference_times.get(date_obj)
        if not ref:
            continue
        try:
            request = PrayerCalculationRequest(
                lat_dec=lat,
                lon_dec=lon,
                elevation=elevation,
                pressure=pressure,
                temp=temp,
                tz_name=tz_name or "",
                tz_offset_hours=timezone,
                fajr_angle=fajr_angle,
                isha_angle=isha_angle,
                isha_minutes=isha_minutes,
                target_date=date_obj,
                rounding="off",
                **(extra_calc_kwargs or {}),
                **(fixed_offsets or {}),
            )
            result = calculate_prayer_times(request)
            for prayer in PRAYER_NAMES:
                calc_time = result.times.get(prayer, "N/A")
                ref_time = ref.get(prayer, "N/A")
                diff_s = time_diff_seconds(calc_time, ref_time)
                if diff_s is not None:
                    diff_min = diff_s / 60.0
                    weight = prayer_weights.get(prayer, 1.0)
                    total_weighted_sq += weight * (diff_min**2)
                    total_weight += weight
        except (ValueError, TypeError, KeyError, RuntimeError, OSError):
            continue

    if total_weight == 0:
        return float("inf")
    return math.sqrt(total_weighted_sq / total_weight)
