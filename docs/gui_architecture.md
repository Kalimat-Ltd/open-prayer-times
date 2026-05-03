# GUI Architecture

For field semantics and optimizer parameter meaning, see [parameter_glossary.md](parameter_glossary.md).

## Window Layout

The application window title is **"Open Prayer Times"** (1920×1080 default). The layout has three regions:

### Left Panel — City List and Actions

- **Search box** (`Search City:`) — filters the city list by name as you type
- **Show Distances** checkbox — when checked, shows the distance in km from the selected city to each same-country city in the list
- **Country filter** dropdown — lists all countries from `country_codes.csv`. Countries that have a `reference/<CC>/` folder are prefixed with ` * `. Selecting a country filters the list to that country only
- **Latitude filter** dropdown — filters by latitude band: All Latitudes, Normal (<±45°), Grey area (45°–48°), High latitude (48°–66°), Extreme (≥66°)
- **Min MAE / Min RMSE / Max N** filter fields — numeric filters on cached city error metrics. Useful for finding cities that still need optimization work
- **City listbox** — scrollable list of cities. Cities that have reference data are prefixed with ` * ` and show their cached metrics: `[MAE x.xx | RMSE x.xx | N xxx]`. Cities above 45° latitude show their latitude in parentheses
- **Action buttons** (bottom of left panel, in a 2-column grid):
  - **Modify City** — opens the modify form for the selected city
  - **Optimize Parameters** — runs single-city optimization (only enabled when the city has reference data)
  - **Add New City** — opens the add city form
  - **Delete City** — deletes the selected city after confirmation
  - **Copy Times** — copies the current month's calculated prayer times to the clipboard in tab-separated format (paste-friendly for Excel)
  - **Optimize All Countries** — opens the batch optimization dashboard

### Middle Panel — Prayer Times Display

Top controls bar:

- **Rounding** dropdown — `off`, `nearest`, `floor`, `ceil`. Controls how calculated times are rounded. Changing this refreshes the display immediately
- **Month** dropdown — select which month to display (defaults to current month)
- **Year** spinbox — select which year to display calculated prayer times for (defaults to current year, range 2000–2100). Changing the year triggers a full tab refresh. This display year is also used as a fallback when a city has no `reference_year` set

Three tabs (notebook widget):

1. **Calculated Times** — shows a day-by-day table for the selected month with columns: Date, Fajr, Shurooq, Dhuhr, Asr, Maghrib, Isha. Each prayer time may have a **high-latitude method indicator** appended:
   - `°` = Angle-Based Fraction
   - `˅` = One Seventh
   - `!` = Midnight
   - `$` = Aqrab Al-Bilad

   When reference data exists and rounding is not `off`, inline **diff values** appear next to each time (e.g., `(+1)`, `(-2)`) color-coded by magnitude — showing the deviation from the reference schedule.

   A legend at the bottom explains the indicator symbols.

2. **Reference Times** — shows the reference prayer times for the selected city (loaded from `reference/<CC>/<city>.txt`). If no reference file exists, the tab shows options to create or open one

3. **Conclusion** — shows a detailed per-month and per-prayer error summary comparing calculated times against reference data. The table includes unsigned and signed average errors per prayer per month, total errors, most/least accurate months, and overall averages. This calculation takes into account constant offsets, residual corrections, and clock-offset adjustments

   The year used for this computation follows a priority chain:
   1. The city's `reference_year` from `locations.csv` (the year the reference data was collected)
   2. The GUI display year (`year_var`, set in the Year spinbox)
   3. `datetime.date.today().year`

   This ensures the conclusion tab compares calculated times against the same year's reference dates the source data came from, rather than the current year or whatever the display year happens to be.

Each tab has font size `+`/`-` buttons in the top-right corner.

### Status Bar

At the bottom, a status bar shows three counters:
- Countries with reference data / total countries
- Countries with at least one optimized city / total countries
- Optimized cities / total cities

## User Workflows

### Calculate prayer times for a city

1. Find the city using the search box, country filter, or latitude filter
2. Click on the city in the list — calculated times appear immediately in the **Calculated Times** tab
3. Use the **Month** dropdown to switch months. Use the **Rounding** dropdown to change rounding
4. Switch to the **Reference Times** tab to view reference data (if available)
5. Switch to the **Conclusion** tab to see the error summary comparing calculated vs. reference

### Add a new city

