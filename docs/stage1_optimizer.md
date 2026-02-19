# Stage 1 Optimizer Developer Documentation

## 0) Read this first

Before implementation details, read [parameter_glossary.md](parameter_glossary.md) for the canonical definitions of:

- `fajr_angle`, `isha_angle`, `isha_minutes`
- `calculation_method`, `asr_madhab`, `isha_shafaq`
- `clock_offsets`, per-prayer offsets, `residual_corrections`
- stable vs unstable days

## 1) Purpose

Stage 1 is the **astronomical core calibration** stage in the multistage optimizer.

It is responsible for:

1. structural astronomy fit (lat/lon + Fajr/Isha + method behavior)
2. geographic/environment calibration
3. reference clock-shift block normalization
4. stable-date prayer offset fitting

It intentionally does **not** fit residual harmonic corrections (Stage 3 responsibility).

## 2) Entry point and contract

- Function: `optimize_pure_astronomical_core(...)`
- File: `src/app/infrastructure/optimizer/multistage/stage1.py`

Return value is now a typed tuple:

- `PipelineContext` (mutable runtime state for Stage 1 → 2 → 3)
- `Stage1Diagnostics` (lightweight immutable diagnostics)

## 3) Stable-day-first design

Stage 1 separates dates into reliability classes:

- **Stable days** (`dates_used_for_core`) are used for structural fitting
- **Unstable/problematic days** are represented via `excluded_date_ranges` and handled later by Stage 2/3

This avoids overfitting difficult seasonal windows when learning global astronomy parameters.

## 4) Top-level flow

High-level sequence in `optimize_pure_astronomical_core(...)`:

1. **Detect reference clock-shift artifacts** — scan the reference data for date ranges where all prayers are shifted by the same constant amount (e.g., +60 min during DST). These dates are excluded from core fitting to avoid biasing the angle search
2. **Detect non-solar days** — identify dates where astronomical sunrise/sunset cannot be computed normally (polar summer/winter). These are excluded because they require fallback methods, not angle calibration
3. **Build filtered candidate set** — after removing clock-shifted and non-solar dates, assemble the pool of "clean" dates used for structural fitting
4. **Iteratively optimize core params and clean-day selection** — run a robust optimization loop (Huber/Tukey loss) over latitude, longitude, fajr_angle, and isha_angle. After each pass, re-evaluate which dates qualify as "clean" vs. outlier, and repeat until the clean set stabilizes. This avoids fitting core parameters to dates that are actually problematic
5. **Method/shafaq selection** — compare `angle_based` vs `moonsighting` (and shafaq variants) to identify which calculation method best matches the reference data
6. **Optional Asr madhab detection** — if Asr errors are high, test Standard vs. Hanafi Asr to find the better fit
7. **Local MAE angle polish** — fine-grid search around the current fajr_angle and isha_angle to minimize MAE on clean dates
8. **Geographic calibration** — grid-search longitude, then latitude, within a configurable radius to compensate for systematic reference biases
9. **Environmental calibration** — sequential grid search over elevation, temperature, and pressure to fine-tune atmospheric refraction effects
10. **Final quick angle retest** — one more angle polish pass after geographic/environmental changes
11. **Clock-shift block normalization** — build the final `clock_offsets` JSON payload from detected clock-shift windows
12. **Stable-date offset fitting** — compute per-prayer constant minute offsets on stable dates to absorb any remaining systematic bias
13. **Build `PipelineContext` + `Stage1Diagnostics`** — package all fitted parameters, correction payloads, and timing metadata for downstream stages

## 5) Core optimization vector

Stage 1 continuous vector is:

- `params[0]`: latitude
- `params[1]`: longitude
- `params[2]`: `fajr_angle`
- `params[3]`: `isha_angle`

Environmental variables (`elevation`, `temp`, `pressure`) are calibrated in dedicated passes and then propagated to context.

## 6) Stage 1 outputs (current)

### 6.1 `PipelineContext` fields set/updated by Stage 1

- Core: `lat`, `lon`, `fajr_angle`, `isha_angle`, `calculation_method`, `isha_shafaq`, `asr_madhab`
- Environment: `elevation`, `temp`, `pressure`
- Corrections: `offsets`, `offsets_accepted`, `clock_offsets`, `clock_blocks_count`
- Date metadata: `excluded_date_ranges`, `artifact_ignored_dates`, `dates_used_for_core`
- Stage-local metrics used downstream: `stable_mae_before_offsets`, `stable_mae_after_offsets`
- Evaluation/correction data pointers: `reference_times_for_corrections`

### 6.2 `Stage1Diagnostics`

- `loss`
- `geographic_calibration`
- `asr_madhab_detection`
- `method_comparison`
- `step_timings`

## 7) Stage 1 configuration reference

`Stage1Config` lives in `src/app/infrastructure/optimizer/multistage/shared.py`.

### Robust core fit

- `robust_loss_method`, `huber_delta`, `tukey_c`
- `lambda_coord_shift`

### Stable-day detection/refinement

- `clean_day_threshold_minutes`
- `clean_day_lookahead_days`
- `min_clean_core_days`
- `max_refinement_iterations`

### Angle polish

- `enable_final_mae_angle_polish`
- `final_mae_angle_window_deg`
- `final_mae_angle_step_deg`

### Geographic calibration

- `enable_geographic_calibration`
- `geo_search_radius_km`
- `geo_search_grid_points`

### Asr detection

- `enable_asr_madhab_detection`
- `asr_high_error_threshold_minutes`

### Clock normalization and offsets

- `detect_clock_offsets`
- `optimize_prayer_offsets`
- `min_stable_dates_for_offsets`
- `max_dhuhr_asr_offset_minutes`
- `max_other_prayer_offset_minutes`

### Environmental calibration

- `env_search_grid_points`
- `env_elevation_min_m`, `env_elevation_max_m`, `env_elevation_window_m`
- `env_temperature_min_c`, `env_temperature_max_c`
- `env_pressure_min_mbar`, `env_pressure_max_mbar`

## 8) Downstream propagation

Pipeline uses Stage 1 context as the baseline for Stage 2 and Stage 3.

- Stage 2 mutates high-lat fields only when accepted
- Stage 3 consumes Stage 1 offsets/clock metadata and can refine correction payloads

## 9) Recommended verification

Focused tests:

- `tests/test_multistage_stage1_paris.py`
- `tests/test_multistage_stage1_achim_geo.py`
- `tests/test_multistage_stage1_asr_detection_kabul.py`
- `tests/test_multistage_stage1_kabul_asr_madhab.py`
