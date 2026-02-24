# ruff: noqa: BLE001, F541, ARG002
# pylint: disable=broad-exception-caught
import tkinter as tk
from tkinter import ttk, messagebox, Toplevel
import os
import datetime
import math
import threading
import queue
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
import numpy as np
from typing import List, Literal, Optional

from src.app.infrastructure.optimizer.objective import (
    _compute_detailed_errors,
    _is_mae_priority_improvement,
)
from src.app.infrastructure.optimizer.shared import (
    OFFSET_FIELDS,
    PRAYER_NAMES,
    _filter_cities_by_conservative_rules,
    _find_closest_city_by_distance,
    _load_residual_model_from_json,
    _matches_conservative_rules,
)
from src.app.infrastructure.optimizer.multistage.pipeline import (
    run_multistage_optimization,
)
from src.app.infrastructure.reference_repository import load_reference_times
from src.app.infrastructure.optimizer.optimizer_worker import (
    _run_city_task,
    _reset_stage1_defaults,
    _run_single_city_optimization,
)


# _reset_stage1_defaults and _run_city_task live in optimizer_worker.py so
# that ProcessPoolExecutor subprocesses can import them without pulling in
# tkinter (which would happen if they imported batch_gui directly).
# They are re-imported above from optimizer_worker.


def _is_stage1_output(opt_result):
    convergence_info = str(getattr(opt_result, "convergence_info", "") or "").lower()
    if not convergence_info.startswith("multistage-stage1"):
        return False
    # Full multistage outputs also begin with "multistage-stage1 ..." but include
    # explicit Stage 2/3 markers in the convergence string.
    if (
        "stage2=" in convergence_info
        or "stage3_offsets=" in convergence_info
        or "stage3_residuals=" in convergence_info
    ):
        return False
    return True


_NON_PERSISTENT_OPT_FIELDS = {
    "offsets",
    "rmse_total",
    "mae_total",
    "per_prayer_rmse",
    "per_prayer_mae",
    "per_prayer_max_error",
    "per_prayer_signed_mean",
    "duration_seconds",
    "convergence_info",
    "original_lat",
    "original_lon",
    "distance_moved_km",
    "n_function_evals",
    "auxiliary_cities_used",
    "adaptive_notes",
    "phase_timings",
    "latitude",
    "longitude",
}

_NULLABLE_FIELD_DEFAULTS = {
    "residual_corrections": "",
    "clock_offsets": "",
    "high_lat_start_date": None,
    "high_lat_end_date": None,
    "custom_fajr_angle": None,
    "custom_isha_angle": None,
    "high_lat_fallback_method": None,
}


def _extract_result_fields(opt_result):
    try:
        return dict(vars(opt_result))
    except (TypeError, ValueError):
        return {}


def _apply_optimization_result_to_location(
    loc_dict,
    opt_result,
    *,
    stage1_only=False,
    apply_coordinates=False,
):
    if stage1_only:
        _reset_stage1_defaults(loc_dict)

    result_fields = _extract_result_fields(opt_result)

    for key, value in result_fields.items():
        if key in _NON_PERSISTENT_OPT_FIELDS:
            continue
        if key not in loc_dict:
            continue
        if value is None:
            if key in _NULLABLE_FIELD_DEFAULTS:
                loc_dict[key] = _NULLABLE_FIELD_DEFAULTS[key]
            continue
        loc_dict[key] = value

    if apply_coordinates:
        latitude = result_fields.get("latitude")
        longitude = result_fields.get("longitude")
        if latitude is not None:
            loc_dict["optimized_lat"] = latitude
        if longitude is not None:
            loc_dict["optimized_lon"] = longitude

    if not stage1_only:
        offsets = result_fields.get("offsets")
        if isinstance(offsets, dict):
            for offset_key, offset_value in offsets.items():
                if offset_value is None:
                    continue
                loc_dict[offset_key] = offset_value

    loc_dict["is_optimized"] = 1
    return loc_dict


