"""GUI shell setup, widget construction, and base location-loading lifecycle methods."""

# ruff: noqa: BLE001, ARG001, SLF001
# pylint: disable=broad-exception-caught,protected-access,unused-argument

import re
import tkinter as tk
from tkinter import ttk, messagebox
import os
import csv
import datetime
import threading
from pathlib import Path
from typing import Any, cast

from src.app.config import LOC_CSV_PATH, REFERENCE_DIR, RESOURCES_DIR
from src.app.presentation.gui.shared import (
    _get_observer_class,
    _get_geopy_distance,
    _get_open_batch_optimization_dashboard,
    _get_optimize_parameters_for_city,
    _get_timezone_finder_class,
    _lazy_imports,
    _make_reference_folder_handler_class,
    field_names,
    rewrite_location_file,
)


def __init__(self, root):
    self._open_ref_file_button = None  # Initialize the member variable
    """Initializes the application."""
    _lazy_imports()
    self.root = root
    self.root.title("Open Prayer Times")
    # Maximize window in a cross-platform way
    import sys as _sys

    if _sys.platform.startswith("linux"):
        self.root.attributes("-zoomed", True)
    else:  # Windows and macOS both honour state('zoomed') with Tk 8.6+
        self.root.state("zoomed")

    self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    self.locations_data = []
    self.location_file = str(LOC_CSV_PATH)
    self.location_file_backup = str(Path(LOC_CSV_PATH).with_suffix(".csv.bak"))
    self.reference_dir = str(REFERENCE_DIR)
    self.resources_dir = str(RESOURCES_DIR)
    self._in_dms_update = False
    self._in_decimal_update = False
    self.reference_times = None  # Store reference times when loaded
    self.dst_var = tk.BooleanVar(value=False)

    self.dst_var = tk.BooleanVar(value=True)
    self.check_distances_var = tk.BooleanVar(value=False)
    self.tf = (
        _get_timezone_finder_class()()
    )  # Add this line to initialize timezonefinder
    self.reference_times = None

    self._folder_watch = None
    self._ref_observer = _get_observer_class()()

    # Initialize attributes used later to avoid "defined outside __init__" warnings
    self.country_codes_list = []
    self.country_filter_var = tk.StringVar()
    self.country_filter_combo = None
    self.city_listbox_ids = []
    self.city_name_prefix_index = {}
    self.city_name_lookup = {}
    self.city_has_reference = {}
    self.location_by_id = {}
    self.city_ids_sorted = []
    self.city_rmse_index = {}
    self.city_mae_index = {}
    self.city_n_index = {}
    self.rmse_index_ready = False
    self.is_optimized_var = tk.IntVar(value=0)
    self.min_rmse_var = tk.StringVar(value="")
    self.min_mae_var = tk.StringVar(value="")
    self.max_n_var = tk.StringVar(value="")
    self._modify_string_vars = {}
    self.form_outer_frame = None
    self.rewrite_location_file = lambda: rewrite_location_file(self)
    self._reference_paths_lock = threading.Lock()
    self._pending_reference_paths = set()

    self.create_widgets()
    self.load_locations()
    self.root.after(500, self.process_pending_reference_changes)

    self._ref_observer.start()


def on_closing(self):
    self._ref_observer.stop()
    self._ref_observer.join()
    self.root.destroy()


