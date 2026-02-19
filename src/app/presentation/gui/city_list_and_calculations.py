"""City list interactions, filtering, selection handling, and prayer-time calculation display flows."""

# ruff: noqa: BLE001, ARG001, SLF001
# pylint: disable=broad-exception-caught,protected-access,unused-argument

import re
import traceback
import tkinter as tk
from tkinter import messagebox
import sqlite3
import hashlib
import json
import os
import datetime
import calendar
import math

from src.app.presentation.gui.shared import (
    _get_calculate_prayer_times,
    _get_clock_offset_for_date,
    _get_pytz,
)

_PRAYERS = ["fajr", "shurooq", "dhuhr", "asr", "maghrib", "isha"]
RMSE_CACHE_SCHEMA_VERSION = 3


def _normalize_city_name_tokens(name):
    norm = re.sub(r"\s+", " ", (name or "").strip().lower())
    tokens = [tok for tok in re.split(r"[^a-z0-9]+", norm) if tok]
    return norm, tokens


def _get_index_db_path(self):
    return os.path.join(self.resources_dir, "city_indexes.sqlite3")


def _ensure_index_db(self):
    db_path = _get_index_db_path(self)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rmse_cache (
                city_id INTEGER PRIMARY KEY,
                signature TEXT NOT NULL,
                rmse REAL,
                mae REAL,
                n_samples INTEGER,
                updated_at INTEGER NOT NULL
            )
            """
        )
        existing_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(rmse_cache)").fetchall()
        }
        if "mae" not in existing_cols:
            conn.execute("ALTER TABLE rmse_cache ADD COLUMN mae REAL")
        if "n_samples" not in existing_cols:
            conn.execute("ALTER TABLE rmse_cache ADD COLUMN n_samples INTEGER")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        row = conn.execute(
            "SELECT value FROM metadata WHERE key='rmse_schema_version'"
        ).fetchone()
        if row is None or row[0] != str(RMSE_CACHE_SCHEMA_VERSION):
            conn.execute("DELETE FROM rmse_cache")
            conn.execute(
                "REPLACE INTO metadata(key, value) VALUES('rmse_schema_version', ?)",
                (str(RMSE_CACHE_SCHEMA_VERSION),),
            )
        conn.commit()


def _load_rmse_cache_map(self):
    _ensure_index_db(self)
    db_path = _get_index_db_path(self)
    cache_map = {}
    with sqlite3.connect(db_path) as conn:
        for city_id, signature, rmse, mae, n_samples in conn.execute(
            "SELECT city_id, signature, rmse, mae, n_samples FROM rmse_cache"
        ):
            cache_map[int(city_id)] = (signature, rmse, mae, n_samples)
    return cache_map


def _upsert_rmse_cache_row(self, city_id, signature, rmse, mae, n_samples):
    _ensure_index_db(self)
    db_path = _get_index_db_path(self)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            REPLACE INTO rmse_cache(city_id, signature, rmse, mae, n_samples, updated_at)
            VALUES(?, ?, ?, ?, ?, strftime('%s','now'))
            """,
            (int(city_id), str(signature), rmse, mae, n_samples),
        )
        conn.commit()


def remove_city_rmse_cache_entries(self, city_ids):
    ids = [int(cid) for cid in city_ids if cid is not None]
    if not ids:
        return
    _ensure_index_db(self)
    db_path = _get_index_db_path(self)
    placeholders = ",".join("?" for _ in ids)
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"DELETE FROM rmse_cache WHERE city_id IN ({placeholders})", ids)
        conn.commit()


def _normalize_reference_city_filename(name):
    return (name or "").replace(" ", "_").replace(",", "").lower()


def get_city_id_from_reference_path(self, reference_path):
    if not reference_path:
        return None
    try:
        norm_path = os.path.normpath(reference_path)
        path_parts = norm_path.replace("\\", "/").split("/")
        if "reference" not in path_parts:
            return None
        ref_idx = path_parts.index("reference")
        if len(path_parts) <= ref_idx + 2:
            return None
        country_code = path_parts[ref_idx + 1].upper()
        filename = os.path.splitext(path_parts[ref_idx + 2])[0].lower()
    except Exception:
        return None

    for loc in self.locations_data:
        if loc.get("country_code", "").upper() != country_code:
            continue
        loc_name_norm = _normalize_reference_city_filename(loc.get("name", ""))
        if loc_name_norm == filename:
            return loc.get("id")
    return None


def refresh_metrics_for_reference_paths(self, reference_paths):
    paths = [p for p in (reference_paths or []) if p]
    self.rebuild_city_name_index()

    touched_ids = set()
    for path in paths:
        city_id = self.get_city_id_from_reference_path(path)
        if city_id is not None:
            touched_ids.add(city_id)

    if touched_ids:
        self.rebuild_city_rmse_for_ids(sorted(touched_ids))
    else:
        self.rmse_index_ready = False
        self.city_rmse_index = {}
        self.city_mae_index = {}
        self.city_n_index = {}

    try:
        self.filter_list()
    except Exception:
        self.populate_listbox(
            self.search_var.get() if hasattr(self, "search_var") else ""
        )


