# Multi-Stage Optimizer Architecture

## Scope

Open Prayer Times uses a multi-stage optimizer to find the best parameters for any given city with reference data. The optimizer takes a city's reference prayer-time schedule and systematically searches for the combination of angles, coordinates, environment settings, and correction layers that minimizes the difference between calculated and reference times, then we can apply these parameters to other cities that we do not have reference for and that is usually better than using generic default parameters.

**Pipeline entry:**
- `run_multistage_optimization(...)` in `src/app/infrastructure/optimizer/multistage/pipeline.py`

## Concepts First

See [parameter_glossary.md](parameter_glossary.md) before reading implementation details.

Most importantly:

- **Stable days** are used for structural fitting
- **Unstable days** are handled by Stage 2 and optional Stage 3 residual modeling

## Runtime Contracts

### Shared context model

Pipeline stages coordinate through `PipelineContext` (`src/app/domain/models.py`).

- Stage 1 initializes core astronomy/environment/correction baselines
- Stage 2 mutates high-latitude fields (only when accepted)
- Stage 3 mutates correction fields (offset/residual payloads and evaluation metadata)

### Stage diagnostics

Each stage returns a lightweight diagnostics dataclass:

- `Stage1Diagnostics`
- `Stage2Diagnostics`
- `Stage3Diagnostics`

## Stage Breakdown

### Stage 1: Astronomical Core

- Fits structural parameters first (coordinates, angles, method behavior)
- Filters artifact/non-solar dates to isolate clean stable days
- Detects both the stable-day base `asr_madhab` and any recurring `asr_madhab_overrides` windows that are justified by Asr-only residuals
- Runs environmental calibration (elevation → temperature → pressure)
- Produces stable baseline parameters, clock-shift blocks, and stable-date offsets

See [stage1_optimizer.md](stage1_optimizer.md) for full details.

### Stage 2: High-Latitude Adaptation

- Runs only when Stage 1 flags problematic date ranges
- Evaluates high-latitude method candidates with acceptance gating
- Preserves Stage 1 settings if no candidate improves quality

See [stage2_optimizer.md](stage2_optimizer.md) for full details.

### Stage 3: Correction Layers

- Optionally fits Fourier residual harmonic corrections on unstable periods
- Acceptance requires net gain thresholds and stability constraints

See [stage3_optimizer.md](stage3_optimizer.md) for full details.

## Shared Support Modules

| Module | Path | Purpose |
|--------|------|---------|
| `shared.py` | `src/app/infrastructure/optimizer/shared.py` | `OptimizationResult` model, helper functions, constants |
| `objective.py` | `src/app/infrastructure/optimizer/objective.py` | Detailed error metrics and objective helpers |
| `robust_loss.py` | `src/app/infrastructure/optimizer/multistage/robust_loss.py` | Huber + Tukey biweight loss functions |
| `shared.py` | `src/app/infrastructure/optimizer/multistage/shared.py` | Stage config dataclasses, math helpers |

## Result Model

The pipeline returns `OptimizationResult` containing:
- fitted parameters (angles, coordinates, environment)
- adaptive method/juristic fields (`calculation_method`, `asr_madhab`, `asr_madhab_overrides`, `isha_shafaq`, high-lat settings)
- aggregate and per-prayer metrics (MAE, RMSE)
- correction payloads (offsets, residual JSON, clock blocks)
- convergence and timing diagnostics

`OptimizationResult` is the only public optimization payload expected by GUI/CLI apply flows.

## Production Principles

- **Stable-day-first fitting** — optimize core parameters (angles, coordinates) on clean dates only, because including dates affected by clock shifts or polar twilight would distort the fit into a compromise that is wrong for both normal and abnormal periods
- **Explicit stage gating** — each stage has quantitative acceptance criteria. A stage's changes are applied only if they produce measurable improvement; otherwise the previous stage's output is preserved unchanged
- **Conservative acceptance** — the default posture is "do no harm." Changes are reverted if they don't measurably improve quality, preventing overfitting to noise in the reference data
- **High-latitude specialization only when needed** — Stage 2 runs only when Stage 1 flags problematic date ranges, avoiding unnecessary complexity for cities where standard calculation already works
- **Correction layers with guardrails** — Stage 3 residual corrections are constrained by gain thresholds and per-prayer deterioration limits to prevent the model from memorizing noise in unstable windows
- **Rounding consistency between optimizer reporting and GUI display** — the internal optimization objective always uses `rounding="off"` (continuous time strings, e.g., `HH:MM:SS`) so the error landscape is smooth and optimizer convergence is not disrupted by minute-boundary quantization. However, the before/after MAE values shown in the optimization result dialog use the same rounding setting as the GUI (`rounding_var`, defaulting to `"nearest"`), so the reported improvement numbers match what the Conclusion tab displays rather than a different scale. `nearest` is the most commonly used rounding method for most prayer time tables available online.