# =============================================================================
# GUI Dialog
# =============================================================================
def ask_optimization_result_dialog(parent, title, message):
    """
    Creates a custom modal dialog with specific action buttons.

    Returns: 'city', 'country', 'ignore', or None if closed unexpectedly.
    """
    dialog = Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)

    result = None

    def set_result_and_close(action):
        nonlocal result
        result = action
        dialog.destroy()

    main_frame = ttk.Frame(dialog, padding="10 10 10 10")
    main_frame.pack(expand=True, fill=tk.BOTH)

    message_label = ttk.Label(main_frame, text=message, justify=tk.LEFT, wraplength=500)
    message_label.pack(pady=(0, 15))

    button_frame = ttk.Frame(main_frame)
    button_frame.pack(fill=tk.X, pady=(5, 0))

    city_button = ttk.Button(
        button_frame, text="Apply to City", command=lambda: set_result_and_close("city")
    )
    city_button.pack(side=tk.LEFT, padx=5, expand=True)
    city_button.focus_set()

    country_button = ttk.Button(
        button_frame,
        text="Apply to Country",
        command=lambda: set_result_and_close("country"),
    )
    country_button.pack(side=tk.LEFT, padx=5, expand=True)

    ignore_button = ttk.Button(
        button_frame, text="Ignore", command=lambda: set_result_and_close("ignore")
    )
    ignore_button.pack(side=tk.LEFT, padx=5, expand=True)

    dialog.update_idletasks()
    parent_x = parent.winfo_rootx()
    parent_y = parent.winfo_rooty()
    parent_width = parent.winfo_width()
    parent_height = parent.winfo_height()
    dialog_width = dialog.winfo_width()
    dialog_height = dialog.winfo_height()

    position_x = parent_x + (parent_width // 2) - (dialog_width // 2)
    position_y = parent_y + (parent_height // 2) - (dialog_height // 2)
    dialog.geometry(f"+{position_x}+{position_y}")

    dialog.protocol("WM_DELETE_WINDOW", lambda: set_result_and_close("ignore"))
    parent.wait_window(dialog)

    return result


# =============================================================================
# Clustering helpers
# =============================================================================


def _cluster_reference_cities(
    cities: list,
    radius_km: float = 150.0,
) -> list:
    """
    Group reference cities into geographic clusters using a greedy radius sweep.

    Cities are sorted by number of reference dates (descending) so the city
    with the most data becomes the cluster representative.  Every unassigned
    city within *radius_km* of the current representative is merged into the
    same cluster.

    Returns a list of (representative_city_dict, [member_city_dicts]) tuples.
    The representative is included in the member list.
    """
    import geopy.distance  # already a project dependency

    # Sort by reference-date count descending so data-rich cities lead
    sorted_cities = sorted(
        cities,
        key=lambda c: len(c.get("available_dates") or []),
        reverse=True,
    )

    assigned: set = set()  # indices into sorted_cities
    clusters: list = []

    for i, rep in enumerate(sorted_cities):
        if i in assigned:
            continue
        assigned.add(i)
        members = [rep]
        rep_lat = float(rep["loc"].get("latitude", 0))
        rep_lon = float(rep["loc"].get("longitude", 0))
        for j, other in enumerate(sorted_cities):
            if j in assigned:
                continue
            other_lat = float(other["loc"].get("latitude", 0))
            other_lon = float(other["loc"].get("longitude", 0))
            try:
                dist_km = geopy.distance.geodesic(
                    (rep_lat, rep_lon), (other_lat, other_lon)
                ).km
            except (ValueError, TypeError, KeyError, RuntimeError, OSError):
                continue
            if dist_km <= radius_km:
                assigned.add(j)
                members.append(other)
        clusters.append((rep, members))

    return clusters


def _select_by_farthest_point(
    representatives: list,
    max_n: int,
) -> list:
    """
    Reduce *representatives* to *max_n* cities that maximise geographic spread
    using a greedy farthest-point (k-centres) algorithm.

    Each entry in *representatives* must be a city dict with a nested 'loc'
    dict that contains 'latitude' and 'longitude'.
    """
    import geopy.distance

    if max_n <= 0 or max_n >= len(representatives):
        return list(representatives)

    def _lat_lon(city):
        loc = city.get("loc", {})
        return float(loc.get("latitude", 0)), float(loc.get("longitude", 0))

    def _dist(a, b):
        try:
            return geopy.distance.geodesic(_lat_lon(a), _lat_lon(b)).km
        except (ValueError, TypeError, KeyError, RuntimeError, OSError):
            return 0.0

    # Start with the city that has the most reference dates
    selected = [max(representatives, key=lambda c: len(c.get("available_dates") or []))]
    remaining = [c for c in representatives if c is not selected[0]]

    while len(selected) < max_n and remaining:
        # Pick the city whose minimum distance to any selected city is greatest
        best = max(remaining, key=lambda c: min(_dist(c, s) for s in selected))
        selected.append(best)
        remaining.remove(best)

    return selected


# =============================================================================
# Batch Optimization Dashboard
# =============================================================================


def _prepare_country_cities(
    country_code,
    ref_files_info,
    tf,
    use_dst,
    result_queue,
    cluster_radius_km=150.0,
    max_cities: Optional[int] = None,
):
    """
    Load reference data, apply clustering and optional max-cities cap.

    Returns (all_ref_cities, representatives) or None on error.
    Posts 'error' or clustering 'progress' messages to result_queue.
    """
    if not ref_files_info:
        result_queue.put((country_code, "error", "No reference cities found"))
        return None

    all_ref_cities = []
    for ref_path, loc_d in ref_files_info:
        _raw_ry = loc_d.get("reference_year") or ""
        _batch_ref_year: int | None = (
            int(str(_raw_ry).strip()) if str(_raw_ry).strip().isdigit() else None
        )
        ref_times, ref_dates = load_reference_times(ref_path, year=_batch_ref_year)
        if not ref_times:
            continue
        city_tz_name = None
        if tf and use_dst:
            try:
                city_tz_name = tf.timezone_at(
                    lng=float(loc_d.get("longitude", 0)),
                    lat=float(loc_d.get("latitude", 0)),
                )
            except (ValueError, TypeError, KeyError, RuntimeError, OSError):
                pass
        all_ref_cities.append(
            {
                "name": loc_d["name"],
                "reference_times": ref_times,
                "available_dates": ref_dates,
                "loc": loc_d,
                "tz_name": city_tz_name,
            }
        )

    if not all_ref_cities:
        result_queue.put((country_code, "error", "No valid reference data"))
        return None

    clusters = _cluster_reference_cities(all_ref_cities, radius_km=cluster_radius_km)
    representatives = [rep for rep, _ in clusters]

    if max_cities and len(representatives) > max_cities:
        representatives = _select_by_farthest_point(representatives, max_cities)

    total_ref = len(all_ref_cities)
    total_opt = len(representatives)
    n_clustered = total_ref - total_opt
    if n_clustered > 0:
        result_queue.put(
            (
                country_code,
                "progress",
                f"Clustering: {total_opt}/{total_ref} cities selected "
                f"({n_clustered} merged into nearest representative).",
            )
        )

    return all_ref_cities, representatives


# _run_city_task is defined in optimizer_worker.py (imported above).


def _aggregate_and_post_country_done(
    country_code,
    city_results,
    all_ref_cities,
    rounding,
    result_queue,
):
    """
    Compute baseline-vs-after aggregate metrics for a completed country and
    post a 'done' message to result_queue.
    """

    def _mean_or_inf(vals):
        return float(np.mean(vals)) if vals else float("inf")

    baseline_mae_vals = []
    baseline_rmse_vals = []
    after_mae_vals = []
    after_rmse_vals = []
    baseline_per_prayer_mae = {p: [] for p in PRAYER_NAMES}
    baseline_per_prayer_rmse = {p: [] for p in PRAYER_NAMES}
    baseline_per_prayer_max = {p: [] for p in PRAYER_NAMES}
    after_per_prayer_mae = {p: [] for p in PRAYER_NAMES}
    after_per_prayer_rmse = {p: [] for p in PRAYER_NAMES}
    after_per_prayer_max = {p: [] for p in PRAYER_NAMES}

    ref_city_by_name = {c["name"]: c for c in all_ref_cities}
    for city_name, city_payload in city_results.items():
        ref_city = ref_city_by_name.get(city_name)
        if not ref_city:
            continue

        loc = city_payload.get("loc_raw") or ref_city["loc"]
        ref_times = ref_city["reference_times"]
        dates = ref_city["available_dates"]
        city_tz_name = ref_city.get("tz_name")
        timezone_val = loc.get("timezone", 0)
        elevation_val = float(loc.get("elevation", 0) or 0)
        isha_minutes_val = float(loc.get("isha_minutes", 0) or 0)

        baseline_params = np.array(
            [
                float(loc.get("fajr_angle", 18.0) or 18.0),
                float(loc.get("isha_angle", 17.0) or 17.0),
                float(loc.get("optimized_lat") or loc.get("latitude") or 0.0),
                float(loc.get("optimized_lon") or loc.get("longitude") or 0.0),
                float(loc.get("temp", 10.0) or 10.0),
                float(loc.get("pressure", 1010.0) or 1010.0),
            ],
            dtype=float,
        )
        baseline_offsets = {f: float(loc.get(f, 0.0) or 0.0) for f in OFFSET_FIELDS}
        baseline_residual_model = _load_residual_model_from_json(
            loc.get("residual_corrections", "")
        )
        (
            baseline_city_rmse,
            baseline_city_mae,
            baseline_city_per_prayer_rmse,
            baseline_city_per_prayer_mae,
            baseline_city_per_prayer_max,
            _,
        ) = _compute_detailed_errors(
            baseline_params,
            available_dates=dates,
            reference_times=ref_times,
            elevation=elevation_val,
            timezone=timezone_val,
            tz_name=city_tz_name,
            isha_minutes=isha_minutes_val,
            offsets=baseline_offsets,
            residual_model=baseline_residual_model,
            settings_source=loc,
            clock_offsets_json=loc.get("clock_offsets", "") or "",
            rounding=rounding,
        )

        opt = city_payload["opt_result"]
        after_params = np.array(
            [
                float(opt.fajr_angle),
                float(opt.isha_angle),
                float(opt.latitude),
                float(opt.longitude),
                float(opt.temp),
                float(opt.pressure),
            ],
            dtype=float,
        )
        after_offsets = dict(opt.offsets) if opt.offsets else {}
        after_residual_model = _load_residual_model_from_json(opt.residual_corrections)
        (
            after_city_rmse,
            after_city_mae,
            after_city_per_prayer_rmse,
            after_city_per_prayer_mae,
            after_city_per_prayer_max,
            _,
        ) = _compute_detailed_errors(
            after_params,
            available_dates=dates,
            reference_times=ref_times,
            elevation=elevation_val,
            timezone=timezone_val,
            tz_name=city_tz_name,
            isha_minutes=isha_minutes_val,
            offsets=after_offsets,
            residual_model=after_residual_model,
            settings_source=[loc, opt],
            clock_offsets_json=opt.clock_offsets or "",
            rounding=rounding,
        )

        if math.isfinite(baseline_city_mae):
            baseline_mae_vals.append(float(baseline_city_mae))
        if math.isfinite(baseline_city_rmse):
            baseline_rmse_vals.append(float(baseline_city_rmse))
        if math.isfinite(after_city_mae):
            after_mae_vals.append(float(after_city_mae))
        if math.isfinite(after_city_rmse):
            after_rmse_vals.append(float(after_city_rmse))

        for prayer in PRAYER_NAMES:
            b_mae = float(baseline_city_per_prayer_mae.get(prayer, float("inf")))
            b_rmse = float(baseline_city_per_prayer_rmse.get(prayer, float("inf")))
            b_max = float(baseline_city_per_prayer_max.get(prayer, float("inf")))
            a_mae = float(after_city_per_prayer_mae.get(prayer, float("inf")))
            a_rmse = float(after_city_per_prayer_rmse.get(prayer, float("inf")))
            a_max = float(after_city_per_prayer_max.get(prayer, float("inf")))
            if math.isfinite(b_mae):
                baseline_per_prayer_mae[prayer].append(b_mae)
            if math.isfinite(b_rmse):
                baseline_per_prayer_rmse[prayer].append(b_rmse)
            if math.isfinite(b_max):
                baseline_per_prayer_max[prayer].append(b_max)
            if math.isfinite(a_mae):
                after_per_prayer_mae[prayer].append(a_mae)
            if math.isfinite(a_rmse):
                after_per_prayer_rmse[prayer].append(a_rmse)
            if math.isfinite(a_max):
                after_per_prayer_max[prayer].append(a_max)

    baseline_mae = _mean_or_inf(baseline_mae_vals)
    baseline_rmse = _mean_or_inf(baseline_rmse_vals)
    after_mae = _mean_or_inf(after_mae_vals)
    after_rmse = _mean_or_inf(after_rmse_vals)

    improved = _is_mae_priority_improvement(
        baseline_mae=baseline_mae,
        candidate_mae=after_mae,
        baseline_rmse=baseline_rmse,
        candidate_rmse=after_rmse,
        baseline_per_prayer_max={
            p: _mean_or_inf(v) for p, v in baseline_per_prayer_max.items()
        },
        candidate_per_prayer_max={
            p: _mean_or_inf(v) for p, v in after_per_prayer_max.items()
        },
    )
    improvement_pct = (
        ((baseline_mae - after_mae) / baseline_mae) * 100
        if baseline_mae > 0 and baseline_mae != float("inf")
        else 0.0
    )
    total_duration = sum(
        float(p["opt_result"].duration_seconds or 0.0) for p in city_results.values()
    )

    result_queue.put(
        (
            country_code,
            "done",
            {
                "city_results": city_results,
                "baseline_rmse": baseline_rmse,
                "baseline_mae": baseline_mae,
                "baseline_per_prayer_rmse": {
                    p: _mean_or_inf(v) for p, v in baseline_per_prayer_rmse.items()
                },
                "baseline_per_prayer_mae": {
                    p: _mean_or_inf(v) for p, v in baseline_per_prayer_mae.items()
                },
                "after_rmse": after_rmse,
                "after_mae": after_mae,
                "after_per_prayer_rmse": {
                    p: _mean_or_inf(v) for p, v in after_per_prayer_rmse.items()
                },
                "after_per_prayer_mae": {
                    p: _mean_or_inf(v) for p, v in after_per_prayer_mae.items()
                },
                "improved": improved,
                "improvement_pct": improvement_pct,
                "n_reference_cities": len(city_results),
                "duration_seconds": total_duration,
            },
        )
    )


# Keep the old entry-point name for backward compatibility; it now simply
# delegates to the decomposed helpers (used only by legacy paths / tests).
def _run_country_optimization(
    country_code,
    ref_files_info,
    tf,
    use_dst,
    result_queue,
    stop_event,
    max_cpu_workers=None,
    rounding="nearest",
    cluster_radius_km=150.0,
    max_cities: Optional[int] = None,
):
    """Legacy single-country wrapper.  New code should use the global pool."""
    try:
        prep = _prepare_country_cities(
            country_code,
            ref_files_info,
            tf,
            use_dst,
            result_queue,
            cluster_radius_km=cluster_radius_km,
            max_cities=max_cities,
        )
        if prep is None:
            return
        all_ref_cities, representatives = prep
        total_opt = len(representatives)
        city_results = {}
        completed = [0]
        for city in representatives:
            if stop_event.is_set():
                result_queue.put((country_code, "error", "Cancelled"))
                return
            _, city_name, payload = _run_city_task(country_code, city, all_ref_cities)
            if payload is None:
                continue
            city_results[city_name] = payload
            completed[0] += 1
            result_queue.put(
                (
                    country_code,
                    "city_done",
                    {
                        "city": city_name,
                        "index": completed[0],
                        "total": total_opt,
                        "has_residual": payload["has_residual"],
                        "duration_seconds": payload["duration_seconds"],
                    },
                )
            )
        _aggregate_and_post_country_done(
            country_code, city_results, all_ref_cities, rounding, result_queue
        )
    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as e:
        import traceback

        result_queue.put((country_code, "error", f"{e}\n{traceback.format_exc()}"))
        return

    try:
        if not ref_files_info:
            result_queue.put((country_code, "error", "No reference cities found"))
            return

        all_ref_cities = []
        for ref_path, loc_d in ref_files_info:
            _raw_ry = loc_d.get("reference_year") or ""
            _batch_ref_year: int | None = (
                int(str(_raw_ry).strip()) if str(_raw_ry).strip().isdigit() else None
            )
            ref_times, ref_dates = load_reference_times(ref_path, year=_batch_ref_year)
            if not ref_times:
                continue
            city_tz_name = None
            if tf and use_dst:
                try:
                    city_tz_name = tf.timezone_at(
                        lng=float(loc_d.get("longitude", 0)),
                        lat=float(loc_d.get("latitude", 0)),
                    )
                except (ValueError, TypeError, KeyError, RuntimeError, OSError):
                    pass
            all_ref_cities.append(
                {
                    "name": loc_d["name"],
                    "reference_times": ref_times,
                    "available_dates": ref_dates,
                    "loc": loc_d,
                    "tz_name": city_tz_name,
                }
            )

        if not all_ref_cities:
            result_queue.put((country_code, "error", "No valid reference data"))
            return

        # ------------------------------------------------------------------
        # Step 1: Geographic clustering — pick one representative per cluster
        # ------------------------------------------------------------------
        clusters = _cluster_reference_cities(
            all_ref_cities, radius_km=cluster_radius_km
        )
        representatives = [rep for rep, _ in clusters]

        # Step 2: Optional max-cities cap using farthest-point selection
        if max_cities and len(representatives) > max_cities:
            representatives = _select_by_farthest_point(representatives, max_cities)

        total_ref = len(all_ref_cities)  # total reference cities in country
        total_opt = len(representatives)  # cities that will actually be optimized
        n_clustered = total_ref - total_opt

        if n_clustered > 0:
            result_queue.put(
                (
                    country_code,
                    "progress",
                    f"Clustering: {total_opt}/{total_ref} cities selected "
                    f"({n_clustered} merged into nearest representative).",
                )
            )

        # ------------------------------------------------------------------
        # Step 3: Define per-city worker (called from thread pool)
        # ------------------------------------------------------------------
        city_results = {}
        completed_count = [0]  # mutable box so closure can mutate it

        def _optimize_one(primary_city):
            if stop_event.is_set():
                return primary_city["name"], None

            primary_loc = primary_city["loc"]
            primary_ref_times = primary_city["reference_times"]
            primary_dates = primary_city["available_dates"]
            primary_lat = float(primary_loc["latitude"])
            primary_lon = float(primary_loc["longitude"])
            primary_tz_name = primary_city.get("tz_name")
            selected_timezone = primary_loc.get("timezone", 0)

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

            return primary_city["name"], {
                "opt_result": opt_result,
                "n_dates": len(primary_dates),
                "n_aux": len(conservative_aux),
                "has_residual": bool(opt_result.residual_corrections),
                "duration_seconds": float(opt_result.duration_seconds or 0.0),
            }

        # ------------------------------------------------------------------
        # Step 4: Parallel execution via ThreadPoolExecutor
        #         (scipy/numpy release GIL → real concurrency without pickling)
        # ------------------------------------------------------------------
        workers = max_cpu_workers if max_cpu_workers else None  # None → os.cpu_count()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_city = {
                executor.submit(_optimize_one, city): city for city in representatives
            }
            for future in as_completed(future_to_city):
                if stop_event.is_set():
                    for f in future_to_city:
                        f.cancel()
                    result_queue.put((country_code, "error", "Cancelled"))
                    return
                try:
                    city_name, payload = future.result()
                except Exception as exc:  # noqa: BLE001
                    city_name = future_to_city[future]["name"]
                    result_queue.put(
                        (
                            country_code,
                            "progress",
                            f"Error optimizing {city_name}: {exc}",
                        )
                    )
                    continue

                if payload is None:
                    continue  # cancelled mid-flight

                city_results[city_name] = payload
                completed_count[0] += 1
                result_queue.put(
                    (
                        country_code,
                        "city_done",
                        {
                            "city": city_name,
                            "index": completed_count[0],
                            "total": total_opt,
                            "has_residual": payload["has_residual"],
                            "duration_seconds": payload["duration_seconds"],
                        },
                    )
                )

        baseline_mae_vals = []
        baseline_rmse_vals = []
        after_mae_vals = []
        after_rmse_vals = []
        baseline_per_prayer_mae = {p: [] for p in PRAYER_NAMES}
        baseline_per_prayer_rmse = {p: [] for p in PRAYER_NAMES}
        baseline_per_prayer_max = {p: [] for p in PRAYER_NAMES}
        after_per_prayer_mae = {p: [] for p in PRAYER_NAMES}
        after_per_prayer_rmse = {p: [] for p in PRAYER_NAMES}
        after_per_prayer_max = {p: [] for p in PRAYER_NAMES}

        ref_city_by_name = {c["name"]: c for c in all_ref_cities}
        for city_name, city_payload in city_results.items():
            ref_city = ref_city_by_name.get(city_name)
            if not ref_city:
                continue

            loc = ref_city["loc"]
            ref_times = ref_city["reference_times"]
            dates = ref_city["available_dates"]
            city_tz_name = ref_city.get("tz_name")
            timezone_val = loc.get("timezone", 0)
            elevation_val = float(loc.get("elevation", 0) or 0)
            isha_minutes_val = float(loc.get("isha_minutes", 0) or 0)

            baseline_params = np.array(
                [
                    float(loc.get("fajr_angle", 18.0) or 18.0),
                    float(loc.get("isha_angle", 17.0) or 17.0),
                    float(loc.get("optimized_lat") or loc.get("latitude") or 0.0),
                    float(loc.get("optimized_lon") or loc.get("longitude") or 0.0),
                    float(loc.get("temp", 10.0) or 10.0),
                    float(loc.get("pressure", 1010.0) or 1010.0),
                ],
                dtype=float,
            )
            baseline_offsets = {f: float(loc.get(f, 0.0) or 0.0) for f in OFFSET_FIELDS}

            baseline_residual_model = _load_residual_model_from_json(
                loc.get("residual_corrections", "")
            )
            (
                baseline_city_rmse,
                baseline_city_mae,
                baseline_city_per_prayer_rmse,
                baseline_city_per_prayer_mae,
                baseline_city_per_prayer_max,
                _,
            ) = _compute_detailed_errors(
                baseline_params,
                available_dates=dates,
                reference_times=ref_times,
                elevation=elevation_val,
                timezone=timezone_val,
                tz_name=city_tz_name,
                isha_minutes=isha_minutes_val,
                offsets=baseline_offsets,
                residual_model=baseline_residual_model,
                settings_source=loc,
                clock_offsets_json=loc.get("clock_offsets", "") or "",
                rounding=rounding,
            )

            opt = city_payload["opt_result"]
            after_params = np.array(
                [
                    float(opt.fajr_angle),
                    float(opt.isha_angle),
                    float(opt.latitude),
                    float(opt.longitude),
                    float(opt.temp),
                    float(opt.pressure),
                ],
                dtype=float,
            )
            after_offsets = dict(opt.offsets) if opt.offsets else {}
            after_residual_model = _load_residual_model_from_json(
                opt.residual_corrections
            )
            (
                after_city_rmse,
                after_city_mae,
                after_city_per_prayer_rmse,
                after_city_per_prayer_mae,
                after_city_per_prayer_max,
                _,
            ) = _compute_detailed_errors(
                after_params,
                available_dates=dates,
                reference_times=ref_times,
                elevation=elevation_val,
                timezone=timezone_val,
                tz_name=city_tz_name,
                isha_minutes=isha_minutes_val,
                offsets=after_offsets,
                residual_model=after_residual_model,
                settings_source=[loc, opt],
                clock_offsets_json=opt.clock_offsets or "",
                rounding=rounding,
            )

            if math.isfinite(baseline_city_mae):
                baseline_mae_vals.append(float(baseline_city_mae))
            if math.isfinite(baseline_city_rmse):
                baseline_rmse_vals.append(float(baseline_city_rmse))
            if math.isfinite(after_city_mae):
                after_mae_vals.append(float(after_city_mae))
            if math.isfinite(after_city_rmse):
                after_rmse_vals.append(float(after_city_rmse))

            for prayer in PRAYER_NAMES:
                b_mae = float(baseline_city_per_prayer_mae.get(prayer, float("inf")))
                b_rmse = float(baseline_city_per_prayer_rmse.get(prayer, float("inf")))
                b_max = float(baseline_city_per_prayer_max.get(prayer, float("inf")))
                a_mae = float(after_city_per_prayer_mae.get(prayer, float("inf")))
                a_rmse = float(after_city_per_prayer_rmse.get(prayer, float("inf")))
                a_max = float(after_city_per_prayer_max.get(prayer, float("inf")))
                if math.isfinite(b_mae):
                    baseline_per_prayer_mae[prayer].append(b_mae)
                if math.isfinite(b_rmse):
                    baseline_per_prayer_rmse[prayer].append(b_rmse)
                if math.isfinite(b_max):
                    baseline_per_prayer_max[prayer].append(b_max)
                if math.isfinite(a_mae):
                    after_per_prayer_mae[prayer].append(a_mae)
                if math.isfinite(a_rmse):
                    after_per_prayer_rmse[prayer].append(a_rmse)
                if math.isfinite(a_max):
                    after_per_prayer_max[prayer].append(a_max)

        def _mean_or_inf(vals):
            return float(np.mean(vals)) if vals else float("inf")

        baseline_mae = _mean_or_inf(baseline_mae_vals)
        baseline_rmse = _mean_or_inf(baseline_rmse_vals)
        after_mae = _mean_or_inf(after_mae_vals)
        after_rmse = _mean_or_inf(after_rmse_vals)
        baseline_per_prayer_mae_avg = {
            p: _mean_or_inf(v) for p, v in baseline_per_prayer_mae.items()
        }
        baseline_per_prayer_rmse_avg = {
            p: _mean_or_inf(v) for p, v in baseline_per_prayer_rmse.items()
        }
        baseline_per_prayer_max_avg = {
            p: _mean_or_inf(v) for p, v in baseline_per_prayer_max.items()
        }
        after_per_prayer_mae_avg = {
            p: _mean_or_inf(v) for p, v in after_per_prayer_mae.items()
        }
        after_per_prayer_rmse_avg = {
            p: _mean_or_inf(v) for p, v in after_per_prayer_rmse.items()
        }
        after_per_prayer_max_avg = {
            p: _mean_or_inf(v) for p, v in after_per_prayer_max.items()
        }

        improved = _is_mae_priority_improvement(
            baseline_mae=baseline_mae,
            candidate_mae=after_mae,
            baseline_rmse=baseline_rmse,
            candidate_rmse=after_rmse,
            baseline_per_prayer_max=baseline_per_prayer_max_avg,
            candidate_per_prayer_max=after_per_prayer_max_avg,
        )
        if baseline_mae > 0 and baseline_mae != float("inf"):
            improvement_pct = ((baseline_mae - after_mae) / baseline_mae) * 100
        else:
            improvement_pct = 0.0

        total_duration = 0.0
        for city_payload in city_results.values():
            total_duration += float(city_payload["opt_result"].duration_seconds or 0.0)

        payload = {
            "city_results": city_results,
            "baseline_rmse": baseline_rmse,
            "baseline_mae": baseline_mae,
            "baseline_per_prayer_rmse": baseline_per_prayer_rmse_avg,
            "baseline_per_prayer_mae": baseline_per_prayer_mae_avg,
            "after_rmse": after_rmse,
            "after_mae": after_mae,
            "after_per_prayer_rmse": after_per_prayer_rmse_avg,
            "after_per_prayer_mae": after_per_prayer_mae_avg,
            "improved": improved,
            "improvement_pct": improvement_pct,
            "n_reference_cities": len(city_results),
            "duration_seconds": total_duration,
        }
        result_queue.put((country_code, "done", payload))

    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as e:
        import traceback

        result_queue.put((country_code, "error", f"{e}\n{traceback.format_exc()}"))


class BatchOptimizationDashboard:
    """
    A Toplevel dashboard for optimizing all countries with reference data.

    Features:
    - Shows all countries with reference data and their optimization status
    - Runs optimizations in background threads (UI stays responsive)
    - Before/after MAE comparison per country
    - Per-country apply/ignore controls
    - Start All / Stop / Apply Selected buttons
    """

    # Row status constants
    STATUS_PENDING = "Pending"
    STATUS_RUNNING = "Running..."
    STATUS_DONE_IMPROVED = "Improved"
    STATUS_DONE_NO_IMPROVE = "No Improvement"
    STATUS_ERROR = "Error"
    STATUS_APPLIED = "Applied"
    STATUS_SKIPPED = "Skipped"
    STATUS_CANCELLED = "Cancelled"

    def __init__(self, parent_app):
        """
        parent_app: the PrayerApp instance (has .root, .locations_data, .tf, .dst_var).
        """
        self.app = parent_app
        self.root = parent_app.root
        self.result_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.current_thread = None
        self.country_results = {}  # country_code -> payload dict
        self.is_running = False
        self.pending_queue = []  # List of country codes to process sequentially
        self.use_dst = False
        self.total_to_run = 0
        self.completed_count = 0
        self._active_executor = None  # ProcessPoolExecutor while running
        self.global_thread: threading.Thread | None = None
        self._build_window()
        self._discover_countries()
        self._populate_table()
        self._poll_queue()

    def _build_window(self):
        self.win = Toplevel(self.root)
        self.win.title("Batch Country Optimization Dashboard")
        self.win.geometry("1400x650")
        self.win.minsize(900, 500)
        self.win.transient(self.root)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        # --- Top controls ---
        ctrl_frame = ttk.Frame(self.win, padding="8 8 8 4")
        ctrl_frame.pack(fill=tk.X)

        self.start_btn = ttk.Button(
            ctrl_frame, text="Start All", command=self._start_all
        )
        self.start_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.stop_btn = ttk.Button(
            ctrl_frame, text="Stop", command=self._stop_all, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 5))

        ttk.Separator(ctrl_frame, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=8, pady=2
        )

        self.enable_all_btn = ttk.Button(
            ctrl_frame, text="Enable All", command=self._enable_all
        )
        self.enable_all_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.disable_all_btn = ttk.Button(
            ctrl_frame, text="Disable All", command=self._disable_all
        )
        self.disable_all_btn.pack(side=tk.LEFT, padx=(0, 5))

        ttk.Separator(ctrl_frame, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=8, pady=2
        )

        self.apply_selected_btn = ttk.Button(
            ctrl_frame,
            text="Apply Selected",
            command=self._apply_selected,
            state=tk.DISABLED,
        )
        self.apply_selected_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.select_improved_btn = ttk.Button(
            ctrl_frame,
            text="Select All Improved",
            command=self._select_all_improved,
            state=tk.DISABLED,
        )
        self.select_improved_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.deselect_all_btn = ttk.Button(
            ctrl_frame,
            text="Deselect All",
            command=self._deselect_all,
            state=tk.DISABLED,
        )
        self.deselect_all_btn.pack(side=tk.LEFT, padx=(0, 5))

        # Progress summary
        self.progress_label = ttk.Label(ctrl_frame, text="")
        self.progress_label.pack(side=tk.RIGHT, padx=(10, 0))

        # --- Optimization settings (inline, right-aligned) ---
        settings_frame = ttk.Frame(ctrl_frame)
        settings_frame.pack(side=tk.RIGHT, padx=(0, 8))

        ttk.Label(settings_frame, text="CPU Workers:").pack(side=tk.LEFT)
        self.max_workers_var = tk.StringVar(value="4")
        ttk.Entry(settings_frame, textvariable=self.max_workers_var, width=3).pack(
            side=tk.LEFT, padx=(2, 8)
        )

        ttk.Label(settings_frame, text="Max Cities:").pack(side=tk.LEFT)
        self.max_cities_var = tk.StringVar(value="")
        ttk.Entry(settings_frame, textvariable=self.max_cities_var, width=4).pack(
            side=tk.LEFT, padx=(2, 8)
        )

        ttk.Label(settings_frame, text="Cluster Radius (km):").pack(side=tk.LEFT)
        self.cluster_radius_var = tk.StringVar(value="150")
        ttk.Entry(settings_frame, textvariable=self.cluster_radius_var, width=5).pack(
            side=tk.LEFT, padx=(2, 0)
        )

        # --- Table ---
        table_frame = ttk.Frame(self.win, padding="8 4 8 4")
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = (
            "enable",
            "apply",
            "country",
            "code",
            "cities",
            "status",
            "mae_before",
            "mae_after",
            "improvement",
            "duration",
        )
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="browse"
        )

        col_config: List[
            tuple[
                str,
                str,
                int,
                Literal["nw", "n", "ne", "w", "center", "e", "sw", "s", "se"],
            ]
        ] = [
            ("enable", "Run", 45, tk.CENTER),
            ("apply", "Apply", 50, tk.CENTER),
            ("country", "Country", 140, tk.W),
            ("code", "Code", 50, tk.CENTER),
            ("cities", "Ref Cities", 70, tk.CENTER),
            ("status", "Status", 120, tk.W),
            ("mae_before", "MAE Before", 90, tk.CENTER),
            ("mae_after", "MAE After", 90, tk.CENTER),
            ("improvement", "Change", 100, tk.CENTER),
            ("duration", "Time", 70, tk.CENTER),
        ]
        for col_id, heading, width, anchor in col_config:
            self.tree.heading(col_id, text=heading)
            self.tree.column(col_id, width=width, anchor=anchor, minwidth=40)

        # Scrollbar
        scrollbar = ttk.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Bind click to toggle apply checkbox
        self.tree.bind("<Button-1>", self._on_tree_click)

        # --- Details panel ---
        details_frame = ttk.LabelFrame(self.win, text="Details", padding="8 4 8 4")
        details_frame.pack(fill=tk.X, padx=8, pady=(0, 8))

        self.details_text = tk.Text(
            details_frame,
            height=6,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 9),
        )
        self.details_text.pack(fill=tk.X)

        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)

    def _discover_countries(self):
        """Find all countries with reference data and matching cities."""
        self.countries = {}  # code -> { 'name': ..., 'ref_files': [(path, loc), ...] }

        # Build country code -> name mapping
        code_to_name = {}
        csv_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "resources",
            "country_codes.csv",
        )
        if os.path.exists(csv_path):
            import csv

            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)  # skip header
                for row in reader:
                    if len(row) >= 2:
                        code_to_name[row[1].strip()] = row[0].strip()

        # Normalize name helper
        def _norm(name):
            return name.replace(" ", "_").replace(",", "").lower()

        # Build location lookup per country
        country_locs = {}  # code -> {norm_name: loc_data}
        for loc in self.app.locations_data:
            cc = loc.get("country_code", "")
            if cc:
                if cc not in country_locs:
                    country_locs[cc] = {}
                country_locs[cc][_norm(loc["name"])] = loc

        # Scan reference directories
        ref_base = os.path.normpath("reference")
        if not os.path.isdir(ref_base):
            return

        for cc_dir in sorted(os.listdir(ref_base)):
            cc_path = os.path.join(ref_base, cc_dir)
            if not os.path.isdir(cc_path):
                continue
            cc = cc_dir.upper()
            try:
                ref_files_raw = [f for f in os.listdir(cc_path) if f.endswith(".txt")]
            except OSError:
                continue
            if not ref_files_raw:
                continue

            locs_for_cc = country_locs.get(cc, {})
            ref_files_matched = []
            for rf in sorted(ref_files_raw):
                norm = rf.replace(".txt", "")
                loc_d = locs_for_cc.get(norm)
                if loc_d is not None:
                    ref_files_matched.append((os.path.join(cc_path, rf), loc_d))

            if ref_files_matched:
                self.countries[cc] = {
                    "name": code_to_name.get(cc, cc),
                    "ref_files": ref_files_matched,
                }

        # Track enable and apply checkboxes
        self.enable_vars = {}  # country_code -> bool
        self.apply_vars = {}  # country_code -> bool

    def _populate_table(self):
        for cc, info in self.countries.items():
            self.enable_vars[cc] = True  # Default: all enabled
            self.apply_vars[cc] = False
            self.tree.insert(
                "",
                tk.END,
                iid=cc,
                values=(
                    "[X]",
                    "[ ]",
                    info["name"],
                    cc,
                    len(info["ref_files"]),
                    self.STATUS_PENDING,
                    "",
                    "",
                    "",
                    "",
                ),
            )
        self.progress_label.config(
            text=f"{len(self.countries)} countries with reference data"
        )

    def _on_tree_click(self, event):
        """Toggle enable or apply checkbox when clicking those columns."""
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return

        vals = list(self.tree.item(row_id, "values"))

        if col == "#1":  # "enable" column
            # Can't toggle if optimization is running or already completed
            if self.is_running or row_id in self.country_results:
                return
            self.enable_vars[row_id] = not self.enable_vars.get(row_id, False)
            check_str = "[X]" if self.enable_vars[row_id] else "[ ]"
            vals[0] = check_str
            self.tree.item(row_id, values=vals)

        elif col == "#2":  # "apply" column
            # Only allow toggling for improved results
            if row_id not in self.country_results:
                return
            payload = self.country_results[row_id]
            if not payload.get("improved", False):
                return
            self.apply_vars[row_id] = not self.apply_vars.get(row_id, False)
            check_str = "[X]" if self.apply_vars[row_id] else "[ ]"
            vals[1] = check_str
            self.tree.item(row_id, values=vals)

    def _on_row_select(self, _event):
        """Show details for the selected country."""
        sel = self.tree.selection()
        if not sel:
            return
        cc = sel[0]
        self.details_text.config(state=tk.NORMAL)
        self.details_text.delete("1.0", tk.END)

        if cc in self.country_results:
            p = self.country_results[cc]
            city_results = p.get("city_results", {})
            lines = [
                f"Country: {self.countries[cc]['name']} ({cc})",
                f"Reference cities optimized: {p.get('n_reference_cities', len(city_results))}",
                "",
                f"MAE:  {p['baseline_mae']:.2f} → {p['after_mae']:.2f} min  "
                f"({'%.1f%% better' % p['improvement_pct'] if p['improved'] else 'no improvement'})",
                f"RMSE: {p['baseline_rmse']:.2f} → {p['after_rmse']:.2f} min",
                "",
                "Per-prayer MAE (before → after):",
            ]
            for prayer in PRAYER_NAMES:
                b = p["baseline_per_prayer_mae"].get(prayer, float("inf"))
                a = p["after_per_prayer_mae"].get(prayer, float("inf"))
                lines.append(f"  {prayer:10s}: {b:.2f} → {a:.2f} min")
            if city_results:
                sample_name = sorted(city_results.keys())[0]
                sample_opt = city_results[sample_name]["opt_result"]
                lines.extend(
                    [
                        "",
                        f"Sample city: {sample_name}",
                        f"Fajr angle: {sample_opt.fajr_angle}  |  Isha angle: {sample_opt.isha_angle}",
                        f"Lat: {sample_opt.latitude:.5f}  Lon: {sample_opt.longitude:.5f}",
                    ]
                )
            self.details_text.insert(tk.END, "\n".join(lines))
        else:
            info = self.countries.get(cc, {})
            city_names = [rf[1]["name"] for rf in info.get("ref_files", [])]
            self.details_text.insert(
                tk.END,
                f"Country: {info.get('name', cc)} ({cc})\n"
                f"Reference cities: {', '.join(city_names)}\n"
                f"Status: Pending",
            )
        self.details_text.config(state=tk.DISABLED)

    def _start_all(self):
        """Start sequential optimization for all enabled countries."""
        self.stop_event.clear()
        self.is_running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.apply_selected_btn.config(state=tk.DISABLED)
        self.select_improved_btn.config(state=tk.DISABLED)
        self.deselect_all_btn.config(state=tk.DISABLED)
        self.enable_all_btn.config(state=tk.DISABLED)
        self.disable_all_btn.config(state=tk.DISABLED)

        # Get use_dst flag
        use_dst = False
        try:
            use_dst = self.app.dst_var.get()
        except (ValueError, TypeError, KeyError, RuntimeError, OSError):
            pass
        self.use_dst = use_dst
        # Build queue of enabled countries (allow re-runs)
        self.pending_queue = [
            cc for cc in self.countries if self.enable_vars.get(cc, False)
        ]
        self.total_to_run = len(self.pending_queue)
        self.completed_count = 0

        # Clear prior results for queued countries so metrics reflect latest settings
        for cc in self.pending_queue:
            if cc in self.country_results:
                self.country_results.pop(cc, None)
                self._update_row(
                    cc,
                    status=self.STATUS_PENDING,
                    mae_before="",
                    mae_after="",
                    improvement="",
                    duration="",
                )

        if not self.pending_queue:
            messagebox.showinfo(
                "No Countries Enabled",
                "No enabled countries to optimize. Enable at least one country by clicking the 'Run' column.",
                parent=self.win,
            )
            self._on_all_done()
            return

        # Read settings once (on the main thread where Tkinter vars live)
        rounding = "nearest"
        try:
            rounding = self.app.rounding_var.get() or "nearest"
        except (AttributeError, ValueError, TypeError, RuntimeError):
            pass

        cluster_radius_km = 150.0
        try:
            cluster_radius_km = float(self.cluster_radius_var.get() or 150.0)
        except (ValueError, TypeError, AttributeError):
            pass

        max_cities: Optional[int] = None
        try:
            raw_mc = self.max_cities_var.get().strip()
            if raw_mc:
                max_cities = int(raw_mc)
        except (ValueError, TypeError, AttributeError):
            pass

        max_workers: Optional[int] = None
        try:
            raw_mw = self.max_workers_var.get().strip()
            if raw_mw:
                max_workers = int(raw_mw)
        except (ValueError, TypeError, AttributeError):
            pass
        # Empty field → use all logical cores (user chose to bypass the cap).
        # Filled field → honour their choice.
        # The safe default shown in the field is 4 (set at widget creation).
        if max_workers is None:
            max_workers = os.cpu_count() or 1

        # Snapshot tf reference (safe to read from background thread)
        tf = self.app.tf
        countries_snapshot = {cc: self.countries[cc] for cc in self.pending_queue}

        def _run_global_pool():
            """Background thread: prepare all countries then dispatch cities
            to a single flat ThreadPoolExecutor."""
            try:
                _run_global_pool_inner()
            except Exception as fatal:  # noqa: BLE001
                import traceback

                err_text = (
                    f"Batch optimizer crashed unexpectedly:\n"
                    f"{fatal}\n\n{traceback.format_exc()}"
                )
                # Post error for every country that hasn't finished yet
                for pending_cc in countries_snapshot:
                    self.result_queue.put((pending_cc, "error", str(fatal)))
                try:
                    self.win.after(
                        0,
                        lambda m=err_text: messagebox.showerror(
                            "Batch Optimization Failed", m, parent=self.win
                        ),
                    )
                except Exception:  # noqa: BLE001
                    pass

        def _run_global_pool_inner():
            # ── Phase 1: prepare all countries (fast, sequential) ────────────
            country_prep = {}  # cc → (all_ref_cities, representatives)
            for cc, info in countries_snapshot.items():
                if self.stop_event.is_set():
                    self.result_queue.put((cc, "error", "Cancelled"))
                    continue
                prep = _prepare_country_cities(
                    cc,
                    info["ref_files"],
                    tf,
                    use_dst,
                    self.result_queue,
                    cluster_radius_km=cluster_radius_km,
                    max_cities=max_cities,
                )
                if prep is None:
                    continue  # error already posted
                all_ref_cities, representatives = prep
                country_prep[cc] = (all_ref_cities, representatives)

            # ── Phase 2: submit all cities to a single global pool ───────────
            # Track per-country: how many cities expected and results so far
            country_expected = {cc: len(reps) for cc, (_, reps) in country_prep.items()}
            country_city_results: dict = {cc: {} for cc in country_prep}
            country_completed = {cc: [0] for cc in country_prep}  # [count] box

            # ProcessPoolExecutor: each worker is a separate process with its
            # own GIL.  The prayer calculator is pure Python and holds the GIL
            # constantly inside ThreadPoolExecutor workers — ProcessPoolExecutor
            # fixes this so Tkinter's main thread is never starved.
            # Fall back to a single ThreadPoolExecutor worker if process
            # spawning fails (e.g. page file too small on Windows).
            try:
                executor = ProcessPoolExecutor(max_workers=max_workers)
            except Exception as spawn_exc:  # noqa: BLE001
                msg = (
                    f"Could not start {max_workers} worker processes:\n{spawn_exc}\n\n"
                    "Try reducing CPU Workers or increasing your virtual memory "
                    "(Windows page file size)."
                )
                self.result_queue.put(
                    (
                        next(iter(country_prep), "?"),
                        "progress",
                        f"[Error] {msg}",
                    )
                )
                # Show a dialog on main thread via after()
                self.win.after(
                    0,
                    lambda m=msg: messagebox.showerror(
                        "Worker Spawn Failed", m, parent=self.win
                    ),
                )
                executor = ThreadPoolExecutor(max_workers=1)
            self._active_executor = executor
            try:
                with executor:
                    # Submit every representative city from every country
                    future_to_meta = {}
                    for cc, (all_ref_cities, representatives) in country_prep.items():
                        total_opt = len(representatives)
                        for city in representatives:
                            fut = executor.submit(
                                _run_city_task, cc, city, all_ref_cities
                            )
                            future_to_meta[fut] = (cc, city, all_ref_cities, total_opt)

                    for future in as_completed(future_to_meta):
                        cc, city, all_ref_cities, total_opt = future_to_meta[future]
                        if self.stop_event.is_set():
                            for f in future_to_meta:
                                f.cancel()
                            # post cancel to all countries that haven't finished
                            for pending_cc in country_prep:
                                if (
                                    country_completed[pending_cc][0]
                                    < country_expected[pending_cc]
                                ):
                                    self.result_queue.put(
                                        (pending_cc, "error", "Cancelled")
                                    )
                            return

                        try:
                            _, city_name, payload = future.result()
                        except Exception as exc:  # noqa: BLE001
                            city_name = city["name"]
                            exc_str = str(exc)
                            # Detect catastrophic system-level failures so the
                            # user gets a visible dialog rather than a buried log line.
                            from concurrent.futures import BrokenExecutor
                            from concurrent.futures.process import BrokenProcessPool

                            _is_fatal = (
                                isinstance(
                                    exc,
                                    (MemoryError, BrokenExecutor, BrokenProcessPool),
                                )
                                or "paging file" in exc_str.lower()
                                or "DLL load failed" in exc_str
                                or "cannot allocate memory" in exc_str.lower()
                                or (
                                    "worker" in exc_str.lower()
                                    and "terminated" in exc_str.lower()
                                )
                            )
                            self.result_queue.put(
                                (
                                    cc,
                                    "progress",
                                    f"Error optimizing {city_name}: {exc_str}",
                                )
                            )
                            if _is_fatal:
                                fatal_msg = (
                                    f"A worker process ran out of memory while "
                                    f"optimizing {city_name}:\n{exc_str}\n\n"
                                    "Try reducing the number of CPU Workers, or "
                                    "increase your Windows virtual memory "
                                    "(page file size)."
                                )
                                # Mark ALL remaining pending countries as failed
                                for pending_cc in country_prep:
                                    if (
                                        country_completed[pending_cc][0]
                                        < country_expected[pending_cc]
                                    ):
                                        self.result_queue.put(
                                            (
                                                pending_cc,
                                                "error",
                                                f"Memory error: {exc_str}",
                                            )
                                        )
                                try:
                                    self.win.after(
                                        0,
                                        lambda m=fatal_msg: messagebox.showerror(
                                            "Out of Memory", m, parent=self.win
                                        ),
                                    )
                                except Exception:  # noqa: BLE001
                                    pass
                                return
                            # count as done so the country can still finish
                            country_completed[cc][0] += 1
                            payload = None

                        if payload is not None:
                            country_city_results[cc][city_name] = payload
                            country_completed[cc][0] += 1
                            self.result_queue.put(
                                (
                                    cc,
                                    "city_done",
                                    {
                                        "city": city_name,
                                        "index": country_completed[cc][0],
                                        "total": total_opt,
                                        "has_residual": payload["has_residual"],
                                        "duration_seconds": payload["duration_seconds"],
                                    },
                                )
                            )

                        # When all cities for a country are done → aggregate
                        if country_completed[cc][0] >= country_expected[cc]:
                            if country_city_results[cc]:
                                try:
                                    _aggregate_and_post_country_done(
                                        cc,
                                        country_city_results[cc],
                                        all_ref_cities,
                                        rounding,
                                        self.result_queue,
                                    )
                                except Exception as agg_exc:  # noqa: BLE001
                                    self.result_queue.put(
                                        (cc, "error", f"Aggregation error: {agg_exc}")
                                    )
                            else:
                                self.result_queue.put(
                                    (cc, "error", "All city optimizations failed")
                                )
            finally:
                self._active_executor = None

        self.global_thread = threading.Thread(target=_run_global_pool, daemon=True)
        self.global_thread.start()
        self.root.after(200, self._poll_queue)

    def _kill_active_executor(self):
        """Hard-kill all worker processes in the active ProcessPoolExecutor.

        `f.cancel()` is a no-op for futures already running in a subprocess.
        We snapshot `_processes` BEFORE calling shutdown() because shutdown()
        clears the dict as part of its cleanup, leaving us nothing to kill.
        """
        executor = self._active_executor
        if executor is None:
            return
        # ── Snapshot worker processes BEFORE shutdown clears the dict ─────────
        procs: dict = dict(getattr(executor, "_processes", None) or {})

        # Tell the pool to stop accepting new work (non-blocking).
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001
            pass

        # Hard-kill every worker process that existed at snapshot time.
        import signal as _signal

        for pid_or_proc in procs.values():
            # pid_or_proc is a multiprocessing.Process instance
            try:
                pid_or_proc.kill()  # calls TerminateProcess on Windows
            except Exception:  # noqa: BLE001
                pass
            # Belt-and-suspenders: also kill by PID directly
            try:
                pid = getattr(pid_or_proc, "pid", None)
                if pid is not None:
                    os.kill(pid, getattr(_signal, "SIGKILL", 9))
            except Exception:  # noqa: BLE001
                pass

    def _stop_all(self):
        """Signal all running threads to stop."""
        self.stop_event.set()
        self._kill_active_executor()
        self.stop_btn.config(state=tk.DISABLED)
        self.progress_label.config(text="Stopping...")

    def _start_next_country(self):
        """Start optimization for the next country in the queue."""
        if not self.pending_queue:
            return

        cc = self.pending_queue.pop(0)
        info = self.countries[cc]
        self._update_row(cc, status=self.STATUS_RUNNING)

        rounding = "nearest"
        try:
            rounding = self.app.rounding_var.get() or "nearest"
        except (AttributeError, ValueError, TypeError, RuntimeError):
            pass

        cluster_radius_km = 150.0
        try:
            cluster_radius_km = float(self.cluster_radius_var.get() or 150.0)
        except (ValueError, TypeError, AttributeError):
            pass

        max_cities: Optional[int] = None
        try:
            raw_mc = self.max_cities_var.get().strip()
            if raw_mc:
                max_cities = int(raw_mc)
        except (ValueError, TypeError, AttributeError):
            pass

        max_workers: Optional[int] = None
        try:
            raw_mw = self.max_workers_var.get().strip()
            if raw_mw:
                max_workers = int(raw_mw)
        except (ValueError, TypeError, AttributeError):
            pass

        self.current_thread = threading.Thread(
            target=_run_country_optimization,
            args=(
                cc,
                info["ref_files"],
                self.app.tf,
                self.use_dst,
                self.result_queue,
                self.stop_event,
                max_workers,
            ),
            kwargs={
                "rounding": rounding,
                "cluster_radius_km": cluster_radius_km,
                "max_cities": max_cities,
            },
            daemon=True,
        )
        self.current_thread.start()

    # -----------------------------------------------------------------------
    # Pending-state dict for coalescing intermediate messages on the main
    # thread.  Keyed by country code; value is the latest (msg_type, payload)
    # that hasn't been applied yet for status-only updates.
    # 'done' and 'error' messages always pass through immediately.
    # -----------------------------------------------------------------------
    _COALESCE_TYPES = frozenset({"progress", "city_done"})
    _MAX_MSGS_PER_POLL = 30  # applied in one shot; rest scheduled via after(0)

    def _poll_queue(self):
        """Poll the result queue and update UI. Called via root.after()."""
        # ---------- Drain up to _MAX_MSGS_PER_POLL messages ----------
        # For high-frequency status messages (progress, city_done) we keep
        # only the LATEST one per country before touching the tree.
        # 'done' and 'error' always go through immediately so country rows
        # reach their final state without waiting.
        coalesced: dict = {}  # cc -> (msg_type, payload) — latest status-only msg
        final_msgs: list = []  # (cc, msg_type, payload) — must be processed in order
        processed = 0
        try:
            while processed < self._MAX_MSGS_PER_POLL:
                cc, msg_type, payload = self.result_queue.get_nowait()
                processed += 1
                if msg_type in self._COALESCE_TYPES:
                    coalesced[cc] = (msg_type, payload)
                else:
                    final_msgs.append((cc, msg_type, payload))
        except queue.Empty:
            pass

        # Apply coalesced status updates first (one tree write per country)
        for cc, (msg_type, payload) in coalesced.items():
            self._handle_message(cc, msg_type, payload)
        # Then process final messages in order
        for cc, msg_type, payload in final_msgs:
            self._handle_message(cc, msg_type, payload)

        # If we hit the cap there are still messages waiting — reschedule
        # immediately (after(0)) so Tkinter can process one event loop tick
        # before we drain again.
        if processed >= self._MAX_MSGS_PER_POLL:
            try:
                self.win.after(0, self._poll_queue)
            except tk.TclError:
                pass
            return

        # Check if the global pool thread has finished
        if self.is_running:
            global_thread = getattr(self, "global_thread", None)
            if global_thread is not None and not global_thread.is_alive():
                # Flush any stragglers left in the queue before declaring done
                try:
                    while True:
                        cc, msg_type, payload = self.result_queue.get_nowait()
                        self._handle_message(cc, msg_type, payload)
                except queue.Empty:
                    pass
                self._on_all_done()
                return

        # Schedule next regular poll if window still exists
        try:
            self.win.after(150, self._poll_queue)
        except tk.TclError:
            pass

    def _handle_message(self, cc, msg_type, payload):
        if msg_type == "progress":
            self._update_row(cc, status=f"Running: {payload}")
        elif msg_type == "city_done":
            city = payload.get("city", "")
            idx = payload.get("index", 0)
            total = payload.get("total", 0)
            model_tag = (
                "with residual" if payload.get("has_residual") else "no residual"
            )
            duration = float(payload.get("duration_seconds") or 0.0)
            self._update_row(
                cc,
                status=(
                    f"Running: {idx}/{total} {city} ({model_tag}, {duration:.1f}s)"
                ),
            )
        elif msg_type == "done":
            self.country_results[cc] = payload
            self.completed_count += 1
            if payload["improved"]:
                self._update_row(
                    cc,
                    scroll_into_view=True,
                    status=self.STATUS_DONE_IMPROVED,
                    mae_before=f"{payload['baseline_mae']:.2f}",
                    mae_after=f"{payload['after_mae']:.2f}",
                    improvement=f"-{payload['improvement_pct']:.1f}%",
                    duration=f"{payload.get('duration_seconds', 0.0):.0f}s",
                )
                # Auto-check improved results
                self.apply_vars[cc] = True
                vals = list(self.tree.item(cc, "values"))
                vals[1] = "[X]"
                self.tree.item(cc, values=vals)
            else:
                self._update_row(
                    cc,
                    scroll_into_view=True,
                    status=self.STATUS_DONE_NO_IMPROVE,
                    mae_before=f"{payload['baseline_mae']:.2f}",
                    mae_after=f"{payload['after_mae']:.2f}",
                    improvement="None",
                    duration=f"{payload.get('duration_seconds', 0.0):.0f}s",
                )
            self._update_progress()
        elif msg_type == "error":
            self.completed_count += 1
            self._update_row(
                cc, scroll_into_view=True, status=f"Error: {str(payload)[:50]}"
            )
            self._update_progress()

    def _update_row(self, cc, scroll_into_view=False, **kwargs):
        """Update specific columns of a tree row."""
        try:
            vals = list(self.tree.item(cc, "values"))
            col_map = {
                "enable": 0,
                "apply": 1,
                "status": 5,
                "mae_before": 6,
                "mae_after": 7,
                "improvement": 8,
                "duration": 9,
            }
            for key, val in kwargs.items():
                if key in col_map:
                    vals[col_map[key]] = val
            self.tree.item(cc, values=vals)
            # Only scroll on meaningful state changes (done / error), not every
            # intermediate progress tick — tree.see() is surprisingly expensive.
            if scroll_into_view:
                self.tree.see(cc)
        except tk.TclError:
            pass

    def _update_progress(self):
        total = self.total_to_run
        done = self.completed_count
        improved = sum(
            1 for p in self.country_results.values() if p.get("improved", False)
        )
        self.progress_label.config(text=f"{done}/{total} done  |  {improved} improved")

    def _on_all_done(self):
        """Called when all optimization threads have finished."""
        self.is_running = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.enable_all_btn.config(state=tk.NORMAL)
        self.disable_all_btn.config(state=tk.NORMAL)
        self.current_thread = None

        has_improved = any(
            p.get("improved", False) for p in self.country_results.values()
        )
        if has_improved:
            self.apply_selected_btn.config(state=tk.NORMAL)
            self.select_improved_btn.config(state=tk.NORMAL)
            self.deselect_all_btn.config(state=tk.NORMAL)

        total = len(self.country_results)
        improved = sum(
            1 for p in self.country_results.values() if p.get("improved", False)
        )
        self.progress_label.config(
            text=f"Complete: {total} countries  |  {improved} improved"
        )

    def _enable_all(self):
        """Enable all countries for optimization."""
        if self.is_running:
            return
        for cc in self.countries:
            if cc not in self.country_results:  # Only unprocessed countries
                self.enable_vars[cc] = True
                vals = list(self.tree.item(cc, "values"))
                vals[0] = "[X]"
                self.tree.item(cc, values=vals)

    def _disable_all(self):
        """Disable all countries for optimization."""
        if self.is_running:
            return
        for cc in self.countries:
            if cc not in self.country_results:  # Only unprocessed countries
                self.enable_vars[cc] = False
                vals = list(self.tree.item(cc, "values"))
                vals[0] = "[ ]"
                self.tree.item(cc, values=vals)

    def _select_all_improved(self):
        for cc, payload in self.country_results.items():
            if payload.get("improved", False):
                self.apply_vars[cc] = True
                vals = list(self.tree.item(cc, "values"))
                vals[1] = "[X]"  # Apply column is now index 1
                self.tree.item(cc, values=vals)

    def _deselect_all(self):
        for cc in self.apply_vars:
            self.apply_vars[cc] = False
            try:
                vals = list(self.tree.item(cc, "values"))
                vals[1] = "[ ]"
                self.tree.item(cc, values=vals)
            except tk.TclError:
                pass

    def _apply_selected(self):
        """Apply optimization results for all selected (checked) countries."""
        rewrite_location_file = getattr(self.app, "rewrite_location_file", None)
        if not callable(rewrite_location_file):
            messagebox.showerror(
                "Apply Failed",
                "No rewrite hook is available on the host app instance.",
                parent=self.win,
            )
            return

        to_apply = [
            cc
            for cc, checked in self.apply_vars.items()
            if checked
            and cc in self.country_results
            and self.country_results[cc].get("improved", False)
        ]

        if not to_apply:
            messagebox.showinfo(
                "Nothing Selected",
                "No countries with improvements are selected.",
                parent=self.win,
            )
            return

        confirm = messagebox.askyesno(
            "Confirm Apply",
            f"Apply optimized settings to {len(to_apply)} countries?\n\n"
            + "\n".join(f"  {self.countries[cc]['name']} ({cc})" for cc in to_apply),
            parent=self.win,
        )
        if not confirm:
            return

        applied_count = 0
        updated_city_ids = []
        for cc in to_apply:
            payload = self.country_results[cc]
            city_results = payload.get("city_results", {})

            reference_result_by_name = {
                name: data.get("opt_result") for name, data in city_results.items()
            }
            has_any_stage1_results = any(
                _is_stage1_output(opt)
                for opt in reference_result_by_name.values()
                if opt is not None
            )
            has_any_full_results = any(
                not _is_stage1_output(opt)
                for opt in reference_result_by_name.values()
                if opt is not None
            )
            country_stage1_only = has_any_stage1_results and not has_any_full_results
            reference_cities_for_angle_transfer = []
            for name, opt in reference_result_by_name.items():
                if not opt:
                    continue
                reference_cities_for_angle_transfer.append(
                    {
                        "name": name,
                        "latitude": float(opt.latitude),
                        "longitude": float(opt.longitude),
                        "fajr_angle": float(opt.fajr_angle),
                        "isha_angle": float(opt.isha_angle),
                    }
                )
            reference_cities_with_models = []
            for name, opt in reference_result_by_name.items():
                if opt and opt.residual_corrections:
                    reference_cities_with_models.append(
                        {
                            "name": name,
                            "latitude": float(opt.latitude),
                            "longitude": float(opt.longitude),
                            "residual_corrections": opt.residual_corrections,
                        }
                    )

            for i, loc in enumerate(self.app.locations_data):
                if loc.get("country_code") != cc:
                    continue

                city_opt = reference_result_by_name.get(loc.get("name"))
                if city_opt is not None:
                    is_stage1 = _is_stage1_output(city_opt)
                    _apply_optimization_result_to_location(
                        self.app.locations_data[i],
                        city_opt,
                        stage1_only=is_stage1,
                        apply_coordinates=not is_stage1,
                    )
                else:
                    loc_lat = float(
                        loc.get("optimized_lat") or loc.get("latitude") or 0.0
                    )
                    loc_lon = float(
                        loc.get("optimized_lon") or loc.get("longitude") or 0.0
                    )

                    closest_for_angles = _find_closest_city_by_distance(
                        loc_lat,
                        loc_lon,
                        reference_cities_for_angle_transfer,
                    )
                    if closest_for_angles:
                        if country_stage1_only:
                            _reset_stage1_defaults(self.app.locations_data[i])
                        self.app.locations_data[i]["fajr_angle"] = closest_for_angles[
                            "fajr_angle"
                        ]
                        self.app.locations_data[i]["isha_angle"] = closest_for_angles[
                            "isha_angle"
                        ]
                        self.app.locations_data[i]["is_optimized"] = 1

                    if not country_stage1_only:
                        closest_ref = _find_closest_city_by_distance(
                            loc_lat,
                            loc_lon,
                            reference_cities_with_models,
                        )
                        if closest_ref and _matches_conservative_rules(
                            closest_ref["latitude"],
                            closest_ref["longitude"],
                            loc_lat,
                            loc_lon,
                        ):
                            self.app.locations_data[i]["residual_corrections"] = (
                                closest_ref["residual_corrections"]
                            )
                        else:
                            self.app.locations_data[i]["residual_corrections"] = ""

                updated_city_ids.append(self.app.locations_data[i].get("id"))
                applied_count += 1

            # Update row status
            self._update_row(cc, status=self.STATUS_APPLIED)
            self.apply_vars[cc] = False
            vals = list(self.tree.item(cc, "values"))
            vals[1] = "[X]"  # Keep apply column checked to show it was applied
            self.tree.item(cc, values=vals)

        try:
            rewrite_location_file(self.app)
        except TypeError:
            rewrite_location_file()
        if hasattr(self.app, "rebuild_city_rmse_for_ids"):
            try:
                self.app.rebuild_city_rmse_for_ids(updated_city_ids)
            except (ValueError, TypeError, KeyError, RuntimeError, OSError):
                pass
        if hasattr(self.app, "filter_list"):
            try:
                self.app.filter_list()
            except (ValueError, TypeError, KeyError, RuntimeError, OSError):
                pass
        try:
            self.app.on_city_select(None)
        except (ValueError, TypeError, KeyError, RuntimeError, OSError):
            pass

        messagebox.showinfo(
            "Applied",
            f"Applied optimizations to {applied_count} cities across {len(to_apply)} countries.",
            parent=self.win,
        )

    def _on_close(self):
        """Handle window close — immediately kill worker processes then destroy."""
        self.stop_event.set()
        self._kill_active_executor()
        self._force_close()

    def _force_close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def open_batch_optimization_dashboard(app):
    """Entry point to open the batch dashboard from the main GUI."""
    BatchOptimizationDashboard(app)