def _rmse_cache_signature_for_city(self, location_data, ref_file):
    stat = os.stat(ref_file)
    serializable = {
        "schema": RMSE_CACHE_SCHEMA_VERSION,
        "city_id": location_data.get("id"),
        "ref_path": os.path.abspath(ref_file),
        "ref_mtime_ns": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))),
        "ref_size": int(stat.st_size),
        "fajr_angle": location_data.get("fajr_angle"),
        "isha_angle": location_data.get("isha_angle"),
        "optimized_lat": location_data.get("optimized_lat"),
        "optimized_lon": location_data.get("optimized_lon"),
        "latitude": location_data.get("latitude"),
        "longitude": location_data.get("longitude"),
        "temp": location_data.get("temp"),
        "pressure": location_data.get("pressure"),
        "timezone": location_data.get("timezone"),
        "calculation_method": location_data.get("calculation_method"),
        "asr_madhab": location_data.get("asr_madhab"),
        "high_lat_method": location_data.get("high_lat_method"),
        "isha_shafaq": location_data.get("isha_shafaq"),
        "isha_minutes": location_data.get("isha_minutes"),
        "fajr_offset": location_data.get("fajr_offset"),
        "shurooq_offset": location_data.get("shurooq_offset"),
        "dhuhr_offset": location_data.get("dhuhr_offset"),
        "asr_offset": location_data.get("asr_offset"),
        "maghrib_offset": location_data.get("maghrib_offset"),
        "isha_offset": location_data.get("isha_offset"),
        "clock_offsets": location_data.get("clock_offsets"),
        "residual_corrections": location_data.get("residual_corrections"),
    }
    blob = json.dumps(serializable, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()


def rebuild_city_name_index(self):
    self.city_name_prefix_index = {}
    self.city_name_lookup = {}
    self.city_has_reference = {}
    self.location_by_id = {}
    self.city_ids_sorted = []
    for loc in self.locations_data:
        city_id = loc.get("id")
        if city_id is None:
            continue
        self.location_by_id[city_id] = loc
        self.city_ids_sorted.append(city_id)
        norm_name, tokens = _normalize_city_name_tokens(loc.get("name", ""))
        self.city_name_lookup[city_id] = norm_name
        ref_file = self._get_reference_file_path(loc)
        self.city_has_reference[city_id] = bool(ref_file and os.path.exists(ref_file))
        candidates = list(tokens)
        if norm_name:
            candidates.append(norm_name.replace(" ", ""))
        for token in candidates:
            max_len = min(len(token), 12)
            for plen in range(1, max_len + 1):
                pref = token[:plen]
                self.city_name_prefix_index.setdefault(pref, set()).add(city_id)
    self.city_ids_sorted.sort()


def _ensure_city_name_index(self):
    if not self.city_name_lookup or len(self.city_name_lookup) != len(
        [loc for loc in self.locations_data if loc.get("id") is not None]
    ):
        self.rebuild_city_name_index()


def _search_candidate_ids(self, filter_text):
    self._ensure_city_name_index()
    if not filter_text:
        return set(self.city_name_lookup.keys())

    _, terms = _normalize_city_name_tokens(filter_text)
    if not terms:
        return set(self.city_name_lookup.keys())

    matched = None
    for term in terms:
        prefix_set = self.city_name_prefix_index.get(term, set())
        if not prefix_set:
            prefix_set = {
                city_id
                for city_id, name in self.city_name_lookup.items()
                if term in name
            }
        matched = set(prefix_set) if matched is None else (matched & set(prefix_set))
        if not matched:
            return set()
    return matched if matched is not None else set(self.city_name_lookup.keys())


def _parse_reference_file_all_dates(filepath):
    ref_by_date = {}
    current_year = datetime.date.today().year
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) != 7:
                continue
            date_str, fajr, sunrise, dhuhr, asr, maghrib, isha = parts
            parsed_date = None
            for fmt in ("%d-%b", "%d/%m", "%m/%d", "%Y-%m-%d", "%d-%m-%Y"):
                try:
                    dt = datetime.datetime.strptime(date_str, fmt)
                    year = dt.year if dt.year != 1900 else current_year
                    parsed_date = datetime.date(year, dt.month, dt.day)
                    break
                except ValueError:
                    continue
            if not parsed_date:
                continue
            ref_by_date[parsed_date] = {
                "fajr": fajr,
                "shurooq": sunrise,
                "dhuhr": dhuhr,
                "asr": asr,
                "maghrib": maghrib,
                "isha": isha,
            }
    return ref_by_date


def _build_day_offsets_for_rmse(location_data, target_date):
    day_offsets = {
        "fajr_offset": float(location_data.get("fajr_offset", 0.0) or 0.0),
        "shurooq_offset": float(location_data.get("shurooq_offset", 0.0) or 0.0),
        "dhuhr_offset": float(location_data.get("dhuhr_offset", 0.0) or 0.0),
        "asr_offset": float(location_data.get("asr_offset", 0.0) or 0.0),
        "maghrib_offset": float(location_data.get("maghrib_offset", 0.0) or 0.0),
        "isha_offset": float(location_data.get("isha_offset", 0.0) or 0.0),
    }

    rc_json = location_data.get("residual_corrections", "")
    if rc_json:
        try:
            from src.app.infrastructure.residual_model import PrayerResidualModel

            model = PrayerResidualModel.from_json(rc_json)
            if model is not None and model.fitted:
                corrections = model.predict_all(target_date)
                prayer_to_field = {
                    "fajr": "fajr_offset",
                    "shurooq": "shurooq_offset",
                    "dhuhr": "dhuhr_offset",
                    "asr": "asr_offset",
                    "maghrib": "maghrib_offset",
                    "isha": "isha_offset",
                }
                for prayer, field in prayer_to_field.items():
                    day_offsets[field] += float(corrections.get(prayer, 0.0) or 0.0)
        except Exception:
            pass

    clk = _get_clock_offset_for_date(
        location_data.get("clock_offsets", ""), target_date
    )
    if clk:
        for f in (
            "fajr_offset",
            "shurooq_offset",
            "dhuhr_offset",
            "asr_offset",
            "maghrib_offset",
            "isha_offset",
        ):
            day_offsets[f] += float(clk)

    return day_offsets