1. Click **Add New City** in the action buttons
2. A scrollable form opens with sections for:
   - Country code (dropdown), City name
   - Coordinates (latitude, longitude, optimized lat/lon)
   - Environment (timezone, elevation, pressure, temperature)
   - Calculation method (`angle_based` or `moonsighting`):
     - For `angle_based`: Fajr Angle, Isha Angle, Isha Minutes fields are shown
     - For `moonsighting`: Isha Shafaq dropdown (`ahmer`, `abyad`, `general`) is shown instead
   - Asr Madhab (Standard or Hanafi), Isha Harag (Off / Method 1–3)
   - High-Latitude Method (Angle Based, One Seventh, Midnight, Aqrab Al-Bilad)
   - High-Latitude date range (start/end YYYY-MM-DD)
   - When high-lat method is Angle Based: Custom Fajr Angle, Custom Isha Angle, and Fallback Method fields appear
   - Per-prayer offsets (Fajr, Shurooq, Dhuhr, Asr, Maghrib, Isha) in minutes
   - Is Optimized checkbox
   - **Reference Year** spinbox — the year the reference data was sourced from. Used by the optimizer, conclusion tab, and RMSE cache to load reference dates in the correct year context. Set this to the year that matches the reference data for the city. Leave empty if the reference year is unknown
   - Advanced JSON fields: Residual Corrections, Clock Offsets (scrollable text boxes)
3. Click **Save City** to persist. The city is appended to `locations.csv`

### Modify an existing city

1. Select a city, then click **Modify City**
2. The same form opens pre-filled with the city's current values
3. Buttons at the bottom:
   - **Save Changes** — saves modifications for this city only
   - **Cancel** — discards changes
   - **Apply to Country** — applies the current form values (calculation method, angles, offsets, high-lat settings, residual corrections, clock offsets, etc.) to **all cities** sharing the same country code, after a confirmation dialog listing every field that will change

### Delete a city

1. Select a city, click **Delete City**, and confirm in the dialog

### Optimize a single city

1. Select a city that has reference data (the **Optimize Parameters** button is only enabled for cities with a matching reference file)
2. Click **Optimize Parameters**
3. The optimizer:
   - Resets city parameters to Stage 1 defaults (angles, offsets, residual corrections all cleared)
   - Discovers auxiliary cities in the same country for residual validation
   - Computes the baseline error with the city's current parameters
   - Runs the full multistage pipeline (`run_multistage_optimization`)
   - Computes after-optimization error
4. A results dialog appears showing a before/after comparison: MAE, RMSE, per-prayer MAE, optimized parameters (angles, coordinates, offsets), distance moved, and any adaptive detection notes. The dialog has three buttons:
   - **Apply to City** — applies the optimized parameters to the selected city only (including optimized coordinates)
   - **Apply to Country** — applies optimized parameters (angles, offsets, corrections, etc.) to all cities with the same country code. Only the selected city gets optimized coordinates
   - **Ignore** — discards the optimization result
5. After applying, `locations.csv` is rewritten, the RMSE cache is updated, and the city list refreshes

### Batch country optimization

1. Click **Optimize All Countries** to open the Batch Optimization Dashboard
2. The dashboard automatically discovers all countries that have both reference data files and matching city entries in `locations.csv`
3. A table shows each country with columns: Run (enable checkbox), Apply (checkbox), Country, Code, Ref Cities, Status, MAE Before, MAE After, Change, Time
4. Controls:
   - **Start All** — begins optimization of all enabled countries using a configurable multiprocessing pool; multiple cities across multiple countries are processed simultaneously. The worker count is user-configurable. The UI remains responsive
   - **Stop** — signals the current run to stop after the active country completes
   - **Enable All / Disable All** — toggle the Run checkbox for all unprocessed countries
   - **Select All Improved / Deselect All** — toggle Apply checkboxes for completed countries
   - **Apply Selected** — writes optimization results to `locations.csv` for all countries with the Apply checkbox checked
5. For each country, the dashboard:
   - Runs `run_multistage_optimization` independently for every city that has reference data
   - Computes before/after MAE and RMSE (per-country aggregate)
   - Shows per-city progress (city name, residual model status, timing)
   - After all cities complete, marks the country as "Improved" or "No Improvement"
   - Countries that improved are auto-checked in the Apply column
6. When applying results:
   - Reference cities get the full optimization result (coordinates, angles, offsets, residual corrections, clock offsets)
   - Non-reference cities in the same country receive angles from the closest reference city by geographic distance. Residual corrections are only transferred when the closest reference city passes conservative distance rules
7. Clicking a row in the table shows a detail panel with per-prayer MAE breakdown and sample city parameters

### Copy times to clipboard

1. Select a city, then click **Copy Times**
2. The calculated prayer times for the displayed month are copied as tab-separated text (compatible with pasting into Excel or a reference file)

## Entry Points

| File | Role |
|------|------|
| `src/app/presentation/gui_app.py` | Module entry — creates tkinter root and launches `PrayerApp` |
| `src/app/presentation/prayer_times_gui.py` | Re-exports `PrayerApp` and `_lazy_imports` |
| `src/app/presentation/gui/full_app.py` | `PrayerApp` class — method-binder orchestrator |

## Method-Binder Pattern

`PrayerApp` uses a **method-binder pattern** to keep the large GUI manageable across multiple files. The class body in `full_app.py` is minimal — it declares only `__init__` as a stub. All real methods are defined as module-level functions in separate sub-modules and dynamically bound to the class at import time via `_resolve_method()`.