# =============================================================================
# GUI Wrapper — Entry point called from PrayerApp
# =============================================================================


def optimize_parameters_for_city(
    self,
    ref_file,
    max_cpu_workers=None,
):
    """
    Main entry point called from the GUI. Loads reference data,
    runs the optimization engine, and displays results.
    """
    _ = max_cpu_workers
    rewrite_location_file = getattr(self, "rewrite_location_file", None)
    if not callable(rewrite_location_file):
        messagebox.showerror(
            "Optimization Error",
            "No rewrite hook is available on this app instance.",
        )
        return

    selected_data = self.get_selected_location_data()
    # ------- Guard: prevent concurrent optimizations -------
    _opt_btn = getattr(self, "optimize_settings_button", None)
    _listbox = getattr(self, "city_listbox", None)
    if _opt_btn is not None:
        try:
            if str(_opt_btn.cget("state")) == "disabled":
                return  # already running
            _opt_btn.config(state="disabled", text="Optimizing\u2026 ⏳")
        except Exception:  # noqa: BLE001
            _opt_btn = None

    # Lock city list so user can't switch cities mid-optimization
    self._single_city_opt_running = True
    if _listbox is not None:
        try:
            _listbox.config(state="disabled")
        except Exception:  # noqa: BLE001
            pass

    def _restore_button():
        """Re-enables the button AND the city listbox — always called on main thread."""
        self._single_city_opt_running = False
        if _opt_btn is not None:
            try:
                _opt_btn.config(state="normal", text="Optimize Parameters")
            except Exception:  # noqa: BLE001
                pass
        if _listbox is not None:
            try:
                _listbox.config(state="normal")
            except Exception:  # noqa: BLE001
                pass

    if not selected_data:
        _restore_button()
        messagebox.showwarning("No Selection", "Please select a city to optimize.")
        return
    optimization_started_at = datetime.datetime.now()

    def _progress_log(msg):
        text = str(msg or "").strip()
        if not text:
            return
        lower = text.lower()
        if not any(
            token in lower
            for token in (
                "phase ",
                "stage ",
                "accepted",
                "rejected",
                "summary",
                "final",
                "switch",
            )
        ):
            return
        print(f"[2/3] {text}")

    print(
        f"--- Optimization started for {selected_data['name']} at {datetime.datetime.now()} ---"
    )
    if not ref_file or not os.path.exists(ref_file):
        _restore_button()
        messagebox.showerror(
            "No Reference Data",
            f"No reference data found at '{ref_file}'. Optimization requires reference data.",
        )
        return

    # --- Load reference data ---
    # Use the stored reference_year for this city (if set) so the optimizer sees
    # the correct leap-cycle year that best matches the reference data.
    raw_ref_year = selected_data.get("reference_year", "") or ""
    reference_year: int | None = None
    if str(raw_ref_year).strip().isdigit():
        reference_year = int(str(raw_ref_year).strip())

    try:
        all_reference_times, available_dates = load_reference_times(
            ref_file, year=reference_year
        )
    except (TypeError, KeyError, RuntimeError, OSError) as e:
        _restore_button()
        messagebox.showerror(
            "Error", f"Failed to read reference file '{ref_file}': {e}"
        )
        return

    if not all_reference_times:
        _restore_button()
        messagebox.showerror(
            "No Reference Data",
            f"Reference file '{ref_file}' did not contain valid data.",
        )
        return

    # --- Determine timezone name ---
    print("[1/3] Preparing optimization inputs and baseline...")
    original_lat = selected_data["latitude"]
    original_lon = selected_data["longitude"]
    tz_name = None
    if self.tf and self.dst_var.get():
        try:
            tz_name = self.tf.timezone_at(lng=original_lon, lat=original_lat)
        except (ValueError, TypeError, KeyError, RuntimeError, OSError) as tz_e:
            print(f"Warning: Could not determine timezone name: {tz_e}")

    selected_timezone = selected_data["timezone"]
    sel_elevation = float(selected_data.get("elevation", 0) or 0)
    baseline_location_data = dict(selected_data)
    optimization_location_data = _reset_stage1_defaults(dict(selected_data))

    # --- Discover auxiliary cities in same country (for residual model only) ---
    auxiliary_cities = []
    country_code = selected_data.get("country_code", "")
    ref_dir = os.path.normpath(os.path.join("reference", country_code))
    selected_ref_basename = os.path.basename(ref_file)

    if country_code and os.path.isdir(ref_dir):

        def _normalize_name(name):
            return name.replace(" ", "_").replace(",", "").lower()

        country_locations = {}
        for loc in self.locations_data:
            if loc.get("country_code") == country_code:
                country_locations[_normalize_name(loc["name"])] = loc

        try:
            ref_files_in_dir = [
                f
                for f in os.listdir(ref_dir)
                if f.endswith(".txt") and f != selected_ref_basename
            ]
        except OSError:
            ref_files_in_dir = []

        for ref_filename in ref_files_in_dir:
            norm_name = ref_filename.replace(".txt", "")
            loc_data = country_locations.get(norm_name)
            if loc_data is None:
                continue

            aux_ref_path = os.path.join(ref_dir, ref_filename)
            _raw_aux_ry = loc_data.get("reference_year") or ""
            _aux_ref_year: int | None = (
                int(str(_raw_aux_ry).strip())
                if str(_raw_aux_ry).strip().isdigit()
                else None
            )
            aux_ref_times, aux_dates = load_reference_times(
                Path(aux_ref_path), year=_aux_ref_year
            )
            if not aux_ref_times or not aux_dates:
                continue

            aux_tz_name = None
            aux_lat = float(loc_data.get("latitude", 0))
            aux_lon = float(loc_data.get("longitude", 0))
            aux_tz = loc_data.get("timezone", selected_timezone) or selected_timezone
            if self.tf and self.dst_var.get():
                try:
                    aux_tz_name = self.tf.timezone_at(lng=aux_lon, lat=aux_lat)
                except (ValueError, TypeError, KeyError, RuntimeError, OSError):
                    pass

            auxiliary_cities.append(
                {
                    "name": loc_data["name"],
                    "latitude": aux_lat,
                    "longitude": aux_lon,
                    "elevation": float(loc_data.get("elevation", 0) or 0),
                    "timezone": aux_tz,
                    "tz_name": aux_tz_name,
                    "reference_times": aux_ref_times,
                    "available_dates": aux_dates,
                    "temp": float(loc_data.get("temp", 10.0) or 10.0),
                    "pressure": float(loc_data.get("pressure", 1010.0) or 1010.0),
                    "isha_minutes": float(loc_data.get("isha_minutes", 0) or 0),
                }
            )

    conservative_auxiliary_cities = _filter_cities_by_conservative_rules(
        original_lat,
        original_lon,
        auxiliary_cities,
    )
    if auxiliary_cities:
        print(
            f"Using {len(conservative_auxiliary_cities)}/{len(auxiliary_cities)} "
            "auxiliary cities for residual validation"
        )

    # --- Compute baseline error with current parameters ---
    print("[1/3] Computing baseline error with current parameters...")
    baseline_params = np.array(
        [
            float(baseline_location_data.get("fajr_angle", 18.0) or 18.0),
            float(baseline_location_data.get("isha_angle", 17.0) or 17.0),
            float(
                baseline_location_data.get("optimized_lat")
                or baseline_location_data["latitude"]
            ),
            float(
                baseline_location_data.get("optimized_lon")
                or baseline_location_data["longitude"]
            ),
            float(baseline_location_data.get("temp", 10.0) or 10.0),
            float(baseline_location_data.get("pressure", 1010.0) or 1010.0),
        ],
        dtype=float,
    )

    baseline_offsets = {
        "fajr_offset": float(baseline_location_data.get("fajr_offset", 0.0) or 0.0),
        "shurooq_offset": float(
            baseline_location_data.get("shurooq_offset", 0.0) or 0.0
        ),
        "dhuhr_offset": float(baseline_location_data.get("dhuhr_offset", 0.0) or 0.0),
        "asr_offset": float(baseline_location_data.get("asr_offset", 0.0) or 0.0),
        "maghrib_offset": float(
            baseline_location_data.get("maghrib_offset", 0.0) or 0.0
        ),
        "isha_offset": float(baseline_location_data.get("isha_offset", 0.0) or 0.0),
    }

    (
        baseline_rmse,
        baseline_mae,
        _,
        baseline_per_prayer_mae,
        _,
        _,
    ) = _compute_detailed_errors(
        baseline_params,
        available_dates=available_dates,
        reference_times=all_reference_times,
        elevation=sel_elevation,
        timezone=selected_timezone,
        tz_name=tz_name,
        isha_minutes=float(baseline_location_data.get("isha_minutes", 0) or 0),
        offsets=baseline_offsets,
        residual_model=_load_residual_model_from_json(
            baseline_location_data.get("residual_corrections", "")
        ),
        settings_source=baseline_location_data,
        clock_offsets_json=baseline_location_data.get("clock_offsets", "") or "",
        rounding=self.rounding_var.get() or "nearest",
    )

    print(f"[1/3] Baseline MAE: {baseline_mae:.2f} min, RMSE: {baseline_rmse:.2f} min")

    # --- Run the optimization engine ---
    print(
        "[2/3] Running multistage optimizer "
        f"(dates={len(available_dates)}, tz={tz_name or selected_timezone})..."
    )

    # -----------------------------------------------------------------
    # Everything below the baseline computation is CPU-heavy.  Run the
    # optimizer in a subprocess (via ProcessPoolExecutor) so the main
    # Tkinter thread stays fully responsive.  A lightweight background
    # thread drives the future and schedules the result dialog back on
    # the main thread via root.after().
    # -----------------------------------------------------------------
    rounding = getattr(self, "rounding_var", None)
    rounding_str = rounding.get() if rounding else "nearest"

    def _run_in_background():
        """Background thread: submit to process pool, then schedule UI callback."""
        try:
            with ProcessPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    _run_single_city_optimization,
                    optimization_location_data,
                    all_reference_times,
                    available_dates,
                    tz_name,
                )
                opt_result = future.result()  # blocks only this thread
        except Exception as exc:  # noqa: BLE001
            err_msg = (
                f"Optimization failed:\n{exc}\n\n"
                "If you see a memory/page-file error, try increasing the "
                "Windows virtual memory (page file) size."
            )
            try:
                self.root.after(0, _restore_button)
                self.root.after(
                    0, lambda m=err_msg: messagebox.showerror("Optimization Error", m)
                )
            except Exception:  # noqa: BLE001
                pass
            return

        # Back on a background thread — schedule all post-processing on main thread
        self.root.after(0, lambda: _finish_optimization(opt_result))

    def _finish_optimization(opt_result):
        """Runs on main thread: compute after-metrics, show dialog, apply results."""
        # Restore the button as the first thing so it's re-enabled even if
        # something below raises.
        _restore_button()
        after_params = np.array(
            [
                float(opt_result.fajr_angle),
                float(opt_result.isha_angle),
                float(opt_result.latitude),
                float(opt_result.longitude),
                float(opt_result.temp),
                float(opt_result.pressure),
            ],
            dtype=float,
        )
        after_elevation = float(getattr(opt_result, "elevation", sel_elevation) or 0.0)
        after_offsets = dict(opt_result.offsets) if opt_result.offsets else {}

        (
            after_rmse,
            after_mae,
            _,
            after_per_prayer_mae,
            _,
            _,
        ) = _compute_detailed_errors(
            after_params,
            available_dates=available_dates,
            reference_times=all_reference_times,
            elevation=after_elevation,
            timezone=selected_timezone,
            tz_name=tz_name,
            isha_minutes=float(baseline_location_data.get("isha_minutes", 0) or 0),
            offsets=after_offsets,
            residual_model=_load_residual_model_from_json(
                opt_result.residual_corrections
            ),
            settings_source=[baseline_location_data, opt_result],
            clock_offsets_json=opt_result.clock_offsets or "",
            rounding=rounding_str,
        )

        if baseline_mae > 0 and baseline_mae != float("inf"):
            improvement_pct = ((baseline_mae - after_mae) / baseline_mae) * 100
        else:
            improvement_pct = 0.0
        print(
            "[3/3] Optimization complete. "
            f"MAE {baseline_mae:.2f}\u2192{after_mae:.2f}, "
            f"RMSE {baseline_rmse:.2f}\u2192{after_rmse:.2f}."
        )
        phase_timings = getattr(opt_result, "phase_timings", None)
        if isinstance(phase_timings, dict) and phase_timings:
            ranked = [
                (k, float(v))
                for k, v in phase_timings.items()
                if k != "total"
                and not k.endswith(".total")
                and isinstance(v, (int, float))
            ]
            ranked.sort(key=lambda item: item[1], reverse=True)
            if ranked:
                print("[3/3] Slowest steps:")
                for step_name, secs in ranked[:5]:
                    print(f"  - {step_name}: {secs:.4f}")

        msg = (
            f"Optimization complete for {selected_data['name']}!\n\n"
            f"--- IMPROVEMENT SUMMARY ---\n"
            f"Before: MAE = {baseline_mae:.2f} min, RMSE = {baseline_rmse:.2f} min\n"
            f"After:  MAE = {after_mae:.2f} min, RMSE = {after_rmse:.2f} min\n"
            f"MAE Improvement: {improvement_pct:.1f}% better\n\n"
            f"--- Optimized Parameters ---\n"
            f"Fajr Angle: {baseline_params[0]:.1f}\u00b0 \u2192 {opt_result.fajr_angle}\n"
            f"Isha Angle: {baseline_params[1]:.1f}\u00b0 \u2192 {opt_result.isha_angle}\n"
            f"Temp: {baseline_params[4]:.1f}\u00b0C \u2192 {opt_result.temp}\u00b0C\n"
            f"Pressure: {baseline_params[5]:.0f} mb \u2192 {opt_result.pressure} mb\n\n"
            f"--- Offsets (minutes) ---\n"
            f"  Fajr: {baseline_offsets['fajr_offset']:.1f} \u2192 {opt_result.offsets.get('fajr_offset', 0.0):.1f}\n"
            f"  Shurooq: {baseline_offsets['shurooq_offset']:.1f} \u2192 {opt_result.offsets.get('shurooq_offset', 0.0):.1f}\n"
            f"  Dhuhr: {baseline_offsets['dhuhr_offset']:.1f} \u2192 {opt_result.offsets.get('dhuhr_offset', 0.0):.1f}\n"
            f"  Asr: {baseline_offsets['asr_offset']:.1f} \u2192 {opt_result.offsets.get('asr_offset', 0.0):.1f}\n"
            f"  Maghrib: {baseline_offsets['maghrib_offset']:.1f} \u2192 {opt_result.offsets.get('maghrib_offset', 0.0):.1f}\n"
            f"  Isha: {baseline_offsets['isha_offset']:.1f} \u2192 {opt_result.offsets.get('isha_offset', 0.0):.1f}\n\n"
            f"--- Coordinates ---\n"
            f"Original: {original_lat:.5f}, {original_lon:.5f}\n"
            f"Before: {baseline_params[2]:.5f}, {baseline_params[3]:.5f}\n"
            f"After: {opt_result.latitude}, {opt_result.longitude}\n"
            f"Distance moved: {opt_result.distance_moved_km:.3f} km\n\n"
            f"--- Per-Prayer Error (MAE: Before \u2192 After) ---\n"
            + "\n".join(
                f"  {p}: {baseline_per_prayer_mae[p]:.2f} \u2192 {after_per_prayer_mae[p]:.2f} min"
                for p in PRAYER_NAMES
            )
            + f"\n\n({opt_result.n_function_evals} evals in {opt_result.duration_seconds:.1f}s)"
            + (
                f"\n\n--- Adaptive Detection ---\n{opt_result.adaptive_notes}"
                if opt_result.adaptive_notes
                else ""
            )
        )

        print(f"--- Showing results dialog at {datetime.datetime.now()} ---")
        try:
            parent_window = self.root
        except AttributeError:
            parent_window = None

        result_action = ask_optimization_result_dialog(
            parent_window, "Optimization Complete", msg
        )
        print(
            f"--- Dialog closed, result: {result_action} at {datetime.datetime.now()} ---"
        )
        total_runtime = (
            datetime.datetime.now() - optimization_started_at
        ).total_seconds()
        print(f"Total runtime: {total_runtime:.1f}s")

        stage1_only_output = _is_stage1_output(opt_result)

        if result_action == "city":
            print("Applying changes to current city...")
            updated_city_ids = []
            for i, loc in enumerate(self.locations_data):
                if loc["name"] == selected_data["name"]:
                    _apply_optimization_result_to_location(
                        self.locations_data[i],
                        opt_result,
                        stage1_only=stage1_only_output,
                        apply_coordinates=True,
                    )
                    updated_city_ids.append(self.locations_data[i].get("id"))
                    break
            try:
                rewrite_location_file(self)
            except TypeError:
                rewrite_location_file()
            if hasattr(self, "rebuild_city_rmse_for_ids"):
                try:
                    self.rebuild_city_rmse_for_ids(updated_city_ids)
                except (ValueError, TypeError, KeyError, RuntimeError, OSError):
                    pass
            if hasattr(self, "filter_list"):
                try:
                    self.filter_list()
                except (ValueError, TypeError, KeyError, RuntimeError, OSError):
                    pass
            self.on_city_select(None)

        elif result_action == "country":
            print("Applying changes to whole country...")
            current_country_code = selected_data.get("country_code")
            if not current_country_code:
                messagebox.showwarning(
                    "Cannot Apply to Country",
                    "Could not determine country code. Changes not applied.",
                )
                return
            applied_count = 0
            updated_city_ids = []
            for i, loc in enumerate(self.locations_data):
                if loc.get("country_code") == current_country_code:
                    _apply_optimization_result_to_location(
                        self.locations_data[i],
                        opt_result,
                        stage1_only=stage1_only_output,
                        apply_coordinates=False,
                    )
                    updated_city_ids.append(self.locations_data[i].get("id"))
                    applied_count += 1
                    if loc["name"] == selected_data["name"]:
                        _apply_optimization_result_to_location(
                            self.locations_data[i],
                            opt_result,
                            stage1_only=False,
                            apply_coordinates=True,
                        )
            print(
                f"Applied settings to {applied_count} cities with "
                f"country code {current_country_code}."
            )
            try:
                rewrite_location_file(self)
            except TypeError:
                rewrite_location_file()
            if hasattr(self, "rebuild_city_rmse_for_ids"):
                try:
                    self.rebuild_city_rmse_for_ids(updated_city_ids)
                except (ValueError, TypeError, KeyError, RuntimeError, OSError):
                    pass
            if hasattr(self, "filter_list"):
                try:
                    self.filter_list()
                except (ValueError, TypeError, KeyError, RuntimeError, OSError):
                    pass
            self.on_city_select(None)

        elif result_action == "ignore" or result_action is None:
            print("Ignoring optimization changes.")
        else:
            print(f"Warning: Unexpected dialog result '{result_action}'")

    # Kick off the background thread — returns immediately so main thread is free
    threading.Thread(target=_run_in_background, daemon=True).start()
