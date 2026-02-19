# Open Prayer Times Overview

Open Prayer Times is an open-source Python application for computing and calibrating daily prayer times for cities worldwide.

The project combines:
- a desktop GUI (tkinter) for city management, prayer-time display, and optimization workflows
- CLI entry points for automation and scripting
- a multi-stage optimizer to calibrate city parameters against trusted reference schedules
- data tooling for reference file ingestion and quality checks

## Read This First: Key Parameters and Concepts

Start with [parameter_glossary.md](parameter_glossary.md). It defines:

- `fajr_angle`, `isha_angle`, `isha_minutes`
- `calculation_method`, `asr_madhab`, `isha_shafaq`
- `high_lat_method`, `isha_harag`, custom high-lat angles
- per-prayer offsets, `clock_offsets`, `residual_corrections`
- stable vs unstable day concepts used by the optimizer

This glossary is the canonical terminology source for all architecture docs.

## Core Goals

- Produce accurate daily prayer times per city using astronomical calculations (PyEphem)
- Fit city-specific parameters from authoritative reference files
- Handle difficult regions (high latitude, seasonal clock shifts, unstable periods)

## Real-World Challenges

Computing prayer times involves well-understood astronomy, but matching the times communities actually use is harder than it appears.

1. **No single global standard.** There is no universal authority. Each country, organization, or mosque may use its own angles, calculation method, and post-processing rules. In non-Muslim-majority countries there is often no official ministry producing a national schedule, so even neighboring mosques may display different times. This project identifies the most widely used schedule per region, treats it as the reference, and calibrates parameters to reproduce it

2. **High-latitude difficulties.** Above roughly 48° latitude, the sun may not reach the depression angles needed for standard Fajr/Isha computation — especially from late March through late October (northern hemisphere). Approximation methods (angle fractions, 1/7 of night, midnight, nearest-city) are commonly used during those windows. Stage 2 of the optimizer detects the problematic dates and evaluates which fallback best matches the reference

3. **Extreme latitudes.** Above roughly 64°, the sun may not set or rise for weeks. Approximation is then needed for Maghrib and Shurooq as well, and communities typically borrow schedules from lower-latitude cities or use simplified rules

4. **Unknown calculation methods.** Authorities publish prayer times without disclosing internal calculations, rounding, safety buffers, or seasonal adjustments. At high latitudes the unknowns multiply because the approximation method is also undisclosed. The optimizer addresses this by searching for the parameter combination that best reproduces the published schedule — regardless of how it was originally computed

## Architecture

The project follows a **layered architecture** under `src/app/`:

| Layer | Path | Responsibility |
|-------|------|---------------|
| **Domain** | `domain/` | Pure models, constants, time/prayer logic |
| **Application** | `application/` | Use-case orchestration for GUI/CLI flows |
| **Infrastructure** | `infrastructure/` | Repositories, calculator, optimizer pipeline, residual model |
| **Presentation** | `presentation/` | GUI (tkinter), CLI entry points, controller |

### Multistage pipeline contract

The three optimizer stages share state through a single mutable `PipelineContext` object that flows from Stage 1 → Stage 2 → Stage 3. Each stage reads the parameters set by previous stages, applies its own changes (if accepted), and passes the context forward. This makes it clear which stage owns which fields and prevents accidental overwrites.

Each stage also returns a lightweight diagnostics dataclass (`Stage1Diagnostics`, `Stage2Diagnostics`, `Stage3Diagnostics`) containing timing, acceptance decisions, and error metrics for debugging and reporting — without cluttering the shared context.

## Main Runtime Paths

| Path | Command |
|------|---------|
| GUI | `python -m src.app.presentation.gui_app` |
| CLI day calculation | `python -m src.app.presentation.cli --city "Egypt, Cairo" --date 2025-01-15` |

## Documentation Map

- [Parameter glossary](parameter_glossary.md)
- [Optimizer architecture](optimizer_architecture.md)
- [Stage 1 optimizer](stage1_optimizer.md)
- [Stage 2 optimizer](stage2_optimizer.md)
- [Stage 3 optimizer](stage3_optimizer.md)
- [GUI architecture](gui_architecture.md)
- [Data formats](data_formats.md)
- [API guide](api_guide.md)

## Practical usage and limits

- GUI usage workflows (window layout, calculation, city management, single/batch optimization, reference file watcher, RMSE cache) are documented in [gui_architecture.md](gui_architecture.md)
- Reference file authoring format is documented in [data_formats.md](data_formats.md)
- Current reference-data coverage is incomplete (about 81 countries in `reference/`), so adding validated datasets for new locations is a key contribution area
