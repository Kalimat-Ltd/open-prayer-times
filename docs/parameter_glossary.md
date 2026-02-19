# Parameter Glossary and Core Concepts

This page defines the key parameters and concepts used across calculation and optimization.

## 1) Core Astronomical Parameters

- `fajr_angle`:
  Solar depression angle (degrees) used to compute Fajr. Higher absolute angle generally shifts Fajr earlier.
- `isha_angle`:
  Solar depression angle (degrees) used to compute Isha when fixed-minute mode is not active.
- `isha_minutes`:
  Fixed-minute Isha mode. When non-zero, Isha is computed relative to Maghrib by minutes instead of `isha_angle`.
- `latitude`, `longitude`:
  City coordinates used for all solar calculations.
- `optimized_lat`, `optimized_lon`:
  Calibrated coordinates produced by optimization (typically Stage 1).

## 2) Method and Jurisprudence Parameters

- `calculation_method`:
  Main astronomical mode. Runtime normalized values are:
  - `angle_based`
  - `moonsighting`
- `asr_madhab`:
  Asr juristic mode:
  - `0` = Standard
  - `1` = Hanafi
- `isha_shafaq`:
  Moonsighting twilight variant (`general`, `ahmer`, `abyad`).

## 3) High-Latitude Parameters

At high latitudes (roughly above 48°), the sun may not dip far enough below the horizon for standard Fajr/Isha angles to produce meaningful times — or may not set/rise at all during polar summer. The parameters below control how the calculator handles these situations.

- `high_lat_method`:
  Fallback strategy used when astronomical Fajr or Isha cannot be computed:
  - `0` = **Angle-Based Fraction** — proportions the available night by the ratio of the requested angle to a reference angle.
  - `1` = **One Seventh of Night** — divides sunset-to-sunrise into sevenths; Isha = sunset + 1/7, Fajr = sunrise − 1/7.
  - `2` = **Midnight** — Isha and Fajr are placed symmetrically around solar midnight.
  - `3` = **Aqrab Al-Bilad** — borrows prayer times from the nearest lower-latitude city where standard calculation works.
- `high_lat_start_date`, `high_lat_end_date`:
  Calendar window (YYYY-MM-DD) where high-latitude handling is active. Outside this window the calculator uses standard astronomical computation. Typically set to the seasonal period when the city experiences problematic twilight.
- `isha_harag`:
  Post-calculation Isha capping for cities where Isha would otherwise fall unreasonably late, even outside the high-lat window:
  - `0` = off (default — no capping).
  - `1` = cap Isha at the summer-solstice sunset time + 65 minutes.
  - `2` = take the earlier of the 15° solar depression time or one-seventh of night.
  - `3` = hard cap Isha at 23:00 local time.
- `custom_fajr_angle`, `custom_isha_angle`:
  Optional custom angles used by Stage 2 when high-lat improvement requires explicit angle override.
- `high_lat_fallback_method`:
  Fallback method used when custom high-lat handling cannot be applied for a date.

## 4) Correction Parameters

- Per-prayer offsets:
  `fajr_offset`, `shurooq_offset`, `dhuhr_offset`, `asr_offset`, `maghrib_offset`, `isha_offset`
  These are additive minute corrections applied after astronomical computation.
- `clock_offsets`:
  JSON-encoded reference clock-shift blocks (e.g., DST-style periods) discovered during optimization and applied as date-window offsets.
- `residual_corrections`:
  JSON-encoded harmonic residual model (`PrayerResidualModel`) learned on unstable periods.  The JSON payload embeds explicit `active_month_day_ranges`; corrections are applied **only** on dates within those ranges.  If the payload has no ranges, the model produces zero corrections everywhere — no fallback is attempted.

## 5) Environment Parameters

- `elevation`:
  Elevation (meters), mainly affects sunrise/sunset geometry and downstream prayer timing.
- `temp`:
  Air temperature (°C), used in atmospheric refraction adjustments.
- `pressure`:
  Pressure (mbar), also used in refraction adjustments.

## 5.1) Practical Parameter Impact by Prayer

This section is a practical guide for how parameters usually shift prayer times during optimization.

### Parameter-level effects (directional)

