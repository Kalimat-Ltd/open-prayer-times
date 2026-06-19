"""Multi-stage optimization pipeline orchestrator.

Executes Stage 1 (pure astronomical core) then Stage 2 (high latitude)
when Stage 1 reports problematic date ranges.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

import geopy.distance
import numpy as np

from src.app.domain.models import Stage2Diagnostics
from src.app.infrastructure.optimizer.objective import _compute_detailed_errors
from src.app.infrastructure.optimizer.shared import (
    OFFSET_FIELDS,
    PRAYER_NAMES,
    OptimizationResult,
    _load_residual_model_from_json,
)

from .shared import (
    Stage1Config,
    Stage2Config,
    Stage3Config,
    resolve_timezone_offset_hours,
)
from .stage1 import optimize_pure_astronomical_core
from .stage2 import optimize_high_latitude_parameters
from .stage3 import optimize_correction_layers


def run_multistage_optimization(
    location_data: Dict[str, Any],
    reference_times: Dict,
    available_dates: List,
    tz_name: Optional[str] = None,
    progress_callback=None,
    stage1_config: Optional[Stage1Config] = None,
    stage2_config: Optional[Stage2Config] = None,
    stage3_config: Optional[Stage3Config] = None,
) -> OptimizationResult:
    """Run the multi-stage optimizer (Stage 1 + Stage 2 + Stage 3)."""

    start = time.time()
    timings: Dict[str, float] = {}

    def _merge_step_timings(prefix: str, step_timings: Dict[str, float]):
        for key, value in step_timings.items():
            try:
                timings[f"{prefix}.{key}"] = float(value)
            except (TypeError, ValueError):
                continue

    def _log(msg: str):
        if progress_callback:
            try:
                progress_callback(msg)
            except (ValueError, TypeError, KeyError, RuntimeError, OSError):
                pass

    # ── Stage 1 ───────────────────────────────────────────────────────────
    _log("[multistage] Stage 1: optimizing pure astronomical core")
    stage1_start = time.time()
    context, s1_diag = optimize_pure_astronomical_core(
        location_data=location_data,
        reference_times=reference_times,
        available_dates=available_dates,
        tz_name=tz_name,
        config=stage1_config,
    )
    timings["stage1"] = float(time.time() - stage1_start)
    _merge_step_timings("stage1", s1_diag.step_timings)

    if context.artifact_ignored_dates:
        _log(
            "[multistage] Stage 1 ignored clock-shift artifact dates: "
            f"{context.artifact_ignored_dates}"
        )
    if context.excluded_date_ranges:
        _log("[multistage] Stage 1 excluded date ranges (start -> end):")
        for item in context.excluded_date_ranges:
            _log(
                f"  - {item.get('start')} -> {item.get('end')} ({item.get('days')} days)"
            )

    # ── Initialise high-lat context from location_data baselines ─────────
    context.high_lat_method = int(location_data.get("high_lat_method", 0) or 0)
    context.isha_harag = int(location_data.get("isha_harag", 0) or 0)
    context.custom_fajr_angle = location_data.get("custom_fajr_angle")
    context.custom_isha_angle = location_data.get("custom_isha_angle")
    context.high_lat_fallback_method = location_data.get("high_lat_fallback_method")

    # ── Stage 2 ───────────────────────────────────────────────────────────
    s2_diag = Stage2Diagnostics(reason="not-run")
    if context.excluded_date_ranges:
        _log("[multistage] Stage 2: optimizing high-latitude handling")
        stage2_start = time.time()
        s2_diag = optimize_high_latitude_parameters(
            context=context,
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name=tz_name,
            config=stage2_config,
        )
        timings["stage2"] = float(time.time() - stage2_start)
        _merge_step_timings("stage2", s2_diag.step_timings)
        if s2_diag.ran:
            _log(
                "[multistage] Stage 2 evaluated high-lat candidates; "
                f"accepted={s2_diag.accepted} "
                f"mae_before={s2_diag.problematic_mae_before:.3f} "
                f"mae_after={s2_diag.problematic_mae_after:.3f}"
            )
        else:
            _log(f"[multistage] Stage 2 skipped: {s2_diag.reason}")
    else:
        timings["stage2"] = 0.0

    # ── Stage 3 ───────────────────────────────────────────────────────────
    _log("[multistage] Stage 3: applying correction layers")
    stage3_start = time.time()
    s3_diag = optimize_correction_layers(
        context=context,
        location_data=location_data,
        reference_times=reference_times,
        available_dates=available_dates,
        tz_name=tz_name,
        config=stage3_config,
    )
    timings["stage3"] = float(time.time() - stage3_start)
    _merge_step_timings("stage3", s3_diag.step_timings)
    _log(
        "[multistage] Stage 3 summary: "
        f"clock_blocks={context.clock_blocks_count}, "
        f"offsets_accepted={context.offsets_accepted}, "
        f"residuals_accepted={context.residuals_accepted}"
    )

    # ── Final metrics evaluation ──────────────────────────────────────────
    sample_date = available_dates[0] if available_dates else None
    timezone = resolve_timezone_offset_hours(
        location_data.get("timezone"),
        tz_name=tz_name,
        sample_date=sample_date,
    )

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

    final_offsets = {
        f: float((context.offsets or {}).get(f, 0.0) or 0.0) for f in OFFSET_FIELDS
    }
    final_residual_json = context.residual_corrections
    final_residual_model = _load_residual_model_from_json(final_residual_json)
    eval_reference_times = context.reference_times_for_evaluation or reference_times
    residual_active_dates = set(context.residual_active_dates or [])

    final_eval_start = time.time()
    rmse, mae, per_rmse, per_mae, per_max, per_signed = _compute_detailed_errors(
        params,
        available_dates=available_dates,
        reference_times=eval_reference_times,
        elevation=context.elevation,
        timezone=timezone,
        tz_name=tz_name,
        isha_minutes=0.0,
        offsets=final_offsets,
        extra_calc_kwargs=extra_calc_kwargs,
        residual_model=final_residual_model,
        residual_active_dates=residual_active_dates,
    )
    timings["final_metrics_eval"] = float(time.time() - final_eval_start)

    distance_km = 0.0
    try:
        distance_km = float(
            geopy.distance.geodesic(
                (float(location_data["latitude"]), float(location_data["longitude"])),
                (context.lat, context.lon),
            ).km
        )
    except (ValueError, TypeError, KeyError, RuntimeError, OSError):
        distance_km = 0.0

    notes = (
        "Stage1 core optimization completed. "
        f"Excluded date ranges from core fit: {context.excluded_date_ranges}. "
        f"Ignored clock-shift artifact dates: {context.artifact_ignored_dates}. "
        f"Stage2 high-latitude optimization: reason={s2_diag.reason}. "
        f"Stage3 correction layers: "
        f"clock_blocks={context.clock_blocks_count}, "
        f"offsets_accepted={context.offsets_accepted}, "
        f"residuals_accepted={context.residuals_accepted}."
    )

    convergence = (
        f"multistage-stage1 loss={s1_diag.loss:.3f}; "
        f"core_months={len(context.dates_used_for_core)}; "
        f"excluded_ranges={context.excluded_date_ranges}; "
        f"stage2={s2_diag.reason}; "
        f"stage3_offsets={context.offsets_accepted}; "
        f"stage3_residuals={context.residuals_accepted}"
    )

    timings["total"] = float(time.time() - start)

    return OptimizationResult(
        fajr_angle=context.fajr_angle,
        isha_angle=context.isha_angle,
        latitude=context.lat,
        longitude=context.lon,
        temp=context.temp,
        pressure=context.pressure,
        offsets=final_offsets,
        rmse_total=float(rmse),
        mae_total=float(mae),
        per_prayer_rmse={p: float(per_rmse.get(p, math.inf)) for p in PRAYER_NAMES},
        per_prayer_mae={p: float(per_mae.get(p, math.inf)) for p in PRAYER_NAMES},
        per_prayer_max_error={p: float(per_max.get(p, math.inf)) for p in PRAYER_NAMES},
        per_prayer_signed_mean={p: float(per_signed.get(p, 0.0)) for p in PRAYER_NAMES},
        duration_seconds=float(time.time() - start),
        convergence_info=convergence,
        original_lat=float(location_data["latitude"]),
        original_lon=float(location_data["longitude"]),
        distance_moved_km=distance_km,
        n_function_evals=0,
        auxiliary_cities_used=0,
        asr_madhab=context.asr_madhab,
        asr_madhab_overrides=context.asr_madhab_overrides,
        calculation_method=context.calculation_method,
        isha_shafaq=context.isha_shafaq,
        isha_harag=context.isha_harag,
        high_lat_method=context.high_lat_method,
        high_lat_start_date=context.high_lat_start_date,
        high_lat_end_date=context.high_lat_end_date,
        custom_fajr_angle=context.custom_fajr_angle,
        custom_isha_angle=context.custom_isha_angle,
        high_lat_fallback_method=context.high_lat_fallback_method,
        adaptive_notes=notes,
        residual_corrections=final_residual_json,
        clock_offsets=context.clock_offsets or location_data.get("clock_offsets"),
        phase_timings=timings,
        elevation=context.elevation,
    )