def _compute_city_rmse_from_reference(self, location_data):
    ref_file = self._get_reference_file_path(location_data)
    if not ref_file or not os.path.exists(ref_file):
        return None, None, 0

    try:
        reference_times = _parse_reference_file_all_dates(ref_file)
    except Exception:
        return None, None, 0

    if not reference_times:
        return None, None, 0

    lat_dec = (
        location_data.get("optimized_lat")
        if location_data.get("optimized_lat") is not None
        else location_data.get("latitude")
    )
    lon_dec = (
        location_data.get("optimized_lon")
        if location_data.get("optimized_lon") is not None
        else location_data.get("longitude")
    )
    if lat_dec is None or lon_dec is None:
        return None, None, 0

    tz_raw = location_data.get("timezone")
    tz_offset_hours = 0.0
    tz_name = None
    try:
        tz_obj = _get_pytz().timezone(tz_raw)
        tz_name = tz_raw
    except Exception:
        tz_obj = None
        try:
            tz_offset_hours = float(tz_raw)
        except Exception:
            tz_offset_hours = 0.0

    total_sq = 0.0
    total_abs = 0.0
    total_count = 0
    valid_day_count = 0
    for date_obj, ref in reference_times.items():
        day_has_valid_sample = False
        try:
            if tz_obj is not None:
                dt = datetime.datetime(date_obj.year, date_obj.month, date_obj.day, 12)
                tz_offset_hours = tz_obj.utcoffset(dt).total_seconds() / 3600.0

            calc_kwargs = dict(
                lat_dec=lat_dec,
                lon_dec=lon_dec,
                elevation=float(location_data.get("elevation", 0) or 0),
                pressure=float(location_data.get("pressure", 1010.0) or 1010.0),
                temp=float(location_data.get("temp", 10.0) or 10.0),
                tz_offset_hours=tz_offset_hours,
                tz_name=tz_name,
                calculation_method=location_data.get("calculation_method")
                or "angle_based",
                fajr_angle=float(location_data.get("fajr_angle", 18.0) or 18.0),
                isha_angle=float(location_data.get("isha_angle", 17.0) or 17.0),
                isha_minutes=location_data.get("isha_minutes"),
                isha_shafaq=location_data.get("isha_shafaq") or "general",
                asr_madhab=location_data.get("asr_madhab") or 0,
                isha_harag=location_data.get("isha_harag") or 0,
                high_lat_method=location_data.get("high_lat_method") or 0,
                high_lat_start_date=location_data.get("high_lat_start_date"),
                high_lat_end_date=location_data.get("high_lat_end_date"),
                custom_fajr_angle=location_data.get("custom_fajr_angle"),
                custom_isha_angle=location_data.get("custom_isha_angle"),
                high_lat_fallback_method=location_data.get("high_lat_fallback_method"),
                target_date=date_obj,
                rounding="off",
            )
            calc_kwargs.update(_build_day_offsets_for_rmse(location_data, date_obj))
            times, _, error_msg = _get_calculate_prayer_times()(**calc_kwargs)  # type: ignore[arg-type]
            if error_msg:
                continue
            for prayer in _PRAYERS:
                calc_time = times.get(prayer, "N/A")
                ref_time = ref.get(prayer, "N/A")
                diff = self._calculate_time_difference(calc_time, ref_time)
                if diff is None:
                    continue
                diff_val = float(diff)
                total_sq += diff_val * diff_val
                total_abs += abs(diff_val)
                total_count += 1
                day_has_valid_sample = True
        except Exception:
            continue

        if day_has_valid_sample:
            valid_day_count += 1

    if total_count == 0:
        return None, None, 0
    rmse = math.sqrt(total_sq / total_count)
    mae = (total_abs / total_count) if total_count > 0 else None
    return rmse, mae, valid_day_count


def rebuild_city_rmse_index(self):
    rmse_map = (
        dict(self.city_rmse_index) if isinstance(self.city_rmse_index, dict) else {}
    )
    mae_map = dict(self.city_mae_index) if isinstance(self.city_mae_index, dict) else {}
    n_map = dict(self.city_n_index) if isinstance(self.city_n_index, dict) else {}
    cache_map = _load_rmse_cache_map(self)
    for loc in self.locations_data:
        city_id = loc.get("id")
        if city_id is None:
            continue
        ref_file = self._get_reference_file_path(loc)
        if not ref_file or not os.path.exists(ref_file):
            rmse_map[city_id] = None
            mae_map[city_id] = None
            n_map[city_id] = 0
            continue
        signature = _rmse_cache_signature_for_city(self, loc, ref_file)
        cached = cache_map.get(int(city_id))
        if cached and cached[0] == signature:
            rmse, mae, n_samples = cached[1], cached[2], int(cached[3] or 0)
        else:
            rmse, mae, n_samples = _compute_city_rmse_from_reference(self, loc)
            _upsert_rmse_cache_row(self, city_id, signature, rmse, mae, n_samples)
        rmse_map[city_id] = rmse
        mae_map[city_id] = mae
        n_map[city_id] = n_samples
    self.city_rmse_index = rmse_map
    self.city_mae_index = mae_map
    self.city_n_index = n_map
    self.rmse_index_ready = True