Each sub-module defines functions whose first parameter is `self` (the `PrayerApp` instance). After import, `full_app.py` assigns each function to the class:

```python
PrayerApp.create_widgets = _resolve_method("create_widgets")
PrayerApp.on_city_select = _resolve_method("on_city_select")
# ... etc.
```

This allows the GUI code to be split into focused, maintainable modules while presenting a single `PrayerApp` class to callers.

## Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `app_shell_and_loading.py` | `__init__`, `create_widgets`, `on_closing`, `on_tab_changed`, `load_locations`, `process_pending_reference_changes`, `refresh_country_filter`, `run_selected_city_optimizer` — root layout, widget creation (including Month/Year selectors and Rounding dropdown), loading lifecycle, reference file watcher setup, status wiring |
| `city_list_and_calculations.py` | City name/RMSE indexing (SQLite cache), listbox population, filtering (search / country / latitude / RMSE / MAE / N), city selection handling, daily prayer-time rendering with inline diffs and high-lat indicators, clipboard copy, month selection |
| `city_form_workflows.py` | `create_city_form` (scrollable form with dynamic field visibility based on calculation method and high-lat method, including the Reference Year spinbox), `open_add_city_window`, `open_modify_city_window`, `validate_and_get_form_data` |
| `city_data_and_reference_actions.py` | `save_new_city`, `save_modified_city`, `apply_to_country`, `delete_selected_city` — city CRUD with CSV persistence, country-wide parameter propagation |
| `summary_and_status_views.py` | `show_conclusion_summary` (per-month per-prayer error analysis with residual and clock-offset accounting, reference-year-aware date loading), `update_status_bar` (optimization coverage statistics) |
| `constants.py` | `FIELD_NAMES` — canonical `locations.csv` column order (37 fields) |
| `deps.py` | Lazy dependency loader for heavy runtime imports (TimezoneFinder, geopy, watchdog, pytz, prayer calculator, optimizer functions) |
| `shared.py` | Wiring helpers re-exporting lazy-loaded dependencies and the `ReferenceFolderHandler` class |
| `clock.py` | `get_clock_offset_for_date` — applies per-date reference clock-shift offset from JSON blocks |
| `file_ops.py` | `rewrite_location_file` — safe CSV rewrite with backup/restore semantics |

## Reference File Watcher

On startup, a `watchdog` observer monitors the `reference/` directory recursively. When a `.txt` file is created, modified, deleted, or moved, the watcher:

1. Queues the changed file path
2. Every 500ms, the main thread processes queued paths: identifies the affected city by matching the file path to `reference/<CC>/<city>.txt`, refreshes the RMSE cache for that city, updates the country filter (to reflect new `*` markers), and refreshes the active tab

This means external edits to reference files (e.g., saving a new schedule from a text editor) are picked up automatically without restarting the application.

## RMSE / MAE Cache

The city list displays live error metrics (MAE, RMSE, sample count N) next to each city that has reference data. Computing these for every city on every refresh would be slow, so the GUI maintains a SQLite-backed cache (`resources/city_indexes.sqlite3`):

- Each city's RMSE/MAE/N is cached with a signature hash derived from the city's current parameters and reference file metadata (mtime, size)
- When a city's parameters or reference file change, the cache entry is invalidated and recomputed
- The cache is rebuilt lazily on first access or explicitly after optimization applies changes

## Optimization Entry Points

Both single-city and batch optimization are implemented in `src/app/infrastructure/optimizer/batch_gui.py`:

- **`optimize_parameters_for_city(self, ref_file)`** — called from the GUI's **Optimize Parameters** button. Loads reference data, computes baseline error, runs `run_multistage_optimization`, computes after-error, shows the results dialog, and applies based on user choice
- **`open_batch_optimization_dashboard(app)`** — opens the `BatchOptimizationDashboard` toplevel window. Country discovery, multiprocessing-based parallel optimization (with configurable worker count), before/after comparison, and batch apply logic are all handled within this class

## Data Flow

1. On startup, `locations.csv` is parsed into `self.locations_data` (list of dicts)
2. City name prefix index and RMSE cache are built for fast filtering
3. User selects a city → `on_city_select` fires → `calculate_and_display_prayer_times` renders the Calculated Times tab. Tab switches trigger reference display or conclusion summary
4. Optimization results are applied back to `self.locations_data` entries and persisted via `rewrite_location_file` (backup → write → remove backup)
5. The reference file watcher ensures external changes to reference data are reflected in the UI

## Extension Guidance

- Keep UI logic in presentation modules; avoid embedding heavy optimization logic in widget code
- Prefer helpers in `shared.py`/`deps.py` for optional runtime imports
- The method-binder pattern means new GUI methods should be added as module-level functions in the appropriate sub-module, then bound in `full_app.py`
- Preserve existing field names and save flows to maintain CSV compatibility

