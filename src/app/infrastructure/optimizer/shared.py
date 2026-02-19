# ruff: noqa: BLE001, F541, ARG002
# pylint: disable=broad-exception-caught
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, cast

import geopy.distance

from src.app.domain.constants import PRAYER_NAMES as DOMAIN_PRAYER_NAMES
from src.app.domain.time_utils import parse_time_to_seconds, time_diff_seconds
from src.app.infrastructure.prayer_calculator import calculate_prayer_times
from src.app.infrastructure.reference_parser import load_reference_file

OFFSET_FIELDS = [
    "fajr_offset",
    "shurooq_offset",
    "dhuhr_offset",
    "asr_offset",
    "maghrib_offset",
    "isha_offset",
]
PRAYER_NAMES = DOMAIN_PRAYER_NAMES

KNOWN_FAJR_ANGLES = [
    12.0,
    13.0,
    14.0,
    15.0,
    16.0,
    17.0,
    17.7,
    18.0,
    18.2,
    18.5,
    19.0,
    19.5,
    20.0,
]
KNOWN_ISHA_ANGLES = [
    12.0,
    13.0,
    14.0,
    15.0,
    16.0,
    17.0,
    17.5,
    18.0,
    18.2,
    18.5,
    19.0,
    19.5,
    20.0,
]
ANGLE_SNAP_TOLERANCE = 0.3
ANGLE_SNAP_SCORE_PENALTY = 0.15
MAE_OBJECTIVE_RMSE_WEIGHT = 0.15
MAE_OBJECTIVE_MAX_ERROR_WEIGHT = 0.05
RMSE_GUARDRAIL_TOLERANCE = 0.20
TAIL_GUARDRAIL_TOLERANCE = 0.20
CONSERVATIVE_LAT_DIFF_MAX_DEG = 1.5
CONSERVATIVE_DISTANCE_MAX_KM = 500.0


@dataclass
class OptimizationResult:
    """Holds the complete result of a parameter optimization run."""

    fajr_angle: float
    isha_angle: float
    latitude: float
    longitude: float
    temp: float
    pressure: float
    offsets: Dict[str, float]  # e.g. {'fajr_offset': -1.0, ...}
    rmse_total: float
    mae_total: float
    per_prayer_rmse: Dict[str, float]
    per_prayer_mae: Dict[str, float]
    per_prayer_max_error: Dict[str, float]
    per_prayer_signed_mean: Dict[str, float]
    duration_seconds: float
    convergence_info: str = ""
    original_lat: float = 0.0
    original_lon: float = 0.0
    distance_moved_km: float = 0.0
    n_function_evals: int = 0
    auxiliary_cities_used: int = 0
    # Adaptive method detection results
    asr_madhab: Optional[int] = None  # 0=Standard, 1=Hanafi; None=unchanged
    calculation_method: Optional[str] = None  # 'angle_based' or 'moonsighting'
    isha_shafaq: Optional[str] = None  # 'general', 'ahmer', 'abyad'
    isha_harag: Optional[int] = None  # 0=off, 1..3 alternate handling
    high_lat_method: Optional[int] = None  # 0=angle, 1=one-seventh, 2=midnight
    high_lat_start_date: Optional[Any] = None  # datetime.date or None
    high_lat_end_date: Optional[Any] = None  # datetime.date or None
    custom_fajr_angle: Optional[float] = None
    custom_isha_angle: Optional[float] = None
    high_lat_fallback_method: Optional[int] = None
    adaptive_notes: str = ""  # Human-readable notes about what was detected
    # Residual correction model (Phase 5)
    residual_corrections: Optional[str] = None  # JSON-encoded PrayerResidualModel
    # Clock-shift blocks (DST in reference data)
    # JSON: [{"start": "MM-DD", "end": "MM-DD", "offset": 60}, ...]
    clock_offsets: Optional[str] = None
    # Duration by stage in seconds
    phase_timings: Optional[Dict[str, float]] = None
    elevation: Optional[float] = None


def km_to_degrees(km, latitude):
    """Approximate conversion of km to degrees latitude and longitude."""
    cos_lat = math.cos(math.radians(latitude))
    if abs(cos_lat) < 1e-9:
        cos_lat = 1e-9
    delta_lat = km / 111.0
    delta_lon = km / (111.0 * cos_lat)
    return delta_lat, delta_lon


def _parse_time_to_seconds(time_str):
    return parse_time_to_seconds(time_str)


def _time_diff_seconds(calc_str, ref_str):
    return time_diff_seconds(calc_str, ref_str)


def _calculate_prayer_times_dynamic(kwargs: Dict[str, Any]):
    return calculate_prayer_times(**cast(Any, kwargs))


def _load_residual_model_from_json(residual_json):
    if not residual_json or not str(residual_json).strip():
        return None
    try:
        from src.app.infrastructure.residual_model import PrayerResidualModel

        model = PrayerResidualModel.from_json(str(residual_json))
        if model.fitted:
            return model
    except (ValueError, TypeError, KeyError, RuntimeError, OSError):
        pass
    return None


def _matches_conservative_rules(source_lat, source_lon, target_lat, target_lon):
    lat_diff = abs(float(source_lat) - float(target_lat))
    if lat_diff > CONSERVATIVE_LAT_DIFF_MAX_DEG:
        return False
    try:
        dist_km = geopy.distance.geodesic(
            (float(source_lat), float(source_lon)),
            (float(target_lat), float(target_lon)),
        ).km
    except (ValueError, TypeError, KeyError, RuntimeError, OSError):
        return False
    return dist_km <= CONSERVATIVE_DISTANCE_MAX_KM


def _filter_cities_by_conservative_rules(primary_lat, primary_lon, candidate_cities):
    filtered = []
    for city in candidate_cities:
        if _matches_conservative_rules(
            primary_lat,
            primary_lon,
            city.get("latitude", 0.0),
            city.get("longitude", 0.0),
        ):
            filtered.append(city)
    return filtered


def _find_closest_city_by_distance(target_lat, target_lon, candidate_cities):
    best_city = None
    best_dist = float("inf")
    for city in candidate_cities:
        try:
            dist_km = geopy.distance.geodesic(
                (float(target_lat), float(target_lon)),
                (float(city.get("latitude", 0.0)), float(city.get("longitude", 0.0))),
            ).km
        except (ValueError, TypeError, KeyError, RuntimeError, OSError):
            continue
        if dist_km < best_dist:
            best_dist = dist_km
            best_city = city
    return best_city


def _load_reference_file(filepath):
    try:
        return load_reference_file(Path(filepath))
    except (ValueError, TypeError, KeyError, RuntimeError, OSError):
        return {}, []