def _ensure_city_rmse_index(self):
    if not getattr(self, "rmse_index_ready", False):
        try:
            if hasattr(self, "status_bar"):
                self.status_bar.config(
                    text="Building RMSE index (first run may take a while)..."
                )
                self.root.update_idletasks()
        except Exception:
            pass

        self.rebuild_city_rmse_index()

        try:
            self.update_status_bar()
            self.root.update_idletasks()
        except Exception:
            pass


def rebuild_city_rmse_for_ids(self, city_ids):
    if city_ids is None:
        self.rebuild_city_rmse_index()
        return

    current_by_id = {
        loc.get("id"): loc
        for loc in self.locations_data
        if isinstance(loc, dict) and loc.get("id") is not None
    }
    self.location_by_id.update(current_by_id)
    cache_map = _load_rmse_cache_map(self)
    for city_id in city_ids:
        loc = current_by_id.get(city_id)
        if not loc:
            self.city_rmse_index.pop(city_id, None)
            self.city_mae_index.pop(city_id, None)
            self.city_n_index.pop(city_id, None)
            continue
        ref_file = self._get_reference_file_path(loc)
        if not ref_file or not os.path.exists(ref_file):
            self.city_rmse_index[city_id] = None
            self.city_mae_index[city_id] = None
            self.city_n_index[city_id] = 0
            continue
        signature = _rmse_cache_signature_for_city(self, loc, ref_file)
        cached = cache_map.get(int(city_id))
        if cached and cached[0] == signature:
            self.city_rmse_index[city_id] = cached[1]
            self.city_mae_index[city_id] = cached[2]
            self.city_n_index[city_id] = int(cached[3] or 0)
            continue
        rmse, mae, n_samples = _compute_city_rmse_from_reference(self, loc)
        self.city_rmse_index[city_id] = rmse
        self.city_mae_index[city_id] = mae
        self.city_n_index[city_id] = n_samples
        _upsert_rmse_cache_row(self, city_id, signature, rmse, mae, n_samples)

    self.rmse_index_ready = True


def populate_listbox(
    self,
    filter_text="",
    selected_city=None,
    country_code_filter=None,
    lat_filter=None,
    min_rmse_filter=None,
    min_mae_filter=None,
    max_n_filter=None,
):
    """Populates the city listbox, optionally filtering, and shows distance to selected city for same-country cities. Supports country and latitude filter."""
    if country_code_filter is None and hasattr(self, "country_filter_var"):
        country_filter = self.country_filter_var.get()
        if country_filter and country_filter != "All Countries":
            country_code_filter = country_filter.split(" - ")[0]
    if lat_filter is None and hasattr(self, "lat_filter_var"):
        lat_filter = self.lat_filter_var.get()
    if min_rmse_filter is None and hasattr(self, "min_rmse_var"):
        min_rmse_filter = self.min_rmse_var.get().strip()
    if min_mae_filter is None and hasattr(self, "min_mae_var"):
        min_mae_filter = self.min_mae_var.get().strip()
    if max_n_filter is None and hasattr(self, "max_n_var"):
        max_n_filter = self.max_n_var.get().strip()
    min_rmse = None
    if min_rmse_filter not in (None, ""):
        try:
            min_rmse = float(min_rmse_filter)
        except ValueError:
            min_rmse = None
    min_mae = None
    if min_mae_filter not in (None, ""):
        try:
            min_mae = float(min_mae_filter)
        except ValueError:
            min_mae = None
    max_n = None
    if max_n_filter not in (None, ""):
        try:
            max_n = int(float(max_n_filter))
        except ValueError:
            max_n = None

    self._ensure_city_name_index()
    candidate_ids = _search_candidate_ids(self, filter_text)

    if not self.rmse_index_ready and any(
        self.city_has_reference.get(city_id, False) for city_id in candidate_ids
    ):
        self._ensure_city_rmse_index()

    if min_rmse is not None or min_mae is not None or max_n is not None:
        self._ensure_city_rmse_index()
    if selected_city is None:
        selected_city = self.get_selected_location_data()
    current_selection_name = selected_city["name"] if selected_city else None
    self.city_listbox.delete(0, tk.END)
    self.city_listbox_ids = []  # city ids for each entry
    new_selection_index = -1
    inserted_count = 0
    selected_country_code = selected_city.get("country_code") if selected_city else None
    display_list = []
    ordered_candidate_ids = sorted(
        [city_id for city_id in candidate_ids if city_id in self.location_by_id]
    )
    for city_id in ordered_candidate_ids:
        location = self.location_by_id.get(city_id)
        if not location:
            continue
        # --- Latitude filter logic ---
        lat = (
            location.get("optimized_lat")
            if location.get("optimized_lat") is not None
            else location.get("latitude")
        )
        if lat is None:
            continue
        show = True
        if lat_filter and lat_filter != "All Latitudes":
            abs_lat = abs(lat)
            if lat_filter.startswith("Normal"):
                show = abs_lat < 45
            elif lat_filter.startswith("Grey"):
                show = 45 < abs_lat < 48
            elif lat_filter.startswith("High"):
                show = 48 < abs_lat < 66
            elif lat_filter.startswith("Extreme"):
                show = abs_lat >= 66
        if not show:
            continue
        if country_code_filter and location.get("country_code") != country_code_filter:
            continue

        has_ref = bool(self.city_has_reference.get(city_id, False))

        if min_rmse is not None:
            city_rmse = self.city_rmse_index.get(city_id)
            if city_rmse is None or city_rmse < min_rmse:
                continue
        if min_mae is not None:
            city_mae = self.city_mae_index.get(city_id)
            if city_mae is None or city_mae < min_mae:
                continue
        if max_n is not None:
            city_n = int(self.city_n_index.get(city_id, 0) or 0)
            if city_n <= 0 or city_n > max_n:
                continue

        display_name = location["name"]
        if has_ref:
            city_rmse = self.city_rmse_index.get(city_id)
            city_mae = self.city_mae_index.get(city_id)
            city_n = int(self.city_n_index.get(city_id, 0) or 0)
            rmse_label = (
                f"RMSE {city_rmse:.2f}" if city_rmse is not None else "RMSE n/a"
            )
            mae_label = f"MAE {city_mae:.2f}" if city_mae is not None else "MAE n/a"
            n_label = f"N {city_n}" if city_n > 0 else "N 0"
            display_name = f" * {display_name} [{mae_label} | {rmse_label} | {n_label}]"
        distance_str = ""
        color = "black"
        if (
            self.check_distances_var.get()
            and selected_city
            and location.get("country_code") == selected_country_code
            and location["name"] != selected_city["name"]
        ):
            km = self._distance_km(selected_city, location)
            if km is not None:
                distance_str = f"    {int(round(km))}Km"
                color = self._distance_color(km)
        abs_lat = abs(lat)
        lat_str = ""
        if abs_lat > 45:
            lat_str = f"    ({lat:.2f}°)"
        display_list.append((display_name + distance_str + lat_str, color, city_id))
        if current_selection_name and location["name"] == current_selection_name:
            new_selection_index = inserted_count
        inserted_count += 1
    self.city_listbox.delete(0, tk.END)
    display_texts = []
    colors = []
    for idx, (display_text, color, city_id) in enumerate(display_list):
        display_texts.append(display_text)
        colors.append(color)
        self.city_listbox_ids.append(city_id)  # <-- Store id for each city
        if selected_city and city_id == selected_city.get("id"):
            new_selection_index = idx
    self.city_listbox.insert(tk.END, *display_texts)
    for idx, color in enumerate(colors):
        if color != "black":
            self.city_listbox.itemconfig(idx, foreground=color)
    if new_selection_index != -1:
        self.city_listbox.selection_set(new_selection_index)
        self.city_listbox.see(new_selection_index)
        self.enable_action_buttons()
        selected_data = self.get_selected_location_data()
        if selected_data:
            self.calculate_and_display_prayer_times(selected_data)
    elif self.city_listbox.size() == 0:
        self.update_prayer_times_display(
            "No cities found." if filter_text else "No cities loaded."
        )
        self.disable_action_buttons()
    else:
        self.update_prayer_times_display("Select a city from the list.")
        self.enable_action_buttons()