def create_widgets(self):
    """Creates and arranges the GUI widgets."""
    # --- Main Frames ---
    self.root.grid_columnconfigure(0, weight=1)  # Left frame
    self.root.grid_columnconfigure(1, weight=3)  # Middle frame (prayer times)
    self.root.grid_rowconfigure(0, weight=1)  # Top row

    left_frame = ttk.Frame(self.root, padding="10")
    left_frame.grid(row=0, column=0, sticky="nsew")
    # Configure left_frame rows: row 2 (listbox) gets all extra vertical space
    left_frame.grid_rowconfigure(0, weight=0)  # Search/Checkbox row
    left_frame.grid_rowconfigure(1, weight=0)  # Country filter row
    left_frame.grid_rowconfigure(2, weight=1)  # Listbox row expands
    left_frame.grid_rowconfigure(3, weight=0)  # Action buttons row

    # Configure left_frame columns specifically for the top row and action buttons frame
    left_frame.grid_columnconfigure(0, weight=0)  # Search label column
    left_frame.grid_columnconfigure(1, weight=1)  # Search entry column
    left_frame.grid_columnconfigure(2, weight=0)  # Checkbox column
    left_frame.grid_columnconfigure(
        3, weight=1
    )  # Column to absorb extra space in row 0

    middle_frame = ttk.Frame(self.root, padding="10")
    middle_frame.grid(row=0, column=1, sticky="nsew")
    middle_frame.grid_rowconfigure(1, weight=1)  # Make the prayer times notebook expand
    middle_frame.grid_columnconfigure(0, weight=1)
    middle_frame.grid_columnconfigure(
        1, weight=1
    )  # Ensure the second internal column also expands

    # Top controls frame (moved back to middle frame top)
    controls_frame = ttk.Frame(middle_frame)
    controls_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 5))
    controls_frame.grid_columnconfigure(1, weight=1)
    controls_frame.grid_columnconfigure(3, weight=1)
    # Consider adding weight to a column after the last widget in controls_frame if the gap persists within controls_frame

    # Rounding controls
    ttk.Label(controls_frame, text="Rounding:").grid(row=0, column=0, padx=(10, 5))
    self.rounding_var = tk.StringVar(value="nearest")
    rounding_combo = ttk.Combobox(
        controls_frame, textvariable=self.rounding_var, width=10, state="readonly"
    )
    rounding_combo.grid(row=0, column=1, sticky="w")
    rounding_combo["values"] = ("off", "nearest", "floor", "ceil")
    self.rounding_combo = rounding_combo
    rounding_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_prayer_times())

    # Month selector
    ttk.Label(controls_frame, text="Month:").grid(row=0, column=4, padx=(10, 5))
    current_month = datetime.date.today().month
    self.month_var = tk.IntVar(value=current_month)
    month_combo = ttk.Combobox(controls_frame, width=15, state="readonly")
    month_combo.grid(row=0, column=5, sticky="w")
    month_names = [datetime.date(2000, m, 1).strftime("%B") for m in range(1, 13)]
    month_combo["values"] = month_names
    month_combo.current(current_month - 1)
    month_combo.bind(
        "<<ComboboxSelected>>", lambda e: self.on_month_select(e, month_combo)
    )

    # Year selector (controls which year prayer times are calculated/displayed for)
    ttk.Label(controls_frame, text="Year:").grid(row=0, column=6, padx=(10, 5))
    current_year = datetime.date.today().year
    self.year_var = tk.IntVar(value=current_year)
    year_spinbox = ttk.Spinbox(
        controls_frame,
        from_=2000,
        to=2100,
        textvariable=self.year_var,
        width=6,
        command=self.on_tab_changed,
    )
    year_spinbox.grid(row=0, column=7, sticky="w")
    year_spinbox.bind("<Return>", lambda e: self.on_tab_changed())
    year_spinbox.bind("<FocusOut>", lambda e: self.on_tab_changed())

    # Create notebook for tabs
    self.notebook = ttk.Notebook(middle_frame)
    self.notebook.grid(row=1, column=0, columnspan=2, sticky="nsew")

    # Create frames for each tab
    self.calc_tab = ttk.Frame(self.notebook)
    self.ref_tab = ttk.Frame(self.notebook)
    self.conclusion_tab = ttk.Frame(self.notebook)
    self.notebook.add(self.calc_tab, text="Calculated Times")
    self.notebook.add(self.ref_tab, text="Reference Times")
    self.notebook.add(self.conclusion_tab, text="Conclusion")

    # Configure tab frames and bind tab change event
    for tab in (self.calc_tab, self.ref_tab, self.conclusion_tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)  # Make content expand

    self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

    # Add font size control to header row of each tab
    def create_font_controls(parent, target_widget, row=0):
        font_frame = ttk.Frame(parent)
        font_frame.grid(row=row, column=0, sticky="e")

        def change_font_size(delta):
            import tkinter.font as _tkfont

            try:
                f = _tkfont.Font(font=target_widget["font"])
                family = f.actual("family")
                size = abs(f.actual("size"))  # negative = pixels, positive = points
            except Exception:
                family = "Courier New"
                size = 12
            new_size = max(8, min(24, size + delta))
            target_widget.configure(font=(family, new_size))

        decrease_btn = ttk.Button(
            font_frame, text="-", width=2, command=lambda: change_font_size(-1)
        )
        decrease_btn.pack(side=tk.LEFT, padx=2)

        increase_btn = ttk.Button(
            font_frame, text="+", width=2, command=lambda: change_font_size(1)
        )
        increase_btn.pack(side=tk.LEFT, padx=2)

    # Modify the prayer times headers section to include font controls
    # Calculated Times tab
    ttk.Label(
        self.calc_tab, text="Calculated Prayer Times:", font=("Arial", 14, "bold")
    ).grid(row=0, column=0, sticky="nw", pady=(5, 5))

    # Reference Times tab
    ttk.Label(
        self.ref_tab, text="Reference Prayer Times:", font=("Arial", 14, "bold")
    ).grid(row=0, column=0, sticky="nw", pady=(5, 5))

    # Conclusion tab
    ttk.Label(
        self.conclusion_tab,
        text="Conclusion / Error Summary:",
        font=("Arial", 14, "bold"),
    ).grid(row=0, column=0, sticky="nw", pady=(5, 5))

    # Prayer times text areas
    self.prayer_times_text = tk.Text(
        self.calc_tab,
        wrap=tk.WORD,
        bg="#f0f0f0",
        relief=tk.SUNKEN,
        borderwidth=1,
        font=("Courier New", 12),
    )
    self.prayer_times_text.grid(row=1, column=0, sticky="nsew")
    calc_scrollbar = ttk.Scrollbar(
        self.calc_tab, orient=tk.VERTICAL, command=self.prayer_times_text.yview
    )
    calc_scrollbar.grid(row=1, column=1, sticky="ns")
    self.prayer_times_text.config(yscrollcommand=calc_scrollbar.set)
    self.prayer_times_text.config(state=tk.DISABLED)
    create_font_controls(self.calc_tab, self.prayer_times_text)

    # Reference times text area
    self.ref_times_text = tk.Text(
        self.ref_tab,
        wrap=tk.WORD,
        bg="#f0f0f0",
        relief=tk.SUNKEN,
        borderwidth=1,
        font=("Courier New", 12),
    )
    self.ref_times_text.grid(row=1, column=0, sticky="nsew")
    ref_scrollbar = ttk.Scrollbar(
        self.ref_tab, orient=tk.VERTICAL, command=self.ref_times_text.yview
    )
    ref_scrollbar.grid(row=1, column=1, sticky="ns")
    self.ref_times_text.config(yscrollcommand=ref_scrollbar.set)
    self.ref_times_text.config(state=tk.DISABLED)
    create_font_controls(self.ref_tab, self.ref_times_text)

    # Conclusion text area
    self.conclusion_text = tk.Text(
        self.conclusion_tab,
        wrap=tk.WORD,
        bg="#f0f0f0",
        relief=tk.SUNKEN,
        borderwidth=1,
        font=("Courier New", 9),
    )
    self.conclusion_text.grid(row=1, column=0, sticky="nsew")
    conclusion_scrollbar = ttk.Scrollbar(
        self.conclusion_tab, orient=tk.VERTICAL, command=self.conclusion_text.yview
    )
    conclusion_scrollbar.grid(row=1, column=1, sticky="ns")
    self.conclusion_text.config(yscrollcommand=conclusion_scrollbar.set)
    self.conclusion_text.config(state=tk.DISABLED)
    create_font_controls(self.conclusion_tab, self.conclusion_text)

    # Create reference file button frame (hidden initially)
    self.ref_button_frame = ttk.Frame(self.ref_tab)
    self.ref_button_frame.grid(row=1, column=0, sticky="ew")
    self.ref_button_frame.grid_rowconfigure(0, weight=1)
    self.ref_button_frame.grid_columnconfigure(0, weight=1)

    # Analysis frame for displaying errors
    self.analysis_frame = ttk.Frame(self.calc_tab)
    self.analysis_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
    self.analysis_label = ttk.Label(
        self.analysis_frame, text="", justify=tk.LEFT, wraplength=600
    )
    self.analysis_label.grid(row=0, column=0, sticky="w")

    # Left frame widgets
    # Search label, entry, and checkbox are in row 0
    search_label = ttk.Label(left_frame, text="Search City:")
    search_label.grid(row=0, column=0, sticky="w", pady=(0, 5))
    self.search_var = tk.StringVar()
    self.search_entry = ttk.Entry(left_frame, textvariable=self.search_var)
    self.search_entry.grid(row=0, column=1, sticky="ew", pady=(0, 5))
    self.search_var.trace_add("write", self.filter_list)

    # Add Show Distances checkbox in row 0
    self.check_distances_var.set(False)
    self.show_distances_checkbox = ttk.Checkbutton(
        left_frame,
        text="Show Distances",
        variable=self.check_distances_var,
        command=lambda: self.populate_listbox(self.search_var.get()),
    )
    self.show_distances_checkbox.grid(row=0, column=2, padx=(10, 0), sticky="w")

    # --- Country filter combobox ---
    self.refresh_country_filter()

    # --- Latitude filter combobox ---
    self.lat_filter_var = tk.StringVar()
    lat_filter_options = [
        "All Latitudes",
        "Normal cities (lat <±45°)",
        "Grey area (45° < lat < 48°)",
        "High latitude cities (48° < lat < 66°)",
        "Extreme latitude cities (66° < lat)",
    ]
    self.lat_filter_combo = ttk.Combobox(
        left_frame, textvariable=self.lat_filter_var, width=28, state="readonly"
    )
    self.lat_filter_combo["values"] = lat_filter_options
    self.lat_filter_combo.current(0)
    self.lat_filter_combo.grid(row=1, column=3, sticky="ew", padx=(5, 0), pady=(5, 5))
    self.lat_filter_combo.bind("<<ComboboxSelected>>", lambda e: self.filter_list())

    # --- RMSE threshold filter ---
    rmse_filter_frame = ttk.Frame(left_frame)
    rmse_filter_frame.grid(row=0, column=3, sticky="ew", padx=(8, 0), pady=(0, 5))
    rmse_filter_frame.grid_columnconfigure(1, weight=1)
    rmse_filter_frame.grid_columnconfigure(3, weight=1)
    rmse_filter_frame.grid_columnconfigure(5, weight=1)

    ttk.Label(rmse_filter_frame, text="Min MAE:").grid(
        row=0, column=0, sticky="w", padx=(0, 4)
    )
    self.min_mae_entry = ttk.Entry(
        rmse_filter_frame, textvariable=self.min_mae_var, width=8
    )
    self.min_mae_entry.grid(row=0, column=1, sticky="ew")

    ttk.Label(rmse_filter_frame, text="Min RMSE:").grid(
        row=0, column=2, sticky="w", padx=(8, 4)
    )
    self.min_rmse_entry = ttk.Entry(
        rmse_filter_frame, textvariable=self.min_rmse_var, width=8
    )
    self.min_rmse_entry.grid(row=0, column=3, sticky="ew")

    ttk.Label(rmse_filter_frame, text="Max N:").grid(
        row=0, column=4, sticky="w", padx=(8, 4)
    )
    self.max_n_entry = ttk.Entry(
        rmse_filter_frame, textvariable=self.max_n_var, width=8
    )
    self.max_n_entry.grid(row=0, column=5, sticky="ew")

    self.min_rmse_var.trace_add("write", self.filter_list)
    self.min_mae_var.trace_add("write", self.filter_list)
    self.max_n_var.trace_add("write", self.filter_list)

    list_frame = ttk.Frame(left_frame)
    # Listbox frame is now in row 2 and spans all columns
    list_frame.grid(row=2, column=0, columnspan=4, sticky="nsew")
    list_frame.grid_rowconfigure(0, weight=1)
    list_frame.grid_columnconfigure(0, weight=1)
    self.city_listbox = tk.Listbox(list_frame, exportselection=False, height=15)
    self.city_listbox.grid(row=0, column=0, sticky="nsew")
    self.city_listbox.bind("<<ListboxSelect>>", self.on_city_select)
    scrollbar = ttk.Scrollbar(
        list_frame, orient=tk.VERTICAL, command=self.city_listbox.yview
    )
    scrollbar.grid(row=0, column=1, sticky="ns")
    self.city_listbox.config(yscrollcommand=scrollbar.set)

    # Action buttons frame (modified to include all buttons)
    action_button_frame = ttk.Frame(left_frame)
    # Action button frame is now in row 3 and spans all columns
    action_button_frame.grid(row=3, column=0, columnspan=4, pady=(10, 0), sticky="ew")
    action_button_frame.grid_columnconfigure(0, weight=1)
    action_button_frame.grid_columnconfigure(1, weight=1)

    # Top row of action buttons
    self.modify_button = ttk.Button(
        action_button_frame,
        text="Modify City",
        command=self.open_modify_city_window,
    )
    self.modify_button.grid(row=0, column=0, padx=(0, 5), pady=(0, 5), sticky="ew")
    self.optimize_settings_button = ttk.Button(
        action_button_frame,
        text="Optimize Parameters",
        command=self.run_selected_city_optimizer,
    )
    self.optimize_settings_button.grid(
        row=0, column=1, padx=(5, 0), pady=(0, 5), sticky="ew"
    )

    # Second row of action buttons
    self.add_city_button = ttk.Button(
        action_button_frame, text="Add New City", command=self.open_add_city_window
    )
    self.add_city_button.grid(row=1, column=0, padx=(0, 5), pady=(0, 5), sticky="ew")
    # Delete button at the bottom
    self.delete_button = ttk.Button(
        action_button_frame, text="Delete City", command=self.delete_selected_city
    )
    self.delete_button.grid(row=1, column=1, padx=(5, 0), pady=(0, 5), sticky="ew")
    # Copy times button
    self.copy_times_button = ttk.Button(
        action_button_frame, text="Copy Times", command=self.copy_times_to_clipboard
    )
    self.copy_times_button.grid(row=2, column=0, columnspan=2, pady=(0, 5), sticky="ew")

    # Optimize All Countries button
    self.batch_optimize_button = ttk.Button(
        action_button_frame,
        text="Optimize All Countries",
        command=lambda: cast(Any, _get_open_batch_optimization_dashboard())(self),
    )
    self.batch_optimize_button.grid(
        row=3, column=0, columnspan=2, pady=(0, 0), sticky="ew"
    )

    self.disable_action_buttons()  # Disable buttons initially

    if self._folder_watch is None:
        handler_class = _make_reference_folder_handler_class()
        handler = handler_class(self.on_tab_changed)
        watch_dir = self.reference_dir
        os.makedirs(watch_dir, exist_ok=True)
        self._folder_watch = self._ref_observer.schedule(
            handler, watch_dir, recursive=True
        )

    # --- Status Bar ---
    self.status_bar = ttk.Label(self.root, relief=tk.SUNKEN, anchor="w")
    self.status_bar.grid(row=2, column=0, columnspan=2, sticky="ew")
    self.update_status_bar()


