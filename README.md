# Open Prayer Times

Open Prayer Times is an open-source Python application that computes daily prayer times and calibrates city-specific parameters against authoritative reference schedules.

It uses a multi-stage optimization pipeline to fit city parameters from reference data, producing accurate prayer times for cities worldwide.

## Features

- **Prayer-time calculation** — astronomical engine (PyEphem) with angle-based and moonsighting modes, high-latitude handling, and environmental parameters.
- **Multi-stage optimizer** — three-stage pipeline (astronomical core → high-latitude adaptation → correction layers) for reference-driven city calibration.
- **GUI application** — tkinter desktop app for browsing cities, calculating prayer times, running single-city optimization, and batch country optimization.
- **CLI tools** — command-line interfaces for day calculation and city optimization.
- **Reference-driven workflows** — per-country datasets in `reference/` for validation and fitting.
- **Automated test suite** — regression and integration tests for calculator, optimizer stages, and GUI-related flows.

## Background: Why This Project Exists

Computing prayer times may seem straightforward — the underlying astronomy is well understood — but in practice, producing times that match what communities actually use is surprisingly difficult. This section explains the real-world challenges the project is designed to address.

### 1. No single global standard

There is no universal authority for prayer times. Each country, organization, and sometimes individual mosque may use its own combination of angles, calculation method, and post-processing rules. In many non-Muslim-majority countries there is no official ministry of religious affairs or awqaf producing a national schedule, so mosques in the same neighborhood — even on the same street — may display different prayer times. This project tries to identify the most widely used schedule in each region (typically from the largest or most recognized mosques or Islamic centers), treat it as the reference, and calibrate parameters that reproduce it. Those parameters are then applied country-wide to cities without their own reference data.

### 2. High-latitude difficulties

At higher latitudes (roughly above 48°N or below 48°S), the sun may not dip far enough below the horizon for standard Fajr and Isha angles to produce a valid result. This is especially pronounced between late March and late October in the northern hemisphere (and the opposite months in the south). During these periods, alternative approximation methods — such as angle-based fractions, one-seventh of night, or midnight rules — are commonly used. The optimizer's Stage 2 automatically detects these problematic windows and evaluates which high-latitude method best reproduces the reference schedule.

### 3. Extreme latitudes

At extreme latitudes (roughly above 64°), the sun may not set or rise at all for weeks or months. In these cases, approximation is needed not only for Fajr and Isha but sometimes for Maghrib and Shurooq as well. Communities in these regions typically follow schedules borrowed from lower-latitude cities or use highly simplified rules. The optimizer handles these scenarios within the same high-latitude framework but with broader fallback coverage.

### 4. Unknown calculation methods

Countries and mosques publish prayer times, but they rarely disclose exactly how those times were calculated. Some may use non-standard conventions — custom rounding rules, hidden safety buffers, seasonal adjustments, or proprietary formulas that are not documented anywhere. The problem compounds at high latitudes, where the element of approximation adds another layer of unknowns: how exactly did the authority approximate Fajr or Isha during twilight-less nights? This is the core reason this project exists. Rather than guessing which method an authority uses, the optimizer systematically searches for the combination of parameters that best reproduces the published times — even when the underlying method is completely unknown.

## Reference Methodology Notes

- The project supports both angle-based and moonsighting workflows.
- A commonly used public moonsighting reference source is: https://www.moonsighting.com/
- Different providers may publish different conventions; optimizer quality depends on consistent reference methodology per city.

## Installation

**Requirements:** Python 3.10+ (minimum). Tested on Python 3.12.

```bash
# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

## Quick Start

You can run commands as Python modules (works directly from source):

```bash
# GUI
python -m src.app.presentation.gui_app

# Day calculation CLI
python -m src.app.presentation.cli --city "Russia, Kazan" --date 2025-01-15

# Optimization CLI
python -m src.app.presentation.optimizer_cli \
  --city "Russia, Kazan" \
  --reference-file reference/RU/russia_kazan.txt \
  --timezone-offset 3
```

After installing the package, script entry points are also available:

```bash
open-prayer-times --city "Russia, Kazan" --date 2025-01-15
open-prayer-times-optimize --city "Russia, Kazan" --reference-file reference/RU/russia_kazan.txt --timezone-offset 3
open-prayer-times-gui
```

## Project Structure

```
src/app/
├── domain/          # Core models, constants, time utilities (pure logic, no I/O)
├── application/     # Use-case orchestration (calculate city day, run optimization)
├── infrastructure/  # Repositories, calculator, optimizer pipeline, residual model
│   └── optimizer/
│       └── multistage/  # Stage 1/2/3 pipeline implementation
└── presentation/    # GUI (tkinter), CLI entry points, controller
    └── gui/         # Decomposed GUI modules (method-binder pattern)