def filter_list(self, *args):
    """Filters the city listbox based on the search entry, country filter, and latitude filter comboboxes."""
    search_term = self.search_var.get()
    country_filter = self.country_filter_var.get()
    lat_filter = self.lat_filter_var.get() if hasattr(self, "lat_filter_var") else None
    min_rmse_filter = (
        self.min_rmse_var.get().strip() if hasattr(self, "min_rmse_var") else None
    )
    min_mae_filter = (
        self.min_mae_var.get().strip() if hasattr(self, "min_mae_var") else None
    )
    max_n_filter = self.max_n_var.get().strip() if hasattr(self, "max_n_var") else None
    if country_filter and country_filter != "All Countries":
        code = country_filter.lstrip(" *").split(" - ")[0].strip()
    else:
        code = None
    self.populate_listbox(
        search_term,
        country_code_filter=code,
        lat_filter=lat_filter,
        min_rmse_filter=min_rmse_filter,
        min_mae_filter=min_mae_filter,
        max_n_filter=max_n_filter,
    )


def get_selected_location_data(self):
    """Gets the data dictionary for the currently selected city."""
    selection_indices = self.city_listbox.curselection()
    if not selection_indices:
        return None
    selected_id = self.city_listbox_ids[selection_indices[0]]
    for location in self.locations_data:
        if location["id"] == selected_id:
            return location
    return None


def disable_action_buttons(self):
    """Disables buttons that require a selection."""
    self.optimize_settings_button.config(state=tk.DISABLED)
    self.modify_button.config(state=tk.DISABLED)
    self.delete_button.config(state=tk.DISABLED)


def enable_action_buttons(self):
    """Enables buttons that require a selection."""
    self.optimize_settings_button.config(state=tk.NORMAL)
    self.modify_button.config(state=tk.NORMAL)
    self.delete_button.config(state=tk.NORMAL)


def update_prayer_times_display(self, text):
    """Updates the content of the prayer times text area."""
    self.prayer_times_text.config(state=tk.NORMAL)
    self.prayer_times_text.delete("1.0", tk.END)
    self.prayer_times_text.insert(tk.END, text)
    self.prayer_times_text.config(state=tk.DISABLED)