def refresh_country_filter(self):
    """Refreshes the country filter combobox, marking countries with reference folders."""
    # Load country codes for dropdown
    self.country_codes_list = []
    country_codes_path = os.path.join(self.resources_dir, "country_codes.csv")
    try:
        with open(country_codes_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (
                    row["ISO CODES"].strip() if "ISO CODES" in row else row["ISO CODES"]
                )
                country = row["COUNTRY"].strip() if "COUNTRY" in row else row["COUNTRY"]
                self.country_codes_list.append((code, country))
    except Exception:
        self.country_codes_list = []
    # Mark countries with reference folder
    reference_dir = self.reference_dir
    reference_country_codes = set()
    try:
        for entry in os.listdir(reference_dir):
            if os.path.isdir(os.path.join(reference_dir, entry)):
                reference_country_codes.add(entry.upper())
    except Exception:
        pass
    country_values = ["All Countries"] + [
        (
            f" * {code} - {country}"
            if code.upper() in reference_country_codes
            else f"{code} - {country}"
        )
        for code, country in self.country_codes_list
    ]
    # Create the combobox if it doesn't exist
    if self.country_filter_combo is None:
        # Find the left_frame (parent)
        left_frame = (
            self.root.nametowidget(".!frame")
            if hasattr(self.root, "nametowidget")
            else None
        )
        if left_frame is None:
            left_frame = self.root.winfo_children()[0]  # fallback
        self.country_filter_var = tk.StringVar()
        self.country_filter_combo = ttk.Combobox(
            left_frame,
            textvariable=self.country_filter_var,
            width=25,
            state="readonly",
            height=10,
        )
        self.country_filter_combo.grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(5, 5)
        )
        self.country_filter_combo.bind(
            "<<ComboboxSelected>>", lambda e: self.filter_list()
        )
    self.country_filter_combo["values"] = country_values
    # Try to keep the current selection by country code, ignoring ' * '
    current = (
        self.country_filter_var.get() if hasattr(self, "country_filter_var") else None
    )
    selected_code = None
    if current and current != "All Countries":
        selected_code = current.lstrip(" *").split(" - ")[0].strip()
    # Find the matching value in the new list
    if selected_code:
        for val in country_values:
            if val.lstrip(" *").startswith(selected_code + " "):
                self.country_filter_combo.set(val)
                break
        else:
            self.country_filter_combo.current(0)
    else:
        self.country_filter_combo.current(0)


