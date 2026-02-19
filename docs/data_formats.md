# Data Formats

## 0) Concepts first

For parameter meaning (angles, high-lat fields, offsets, residuals), see [parameter_glossary.md](parameter_glossary.md).

## 1) `loc.csv`

Primary city parameter dataset used by GUI, CLI, and runtime calculations.

### Field groups

| Category | Fields |
|----------|--------|
| Identity | `id`, `country_code`, `name` |
| Coordinates | `latitude`, `longitude`, `optimized_lat`, `optimized_lon` |
| Environment | `elevation`, `pressure`, `temp`, `timezone` |
| Core Calculation | `fajr_angle`, `isha_angle`, `isha_minutes`, `asr_madhab`, `calculation_method`, `isha_shafaq` |
| High-Latitude | `high_lat_method`, `isha_harag`, `high_lat_start_date`, `high_lat_end_date`, `custom_fajr_angle`, `custom_isha_angle`, `high_lat_fallback_method` |
| Corrections | `residual_corrections`, `clock_offsets` |
| Per-Prayer Offsets | `fajr_offset`, `shurooq_offset`, `dhuhr_offset`, `asr_offset`, `maghrib_offset`, `isha_offset` |
| Flags | `is_official`, `is_optimized` |

### Field notes

- `optimized_lat` / `optimized_lon` — optional latitude/longitude adjustments produced by Stage 1 optimization. When present, the calculator uses these instead of the original `latitude`/`longitude`. This compensates for systematic reference-data biases without overwriting the city's true coordinates.
- `is_optimized` — `1` if the city's parameters have been fitted by the multistage optimizer; `0` or empty if still using manually-entered defaults.
- `is_official` — `1` if this city's parameters are curated/verified by a maintainer. Used as a visual marker; does not affect calculation.
- `isha_shafaq` — only meaningful when `calculation_method` is `moonsighting`. Controls the twilight variant: `general` (default), `ahmer` (red twilight), or `abyad` (white twilight).
- `isha_harag` — post-calculation Isha adjustment for locations where Isha would otherwise fall unreasonably late. Values: `0` = off (default), `1` = cap Isha at summer-solstice sunset + 65 min, `2` = take the earlier of 15° depression or one-seventh of night, `3` = hard cap Isha at 23:00 local.
- `high_lat_method` — which fallback to use when Fajr or Isha cannot be computed astronomically (sun doesn't reach the required angle): `0` = Angle-Based Fraction, `1` = One Seventh of Night, `2` = Midnight, `3` = Aqrab Al-Bilad (nearest-city transfer).
- `high_lat_start_date` / `high_lat_end_date` — YYYY-MM-DD window during which `high_lat_method` is active. Outside this window, standard astronomical calculation is used.
- `residual_corrections` / `clock_offsets` — JSON payloads; see Section 3 below.
- Canonical column order is managed by GUI constants (`FIELD_NAMES` in the presentation layer).
- Many values may be empty/nullable in the source CSV and are normalized to sensible defaults at runtime.

## 2) Reference text files

Location: `reference/<COUNTRY_CODE>/*.txt`

Expected row format: tab-separated, 7 columns:

```text
date\tfajr\tshurooq\tdhuhr\tasr\tmaghrib\tisha
```

Parsing and normalization are handled by infrastructure reference parsing utilities.

### Adding new reference data

Fastest workflow (GUI): in the app, select the city, open the **Reference Times** tab, and click **Create empty reference file**. This auto-creates both the country folder and city file using the expected naming/location pattern.

Manual workflow:

1. Create country folder if needed: `reference/<COUNTRY_CODE>/`
2. Create file name in lowercase country_city style (for example: `country_city.txt`).
3. Add one row per day with **exactly** 7 tab-separated fields:
  - `date`, `fajr`, `shurooq`, `dhuhr`, `asr`, `maghrib`, `isha`
4. Keep time values in local civil time for the target city.
5. Keep prayer labels aligned to project naming (`shurooq`, not `sunrise`).

Example row:

```text
01-Jan	05:31	06:52	12:17	15:25	17:42	19:02
```

### External source note

When adding a new reference file, you must mention the url of where you got this data from.

When using moonsighting-based schedules, document the source and methodology used.
Public reference source example: https://www.moonsighting.com/

Different organizations may use different conventions, so consistency within a city dataset is critical for reliable optimization.

## 3) Correction payload formats

### 3.1 `clock_offsets`

Some reference datasets shift all prayer times by a constant amount during certain date ranges (common causes: DST transitions that the reference source applied but the astronomical engine does not, or systematic rounding conventions that differ between periods). Stage 1 detects these shifts automatically.

Serialized JSON: array of date-window blocks.

```json
[
  {"start": "MM-DD", "end": "MM-DD", "offset": 60}
]
```

- `start` / `end` — calendar day range (month-day, no year; applies each year).
- `offset` — shift in **minutes**, added to all calculated prayer times during the window so they match the reference convention.

Example: `{"start": "03-30", "end": "10-26", "offset": 60}` means "add 60 minutes to every prayer time between 30 Mar and 26 Oct" — a typical summer-time clock offset.

### 3.2 `residual_corrections`

After Stage 1's structural fit and Stage 2's high-latitude adaptation, some date ranges may still show systematic per-prayer errors that follow a smooth seasonal curve. Stage 3 fits a **Fourier harmonic model** (`PrayerResidualModel`) to these residuals — essentially a small periodic correction curve per prayer that varies smoothly over the year.

The JSON payload is written and read by `PrayerResidualModel`'s serialization methods. You should not edit it by hand; the optimizer produces it automatically and the calculator consumes it at runtime. The payload includes harmonic coefficients, active date ranges, and versioning metadata.

## 4) Generated CSVs under `data/`

### Why these exist (instead of always calculating on the fly)

The GUI and CLI calculate prayer times on the fly for a single city/date — that's fast enough for interactive use. But some workflows need to operate on many cities × many dates at once:

- **Batch comparison and benchmarking** — comparing calculated vs. reference times across hundreds of cities and thousands of dates. Doing this on the fly each time would be slow and redundant.
- **Reproducibility** — a CSV snapshot captures the exact state of reference data or calculations at a point in time, making reports and regression checks repeatable.
- **Decoupled tooling** — scripts in `tools/` (parsers, mappers, analyzers) can work directly from CSV files without needing the full application stack running.

### What each file contains

| File | Source | Columns | Purpose |
|------|--------|---------|--------|
| `reference_times.csv` | Parsed from `reference/<CC>/*.txt` by `tools/prayer_times_parser.py` | `year, month, day, city, Fajr, Shurooq, Duhur, Asr, Magrib, Isha, lat, long, area` | Normalized raw reference data. Keeps the original city file name as the `city` column. This is the closest representation to the source truth before any mapping. |
| `all_times.csv` | Built by `tools/map_times_to_cities.py` from `reference_times.csv` (+ official times) | `year, month, day, Fajr, Shurooq, Duhur, Asr, Magrib, Isha, city_id` | Mapped dataset: each row is linked to a `loc.csv` city by numeric `city_id`. Raw text fields (`city`, `lat`, `long`, `area`) are removed. This is the file used by downstream analysis scripts that need to join reference times with city parameters. |

### Why three files instead of one

Each file represents a different stage in the data pipeline:

1. **`reference_times.csv`** = raw parsed reference (stage: ingestion)
2. **`all_times.csv`** = mapped and ID-linked reference (stage: preparation for analysis)

Merging them into a single file would mix input data with output data, make schema changes harder, and increase the risk of accidentally corrupting source truth with calculated values.

## 5) Test fixtures

Fixtures live under `tests/fixtures/` and provide deterministic baseline inputs/outputs for regression tests.