def on_city_select(self, event):
    # Use event.widget to get the correct selection
    listbox = event.widget if event and hasattr(event, "widget") else self.city_listbox
    selection_indices = listbox.curselection()
    if not selection_indices:
        self.update_prayer_times_display("Select a city from the list.")
        self.disable_action_buttons()
        return
    selected_id = self.city_listbox_ids[selection_indices[0]]
    selected_data = None
    for location in self.locations_data:
        if location["id"] == selected_id:
            selected_data = location
            break
    if not selected_data:
        self.update_prayer_times_display("Select a city from the list.")
        self.disable_action_buttons()
        return
    # Update the listbox to show distances from the newly selected city
    if self.check_distances_var.get():
        self.populate_listbox(self.search_var.get(), selected_city=selected_data)
    # Ensure the correct city is selected and visible after repopulating
    all_items = self.city_listbox.get(0, tk.END)
    for idx, _ in enumerate(all_items):
        if selected_data["id"] == self.city_listbox_ids[idx]:
            self.city_listbox.selection_clear(0, tk.END)
            self.city_listbox.selection_set(idx)
            self.city_listbox.see(idx)
            break
    ref_file = self._get_reference_file_path(selected_data)
    if ref_file and os.path.exists(ref_file):
        self.optimize_settings_button.config(state=tk.NORMAL)
    else:
        self.optimize_settings_button.config(state=tk.DISABLED)
    self.enable_action_buttons()
    if selected_data.get("timezone_name"):
        self.dst_var.set(False)
    self.on_tab_changed()
    self.calculate_and_display_prayer_times(selected_data)
    self.update_status_bar()


