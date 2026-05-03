# Data Formats

## 0) Concepts first

For parameter meaning (angles, high-lat fields, offsets, residuals), see [parameter_glossary.md](parameter_glossary.md).

## 1) `locations.csv`

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
| Reference | `reference_year` |

### Field notes

- `optimized_lat` / `optimized_lon` — optional latitude/longitude adjustments produced by Stage 1 optimization. When present, the calculator uses these instead of the original `latitude`/`longitude`. This compensates for systematic reference-data biases without overwriting the city's true coordinates
- `is_optimized` — `1` if the city's parameters have been fitted by the multistage optimizer; `0` or empty if still using manually-entered defaults
- `is_official` — `1` if this city's parameters are curated/verified by a maintainer. Used as a visual marker; does not affect calculation
- `isha_shafaq` — only meaningful when `calculation_method` is `moonsighting`. Controls the twilight variant: `general` (default), `ahmer` (red twilight), or `abyad` (white twilight)
- `isha_harag` — post-calculation Isha adjustment for locations where Isha would otherwise fall unreasonably late. Values: `0` = off (default), `1` = cap Isha at summer-solstice sunset + 65 min, `2` = take the earlier of 15° depression or one-seventh of night, `3` = hard cap Isha at 23:00 local
- `high_lat_method` — which fallback to use when Fajr or Isha cannot be computed astronomically (sun doesn't reach the required angle): `0` = Angle-Based Fraction, `1` = One Seventh of Night, `2` = Midnight, `3` = Aqrab Al-Bilad (nearest-city transfer)
- `high_lat_start_date` / `high_lat_end_date` — YYYY-MM-DD window during which `high_lat_method` is active. Outside this window, standard astronomical calculation is used
- `reference_year` — the calendar year the reference prayer-time data was sourced from (e.g., `2025`). Reference text files use a `DD-Mon` date format with no embedded year, so a year must be supplied externally to construct full `datetime.date` keys. When set, this value is used by the optimizer, the conclusion-tab error summary, and the RMSE cache to load and evaluate reference data against the correct year's dates. When empty, the code falls back to the GUI display year (`year_var`) and then to today's year. You can set this value from the Modify City form
- `residual_corrections` / `clock_offsets` — JSON payloads; see Section 3 below
- Canonical column order is managed by GUI constants (`FIELD_NAMES` in `src/app/presentation/gui/constants.py`, 37 fields total)
- Many values may be empty/nullable in the source CSV and are normalized to sensible defaults at runtime

## 2) Reference text files

Location: `reference/<COUNTRY_CODE>/*.txt`

Expected row format: tab-separated, 7 columns:

```text
date\tfajr\tshurooq\tdhuhr\tasr\tmaghrib\tisha
```

Parsing and normalization are handled by `src/app/infrastructure/reference_parser.py` (`load_reference_file`) and the thin `reference_repository.py` wrapper (`load_reference_times`). Both accept an optional `year` parameter.

### Date format and year resolution

The standard date column format is `DD-Mon` (e.g., `01-Jan`, `15-Mar`). This format carries no year information. The parser also recognizes `DD/MM`, `MM/DD`, `YYYY-MM-DD`, and `DD-MM-YYYY` as fallback formats; the last two are self-documenting.

For `DD-Mon` rows, the parser assigns the supplied `year` parameter to every parsed date. If `year` is `None`, it falls back to `datetime.date.today().year`. This means:

- if you load the same reference file with `year=2024` vs. `year=2025`, you get different `datetime.date` keys
- prayer times are slightly different for the same calendar day across years (different day of week, leap-day presence, etc.)
- using the wrong year causes a small but measurable increase in MAE when comparing calculated times against reference times

See `reference_year` in `locations.csv` (Section 1) for how this value is stored and used.

### Adding new reference data

Fastest workflow (GUI): in the app, select the city, open the **Reference Times** tab, and click **Create empty reference file**. This auto-creates both the country folder and city file using the expected naming/location pattern.

Manual workflow:

1. Create country folder if needed: `reference/<COUNTRY_CODE>/`
2. Create file name in lowercase country_city style (for example: `country_city.txt`)
3. Add one row per day with **exactly** 7 tab-separated fields:
    - `date`, `fajr`, `shurooq`, `dhuhr`, `asr`, `maghrib`, `isha`
4. Keep time values in local civil time for the target city
5. Keep prayer labels aligned to project naming (`shurooq`, not `sunrise`)

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

> **Note on DST and `reference_year`:** DST-driven clock offsets are only necessary when the calculator's UTC-offset computation disagrees with the reference source's DST assumptions. When `reference_year` is set correctly, `pytz` resolves DST transitions using the actual dates of the reference year, so the calculator's offsets already match what the reference source used — and no `clock_offsets` block is needed for those periods. If you find a city has a spurious summer-time `clock_offsets` block, check that `reference_year` is set to the correct year before assuming the offset is real.

Serialized JSON: array of date-window blocks.

```json
[
  {"start": "MM-DD", "end": "MM-DD", "offset": 60}
]
```

- `start` / `end` — calendar day range (month-day, no year; applies each year)
- `offset` — shift in **minutes**, added to all calculated prayer times during the window so they match the reference convention

Example: `{"start": "03-30", "end": "10-26", "offset": 60}` means "add 60 minutes to every prayer time between 30 Mar and 26 Oct" — a typical summer-time clock offset.

### 3.2 `residual_corrections`

After Stage 1's structural fit and Stage 2's high-latitude adaptation, some date ranges may still show systematic per-prayer errors that follow a smooth seasonal curve. Stage 3 fits a **Fourier harmonic model** (`PrayerResidualModel`) to these residuals — essentially a small periodic correction curve per prayer that varies smoothly over the year.

The JSON payload is written and read by `PrayerResidualModel`'s serialization methods. You should not edit it by hand; the optimizer produces it automatically and the calculator consumes it at runtime. The payload includes harmonic coefficients, active date ranges, and versioning metadata.

## 4) Test fixtures

Fixtures live under `tests/fixtures/` and provide deterministic baseline inputs/outputs for regression tests.
