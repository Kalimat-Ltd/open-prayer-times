# API Guide

## 0) Concepts first

Read [parameter_glossary.md](parameter_glossary.md) before using optimizer/calculator APIs.

## 1) Application-layer APIs

### `optimize_city_from_reference`

- Module: `src/app/application/optimization_use_case.py`
- Purpose: optimize one city using a reference schedule and return `OptimizationResult`

Parameters:

| Name | Type | Description |
|------|------|-------------|
| `city_name` | `str` | Location name as in `locations.csv` |
| `reference_file` | `Path` | Path to reference prayer-time file |
| `loc_csv_path` | `Path` | Path to `locations.csv` |
| `timezone_offset_hours` | `float` | UTC offset hours |
| `tz_name` | `str | None` | IANA timezone name |

### `estimate_city_angles_from_reference`

- Module: `src/app/application/optimizer_service.py`
- Purpose: quick angle estimation from reference data — returns approximate `(fajr_angle, isha_angle)` without running the full multistage pipeline. Useful for getting a reasonable starting point before full optimization

Parameters:

| Name | Type | Description |
|------|------|-------------|
| `city_name` | `str` | Location name as in `locations.csv` |
| `reference_file` | `Path` | Path to reference prayer-time file |
| `loc_csv_path` | `Path` | Path to `locations.csv` |
| `timezone_offset_hours` | `float` | UTC offset hours |

Returns: `Optional[Tuple[float, float]]` — `(fajr_angle, isha_angle)` or `None` if estimation fails.

### `calculate_city_day_from_loc_csv`

- Module: `src/app/application/use_cases.py`
- Purpose: compute one city-day's prayer times using the parameters stored in `locations.csv`. This is the same calculation the GUI performs when you click on a city

Parameters:

| Name | Type | Description |
|------|------|-------------|
| `city_name` | `str` | Location name as in `locations.csv` |
| `target_date` | `datetime.date` | The date to calculate for |
| `loc_csv_path` | `Path` | Path to `locations.csv` |

Returns: `PrayerCalculationResult` — a dataclass with a `.times` dict (keys: `fajr`, `shurooq`, `dhuhr`, `asr`, `maghrib`, `isha`), a `.method_used` dict, and an optional `.error` string.

## 2) Infrastructure-layer APIs

### `run_multistage_optimization`

- Module: `src/app/infrastructure/optimizer/multistage/pipeline.py`
- Purpose: full Stage 1 → Stage 2 → Stage 3 optimization
- Returns: `OptimizationResult`

Key inputs:

| Name | Description |
|------|-------------|
| `location_data` | City parameter dict |
| `reference_times` | `date -> prayer map` |
| `available_dates` | Dates used for optimization |
| `tz_name` | IANA timezone (optional) |
| `stage1_config`, `stage2_config`, `stage3_config` | Optional stage config dataclasses |

### Stage-level internals (advanced)

These are internal optimizer components but useful for advanced workflows:

- `optimize_pure_astronomical_core(...) -> tuple[PipelineContext, Stage1Diagnostics]`
- `optimize_high_latitude_parameters(..., context: PipelineContext) -> Stage2Diagnostics`
- `optimize_correction_layers(..., context: PipelineContext) -> Stage3Diagnostics`

### `calculate_prayer_times`

- Module: `src/app/infrastructure/prayer_calculator.py`
- Purpose: core prayer-time computation engine (PyEphem-based). This is the low-level function that all higher-level APIs ultimately call. It takes raw numeric parameters (lat, lon, angles, offsets, etc.) and returns calculated times for a single date

For typed call construction, use `PrayerCalculationRequest` (`src/app/domain/models.py`) which bundles all required parameters into a single object — then pass its fields to `calculate_prayer_times`.

## 3) Presentation entry points

### GUI

```bash
python -m src.app.presentation.gui_app
```

### CLI: day calculation

```bash
python -m src.app.presentation.cli --city "Egypt, Cairo" --date 2025-01-15
```

## 4) Programmatic examples

### 4.1 Application-level optimization

```python
from pathlib import Path
from src.app.application.optimization_use_case import optimize_city_from_reference

result = optimize_city_from_reference(
    city_name="Egypt, Cairo",
    reference_file=Path("reference/EG/egypt_cairo.txt"),
    loc_csv_path=Path("resources/locations.csv"),
    timezone_offset_hours=2,
)

print(result.mae_total, result.rmse_total)
```

### 4.2 Direct multistage pipeline

```python
from src.app.infrastructure.optimizer.multistage.pipeline import run_multistage_optimization

result = run_multistage_optimization(
    location_data=location_data,
    reference_times=reference_times,
    available_dates=available_dates,
  tz_name="Africa/Cairo",
)

print(result.calculation_method, result.asr_madhab)
```

## 5) Integration guidance

- Prefer application-layer APIs for external integrations
- Treat stage functions as advanced/internal APIs
- Keep reference data quality high; optimizer quality is reference-bound
- Re-check docs when stage contracts evolve