def calculate_and_display_prayer_times(self, location_data):
    """Calculates prayer times locally for the current month and displays them."""
    # Show "calculating..." message
    self.update_prayer_times_display(
        f"Calculating prayer times for {location_data['name']}"
    )
    self.root.update_idletasks()

    try:
        # Coordinates
        lat_dec = (
            location_data.get("optimized_lat")
            if location_data.get("optimized_lat") is not None
            else location_data["latitude"]
        )
        lon_dec = (
            location_data.get("optimized_lon")
            if location_data.get("optimized_lon") is not None
            else location_data["longitude"]
        )

        # Timezone
        tz_name = location_data["timezone"] if self.dst_var.get() else None
        tz = _get_pytz().timezone(location_data["timezone"])
        today = datetime.date.today()
        dt = datetime.datetime(today.year, today.month, today.day, 12)
        tz_offset_hours = tz.utcoffset(dt).total_seconds() / 3600.0

        # Month/year
        year = today.year
        month = self.month_var.get()
        num_days = calendar.monthrange(year, month)[1]

        # Reference times
        ref_file = self._get_reference_file_path(location_data)
        ref_times = (
            self._parse_reference_times(ref_file)
            if ref_file and os.path.exists(ref_file)
            else None
        )

        # Prepare the text widget
        self.prayer_times_text.config(state=tk.NORMAL)
        self.prayer_times_text.delete("1.0", tk.END)

        # Header lines
        month_name = datetime.date(year, month, 1).strftime("%B")
        header_lines = [
            f"Prayer Times for {location_data['name']} - {month_name} {year}",
            f"Lat: {lat_dec:.5f}, Lon: {lon_dec:.5f}, Elev: {location_data['elevation']}m, "
            + (tz_name if tz_name else f"{tz_offset_hours:+.1f}h"),
            f"Fajr Angle: {location_data['fajr_angle']}°, "
            + (
                f"Isha Minutes: {location_data['isha_minutes']}m"
                if location_data["isha_minutes"]
                else f"Isha Angle: {location_data['isha_angle']}°"
            ),
            "-" * 80,
            "Date      Fajr         Shurooq       Dhuhr        Asr          Maghrib      Isha",
            "-" * 80,
            "",
        ]
        for line in header_lines:
            self.prayer_times_text.insert(tk.END, line + "\n")

        # Column settings
        date_width = 9
        time_width = 12
        prayers = ["fajr", "shurooq", "dhuhr", "asr", "maghrib", "isha"]

        # Load residual correction model (if fitted)
        _res_model = None
        _rc_json = location_data.get("residual_corrections", "")
        if _rc_json:
            try:
                from src.app.infrastructure.residual_model import (
                    PrayerResidualModel,
                )

                _res_model = PrayerResidualModel.from_json(_rc_json)
                if _res_model is not None and not _res_model.fitted:
                    _res_model = None
            except Exception:
                _res_model = None

        # Generate rows
        for day in range(1, num_days + 1):
            current_date = datetime.date(year, month, day)
            date_str = current_date.strftime("%#d-%b")

            # Insert date
            self.prayer_times_text.insert(tk.END, f"{date_str:<{date_width}}")

            # Per-date residual corrections (added to constant offsets)
            _fajr_off = float(location_data.get("fajr_offset", 0.0) or 0.0)
            _shurooq_off = float(location_data.get("shurooq_offset", 0.0) or 0.0)
            _dhuhr_off = float(location_data.get("dhuhr_offset", 0.0) or 0.0)
            _asr_off = float(location_data.get("asr_offset", 0.0) or 0.0)
            _maghrib_off = float(location_data.get("maghrib_offset", 0.0) or 0.0)
            _isha_off = float(location_data.get("isha_offset", 0.0) or 0.0)
            if _res_model is not None:
                _rc = _res_model.predict_all(current_date)
                _fajr_off += _rc.get("fajr", 0.0)
                _shurooq_off += _rc.get("shurooq", 0.0)
                _dhuhr_off += _rc.get("dhuhr", 0.0)
                _asr_off += _rc.get("asr", 0.0)
                _maghrib_off += _rc.get("maghrib", 0.0)
                _isha_off += _rc.get("isha", 0.0)

            # Apply clock-shift offset (DST in reference source)
            _clk = _get_clock_offset_for_date(
                location_data.get("clock_offsets", ""), current_date
            )
            if _clk:
                _fajr_off += _clk
                _shurooq_off += _clk
                _dhuhr_off += _clk
                _asr_off += _clk
                _maghrib_off += _clk
                _isha_off += _clk

            # Calculate times, methods, and error
            times, methods, error_msg = _get_calculate_prayer_times()(
                lat_dec=lat_dec,
                lon_dec=lon_dec,
                elevation=location_data.get("elevation", 0.0),
                pressure=location_data.get("pressure", 1010.0),
                temp=location_data.get("temp", 10.0),
                tz_offset_hours=tz_offset_hours,
                tz_name=tz_name,
                calculation_method=location_data["calculation_method"],
                fajr_angle=location_data.get("fajr_angle", 18.0),
                isha_angle=location_data.get("isha_angle", 18.0),
                isha_minutes=location_data.get("isha_minutes", 0.0),
                isha_shafaq=location_data.get("isha_shafaq", "general"),
                asr_madhab=location_data["asr_madhab"],
                isha_harag=location_data["isha_harag"],
                high_lat_method=location_data["high_lat_method"],
                target_date=current_date,
                high_lat_start_date=location_data.get("high_lat_start_date", None),
                high_lat_end_date=location_data.get("high_lat_end_date", None),
                custom_fajr_angle=location_data.get("custom_fajr_angle"),
                custom_isha_angle=location_data.get("custom_isha_angle"),
                high_lat_fallback_method=location_data.get("high_lat_fallback_method"),
                fajr_offset=_fajr_off,
                shurooq_offset=_shurooq_off,
                dhuhr_offset=_dhuhr_off,
                asr_offset=_asr_off,
                maghrib_offset=_maghrib_off,
                isha_offset=_isha_off,
                rounding=self.rounding_var.get(),
            )

            if error_msg:
                # On error, fill each prayer slot with "Error"
                for _ in prayers:
                    self.prayer_times_text.insert(tk.END, f"{'Error':<{time_width}}")
            else:
                for prayer in prayers:
                    # Base time + high-latitude symbol
                    disp_time = times[prayer]
                    symbol = {0: "°", 1: "˅", 2: "!", 3: "$"}.get(
                        methods.get(prayer, 0), ""
                    )
                    base_str = f"{disp_time}{symbol}"
                    self.prayer_times_text.insert(tk.END, base_str)

                    # Compute diff
                    diff_str = ""
                    if ref_times and date_str in ref_times:
                        ref = ref_times[date_str].get(prayer)
                        if ref:
                            diff = self._calculate_time_difference(disp_time, ref)
                            if (
                                diff is not None
                                and diff != 0
                                and self.rounding_var.get() != "off"
                            ):
                                diff_str = f" ({diff:+.0f})"
                                # Record start, insert diff, tag with correct color
                                start_index = self.prayer_times_text.index("insert")
                                self.prayer_times_text.insert(tk.END, diff_str)
                                end_index = self.prayer_times_text.index("insert")
                                tag_name = f"diff_{day}_{prayer}"
                                color = self._get_color_for_difference(diff)
                                self.prayer_times_text.tag_add(
                                    tag_name, start_index, end_index
                                )
                                self.prayer_times_text.tag_config(
                                    tag_name, foreground=color
                                )

                    # Pad to full width
                    padding = time_width - len(base_str) - len(diff_str)
                    if padding > 0:
                        self.prayer_times_text.insert(tk.END, " " * padding)

            # Newline
            self.prayer_times_text.insert(tk.END, "\n")

        # Add indicator legend at the bottom
        self.prayer_times_text.insert(tk.END, "-" * 85 + "\n")  # Separator
        legend = "Indicators: "
        legend += (
            "°: Angle-Based Fraction  ˅: One Seventh  !: Midnight  $: Aqrab Al-Bilad"
        )
        # Add more legends if needed, e.g., M: Fixed Minutes
        self.prayer_times_text.insert(tk.END, legend + "\n")

        # Disable editing
        self.prayer_times_text.config(state=tk.DISABLED)

    except Exception as e:
        error_display = (
            f"An error occurred calculating times for {location_data['name']}:\n{e}"
        )
        self.update_prayer_times_display(error_display)
        print(traceback.format_exc())


def refresh_prayer_times(self):
    """Refreshes the prayer times display with current settings."""
    selected_data = self.get_selected_location_data()
    if selected_data:
        self.calculate_and_display_prayer_times(selected_data)


def on_month_select(self, _, combo):
    """Convert month name to number and update the month variable"""
    month_name = combo.get()
    try:
        # Convert month name to number (1-12)
        month_num = datetime.datetime.strptime(month_name, "%B").month
        self.month_var.set(month_num)
        # self.refresh_prayer_times()
        self.on_tab_changed()  # Trigger tab change to refresh times
    except ValueError as e:
        print(f"Error converting month: {e}")
        # Reset to current month on error
        current_month = datetime.date.today().month
        self.month_var.set(current_month)
        combo.current(current_month - 1)


