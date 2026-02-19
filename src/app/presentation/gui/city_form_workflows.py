"""Add/modify city form construction and validation workflow methods."""

import re
import traceback
import tkinter as tk
from tkinter import ttk, messagebox
import os
import csv
import shutil
import datetime
import calendar
import math
from pathlib import Path
from typing import Any, cast

from src.app.config import LOC_CSV_PATH, REFERENCE_DIR, RESOURCES_DIR
from src.app.presentation.gui.shared import (
    _get_calculate_prayer_times,
    _get_clock_offset_for_date,
    _get_geopy_distance,
    _get_open_batch_optimization_dashboard,
    _get_optimize_parameters_for_city,
    _get_pytz,
    _lazy_imports,
    _make_reference_folder_handler_class,
    field_names,
    rewrite_location_file,
)


def _create_city_form(self, parent_window, initial_data=None):
    """Creates the labels and entry fields for the city form, including decimal coords and country code."""
    entries = {}
    string_vars = {}

    # --- Scrollable container ---
    outer_frame = ttk.Frame(parent_window)
    # Don't pack yet — caller may pack buttons at BOTTOM first
    # outer_frame will be packed via the returned frame reference
    self._form_outer_frame = outer_frame

    canvas = tk.Canvas(outer_frame, highlightthickness=0)
    v_scrollbar = ttk.Scrollbar(outer_frame, orient=tk.VERTICAL, command=canvas.yview)
    canvas.configure(yscrollcommand=v_scrollbar.set)
    v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

    frame = ttk.Frame(canvas, padding="15")
    frame_window = canvas.create_window((0, 0), window=frame, anchor="nw")

    def _on_frame_configure(event):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _on_canvas_configure(event):
        canvas.itemconfig(frame_window, width=event.width)

    frame.bind("<Configure>", _on_frame_configure)
    canvas.bind("<Configure>", _on_canvas_configure)

    # Mouse wheel scrolling
    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind_all("<MouseWheel>", _on_mousewheel)

    # Unbind mouse wheel when window is destroyed
    def _on_destroy(event):
        if event.widget == parent_window:
            try:
                canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass

    parent_window.bind("<Destroy>", _on_destroy)

    # Load country codes for dropdown
    country_codes = []
    country_codes_path = os.path.join(self.resources_dir, "country_codes.csv")
    try:
        with open(country_codes_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row["ISO CODES"].strip()
                country = row["COUNTRY"].strip()
                country_codes.append((code, country))
    except Exception:
        country_codes = []

    def create_entry(field, row, trace_var=None, width=40, readonly=False):
        label_text = field.replace("_", " ").title()
        if field == "timezone":
            label_text += " (e.g., 'America/New_York')"
        elif field == "isha_minutes":
            label_text += " (leave blank if None)"
        ttk.Label(frame, text=label_text + ":").grid(
            row=row, column=0, sticky="w", pady=2
        )
        var = tk.StringVar()
        entry = ttk.Entry(
            frame,
            width=width,
            textvariable=var,
            state="readonly" if readonly else "normal",
        )
        entry.grid(row=row, column=1, sticky="ew", padx=5, pady=2)
        if initial_data:
            initial_value = initial_data.get(field)
            var.set("" if initial_value is None else str(initial_value))
        entries[field] = entry
        string_vars[field] = var
        if trace_var:
            var.trace_add("write", trace_var)

    row_num = 0
    # Country code dropdown
    ttk.Label(frame, text="Country Code:").grid(
        row=row_num, column=0, sticky="w", pady=2
    )
    country_code_var = tk.StringVar()
    country_code_combo = ttk.Combobox(
        frame, textvariable=country_code_var, width=10, state="readonly"
    )
    country_code_combo["values"] = [
        f"{code} - {country}" for code, country in country_codes
    ]
    if initial_data and initial_data.get("country_code"):
        for i, (code, country) in enumerate(country_codes):
            if code == initial_data["country_code"]:
                country_code_combo.current(i)
                break
    country_code_combo.grid(row=row_num, column=1, sticky="ew", padx=5, pady=2)
    entries["country_code"] = country_code_combo
    string_vars["country_code"] = country_code_var
    row_num += 1

    create_entry("name", row_num)
    row_num += 1
    ttk.Label(frame, text="--- Coordinates ---").grid(
        row=row_num, column=0, columnspan=2, pady=(5, 2)
    )
    row_num += 1
    create_entry("latitude", row_num)
    row_num += 1
    create_entry("longitude", row_num)
    row_num += 1
    create_entry("optimized_lat", row_num, readonly=False)
    row_num += 1
    create_entry("optimized_lon", row_num, readonly=False)
    row_num += 1
    ttk.Label(frame, text="--- Other Data ---").grid(
        row=row_num, column=0, columnspan=2, pady=(5, 2)
    )
    row_num += 1
    create_entry("timezone", row_num)
    row_num += 1
    create_entry("elevation", row_num)
    row_num += 1
    create_entry("pressure", row_num)
    row_num += 1
    create_entry("temp", row_num)
    row_num += 1
    row_num += 1
    ttk.Label(frame, text="--- Calculation Method ---").grid(
        row=row_num, column=0, columnspan=2, pady=(5, 2)
    )
    row_num += 1

    # Calculation Method dropdown
    ttk.Label(frame, text="Calculation Method:").grid(
        row=row_num, column=0, sticky="w", pady=2
    )
    calculation_method_var = tk.StringVar()
    calculation_method_combo = ttk.Combobox(
        frame, textvariable=calculation_method_var, width=30, state="readonly"
    )
    calculation_method_combo["values"] = ["angle_based", "moonsighting"]
    if initial_data and initial_data.get("calculation_method"):
        calculation_method_combo.set(initial_data["calculation_method"])
    else:
        calculation_method_combo.set("angle_based")
    calculation_method_combo.grid(row=row_num, column=1, sticky="ew", padx=5, pady=2)
    entries["calculation_method"] = calculation_method_combo
    string_vars["calculation_method"] = calculation_method_var
    row_num += 1

    # --- Angle-based fields (only visible for angle_based) ---
    fajr_angle_label = ttk.Label(frame, text="Fajr Angle:")
    fajr_angle_var = tk.StringVar()
    fajr_angle_entry = ttk.Entry(frame, width=40, textvariable=fajr_angle_var)
    if initial_data and initial_data.get("fajr_angle") is not None:
        fajr_angle_var.set(str(initial_data["fajr_angle"]))
    entries["fajr_angle"] = fajr_angle_entry
    string_vars["fajr_angle"] = fajr_angle_var

    isha_angle_label = ttk.Label(frame, text="Isha Angle:")
    isha_angle_var = tk.StringVar()
    isha_angle_entry = ttk.Entry(frame, width=40, textvariable=isha_angle_var)
    if initial_data and initial_data.get("isha_angle") is not None:
        isha_angle_var.set(str(initial_data["isha_angle"]))
    entries["isha_angle"] = isha_angle_entry
    string_vars["isha_angle"] = isha_angle_var

    isha_minutes_label = ttk.Label(frame, text="Isha Minutes:")
    isha_minutes_var = tk.StringVar()
    isha_minutes_entry = ttk.Entry(frame, width=40, textvariable=isha_minutes_var)
    if initial_data and initial_data.get("isha_minutes") is not None:
        isha_minutes_var.set(str(initial_data["isha_minutes"]))
    entries["isha_minutes"] = isha_minutes_entry
    string_vars["isha_minutes"] = isha_minutes_var

    # --- Isha Shafaq dropdown (only visible for moonsighting) ---
    isha_shafaq_label = ttk.Label(frame, text="Isha Shafaq:")
    isha_shafaq_var = tk.StringVar()
    isha_shafaq_combo = ttk.Combobox(
        frame, textvariable=isha_shafaq_var, width=30, state="readonly"
    )
    isha_shafaq_combo["values"] = ["ahmer", "abyad", "general"]
    if initial_data and initial_data.get("isha_shafaq"):
        isha_shafaq_combo.set(initial_data["isha_shafaq"])
    else:
        isha_shafaq_combo.set("general")
    entries["isha_shafaq"] = isha_shafaq_combo
    string_vars["isha_shafaq"] = isha_shafaq_var

    # Placeholders for dynamic row
    dynamic_row = row_num
    row_num += 1

    # --- Store previous angle values when switching away, restore when switching back ---
    angle_cache = {
        "fajr": fajr_angle_var.get(),
        "isha": isha_angle_var.get(),
        "minutes": isha_minutes_var.get(),
    }

    def update_method_fields(*args):
        method = calculation_method_var.get()
        nonlocal angle_cache
        # Remove all dynamic widgets first
        for widget in [
            fajr_angle_label,
            fajr_angle_entry,
            isha_angle_label,
            isha_angle_entry,
            isha_minutes_label,
            isha_minutes_entry,
            isha_shafaq_label,
            isha_shafaq_combo,
        ]:
            widget.grid_remove()
        if method == "angle_based":
            # Restore cached values
            fajr_angle_var.set(angle_cache.get("fajr", ""))
            isha_angle_var.set(angle_cache.get("isha", ""))
            isha_minutes_var.set(angle_cache.get("minutes", ""))
            fajr_angle_label.grid(row=dynamic_row, column=0, sticky="w", pady=2)
            fajr_angle_entry.grid(
                row=dynamic_row, column=1, sticky="ew", padx=5, pady=2
            )
            isha_angle_label.grid(row=dynamic_row + 1, column=0, sticky="w", pady=2)
            isha_angle_entry.grid(
                row=dynamic_row + 1, column=1, sticky="ew", padx=5, pady=2
            )
            isha_minutes_label.grid(row=dynamic_row + 2, column=0, sticky="w", pady=2)
            isha_minutes_entry.grid(
                row=dynamic_row + 2, column=1, sticky="ew", padx=5, pady=2
            )
        else:  # moonsighting
            # Cache current angle values before hiding
            angle_cache["fajr"] = fajr_angle_var.get()
            angle_cache["isha"] = isha_angle_var.get()
            angle_cache["minutes"] = isha_minutes_var.get()
            isha_shafaq_label.grid(row=dynamic_row, column=0, sticky="w", pady=2)
            isha_shafaq_combo.grid(
                row=dynamic_row, column=1, sticky="ew", padx=5, pady=2
            )

    calculation_method_var.trace_add("write", update_method_fields)
    update_method_fields()

    row_num = dynamic_row + 3

    # Remaining fields
    # Asr Madhab dropdown
    ttk.Label(frame, text="Asr Madhab:").grid(
        row=row_num + 1, column=0, sticky="w", pady=2
    )
    asr_madhab_var = tk.StringVar()
    asr_madhab_combo = ttk.Combobox(
        frame, textvariable=asr_madhab_var, width=30, state="readonly"
    )
    asr_madhab_combo["values"] = ["Standard (Shafi, Maliki, Hanbali)", "Hanafi"]
    if initial_data and initial_data.get("asr_madhab") is not None:
        asr_madhab_combo.current(int(initial_data["asr_madhab"]))
    else:
        asr_madhab_combo.current(0)
    asr_madhab_combo.grid(row=row_num + 1, column=1, sticky="ew", padx=5, pady=2)
    entries["asr_madhab"] = asr_madhab_combo
    string_vars["asr_madhab"] = asr_madhab_var
    row_num += 1

    ttk.Label(frame, text="Isha Harag:").grid(
        row=row_num + 1, column=0, sticky="w", pady=2
    )
    isha_harag_var = tk.StringVar()
    isha_harag_combo = ttk.Combobox(
        frame, textvariable=isha_harag_var, width=30, state="readonly"
    )
    isha_harag_combo["values"] = ["Off", "Method 1", "Method 2", "Method 3"]
    if initial_data and initial_data.get("isha_harag") is not None:
        isha_harag_combo.current(int(initial_data["isha_harag"]))
    else:
        isha_harag_combo.current(0)
    isha_harag_combo.grid(row=row_num + 1, column=1, sticky="ew", padx=5, pady=2)
    entries["isha_harag"] = isha_harag_combo
    string_vars["isha_harag"] = isha_harag_var
    row_num += 1

    # High Latitude Method dropdown
    ttk.Label(frame, text="High Latitude Method:").grid(
        row=row_num + 1, column=0, sticky="w", pady=2
    )
    high_lat_method_var = tk.StringVar()
    high_lat_method_combo = ttk.Combobox(
        frame, textvariable=high_lat_method_var, width=30, state="readonly"
    )
    high_lat_method_combo["values"] = [
        "Angle Based",
        "One Seventh",
        "Midnight",
        "Aqrab Al-Bilad",
    ]
    if initial_data and initial_data.get("high_lat_method") is not None:
        high_lat_method_combo.current(int(initial_data["high_lat_method"]))
    else:
        high_lat_method_combo.current(0)
    high_lat_method_combo.grid(row=row_num + 1, column=1, sticky="ew", padx=5, pady=2)
    entries["high_lat_method"] = high_lat_method_combo
    string_vars["high_lat_method"] = high_lat_method_var
    row_num += 1

    # High Latitude Start/End Dates
    ttk.Label(frame, text="High Latitude Start Date (YYYY-MM-DD):").grid(
        row=row_num + 1, column=0, sticky="w", pady=2
    )
    hl_start_var = tk.StringVar()
    hl_start_entry = ttk.Entry(frame, width=40, textvariable=hl_start_var)
    if initial_data and initial_data.get("high_lat_start_date"):
        hl_start_var.set(initial_data["high_lat_start_date"])
    hl_start_entry.grid(row=row_num + 1, column=1, sticky="ew", padx=5, pady=2)
    entries["high_lat_start_date"] = hl_start_entry
    string_vars["high_lat_start_date"] = hl_start_var
    row_num += 1

    ttk.Label(frame, text="High Latitude End Date (YYYY-MM-DD):").grid(
        row=row_num + 1, column=0, sticky="w", pady=2
    )
    hl_end_var = tk.StringVar()
    hl_end_entry = ttk.Entry(frame, width=40, textvariable=hl_end_var)
    if initial_data and initial_data.get("high_lat_end_date"):
        hl_end_var.set(initial_data["high_lat_end_date"])
    hl_end_entry.grid(row=row_num + 1, column=1, sticky="ew", padx=5, pady=2)
    entries["high_lat_end_date"] = hl_end_entry
    string_vars["high_lat_end_date"] = hl_end_var
    row_num += 1

    custom_fajr_label = ttk.Label(frame, text="Custom Fajr Angle (Optional):")
    custom_fajr_var = tk.StringVar()
    custom_fajr_entry = ttk.Entry(frame, width=40, textvariable=custom_fajr_var)
    if initial_data and initial_data.get("custom_fajr_angle") is not None:
        custom_fajr_var.set(str(initial_data["custom_fajr_angle"]))
    entries["custom_fajr_angle"] = custom_fajr_entry
    string_vars["custom_fajr_angle"] = custom_fajr_var

    custom_isha_label = ttk.Label(frame, text="Custom Isha Angle (Optional):")
    custom_isha_var = tk.StringVar()
    custom_isha_entry = ttk.Entry(frame, width=40, textvariable=custom_isha_var)
    if initial_data and initial_data.get("custom_isha_angle") is not None:
        custom_isha_var.set(str(initial_data["custom_isha_angle"]))
    entries["custom_isha_angle"] = custom_isha_entry
    string_vars["custom_isha_angle"] = custom_isha_var

    fallback_label = ttk.Label(frame, text="High-Lat Fallback Method:")
    fallback_var = tk.StringVar()
    fallback_combo = ttk.Combobox(
        frame, textvariable=fallback_var, width=30, state="readonly"
    )
    fallback_combo["values"] = ["One Seventh", "Midnight", "Aqrab Al-Bilad"]
    fallback_method_value = (
        int(initial_data["high_lat_fallback_method"])
        if initial_data and initial_data.get("high_lat_fallback_method") is not None
        else 1
    )
    fallback_index = {1: 0, 2: 1, 3: 2}.get(fallback_method_value, 0)
    fallback_combo.current(fallback_index)
    entries["high_lat_fallback_method"] = fallback_combo
    string_vars["high_lat_fallback_method"] = fallback_var

    custom_row_start = row_num + 1

    def _toggle_custom_high_lat_fields(*_args):
        is_angle_based = high_lat_method_var.get() == "Angle Based"
        for widget in (
            custom_fajr_label,
            custom_fajr_entry,
            custom_isha_label,
            custom_isha_entry,
            fallback_label,
            fallback_combo,
        ):
            widget.grid_remove()
        if is_angle_based:
            custom_fajr_label.grid(row=custom_row_start, column=0, sticky="w", pady=2)
            custom_fajr_entry.grid(
                row=custom_row_start, column=1, sticky="ew", padx=5, pady=2
            )
            custom_isha_label.grid(
                row=custom_row_start + 1, column=0, sticky="w", pady=2
            )
            custom_isha_entry.grid(
                row=custom_row_start + 1, column=1, sticky="ew", padx=5, pady=2
            )
            fallback_label.grid(row=custom_row_start + 2, column=0, sticky="w", pady=2)
            fallback_combo.grid(
                row=custom_row_start + 2, column=1, sticky="ew", padx=5, pady=2
            )

    high_lat_method_var.trace_add("write", _toggle_custom_high_lat_fields)
    _toggle_custom_high_lat_fields()

    row_num = custom_row_start + 2

    ttk.Label(frame, text="--- Prayer Time Offsets (Minutes) ---").grid(
        row=row_num + 1, column=0, columnspan=2, pady=(5, 2)
    )
    row_num += 1
    create_entry("fajr_offset", row_num + 1)
    row_num += 1
    create_entry("shurooq_offset", row_num + 1)
    row_num += 1
    create_entry("dhuhr_offset", row_num + 1)
    row_num += 1
    create_entry("asr_offset", row_num + 1)
    row_num += 1
    create_entry("maghrib_offset", row_num + 1)
    row_num += 1
    create_entry("isha_offset", row_num + 1)
    row_num += 1

    # Add is_optimized checkbox
    ttk.Label(frame, text="Is Optimized:").grid(
        row=row_num + 1, column=0, sticky="w", pady=2
    )
    self.is_optimized_var = tk.IntVar(
        value=(initial_data.get("is_optimized", 0) if initial_data else 0)
    )
    is_optimized_checkbox = ttk.Checkbutton(frame, variable=self.is_optimized_var)
    is_optimized_checkbox.grid(row=row_num + 1, column=1, sticky="w", padx=5, pady=2)
    entries["is_optimized"] = self.is_optimized_var.get()
    string_vars["is_optimized"] = self.is_optimized_var.get()
    row_num += 1

    # Add residual_corrections (JSON text area)
    ttk.Label(frame, text="--- Advanced (JSON) ---").grid(
        row=row_num + 1, column=0, columnspan=2, pady=(5, 2)
    )
    row_num += 1

    ttk.Label(frame, text="Residual Corrections:").grid(
        row=row_num + 1, column=0, sticky="nw", pady=2
    )
    residual_frame = ttk.Frame(frame)
    residual_frame.grid(row=row_num + 1, column=1, sticky="ew", padx=5, pady=2)
    residual_text = tk.Text(
        residual_frame, width=50, height=4, wrap=tk.NONE, font=("Courier New", 9)
    )
    residual_scroll_y = ttk.Scrollbar(
        residual_frame, orient=tk.VERTICAL, command=residual_text.yview
    )
    residual_scroll_x = ttk.Scrollbar(
        residual_frame, orient=tk.HORIZONTAL, command=residual_text.xview
    )
    residual_text.configure(
        yscrollcommand=residual_scroll_y.set, xscrollcommand=residual_scroll_x.set
    )
    residual_text.grid(row=0, column=0, sticky="nsew")
    residual_scroll_y.grid(row=0, column=1, sticky="ns")
    residual_scroll_x.grid(row=1, column=0, sticky="ew")
    residual_frame.grid_columnconfigure(0, weight=1)
    residual_frame.grid_rowconfigure(0, weight=1)
    if initial_data and initial_data.get("residual_corrections"):
        residual_text.insert("1.0", initial_data["residual_corrections"])
    entries["residual_corrections"] = residual_text
    row_num += 1

    # Add clock_offsets (JSON text area)
    ttk.Label(frame, text="Clock Offsets:").grid(
        row=row_num + 1, column=0, sticky="nw", pady=2
    )
    clock_frame = ttk.Frame(frame)
    clock_frame.grid(row=row_num + 1, column=1, sticky="ew", padx=5, pady=2)
    clock_offsets_text = tk.Text(
        clock_frame, width=50, height=4, wrap=tk.NONE, font=("Courier New", 9)
    )
    clock_scroll_y = ttk.Scrollbar(
        clock_frame, orient=tk.VERTICAL, command=clock_offsets_text.yview
    )
    clock_scroll_x = ttk.Scrollbar(
        clock_frame, orient=tk.HORIZONTAL, command=clock_offsets_text.xview
    )
    clock_offsets_text.configure(
        yscrollcommand=clock_scroll_y.set, xscrollcommand=clock_scroll_x.set
    )
    clock_offsets_text.grid(row=0, column=0, sticky="nsew")
    clock_scroll_y.grid(row=0, column=1, sticky="ns")
    clock_scroll_x.grid(row=1, column=0, sticky="ew")
    clock_frame.grid_columnconfigure(0, weight=1)
    clock_frame.grid_rowconfigure(0, weight=1)
    if initial_data and initial_data.get("clock_offsets"):
        clock_offsets_text.insert("1.0", initial_data["clock_offsets"])
    entries["clock_offsets"] = clock_offsets_text
    row_num += 1

    entries["is_official"] = initial_data.get("is_official", 0) if initial_data else 0

    frame.grid_columnconfigure(1, weight=1)
    return frame, entries, string_vars


def open_add_city_window(self):
    """Opens a new window to add a city."""
    # (Keep existing code)
    add_window = tk.Toplevel(self.root)
    add_window.title("Add New City")
    add_window.geometry("750x700")
    add_window.transient(self.root)
    add_window.grab_set()
    frame, entries, _ = self._create_city_form(add_window)
    button_frame = ttk.Frame(add_window, padding=(15, 5))
    button_frame.pack(fill=tk.X, side=tk.BOTTOM)
    if self._form_outer_frame is not None:
        self._form_outer_frame.pack(expand=True, fill=tk.BOTH)
    button_frame.columnconfigure(0, weight=1)
    button_frame.columnconfigure(1, weight=1)
    save_button = ttk.Button(
        button_frame,
        text="Save City",
        command=lambda: self.save_new_city(entries, add_window),
    )
    save_button.grid(row=0, column=0, padx=5, sticky="ew")
    cancel_button = ttk.Button(button_frame, text="Cancel", command=add_window.destroy)
    cancel_button.grid(row=0, column=1, padx=5, sticky="ew")


def _validate_and_get_form_data(
    self, entries, window, skip_name_check=False, initial_data=None
):
    """Validates form entries and returns a dictionary, or None."""
    try:
        data = {}
        # Country code
        country_code_combo = entries["country_code"]
        code_val = (
            country_code_combo.get().split(" - ")[0] if country_code_combo.get() else ""
        )
        if not code_val:
            messagebox.showerror("Error", "Country code is required.", parent=window)
            return None
        data["country_code"] = code_val

        # Get and validate name if required
        if not skip_name_check:
            name = entries["name"].get().strip()
            if not name:
                messagebox.showerror("Error", "City name is required.", parent=window)
                return None
            data["name"] = name

        # Calculation method
        calculation_method = entries["calculation_method"].get()
        if not calculation_method:
            messagebox.showerror(
                "Error", "Calculation method is required.", parent=window
            )
            return None
        data["calculation_method"] = calculation_method

        # Isha Shafaq (required for moonsighting method)
        isha_shafaq = entries["isha_shafaq"].get()
        if calculation_method == "moonsighting" and not isha_shafaq:
            messagebox.showerror(
                "Error",
                "Isha Shafaq is required for moonsighting method.",
                parent=window,
            )
            return None
        data["isha_shafaq"] = isha_shafaq

        # Validate numeric fields
        numeric_fields = [
            "latitude",
            "longitude",
            "optimized_lat",
            "optimized_lon",
            "elevation",
            "pressure",
            "temp",
        ]

        # Only validate angle fields if calculation_method is angle_based
        if calculation_method == "angle_based":
            numeric_fields.extend(["fajr_angle", "isha_angle"])

        for field in numeric_fields:
            value = entries[field].get().strip()
            if value == "" or value is None:
                data[field] = None
            else:
                try:
                    data[field] = float(value)
                except ValueError:
                    messagebox.showerror(
                        "Error",
                        f"Invalid numeric value for {field.replace('_', ' ').title()}",
                        parent=window,
                    )
                    return None

        data["timezone"] = entries["timezone"].get().strip()

        # Handle isha_minutes (can be None)
        if calculation_method == "angle_based":
            isha_min = entries["isha_minutes"].get().strip()
            data["isha_minutes"] = float(isha_min) if isha_min else None
        else:
            # Preserve previous values if available
            if initial_data:
                data["fajr_angle"] = initial_data.get("fajr_angle")
                data["isha_angle"] = initial_data.get("isha_angle")
                data["isha_minutes"] = initial_data.get("isha_minutes")
            else:
                data["fajr_angle"] = None
                data["isha_angle"] = None
                data["isha_minutes"] = None

        # Asr Madhab - ensure it's always 0 or 1
        asr_madhab_combo = entries["asr_madhab"]
        asr_madhab_val = asr_madhab_combo.get()
        data["asr_madhab"] = 1 if asr_madhab_val == "Hanafi" else 0

        isha_harag_combo = entries["isha_harag"]
        isha_harag_val = isha_harag_combo.get()
        data["isha_harag"] = ["Off", "Method 1", "Method 2", "Method 3"].index(
            isha_harag_val
        )

        # High Latitude Method - ensure it's always 0, 1, 2 or 3
        high_lat_method_combo = entries["high_lat_method"]
        high_lat_method_val = high_lat_method_combo.get()
        data["high_lat_method"] = [
            "Angle Based",
            "One Seventh",
            "Midnight",
            "Aqrab Al-Bilad",
        ].index(high_lat_method_val)

        # High-latitude date range
        for field in ("high_lat_start_date", "high_lat_end_date"):
            val = entries[field].get().strip()
            if val:
                try:
                    data[field] = datetime.datetime.strptime(val, "%Y-%m-%d").date()
                except ValueError:
                    messagebox.showerror(
                        "Error",
                        f"Invalid date format for {field.replace('_', ' ').title()}. Use YYYY-MM-DD.",
                        parent=window,
                    )
                    return None
            else:
                data[field] = None

        custom_fajr_value = entries["custom_fajr_angle"].get().strip()
        custom_isha_value = entries["custom_isha_angle"].get().strip()

        if custom_fajr_value:
            try:
                data["custom_fajr_angle"] = float(custom_fajr_value)
            except ValueError:
                messagebox.showerror(
                    "Error", "Custom Fajr Angle must be numeric.", parent=window
                )
                return None
            if data["custom_fajr_angle"] <= 0:
                messagebox.showerror(
                    "Error", "Custom Fajr Angle must be greater than 0.", parent=window
                )
                return None
        else:
            data["custom_fajr_angle"] = None

        if custom_isha_value:
            try:
                data["custom_isha_angle"] = float(custom_isha_value)
            except ValueError:
                messagebox.showerror(
                    "Error", "Custom Isha Angle must be numeric.", parent=window
                )
                return None
            if data["custom_isha_angle"] <= 0:
                messagebox.showerror(
                    "Error", "Custom Isha Angle must be greater than 0.", parent=window
                )
                return None
        else:
            data["custom_isha_angle"] = None

        fallback_choice = entries["high_lat_fallback_method"].get().strip()
        fallback_map = {
            "One Seventh": 1,
            "Midnight": 2,
            "Aqrab Al-Bilad": 3,
        }
        data["high_lat_fallback_method"] = fallback_map.get(fallback_choice)
        if (
            data["high_lat_method"] != 0
            and data["custom_fajr_angle"] is None
            and data["custom_isha_angle"] is None
        ):
            data["high_lat_fallback_method"] = None

        # Validate offset fields (default to 0.0 if empty)
        offset_fields = [
            "fajr_offset",
            "shurooq_offset",
            "dhuhr_offset",
            "asr_offset",
            "maghrib_offset",
            "isha_offset",
        ]
        for field in offset_fields:
            value = entries[field].get().strip()
            try:
                data[field] = float(value) if value else 0.0
            except ValueError:
                messagebox.showerror(
                    "Error",
                    f"Invalid offset value for {field.replace('_', ' ').title()}",
                    parent=window,
                )
                return None

        # Handle is_optimized checkbox (store as 0/1)
        data["is_official"] = entries["is_official"]
        data["is_optimized"] = self.is_optimized_var.get()

        # Handle residual_corrections and clock_offsets (Text widgets)
        residual_text = entries["residual_corrections"].get("1.0", tk.END).strip()
        data["residual_corrections"] = residual_text if residual_text else ""

        clock_offsets_text = entries["clock_offsets"].get("1.0", tk.END).strip()
        data["clock_offsets"] = clock_offsets_text if clock_offsets_text else ""

        return data

    except Exception as e:
        messagebox.showerror("Error", f"Validation error: {str(e)}", parent=window)
        return None