def run_selected_city_optimizer(self):
    selected_data = self.get_selected_location_data()
    ref_file = self._get_reference_file_path(selected_data)
    cast(Any, _get_optimize_parameters_for_city())(
        self=self,
        ref_file=ref_file,
    )


def on_tab_changed(self, event=None):
    """Handle tab change events."""
    if event is not None and hasattr(event, "src_path"):
        try:
            changed_paths = [getattr(event, "src_path", None)]
            dest_path = getattr(event, "dest_path", None)
            if dest_path:
                changed_paths.append(dest_path)
            with self._reference_paths_lock:
                for path in changed_paths:
                    if path:
                        self._pending_reference_paths.add(os.path.normpath(path))
        except Exception:
            pass
        return

    selected_data = self.get_selected_location_data()
    if not selected_data:
        return

    current_tab = self.notebook.select()
    if current_tab == str(self.ref_tab):
        self._show_reference_times(selected_data)
    elif current_tab == str(self.conclusion_tab):
        self.show_conclusion_summary(selected_data)
    else:
        # Refresh calculated times when switching back
        self.calculate_and_display_prayer_times(selected_data)


def process_pending_reference_changes(self):
    """Apply queued reference-file changes on the Tk main thread."""
    pending_paths = []
    try:
        with self._reference_paths_lock:
            if self._pending_reference_paths:
                pending_paths = list(self._pending_reference_paths)
                self._pending_reference_paths.clear()

        if pending_paths:
            if hasattr(self, "refresh_metrics_for_reference_paths"):
                self.refresh_metrics_for_reference_paths(pending_paths)
            self.refresh_country_filter()
            self.on_tab_changed(None)
    except Exception:
        pass
    finally:
        try:
            self.root.after(500, self.process_pending_reference_changes)
        except Exception:
            pass


