"""
optimizer_worker.py — Pure-computation worker for batch-country parallelism.

This module is intentionally FREE of tkinter so it can be safely imported by
sub-processes spawned by ProcessPoolExecutor on Windows (spawn start method).

The public entry point is _run_city_task, which BatchOptimizationDashboard
submits to the process pool.  Separate processes each have their own Python
interpreter, their own GIL, and therefore impose ZERO GIL pressure on the
Tkinter main thread.
"""
from __future__ import annotations

from src.app.infrastructure.optimizer.shared import (
    OFFSET_FIELDS,
    _filter_cities_by_conservative_rules,
)
from src.app.infrastructure.optimizer.multistage.pipeline import (
    run_multistage_optimization,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_stage1_defaults(loc_dict: dict) -> dict:
    """Reset all Stage-1-tunable fields to neutral defaults.

    Mirrors the identical function in batch_gui.py so that both the single-
    city GUI flow and the batch flow start from the same clean slate,
    independent of whatever is currently saved in loc.csv.
    """
    loc_dict["optimized_lat"] = None
    loc_dict["optimized_lon"] = None
    loc_dict["fajr_angle"] = 17.0
    loc_dict["isha_angle"] = 18.0
    loc_dict["elevation"] = 0.0
    loc_dict["pressure"] = 1010.0
    loc_dict["temp"] = 10.0
    loc_dict["calculation_method"] = "angle_based"
    loc_dict["isha_harag"] = 0
    loc_dict["high_lat_method"] = 0
    loc_dict["high_lat_start_date"] = None
    loc_dict["high_lat_end_date"] = None
    loc_dict["custom_fajr_angle"] = None
    loc_dict["custom_isha_angle"] = None
    loc_dict["high_lat_fallback_method"] = None
    for fld in OFFSET_FIELDS:
        loc_dict[fld] = None
    loc_dict["residual_corrections"] = ""
    loc_dict["clock_offsets"] = ""
    return loc_dict


# ---------------------------------------------------------------------------
# Single-city worker (for optimize_parameters_for_city)
# ---------------------------------------------------------------------------

def _run_single_city_optimization(
    optimization_location_data: dict,
    all_reference_times: dict,
    available_dates: list,
    tz_name,
):
    """Run the multistage optimizer for one city.

    All arguments are plain Python dicts/lists so they pickle cleanly.
    Returns the opt_result object.
    """
    return run_multistage_optimization(
        location_data=optimization_location_data,
        reference_times=all_reference_times,
        available_dates=available_dates,
        tz_name=tz_name,
    )


# ---------------------------------------------------------------------------
# Batch worker entry point
# ---------------------------------------------------------------------------

def _run_city_task(
    country_code: str,
    primary_city: dict,
    all_ref_cities: list,
):
    """Optimize a single representative city.

    Designed to run inside a ProcessPoolExecutor worker (separate process,
    separate GIL).  Uses a clean-slate starting point identical to the
    single-city optimizer so results are start-point independent.

    Returns (country_code, city_name, payload_dict).
    """
    primary_loc_raw = primary_city["loc"]
    primary_ref_times = primary_city["reference_times"]
    primary_dates = primary_city["available_dates"]
    primary_lat = float(primary_loc_raw["latitude"])
    primary_lon = float(primary_loc_raw["longitude"])
    primary_tz_name = primary_city.get("tz_name")
    selected_timezone = primary_loc_raw.get("timezone", 0)

    # Reset to defaults so the result is independent of prior saved state.
    primary_loc = _reset_stage1_defaults(dict(primary_loc_raw))

    auxiliary_cities = []
    for other_city in all_ref_cities:
        if other_city["name"] == primary_city["name"]:
            continue
        loc_d = other_city["loc"]
        aux_ref = other_city["reference_times"]
        aux_dates = other_city["available_dates"]
        if not aux_ref or not aux_dates:
            continue
        aux_lat = float(loc_d.get("latitude", 0))
        aux_lon = float(loc_d.get("longitude", 0))
        aux_tz_name = other_city.get("tz_name")
        aux_tz = loc_d.get("timezone", selected_timezone) or selected_timezone
        auxiliary_cities.append(
            {
                "name": loc_d["name"],
                "latitude": aux_lat,
                "longitude": aux_lon,
                "elevation": float(loc_d.get("elevation", 0) or 0),
                "timezone": aux_tz,
                "tz_name": aux_tz_name,
                "reference_times": aux_ref,
                "available_dates": aux_dates,
                "temp": float(loc_d.get("temp", 10.0) or 10.0),
                "pressure": float(loc_d.get("pressure", 1010.0) or 1010.0),
                "isha_minutes": float(loc_d.get("isha_minutes", 0) or 0),
            }
        )

    conservative_aux = _filter_cities_by_conservative_rules(
        primary_lat, primary_lon, auxiliary_cities
    )

    opt_result = run_multistage_optimization(
        location_data=primary_loc,
        reference_times=primary_ref_times,
        available_dates=primary_dates,
        tz_name=primary_tz_name,
    )

    return country_code, primary_city["name"], {
        "opt_result": opt_result,
        "n_dates": len(primary_dates),
        "n_aux": len(conservative_aux),
        "has_residual": bool(opt_result.residual_corrections),
        "duration_seconds": float(opt_result.duration_seconds or 0.0),
        "loc_raw": primary_loc_raw,
    }
