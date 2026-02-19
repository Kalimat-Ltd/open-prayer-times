# Stage 2 Optimizer Documentation

## 0) Concepts first

Read [parameter_glossary.md](parameter_glossary.md), especially:

- high-latitude parameters (`high_lat_method`, `isha_harag`, custom angles)
- stable vs unstable/problematic day split

## 1) Purpose

Stage 2 is the **high-latitude adaptation** stage.

It runs only when Stage 1 marks problematic windows (`excluded_date_ranges`) and tries to improve those windows without harming the stable baseline behavior.

## 2) Entry point and contract

- Function: `optimize_high_latitude_parameters(...)`
- File: `src/app/infrastructure/optimizer/multistage/stage2.py`

Signature uses typed context:

- input: `context: PipelineContext` (already populated by Stage 1)
- output: `Stage2Diagnostics`
- side effect: mutates `context` high-latitude fields when accepted

## 3) Inputs

- Stage 1 context (`PipelineContext`) including core params and problematic date ranges
- location metadata and timezone context
- reference times and date set
- optional `Stage2Config`

## 4) What Stage 2 evaluates

Stage 2 searches for the best combination of high-latitude handling parameters by testing each option against the problematic date windows identified by Stage 1. The search space includes:

- `high_lat_method` — which fallback algorithm to use (Angle-Based Fraction, One Seventh, Midnight, Aqrab Al-Bilad).
- `isha_harag` — Isha capping mode (off, solstice+65min, min(15°/one-seventh), 23:00 cap).
- optional `custom_fajr_angle` / `custom_isha_angle` — explicit angle overrides for the problematic window that differ from the Stage 1 global angles.
- optional `high_lat_fallback_method` — secondary fallback when the primary high-lat method cannot produce a result for a specific date.

Each combination is evaluated by computing prayer times for the problematic dates and comparing them against the reference schedule. The combination that produces the lowest MAE on those dates wins.

## 5) Acceptance model

Stage 2 compares the problematic-window MAE **before** (using Stage 1 baseline settings) and **after** (using the best high-lat candidate). This ensures the high-lat adaptation is only applied when it actually helps:

- If `require_mae_improvement=True` (default), the candidate must produce a strictly lower MAE on the problematic dates than the Stage 1 baseline.
- If the improvement is too small or the problematic date count is below `min_problematic_days`, Stage 2 is skipped entirely and Stage 1 settings are preserved.

## 6) Context mutations and diagnostics

### 6.1 `PipelineContext` fields Stage 2 can update

- `high_lat_method`
- `isha_harag`
- `high_lat_start_date`
- `high_lat_end_date`
- `custom_fajr_angle`
- `custom_isha_angle`
- `high_lat_fallback_method`

### 6.2 `Stage2Diagnostics`

- `ran`, `accepted`, `reason`
- `problematic_dates_count`, `safe_dates_count`
- `problematic_mae_before`, `problematic_mae_after`
- `step_timings`

## 7) Stage2Config reference

`Stage2Config` in `src/app/infrastructure/optimizer/multistage/shared.py`:

- `candidate_methods`
- `candidate_harag_values`
- `min_problematic_days`
- `require_mae_improvement`
- `optimize_custom_angles`
- `custom_angle_min_deg`
- `custom_angle_max_deg`
- `custom_angle_grid_points`
- `custom_angle_improvement_threshold`

## 8) Operational guidance

- Keep candidate grids practical for production runtime.
- Treat Stage 2 as a targeted seasonal/high-lat correction layer, not a replacement for Stage 1 astronomy.
- Validate problematic-window gain and whole-year stability together.
