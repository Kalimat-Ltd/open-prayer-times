# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false, reportOptionalMemberAccess=false, reportArgumentType=false
# ruff: noqa: E722, BLE001, F541
# pylint: disable=broad-exception-caught,bare-except

"""Conclusion summary and status-bar aggregation view methods for the GUI."""

import tkinter as tk
import os
import datetime
import calendar

from src.app.presentation.gui.shared import (
    _get_calculate_prayer_times,
    _get_clock_offset_for_date,
    _get_pytz,
)


def show_conclusion_summary(self, location_data):
    self.conclusion_text.config(state=tk.NORMAL)
    self.conclusion_text.delete("1.0", tk.END)
    get_reference_file_path = getattr(self, "_get_reference_file_path", None)
    if not callable(get_reference_file_path):
        self.conclusion_text.insert(tk.END, "Reference file resolver is unavailable.\n")
        self.conclusion_text.config(state=tk.DISABLED)
        return
    ref_file = get_reference_file_path(location_data)
    ref_file_path = str(ref_file) if ref_file else ""
    if not ref_file_path or not os.path.exists(ref_file_path):
        self.conclusion_text.insert(
            tk.END, "No reference data found for this location.\n"
        )
        self.conclusion_text.config(state=tk.DISABLED)
        return
    # Use the city's reference_year (the leap-cycle year the reference data was
    # collected for) so errors are computed against the correct calendar year.
    # Fall back to year_var (display year) then today's year.
    _raw_ref_yr = location_data.get("reference_year") or ""
    if str(_raw_ref_yr).strip().isdigit():
        year = int(str(_raw_ref_yr).strip())
    else:
        _yv = getattr(self, "year_var", None)
        year = int(_yv.get()) if _yv is not None else datetime.date.today().year
    prayers = ["fajr", "shurooq", "dhuhr", "asr", "maghrib", "isha"]
    offset_fields = [
        "fajr_offset",
        "shurooq_offset",
        "dhuhr_offset",
        "asr_offset",
        "maghrib_offset",
        "isha_offset",
    ]
    month_names = [calendar.month_name[m] for m in range(1, 13)]
    monthly_errors = {m: {p: [] for p in prayers} for m in range(1, 13)}
    monthly_signed_errors = {m: {p: [] for p in prayers} for m in range(1, 13)}
    total_errors = {p: 0 for p in prayers}
    total_signed_errors = {p: 0 for p in prayers}
    total_counts = {p: 0 for p in prayers}
    all_reference_times = {}
    try:
        with open(ref_file_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) != 7:
                    continue
                date_str, fajr, sunrise, dhuhr, asr, maghrib, isha = parts
                try:
                    date_obj = datetime.datetime.strptime(date_str, "%d-%b")
                    m = date_obj.month
                    d = date_obj.day
                    all_reference_times[(m, d)] = {
                        "fajr": fajr,
                        "shurooq": sunrise,
                        "dhuhr": dhuhr,
                        "asr": asr,
                        "maghrib": maghrib,
                        "isha": isha,
                    }
                except Exception:
                    continue
    except Exception as e:
        self.conclusion_text.insert(tk.END, f"Error reading reference file: {e}\n")
        self.conclusion_text.config(state=tk.DISABLED)
        return
    months_with_data = set(m for (m, d) in all_reference_times.keys())
    # Use current offsets from city data
    offsets = {field: location_data.get(field, 0.0) for field in offset_fields}
    # Load residual correction model for comparison display
    _res_model3 = None
    _rc_json3 = location_data.get("residual_corrections", "")
    if _rc_json3:
        try:
            from src.app.infrastructure.residual_model import PrayerResidualModel

            _res_model3 = PrayerResidualModel.from_json(_rc_json3)
            if _res_model3 is not None and not _res_model3.fitted:
                _res_model3 = None
        except Exception:
            _res_model3 = None
    # Calculate for each month (only those with data)
    for m in months_with_data:
        num_days = calendar.monthrange(year, m)[1]
        for day in range(1, num_days + 1):
            ref = all_reference_times.get((m, day))
            if not ref:
                continue
            # Apply per-date residual corrections to offsets
            day_offsets = dict(offsets)
            if _res_model3 is not None:
                _rc3 = _res_model3.predict_all(datetime.date(year, m, day))
                _prayer_to_field3 = {
                    "fajr": "fajr_offset",
                    "shurooq": "shurooq_offset",
                    "dhuhr": "dhuhr_offset",
                    "asr": "asr_offset",
                    "maghrib": "maghrib_offset",
                    "isha": "isha_offset",
                }
                for _p3, _f3 in _prayer_to_field3.items():
                    day_offsets[_f3] = float(
                        day_offsets.get(_f3, 0.0) or 0.0
                    ) + _rc3.get(_p3, 0.0)
            # Apply clock-shift offset (DST in reference source)
            _clk3 = _get_clock_offset_for_date(
                location_data.get("clock_offsets", ""),
                datetime.date(year, m, day),
            )
            if _clk3:
                for _f3 in (
                    "fajr_offset",
                    "shurooq_offset",
                    "dhuhr_offset",
                    "asr_offset",
                    "maghrib_offset",
                    "isha_offset",
                ):
                    day_offsets[_f3] = float(day_offsets.get(_f3, 0.0) or 0.0) + _clk3
            try:
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
                tz_name = None
                if self.dst_var.get():
                    tz_name = location_data["timezone"]
                tz = _get_pytz().timezone(location_data["timezone"])
                dt = datetime.datetime(year, m, day, 12, 0, 0)
                tz_offset_hours = tz.utcoffset(dt).total_seconds() / 3600.0
                calc_kwargs = dict(
                    lat_dec=lat_dec,
                    lon_dec=lon_dec,
                    elevation=location_data["elevation"],
                    pressure=location_data["pressure"],
                    temp=location_data["temp"],
                    tz_offset_hours=tz_offset_hours,
                    tz_name=tz_name,
                    calculation_method=location_data.get("calculation_method")
                    or "angle_based",
                    fajr_angle=location_data["fajr_angle"],
                    isha_angle=location_data["isha_angle"],
                    isha_minutes=location_data["isha_minutes"],
                    isha_shafaq=location_data.get("isha_shafaq") or "general",
                    asr_madhab=location_data.get("asr_madhab") or 0,
                    asr_madhab_overrides=location_data.get("asr_madhab_overrides")
                    or "",
                    isha_harag=location_data.get("isha_harag") or 0,
                    high_lat_method=location_data.get("high_lat_method") or 0,
                    target_date=datetime.date(year, m, day),
                    high_lat_start_date=location_data.get("high_lat_start_date"),
                    high_lat_end_date=location_data.get("high_lat_end_date"),
                    custom_fajr_angle=location_data.get("custom_fajr_angle"),
                    custom_isha_angle=location_data.get("custom_isha_angle"),
                    high_lat_fallback_method=location_data.get(
                        "high_lat_fallback_method"
                    ),
                    rounding=self.rounding_var.get() or "nearest",
                )
                calc_kwargs.update(day_offsets)
                times, _, error_msg = _get_calculate_prayer_times()(**calc_kwargs)  # type: ignore[arg-type]
                if error_msg:
                    continue
                calculate_time_difference = getattr(
                    self, "_calculate_time_difference", None
                )
                if not callable(calculate_time_difference):
                    continue
                for _, p in enumerate(prayers):
                    calc_time = times.get(p, "N/A")
                    ref_time = ref.get(p, "N/A")
                    if calc_time != "N/A" and ref_time:
                        diff = calculate_time_difference(calc_time, ref_time)
                        if diff is not None:
                            diff_val = float(diff)
                            monthly_errors[m][p].append(abs(diff_val))
                            monthly_signed_errors[m][p].append(diff_val)
                            total_errors[p] += abs(diff_val)
                            total_signed_errors[p] += diff_val
                            total_counts[p] += 1
            except Exception:
                continue
    # Prepare summary (only for months with data)
    summary = "Summary of Errors for Months with Reference Data\n" + ("-" * 160) + "\n"
    summary += (
        f"{'Month':<10} "
        + " ".join([f"{p.title():<18}" for p in prayers])
        + "  |  Unsigned Total  |  Signed Total\n"
    )
    summary += ("-" * 160) + "\n"
    month_totals = {}
    month_signed_totals = {}
    for m in sorted(months_with_data):
        month_total = 0
        month_signed_total = 0
        summary += f"{month_names[m-1]:<10} "
        for p in prayers:
            vals = monthly_errors[m][p]
            signed_vals = monthly_signed_errors[m][p]
            abs_avg = sum(vals) / len(vals) if vals else 0
            signed_avg = sum(signed_vals) / len(signed_vals) if signed_vals else 0
            summary += f"{abs_avg:8.2f} ({signed_avg:+6.2f})  "
            month_total += sum(vals)
            month_signed_total += sum(signed_vals)
        month_totals[m] = month_total
        month_signed_totals[m] = month_signed_total
        summary += f"|   {month_total:8.2f}    |   {month_signed_total:+8.2f}\n"
    summary += ("-" * 160) + "\n"
    # Total and average per prayer (only for months with data)
    summary += "Total error for each prayer (minutes):\n"
    for p in prayers:
        summary += f"  {p.title():<10}: {total_errors[p]:.2f}\n"
    summary += "\nAverage error for each prayer (minutes):\n"
    for p in prayers:
        abs_avg = total_errors[p] / total_counts[p] if total_counts[p] else 0
        signed_avg = total_signed_errors[p] / total_counts[p] if total_counts[p] else 0
        summary += f"  {p.title():<10}: {abs_avg:.2f} ({signed_avg:+.2f})\n"
    # Total and average per month (only for months with data)
    total_error_all = sum(total_errors.values())
    num_months = len(months_with_data)
    summary += f"\nTotal error for all months: {total_error_all:.2f} minutes\n"
    summary += (
        f"Average error per month: {total_error_all/num_months:.2f} minutes\n"
        if num_months
        else "Average error per month: N/A\n"
    )
    # Most/least accurate months (only among months with data)
    if month_totals:
        most_accurate = min(month_totals, key=lambda m: month_totals[m])
        least_accurate = max(month_totals, key=lambda m: month_totals[m])
        summary += f"\nMost accurate month: {month_names[most_accurate-1]} (total error: {month_totals[most_accurate]:.2f})\n"
        summary += f"Least accurate month: {month_names[least_accurate-1]} (total error: {month_totals[least_accurate]:.2f})\n"
    self.conclusion_text.insert(tk.END, summary)
    self.conclusion_text.config(state=tk.DISABLED)


