# Stage 3 Optimizer Documentation

## 0) Concepts first

Read [parameter_glossary.md](parameter_glossary.md), especially:

- `residual_corrections`
- stable vs unstable day semantics

## 1) Purpose

Stage 3 applies **correction layers** on top of the Stage 1/2 baseline output to reduce remaining errors.

Even after core parameter fitting (Stage 1) and high-latitude adaptation (Stage 2), some cities still have per-prayer errors that follow a smooth seasonal pattern — for example, Fajr might be consistently 1–2 minutes late in winter and 1 minute early in summer. Stage 3 addresses this with two mechanisms:

1. **Evaluate Stage 1/2 baseline behavior** on stable and unstable subsets.
2. **Optionally fit residual harmonic corrections** — a Fourier series (sine/cosine curves) per prayer that follows the smooth seasonal error pattern on unstable dates.
3. **Accept residuals only with measurable gain and safety guardrails** — the residual model is applied only on unstable-date windows, so stable dates are not modified by residual corrections.

## 2) Entry point and contract

- Function: `optimize_correction_layers(...)`
- File: `src/app/infrastructure/optimizer/multistage/stage3.py`

Typed contract:

- input: `context: PipelineContext`
- output: `Stage3Diagnostics`
- side effect: may set residual/evaluation fields on `context`

## 3) Processing order

1. Read Stage 1/2-calibrated parameters from `PipelineContext`.
2. Evaluate baseline behavior on stable and full-date subsets.
3. Optionally fit `PrayerResidualModel` on unstable windows.
4. Apply acceptance gates.
5. Persist accepted correction payloads back to `PipelineContext`.

## 4) Context updates and diagnostics

### 4.1 `PipelineContext` fields Stage 3 may update

- `residual_corrections` / `residuals_accepted`
- `reference_times_for_evaluation`
- `residual_active_dates`

### 4.2 `Stage3Diagnostics`

- date counts: `stable_dates_count`, `unstable_dates_count`
- baseline MAE: `all_dates_mae_before_offsets`, `all_dates_mae_after_offsets`
- residual-eval MAE: `stable_mae_before_residual`, `stable_mae_after_residual`, `unstable_mae_before_residual`, `unstable_mae_after_residual`
- `step_timings`

## 5) Acceptance behavior

Residual corrections are accepted only when:

- The unstable-date MAE improves by at least `min_residual_mae_gain` minutes.
- No individual prayer deteriorates on unstable dates by more than `max_unstable_per_prayer_worsen` minutes.
- Each prayer improves on unstable dates by at least `min_unstable_per_prayer_gain` minutes.
- Stable-date accuracy is not significantly degraded (checked via before/after stable MAE comparison).

Application scope guarantee:

- The residual model's JSON payload includes explicit `active_month_day_ranges` derived from the unstable windows detected during optimization.
- **If no active ranges are present in the payload, the model produces zero corrections for every date** — there is no fallback derivation from other fields.
- Outside those ranges (including stable dates), residual correction is zero by design.

If any of these conditions fail, the residual model is discarded and Stage 1/2 baseline behavior is preserved — the city will use only constant offsets, not harmonic corrections.

## 6) Stage3Config reference

`Stage3Config` in `src/app/infrastructure/optimizer/multistage/shared.py`:

### Feature toggle

- `fit_residual_corrections`

### Residual controls

- `max_harmonics`
- `min_unstable_dates_for_residuals`
- `min_residual_mae_gain`
- `min_unstable_per_prayer_gain`
- `max_unstable_per_prayer_worsen`

## 7) Operational guidance

- Use residuals as a targeted unstable-period layer, not a global replacement for core astronomy.
- Keep acceptance conservative; avoid unstable-window overfitting.
- Re-check both unstable gain and stable-date non-regression when tuning thresholds.