def copy_times_to_clipboard(self):
    """Copies the current month's prayer times in Excel-compatible format."""
    selected_data = self.get_selected_location_data()
    if not selected_data:
        messagebox.showwarning("No Selection", "Please select a city first.")
        return

    try:
        # Use optimized coordinates if present
        lat_dec = (
            selected_data.get("optimized_lat")
            if selected_data.get("optimized_lat") is not None
            else selected_data["latitude"]
        )
        lon_dec = (
            selected_data.get("optimized_lon")
            if selected_data.get("optimized_lon") is not None
            else selected_data["longitude"]
        )

        if lat_dec is None or lon_dec is None:
            raise ValueError("Invalid DMS coordinates, cannot calculate.")

        # Get current year and selected month
        year = datetime.date.today().year
        month = self.month_var.get()
        num_days = calendar.monthrange(year, month)[1]

        # Build the tab-separated string
        clipboard_text = ""

        tz_name = None

        if self.dst_var.get():
            # If DST is enabled, set the timezone name
            tz_name = selected_data["timezone"]

        # Calculate tz_offset_hours from timezone string
        tz = _get_pytz().timezone(selected_data["timezone"])
        today = datetime.date.today()
        dt = datetime.datetime(today.year, today.month, today.day, 12, 0, 0)
        tz_offset_hours = tz.utcoffset(dt).total_seconds() / 3600.0

        # Load residual correction model (if fitted)
        _res_model2 = None
        _rc_json2 = selected_data.get("residual_corrections", "")
        if _rc_json2:
            try:
                from src.app.infrastructure.residual_model import (
                    PrayerResidualModel,
                )

                _res_model2 = PrayerResidualModel.from_json(_rc_json2)
                if _res_model2 is not None and not _res_model2.fitted:
                    _res_model2 = None
            except Exception:
                _res_model2 = None

        for day in range(1, num_days + 1):
            current_date = datetime.date(year, month, day)
            # Use cross-platform compatible date formatting
            date_str = f"{day}-{current_date.strftime('%b')}"

            # Per-date residual corrections
            _fo2 = float(selected_data.get("fajr_offset", 0.0) or 0.0)
            _so2 = float(selected_data.get("shurooq_offset", 0.0) or 0.0)
            _do2 = float(selected_data.get("dhuhr_offset", 0.0) or 0.0)
            _ao2 = float(selected_data.get("asr_offset", 0.0) or 0.0)
            _mo2 = float(selected_data.get("maghrib_offset", 0.0) or 0.0)
            _io2 = float(selected_data.get("isha_offset", 0.0) or 0.0)
            if _res_model2 is not None:
                _rc2 = _res_model2.predict_all(current_date)
                _fo2 += _rc2.get("fajr", 0.0)
                _so2 += _rc2.get("shurooq", 0.0)
                _do2 += _rc2.get("dhuhr", 0.0)
                _ao2 += _rc2.get("asr", 0.0)
                _mo2 += _rc2.get("maghrib", 0.0)
                _io2 += _rc2.get("isha", 0.0)

            # Apply clock-shift offset (DST in reference source)
            _clk2 = _get_clock_offset_for_date(
                selected_data.get("clock_offsets", ""), current_date
            )
            if _clk2:
                _fo2 += _clk2
                _so2 += _clk2
                _do2 += _clk2
                _ao2 += _clk2
                _mo2 += _clk2
                _io2 += _clk2

            times, _, error = _get_calculate_prayer_times()(
                lat_dec=lat_dec,
                lon_dec=lon_dec,
                elevation=selected_data["elevation"],
                pressure=selected_data.get("pressure"),
                temp=selected_data.get("temp"),
                tz_offset_hours=tz_offset_hours,
                tz_name=tz_name,
                calculation_method=selected_data["calculation_method"],
                fajr_angle=selected_data["fajr_angle"],
                isha_angle=selected_data["isha_angle"],
                isha_minutes=selected_data["isha_minutes"],
                isha_shafaq=selected_data["isha_shafaq"],
                asr_madhab=selected_data["asr_madhab"],
                isha_harag=selected_data["isha_harag"],
                high_lat_method=selected_data["high_lat_method"],
                target_date=current_date,
                high_lat_start_date=selected_data.get("high_lat_start_date", None),
                high_lat_end_date=selected_data.get("high_lat_end_date", None),
                custom_fajr_angle=selected_data.get("custom_fajr_angle"),
                custom_isha_angle=selected_data.get("custom_isha_angle"),
                high_lat_fallback_method=selected_data.get("high_lat_fallback_method"),
                fajr_offset=_fo2,
                shurooq_offset=_so2,
                dhuhr_offset=_do2,
                asr_offset=_ao2,
                maghrib_offset=_mo2,
                isha_offset=_io2,
                rounding=self.rounding_var.get(),
            )

            if error:
                messagebox.showerror(
                    "Calculation Error",
                    f"Error calculating times for {date_str}: {error}",
                )
                return

            # Format times for Excel
            times_list = []
            for prayer in ["fajr", "shurooq", "dhuhr", "asr", "maghrib", "isha"]:
                time_str = times.get(prayer, "N/A")
                times_list.append(time_str)

            # Join with tabs and add newline
            line = f"{date_str}\t" + "\t".join(times_list)
            clipboard_text += line + "\n"

        # Copy to clipboard
        self.root.clipboard_clear()
        self.root.clipboard_append(clipboard_text)
        self.root.update()  # Required for clipboard to work

    except Exception as e:
        messagebox.showerror("Error", f"An error occurred while copying times: {e}")