def _parse_location_row(self, row, row_num):
    """Parses a single row from the CSV reader into a location dictionary."""
    try:

        def _to_float(value, default=None):
            if value is None:
                return default
            text = str(value).strip()
            if text == "" or text.lower() == "null":
                return default
            return float(text)

        def _to_int(value, default=None):
            if value is None:
                return default
            text = str(value).strip()
            if text == "" or text.lower() == "null":
                return default
            return int(float(text))

        location = {}
        location["id"] = (
            int(row["id"]) if row.get("id") and str(row["id"]).isdigit() else None
        )
        location["country_code"] = row.get("country_code", "").strip('"')
        location["name"] = row.get("name", "").strip('"')
        location["latitude"] = _to_float(row.get("latitude"), None)
        location["longitude"] = _to_float(row.get("longitude"), None)
        location["optimized_lat"] = _to_float(row.get("optimized_lat"), None)
        location["optimized_lon"] = _to_float(row.get("optimized_lon"), None)
        location["timezone"] = row.get("timezone")
        location["elevation"] = _to_float(row.get("elevation"), None)
        location["pressure"] = _to_float(row.get("pressure"), None)
        location["temp"] = _to_float(row.get("temp"), None)
        location["calculation_method"] = row.get("calculation_method", "angle_based")
        location["fajr_angle"] = _to_float(row.get("fajr_angle"), None)
        location["isha_angle"] = _to_float(row.get("isha_angle"), None)
        location["isha_minutes"] = _to_float(row.get("isha_minutes"), 0.0)
        location["asr_madhab"] = (
            int(row["asr_madhab"])
            if row.get("asr_madhab") and str(row["asr_madhab"]).isdigit()
            else None
        )
        location["isha_harag"] = _to_int(row.get("isha_harag"), 0)
        location["high_lat_method"] = _to_int(row.get("high_lat_method"), 0)
        location["isha_shafaq"] = row.get("isha_shafaq", "general")
        location["high_lat_start_date"] = (
            datetime.datetime.strptime(
                row.get("high_lat_start_date"), "%Y-%m-%d"
            ).date()
            if row.get("high_lat_start_date") != ""
            else None
        )
        location["high_lat_end_date"] = (
            datetime.datetime.strptime(row.get("high_lat_end_date"), "%Y-%m-%d").date()
            if row.get("high_lat_end_date") != ""
            else None
        )
        location["custom_fajr_angle"] = _to_float(row.get("custom_fajr_angle"), None)
        location["custom_isha_angle"] = _to_float(row.get("custom_isha_angle"), None)
        location["high_lat_fallback_method"] = _to_int(
            row.get("high_lat_fallback_method"), None
        )
        location["aqrab_al_bilad"] = row.get("aqrab_al_bilad", "")
        location["fajr_offset"] = _to_float(row.get("fajr_offset"), 0.0)
        location["shurooq_offset"] = _to_float(row.get("shurooq_offset"), 0.0)
        location["dhuhr_offset"] = _to_float(row.get("dhuhr_offset"), 0.0)
        location["asr_offset"] = _to_float(row.get("asr_offset"), 0.0)
        location["maghrib_offset"] = _to_float(row.get("maghrib_offset"), 0.0)
        location["isha_offset"] = _to_float(row.get("isha_offset"), 0.0)
        location["is_optimized"] = _to_int(row.get("is_optimized", "0"), 0) == 1
        location["is_official"] = _to_int(row.get("is_official"), 0)
        location["reference_year"] = _to_int(row.get("reference_year"), None)
        location["residual_corrections"] = row.get("residual_corrections", "") or ""
        location["clock_offsets"] = row.get("clock_offsets", "") or ""
        location["asr_madhab_overrides"] = row.get("asr_madhab_overrides", "") or ""
        return location
    except Exception as e:
        print(
            f"Warning: Skipping row {row_num} due to data conversion error: {e}. Content: {row}"
        )
        return None