- `latitude`:
  Affects all prayers except Dhuhr.
  Higher latitude generally gives later Fajr/Shurooq and earlier Asr/Maghrib/Isha.
- `longitude`:
  Affects all prayers.
  Higher longitude shifts all prayers earlier.
- `fajr_angle`:
  Affects Fajr.
  Bigger angle gives earlier Fajr.
- `isha_angle`:
  Affects Isha.
  Bigger angle gives later Isha.
- `asr_madhab`:
  Affects Asr.
  Hanafi gives later Asr.
- `temp`:
  Slightly affects mainly Shurooq and Maghrib.
  Higher temperature tends to give later Shurooq and earlier Maghrib.
- `pressure`:
  Slightly affects mainly Shurooq, Asr, and Maghrib.
  Higher pressure tends to give earlier Shurooq, slightly later Asr, and later Maghrib.
- `elevation`:
  Affects mainly Shurooq, Maghrib, and Isha.
  Higher elevation tends to give later Shurooq, earlier Maghrib, and earlier Isha.

### Prayer-level dependency summary

- **Fajr**: `latitude`, `longitude`, `fajr_angle`
- **Shurooq**: `latitude`, `longitude`, `temp`, `pressure`, `elevation`
- **Dhuhr**: `longitude` is the core driver. In some countries/methodologies, a fixed **safety buffer** is added after calculated zawal (solar noon) to avoid entering prayer at the edge of **وقت النهي**; this shifts published Dhuhr later than pure astronomical noon.
- **Asr**: `latitude`, `longitude`, `asr_madhab`, `pressure`
- **Maghrib**: `latitude`, `longitude`, `temp`, `pressure`, `elevation`
- **Isha**: `latitude`, `longitude`, `isha_angle`, `elevation`

### Why optimized environment values can look unrealistic

Sometimes optimized `temp`, `pressure`, or `elevation` values may look unrealistic for the real city.

This is expected in a reference-matching optimizer: the goal is to reproduce the published reference times as closely as possible, but reference providers may use unknown internal conventions (different rounding, hidden buffers, seasonal rules, local policy decisions, or unexposed correction layers).

Because we only observe final published times (not the provider's full internal model), the optimizer may use environment variables as effective tuning knobs within configured bounds to absorb systematic differences. In other words, these values can function as calibration proxies, not literal meteorological truth.

Operational principle:

- prioritize closeness to trusted reference schedules,
- keep parameters within configured physical search limits,
- and treat optimized environment values as model-fit parameters when necessary.

## 6) Stable vs Unstable Days

This distinction is foundational to the multistage optimizer.

- **Stable days**:
  Dates whose residual behavior is clean and consistent after artifact filtering and core re-fitting.
  They are used to fit structural astronomy (angles/method/geo/environment) and constant prayer offsets.
  In practice these are dates where the calculated prayer times closely match reference data once the right core parameters are in place — typically the majority of the year outside difficult seasonal windows.

- **Unstable days**:
  Dates that remain difficult after Stage 1 core fitting (often seasonal/high-latitude windows).
  They are handled by Stage 2 (high-lat adaptation) and optionally Stage 3 residual corrections.
  Common causes: polar twilight periods, DST transition dates, dates where the reference source used a different calculation convention.

Why this split exists:

- Fitting core astronomical parameters (latitude, longitude, angles) on *all* dates — including ones affected by clock shifts or seasonal anomalies — would distort the fit. The optimizer would try to find a single angle that works for both normal and abnormal dates, producing a compromise that is wrong for both.
- By isolating stable days for core fitting, the resulting parameters are globally meaningful and accurate for the majority of the year.
- Specialized correction layers (Stage 2 high-lat methods, Stage 3 residual harmonics) can then target only the unstable windows without contaminating the core fit.

## 7) Pipeline Runtime Data Model

The production pipeline now threads a mutable `PipelineContext` across all stages:

1. Stage 1 creates context with core results + baseline corrections.
2. Stage 2 mutates only high-latitude fields when accepted.
3. Stage 3 may add residual payloads/evaluation metadata when accepted.

Each stage also returns lightweight diagnostics dataclasses:

- `Stage1Diagnostics`
- `Stage2Diagnostics`
- `Stage3Diagnostics`
