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
  - `angle_based` — uses fixed solar depression angles (e.g. 18° for Fajr, 17° for Isha) throughout the year, regardless of latitude or season. The specific angles are city-specific parameters fitted by the optimizer.
  - `moonsighting` — uses the [Moonsighting.com](https://www.moonsighting.com) method, which derives Fajr and Isha from empirical twilight observations curve-fitted into formulas that are functions of *latitude and season* rather than a single fixed angle. Despite the name, this is a calculation method — not traditional naked-eye moon observation. At the equator the effective angle is roughly 18°, but it varies continuously as latitude and day-of-year change. At latitudes between 55°–60° where a fixed angle would produce hardship times, the method automatically applies a one-seventh-of-night rule. Above 60°, calculations are anchored to the equivalent of 60° latitude. The shafaq variant (red *Ahmer* vs. white *Abyad* twilight for Isha) is also selected seasonally by default rather than being fixed by madhab alone.

- `asr_madhab`:
  Asr juristic mode:
  - `0` = Standard
  - `1` = Hanafi
- `asr_madhab_overrides`:
  Optional JSON payload that overrides `asr_madhab` during recurring seasonal windows. Each block uses `MM-DD` month-day boundaries and an explicit override madhab, for example:
  `[{"start":"03-29","end":"10-24","asr_madhab":0}]`

  Runtime precedence is:
  1. use `asr_madhab_overrides` when the target date falls inside a matching block
  2. otherwise use the base `asr_madhab`

  This is additive: it does not replace high-latitude fields, per-prayer offsets, `clock_offsets`, or `residual_corrections`.
- `isha_shafaq`:
  Moonsighting twilight variant — only meaningful when `calculation_method` is `moonsighting`. Controls which twilight criterion defines the end of Isha:
  - `ahmer` — red twilight (Shafaq Ahmer); preferred by Shafi'i, Maliki, Hanbali
  - `abyad` — white twilight (Shafaq Abyad); preferred by Hanafi
  - `general` — seasonal blend: Ahmer in summer (short nights), Abyad in winter, with Abyad→Ahmer transition in spring and reverse in autumn. Used to avoid hardship at higher latitudes where Abyad would give an unreasonably late Isha in summer.

## 3) High-Latitude Parameters

At high latitudes (roughly above 48°), the sun may not dip far enough below the horizon for standard Fajr/Isha angles to produce meaningful times — or may not set/rise at all during polar summer. The parameters below control how the calculator handles these situations.

- `high_lat_method`:
  Fallback strategy used when astronomical Fajr or Isha cannot be computed:
  - `0` = **Angle-Based Fraction** — proportions the available night by the ratio of the requested angle to a reference angle
  - `1` = **One Seventh of Night** — divides sunset-to-sunrise into sevenths; Isha = sunset + 1/7, Fajr = sunrise − 1/7
  - `2` = **Midnight** — Isha and Fajr are placed symmetrically around solar midnight
  - `3` = **Aqrab Al-Bilad** — borrows prayer times from the nearest lower-latitude city where standard calculation works
- `high_lat_start_date`, `high_lat_end_date`:
  Calendar window (YYYY-MM-DD) where high-latitude handling is active. Outside this window the calculator uses standard astronomical computation. Typically set to the seasonal period when the city experiences problematic twilight.
- `isha_harag`:
  Post-calculation Isha capping for cities where Isha would otherwise fall unreasonably late, even outside the high-lat window:
  - `0` = off (default — no capping)
  - `1` = cap Isha at the summer-solstice sunset time + 65 minutes
  - `2` = take the earlier of the 15° solar depression time or one-seventh of night
  - `3` = hard cap Isha at 23:00 local time
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

`asr_madhab_overrides` differs from these correction layers: it switches the Asr juristic rule itself instead of adding minutes after calculation.

## 5) Environment Parameters

- `elevation`:
  Elevation (meters), mainly affects sunrise/sunset geometry and downstream prayer timing.
- `temp`:
  Air temperature (°C), used in atmospheric refraction adjustments.
- `pressure`:
  Pressure (mbar), also used in refraction adjustments.

## 5.5) Reference Year

- `reference_year`:
  The calendar year the reference prayer-time data for a city was most likely sourced from (e.g., `2025`). Stored as an integer string in `locations.csv`.

  Reference files use a `DD-Mon` date format with no embedded year. The parser must inject a year to construct `datetime.date` keys. Using the wrong year introduces a systematic error because solar geometry differs slightly from year to year (different weekday alignment, leap-year presence, etc.).

  `reference_year` is distinct from the GUI **display year** (`year_var`):
  - `reference_year` — the year the reference data was collected; used when loading and comparing reference times for error computation (conclusion tab, RMSE cache, optimizer baseline/after reporting)
  - `year_var` — the year the user has selected in the GUI's Year spinbox; used for displaying calculated prayer times and as a fallback when `reference_year` is not set

  Priority order when the code needs a year for reference-data comparisons:
  1. `reference_year` from the city's `locations.csv` row (most accurate)
  2. `year_var` from the GUI display spinbox
  3. `datetime.date.today().year` (last resort)

  This field can be set manually in the **Modify City** form.

  Setting the correct `reference_year` also reduces spurious `clock_offsets` detections: when the calculator resolves DST transitions using the right year's dates (via `pytz`), its UTC offsets already match the reference source's assumptions, so Stage 1 does not need to compensate with a clock-offset block for those periods.

## Practical Parameter Impact by Prayer

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
- `asr_madhab_overrides`:
  Affects Asr only on matching dates.
  Lets a city use one base madhab for stable days while switching to the other madhab during a recurring seasonal window.
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
- **Dhuhr**: `longitude` is the core driver. In some countries/methodologies, a fixed **safety buffer** is added after calculated zawal (solar noon) to avoid entering prayer at the edge of **وقت النهي**; this shifts published Dhuhr later than pure astronomical noon
- **Asr**: `latitude`, `longitude`, `asr_madhab`, `asr_madhab_overrides`, `pressure`
- **Maghrib**: `latitude`, `longitude`, `temp`, `pressure`, `elevation`
- **Isha**: `latitude`, `longitude`, `isha_angle`, `elevation`

### Why optimized environment values can look unrealistic

Sometimes optimized `temp`, `pressure`, or `elevation` values may look unrealistic for the real city.

This is expected in a reference-matching optimizer: the goal is to reproduce the published reference times as closely as possible, but reference providers may use unknown internal conventions (different rounding, hidden buffers, seasonal rules, local policy decisions, or unexposed correction layers).

Because we only observe final published times (not the provider's full internal model), the optimizer may use environment variables as effective tuning knobs within configured bounds to absorb systematic differences. In other words, these values can function as calibration proxies, not literal meteorological truth.

Operational principle:

- prioritize closeness to trusted reference schedules
- keep parameters within configured physical search limits
- treat optimized environment values as model-fit parameters when necessary

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

- Fitting core astronomical parameters (latitude, longitude, angles) on *all* dates — including ones affected by clock shifts or seasonal anomalies — would distort the fit. The optimizer would try to find a single angle that works for both normal and abnormal dates, producing a compromise that is wrong for both
- By isolating stable days for core fitting, the resulting parameters are globally meaningful and accurate for the majority of the year
- Specialized correction layers (Stage 2 high-lat methods, Stage 3 residual harmonics) can then target only the unstable windows without contaminating the core fit

## 7) Pipeline Runtime Data Model

The production pipeline now threads a mutable `PipelineContext` across all stages:

1. Stage 1 creates context with core results + baseline corrections
2. Stage 2 mutates only high-latitude fields when accepted
3. Stage 3 may add residual payloads/evaluation metadata when accepted

Each stage also returns lightweight diagnostics dataclasses:

- `Stage1Diagnostics`
- `Stage2Diagnostics`
- `Stage3Diagnostics`