reference/    # Per-country reference prayer-time files (tab-separated .txt)
resources/    # Source datasets (country_codes.csv, worldcities.csv)
data/         # Generated/processed datasets
tools/        # Operational utility scripts (data ingestion, comparison)
tests/        # Automated test suite (includes tests/fixtures/ baselines)
docs/         # Architecture and implementation documentation
```

## Architecture

The codebase follows a layered architecture with clear separation of concerns:

| Layer | Path | Responsibility |
|-------|------|---------------|
| **Domain** | `src/app/domain/` | Pure models (`PrayerCalculationRequest`, `PrayerCalculationResult`, optimization models), constants, time utilities |
| **Application** | `src/app/application/` | Use-case orchestration that wires domain and infrastructure for GUI/CLI flows |
| **Infrastructure** | `src/app/infrastructure/` | PyEphem calculator, CSV repositories, multistage optimizer, residual model |
| **Presentation** | `src/app/presentation/` | Tkinter GUI, CLI entry points, controller/service adapters |

## Optimization Pipeline

Production optimization runs through `run_multistage_optimization(...)` in `src/app/infrastructure/optimizer/multistage/pipeline.py`.

Before stage details, see the shared terminology guide: [docs/parameter_glossary.md](docs/parameter_glossary.md).

- **Stage 1 — Astronomical core + normalization**
  - calibrates core parameters (coordinates, fajr/isha angles, method behavior),
  - performs geographic and environmental calibration,
  - detects and normalizes reference clock-shift blocks,
  - fits stable-date prayer offsets,
  - outputs the baseline parameter set used by downstream stages.
- **Stage 2 — High-latitude adaptation**
  - runs only when Stage 1 flags problematic date ranges,
  - evaluates high-latitude candidates,
  - applies changes only when problematic-window quality improves.
- **Stage 3 — Correction layers**
  - optionally fits residual harmonic corrections on unstable periods,
  - accepts residual layers only when gain thresholds are met.

Internal orchestration uses a typed mutable `PipelineContext` plus per-stage diagnostics dataclasses (`Stage1Diagnostics`, `Stage2Diagnostics`, `Stage3Diagnostics`) instead of large dict handoffs.

## Documentation

| Document | Description |
|----------|-------------|
| [docs/parameter_glossary.md](docs/parameter_glossary.md) | Definitions for core parameters and concepts (fajr/isha angles, residual model, clock offsets, stable vs unstable days) |
| [docs/overview.md](docs/overview.md) | Project overview and architecture summary |
| [docs/optimizer_architecture.md](docs/optimizer_architecture.md) | Multi-stage optimizer design |
| [docs/stage1_optimizer.md](docs/stage1_optimizer.md) | Stage 1 (astronomical core + clock/offset normalization) details |
| [docs/stage2_optimizer.md](docs/stage2_optimizer.md) | Stage 2 (high-latitude adaptation) details |
| [docs/stage3_optimizer.md](docs/stage3_optimizer.md) | Stage 3 (corrections) details |
| [docs/gui_architecture.md](docs/gui_architecture.md) | GUI layout, user workflows, and module decomposition |
| [docs/data_formats.md](docs/data_formats.md) | Data file format reference |
| [docs/api_guide.md](docs/api_guide.md) | Programmatic API guide |

## GUI Workflows (User Guide)

For day-to-day GUI usage, see [docs/gui_architecture.md](docs/gui_architecture.md), including:

- how to calculate prayer times for a city,
- how to add/modify city parameters,
- how to add/manage reference files,
- how single-city optimization works,
- how batch (country-level) optimization works.

Reference-file format details are in [docs/data_formats.md](docs/data_formats.md).

## Current Limitations and Contribution Needs

- Current reference coverage is limited: `reference/` currently contains datasets for about **81 countries**.
- Many countries/cities are still missing reference files.
- Optimization quality is fundamentally limited by reference-data quality and coverage.

High-impact contributions:

1. Add reliable reference schedules for new cities/countries.
2. Improve existing files with more accurate and complete yearly coverage.
3. Include source notes (organization/methodology) when submitting new datasets.

See [docs/data_formats.md](docs/data_formats.md) for required file format and [CONTRIBUTING.md](CONTRIBUTING.md) for contribution workflow.

## Development

```bash
# Run full test suite
python -m pytest tests/ -q

# Focused checks
python -m pytest tests/test_multistage_stage1_paris.py -v
python -m pytest tests/test_batch_gui_field_application.py -v

# Type checking
pip install pyright
pyright src/
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and pull request guidance.

## License

This project is licensed under the [MIT License](LICENSE).