def load_locations(self):
    """Loads location data from resources/locations.csv."""
    # (Keep the existing loading logic)
    self.locations_data = []
    # Use locations.csv as the new location file and DictReader for named access
    self.location_file = str(LOC_CSV_PATH)
    if not os.path.exists(self.location_file):
        messagebox.showwarning(
            "File Not Found",
            f"'{self.location_file}' not found. No locations loaded.",
        )
        self.populate_listbox()
        return
    try:
        missing_columns: list[str] = []
        with open(self.location_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header_fields = list(reader.fieldnames or [])
            missing_columns = [
                field
                for field in (
                    "custom_fajr_angle",
                    "custom_isha_angle",
                    "high_lat_fallback_method",
                    "asr_madhab_overrides",
                )
                if field not in header_fields
            ]
            for i, row in enumerate(reader):
                # Skip empty lines or rows with missing name
                if not row or not row.get("name"):
                    continue
                location = self._parse_location_row(
                    row, i + 2
                )  # +2 for header and 0-based
                if location:
                    self.locations_data.append(location)
        if missing_columns and self.locations_data:
            rewrite_location_file(self)
    except Exception as e:
        messagebox.showerror(
            "Error Loading File",
            f"An error occurred while reading '{self.location_file}':\n{e}",
        )
        self.locations_data = []
    self.rebuild_city_name_index()
    self.rmse_index_ready = False
    self.city_rmse_index = {}
    self.city_mae_index = {}
    self.city_n_index = {}
    self.populate_listbox()
    self.update_status_bar()


def _format_location_for_file(self, location_data):
    """Formats a location dictionary back into a list of values for csv.writer.

    Returns raw values — csv.writer handles all quoting/escaping automatically,
    including fields with commas or double quotes (like JSON strings).
    """
    values = []
    float_defaults = {
        "latitude": 0.0,
        "longitude": 0.0,
        "elevation": 0.0,
        "pressure": 1010.0,
        "temp": 10.0,
        "fajr_angle": 18.0,
        "isha_angle": 17.0,
        "isha_minutes": 0.0,
        "fajr_offset": 0.0,
        "shurooq_offset": 0.0,
        "dhuhr_offset": 0.0,
        "asr_offset": 0.0,
        "maghrib_offset": 0.0,
        "isha_offset": 0.0,
    }
    int_defaults = {
        "asr_madhab": 0,
        "isha_harag": 0,
        "high_lat_method": 0,
        "is_official": 0,
    }
    optional_float_fields = {
        "optimized_lat",
        "optimized_lon",
        "custom_fajr_angle",
        "custom_isha_angle",
    }
    optional_int_fields = {"high_lat_fallback_method"}
    for field in field_names:
        value = location_data.get(field)
        if field == "name":
            values.append(value if value is not None else "")
        elif field == "isha_minutes":
            values.append("null" if value is None else str(value))
        elif field == "asr_madhab":
            # Always write 0 or 1 for asr_madhab
            values.append("0" if value in [None, "", False] else str(int(value)))
        elif field in optional_float_fields:
            if value in (None, "", "null"):
                values.append("")
            else:
                values.append(str(float(value)))
        elif field in optional_int_fields:
            if value in (None, "", "null"):
                values.append("")
            else:
                values.append(str(int(float(value))))
        elif field in float_defaults:
            if value in (None, "", "null"):
                values.append(str(float_defaults[field]))
            else:
                values.append(str(float(value)))
        elif field in int_defaults:
            if value in (None, "", "null"):
                values.append(str(int_defaults[field]))
            else:
                values.append(str(int(float(value))))
        elif field == "id":
            values.append(str(value) if value is not None else "")
        elif field == "is_optimized":
            values.append("1" if value else "0")
        elif field == "residual_corrections":
            # Return raw JSON string — csv.writer will quote/escape it
            values.append(str(value) if value else "")
        elif field == "clock_offsets":
            # Return raw JSON string — csv.writer will quote/escape it
            values.append(str(value) if value else "")
        elif field == "asr_madhab_overrides":
            # Return raw JSON string — csv.writer will quote/escape it
            values.append(str(value) if value else "")
        elif isinstance(value, (int, float)):
            values.append(str(value))
        elif value is not None:
            values.append(str(value))
        else:
            values.append("")
    return values


def _distance_km(self, city1, city2):
    """Calculate the geodesic distance in km between two city dicts using optimized or fallback coordinates."""
    lat1 = (
        city1.get("optimized_lat")
        if city1.get("optimized_lat") is not None
        else city1.get("latitude")
    )
    lon1 = (
        city1.get("optimized_lon")
        if city1.get("optimized_lon") is not None
        else city1.get("longitude")
    )
    lat2 = (
        city2.get("optimized_lat")
        if city2.get("optimized_lat") is not None
        else city2.get("latitude")
    )
    lon2 = (
        city2.get("optimized_lon")
        if city2.get("optimized_lon") is not None
        else city2.get("longitude")
    )
    if None in (lat1, lon1, lat2, lon2):
        return None
    try:
        return _get_geopy_distance().geodesic((lat1, lon1), (lat2, lon2)).km
    except Exception:
        return None


def _distance_color(self, km):
    """Return a color string for a given distance in km."""
    if km is None:
        return "black"
    if km < 200:
        return "green"
    elif km < 500:
        return "salmon3"
    elif km < 1000:
        return "darkorange"
    else:
        return "red"