def update_status_bar(self):
    """Update the status bar with country/city optimization stats."""
    # Get all country codes from locations
    all_country_codes = set(
        loc["country_code"] for loc in self.locations_data if loc.get("country_code")
    )
    total_countries = len(all_country_codes)

    # Group cities by country
    country_to_cities = {}
    for loc in self.locations_data:
        code = loc.get("country_code")
        if not code:
            continue
        country_to_cities.setdefault(code, []).append(loc)

    # Reference-based optimization (existing logic)
    reference_dir = self.reference_dir
    countries_with_ref = set()
    for code in all_country_codes:
        country_folder = os.path.join(reference_dir, code)
        if os.path.isdir(country_folder) and any(
            f.endswith(".txt") for f in os.listdir(country_folder)
        ):
            countries_with_ref.add(code)
    num_optimized_countries_ref = len(countries_with_ref)

    # CSV-based optimization: count countries with at least one city having is_optimized = 1
    countries_with_optimized_city = {
        loc["country_code"]
        for loc in self.locations_data
        if loc.get("country_code") and loc.get("is_optimized") == 1
    }
    num_countries_with_optimized_city = len(countries_with_optimized_city)

    # City-level optimization: count cities flagged as optimized vs all cities
    total_cities = len(self.locations_data)
    optimized_city_count = sum(
        1 for loc in self.locations_data if loc.get("is_optimized") == 1
    )

    # Update status bar text
    if total_countries > 0 and total_cities > 0:
        self.status_bar.config(
            text=(
                f"Countries with reference: {num_optimized_countries_ref}/{total_countries} "
                f"({num_optimized_countries_ref/total_countries*100:.2f}%); "
                f"Countries with optimized cities: {num_countries_with_optimized_city}/{total_countries} "
                f"({num_countries_with_optimized_city/total_countries*100:.2f}%); "
                f"Optimized cities: {optimized_city_count}/{total_cities} "
                f"({optimized_city_count/total_cities*100:.2f}%)"
            )
        )
