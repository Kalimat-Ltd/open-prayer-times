"""City persistence actions plus reference-file parsing, display, and file-management methods."""

# ruff: noqa: BLE001, ARG001, SLF001
# pylint: disable=broad-exception-caught,protected-access,unused-argument

import tkinter as tk
from tkinter import ttk, messagebox
import os
import datetime
import math
from src.app.presentation.gui.shared import rewrite_location_file


def save_new_city(self, entries, window):
    """Validates input, adds the new city, saves file, updates GUI."""
    # (Keep existing code)
    try:
        new_data = self._validate_and_get_form_data(entries, window)
        if new_data is None:
            return
        if any(
            loc["name"].lower() == new_data["name"].lower()
            for loc in self.locations_data
        ):
            messagebox.showerror(
                "Duplicate Name",
                f"City '{new_data['name']}' already exists.",
                parent=window,
            )
            return
        # Ensure optimized fields are present
        new_data["optimized_lat"] = None
        new_data["optimized_lon"] = None
        # Assign id as last id + 1
        if self.locations_data and any(
            "id" in loc and loc["id"] is not None for loc in self.locations_data
        ):
            max_id = max(
                loc["id"] for loc in self.locations_data if loc.get("id") is not None
            )
            new_data["id"] = max_id + 1
        else:
            new_data["id"] = 1
        self.locations_data.append(new_data)
        if rewrite_location_file(self):
            self.rebuild_city_name_index()
            self.rebuild_city_rmse_for_ids([new_data.get("id")])
            self.populate_listbox(self.search_var.get())
            self.update_status_bar()
            try:
                new_id = new_data.get("id")
                if new_id in self.city_listbox_ids:
                    new_index = self.city_listbox_ids.index(new_id)
                    self.city_listbox.selection_clear(0, tk.END)
                    self.city_listbox.selection_set(new_index)
                    self.city_listbox.see(new_index)
                    self.on_city_select(None)
            except Exception:
                pass
            messagebox.showinfo(
                "Success", f"City '{new_data['name']}' added.", parent=self.root
            )
            window.destroy()
        else:
            self.locations_data.pop()
            messagebox.showerror(
                "Save Failed", "Failed to save. Changes not applied.", parent=window
            )
    except Exception as e:
        messagebox.showerror(
            "Error", f"An unexpected error occurred: {e}", parent=window
        )


def open_modify_city_window(self):
    """Opens a window to modify the selected city."""
    # (Modified code)
    selected_data = self.get_selected_location_data()
    if not selected_data:
        messagebox.showwarning("No Selection", "Please select a city to modify.")
        return
    modify_window = tk.Toplevel(self.root)
    modify_window.title(f"Modify City: {selected_data['name']}")
    modify_window.geometry("750x700")
    modify_window.transient(self.root)
    modify_window.grab_set()
    _frame, entries, string_vars = self.create_city_form(
        modify_window, initial_data=selected_data
    )
    self._modify_string_vars = string_vars
    button_frame = ttk.Frame(modify_window, padding=(15, 5))
    button_frame.pack(fill=tk.X, side=tk.BOTTOM)
    if self.form_outer_frame is not None:
        self.form_outer_frame.pack(expand=True, fill=tk.BOTH)
    modify_window.update()
    button_frame.columnconfigure(0, weight=1)
    button_frame.columnconfigure(1, weight=1)
    # Existing buttons
    save_button = ttk.Button(
        button_frame,
        text="Save Changes",
        command=lambda: self.save_modified_city(entries, selected_data, modify_window),
    )
    save_button.grid(row=0, column=0, padx=5, pady=2, sticky="ew")
    cancel_button = ttk.Button(
        button_frame, text="Cancel", command=modify_window.destroy
    )
    cancel_button.grid(row=0, column=1, padx=5, pady=2, sticky="ew")
    # --- NEW BUTTON ---
    apply_angles_button = ttk.Button(
        button_frame,
        text="Apply to Country",
        command=lambda: self.apply_to_country(entries, selected_data, modify_window),
    )
    apply_angles_button.grid(
        row=1, column=0, columnspan=2, padx=5, pady=(5, 0), sticky="ew"
    )


def save_modified_city(self, entries, original_data, window):
    """Validates input, updates the city, saves file, updates GUI."""
    try:
        modified_data = self._validate_and_get_form_data(
            entries, window, initial_data=original_data
        )
        if modified_data is None:
            return

        # Preserve the original ID
        modified_data["id"] = original_data.get("id")

        original_name = original_data["name"]
        modified_name = modified_data["name"]

        # Check for name collision only if name changed
        if original_name.lower() != modified_name.lower():
            if any(
                loc["name"].lower() == modified_name.lower()
                for loc in self.locations_data
            ):
                messagebox.showerror(
                    "Duplicate Name",
                    f"City '{modified_name}' already exists.",
                    parent=window,
                )
                return

        # Find and update the city
        index_to_modify = -1
        for i, loc in enumerate(self.locations_data):
            if loc["name"] == original_name:
                index_to_modify = i
                break

        if index_to_modify == -1:
            messagebox.showerror(
                "Error", "Could not find original data. Reload app.", parent=window
            )
            return

        # Store original data in case save fails
        original = self.locations_data[index_to_modify].copy()

        # Merge: start from original, overlay with modified fields
        # This preserves fields not in the form (e.g. aqrab_al_bilad)
        merged = original.copy()
        merged.update(modified_data)
        self.locations_data[index_to_modify] = merged

        # Try to save to file
        if rewrite_location_file(self):
            name_changed = original_name.lower() != modified_name.lower()
            if name_changed:
                self.rebuild_city_name_index()
            self.rebuild_city_rmse_for_ids([modified_data.get("id")])
            self.filter_list()
            # self.populate_listbox(self.search_var.get())
            try:
                modified_id = modified_data.get("id")
                if modified_id in self.city_listbox_ids:
                    new_index = self.city_listbox_ids.index(modified_id)
                    self.city_listbox.selection_clear(0, tk.END)
                    self.city_listbox.selection_set(new_index)
                    self.city_listbox.see(new_index)
                self.on_city_select(None)
            except Exception:
                self.on_city_select(None)
            window.destroy()
        else:
            # Restore original data if save failed
            self.locations_data[index_to_modify] = original
            messagebox.showerror(
                "Save Failed",
                "Failed to save changes to file. Changes not applied.",
                parent=window,
            )

    except Exception as e:
        messagebox.showerror(
            "Error", f"An unexpected error occurred: {e}", parent=window
        )


def apply_to_country(self, entries, original_data, window):
    """Applies Fajr/Isha angles from the form to all cities with the same country code."""
    try:
        # 1. Get current country code
        current_country_code = original_data.get("country_code")
        if not current_country_code:
            messagebox.showerror(
                "Error",
                "Could not determine the country code for this city.",
                parent=window,
            )
            return
        # 2. Validate and get angle values from the FORM entries
        form_angles = self._validate_and_get_form_data(
            entries, window, skip_name_check=True
        )
        if form_angles is None:
            return
        new_temp = form_angles["temp"]
        new_pressure = form_angles["pressure"]
        new_calculation_method = form_angles["calculation_method"]
        new_fajr_angle = form_angles["fajr_angle"]
        new_isha_angle = form_angles["isha_angle"]
        new_isha_minutes = form_angles["isha_minutes"]
        new_asr_madhab = form_angles["asr_madhab"]
        new_isha_shafaq = form_angles["isha_shafaq"]
        new_isha_harag = form_angles["isha_harag"]
        new_high_lat_method = form_angles["high_lat_method"]
        new_high_lat_start_date = form_angles["high_lat_start_date"]
        new_high_lat_end_date = form_angles["high_lat_end_date"]
        new_custom_fajr_angle = form_angles["custom_fajr_angle"]
        new_custom_isha_angle = form_angles["custom_isha_angle"]
        new_high_lat_fallback_method = form_angles["high_lat_fallback_method"]
        new_fajr_offset = form_angles["fajr_offset"]
        new_shurooq_offset = form_angles["shurooq_offset"]
        new_dhuhr_offset = form_angles["dhuhr_offset"]
        new_asr_offset = form_angles["asr_offset"]
        new_maghrib_offset = form_angles["maghrib_offset"]
        new_isha_offset = form_angles["isha_offset"]
        new_is_official = form_angles["is_official"]
        new_is_optimized = form_angles["is_optimized"]
        new_reference_year = form_angles.get("reference_year", "")
        new_residual_corrections = form_angles["residual_corrections"]
        new_clock_offsets = form_angles["clock_offsets"]
        # 3. Find affected cities by country code
        affected_cities = [
            loc["name"]
            for loc in self.locations_data
            if loc.get("country_code") == current_country_code
        ]
        if not affected_cities:
            messagebox.showinfo(
                "No Action",
                f"No other cities found for country code '{current_country_code}'.",
                parent=window,
            )
            return
        confirm_msg = (
            f"This will apply the following angles:\n"
            f"  Temperature: {new_temp}\n"
            f"  Pressure: {new_pressure}\n"
            f"  Fajr Angle: {new_fajr_angle}\n"
            f"  Isha Angle: {new_isha_angle}\n"
            f"  Isha Minutes: {'None' if new_isha_minutes is None else new_isha_minutes}\n\n"
            f"  Asr Madhab: {'Hanafi' if new_asr_madhab == 1 else 'Standard'}\n\n"
            f"  Isha Shafaq: {new_isha_shafaq}\n"
            f"  High Latitude Method: {['Angle Based', 'One Seventh', 'Midnight', "Aqrab Al-Bilad"][new_high_lat_method]}\n\n"
            f"  High Latitude Start Date: {new_high_lat_start_date}\n"
            f"  High Latitude End Date: {new_high_lat_end_date}\n"
            f"  Custom Fajr Angle: {'None' if new_custom_fajr_angle is None else new_custom_fajr_angle}\n"
            f"  Custom Isha Angle: {'None' if new_custom_isha_angle is None else new_custom_isha_angle}\n"
            f"  High-Lat Fallback Method: {new_high_lat_fallback_method}\n\n"
            f"  Offsets:\n"
            f"    Fajr: {new_fajr_offset}\n"
            f"    Shurooq: {new_shurooq_offset}\n"
            f"    Dhuhr: {new_dhuhr_offset}\n"
            f"    Asr: {new_asr_offset}\n"
            f"    Maghrib: {new_maghrib_offset}\n"
            f"    Isha: {new_isha_offset}\n\n"
            f"  Reference Year: {new_reference_year if new_reference_year else 'not set'}\n\n"
            f"to ALL {len(affected_cities)} cities with country code '{current_country_code}'.\n\n"
            f"Proceed?"
        )
        confirm = messagebox.askyesno(
            "Confirm Country-Wide Update",
            confirm_msg,
            icon="warning",
            parent=window,
        )
        if not confirm:
            return
        # 4. Apply changes
        update_count = 0
        updated_city_ids = []
        original_locations_backup = [loc.copy() for loc in self.locations_data]
        for i, loc in enumerate(self.locations_data):
            if loc.get("country_code") == current_country_code:
                self.locations_data[i]["temp"] = new_temp
                self.locations_data[i]["pressure"] = new_pressure
                self.locations_data[i]["calculation_method"] = new_calculation_method
                if new_fajr_angle is not None:
                    self.locations_data[i]["fajr_angle"] = new_fajr_angle
                if new_isha_angle is not None:
                    self.locations_data[i]["isha_angle"] = new_isha_angle
                if new_isha_minutes is not None:
                    self.locations_data[i]["isha_minutes"] = new_isha_minutes
                self.locations_data[i]["asr_madhab"] = new_asr_madhab
                self.locations_data[i]["isha_shafaq"] = new_isha_shafaq
                self.locations_data[i]["isha_harag"] = new_isha_harag
                self.locations_data[i]["high_lat_method"] = new_high_lat_method
                self.locations_data[i]["high_lat_start_date"] = new_high_lat_start_date
                self.locations_data[i]["high_lat_end_date"] = new_high_lat_end_date
                self.locations_data[i]["custom_fajr_angle"] = new_custom_fajr_angle
                self.locations_data[i]["custom_isha_angle"] = new_custom_isha_angle
                self.locations_data[i][
                    "high_lat_fallback_method"
                ] = new_high_lat_fallback_method
                self.locations_data[i]["fajr_offset"] = new_fajr_offset
                self.locations_data[i]["shurooq_offset"] = new_shurooq_offset
                self.locations_data[i]["dhuhr_offset"] = new_dhuhr_offset
                self.locations_data[i]["asr_offset"] = new_asr_offset
                self.locations_data[i]["maghrib_offset"] = new_maghrib_offset
                self.locations_data[i]["isha_offset"] = new_isha_offset
                self.locations_data[i]["is_official"] = new_is_official
                self.locations_data[i]["is_optimized"] = new_is_optimized
                self.locations_data[i][
                    "residual_corrections"
                ] = new_residual_corrections
                self.locations_data[i]["clock_offsets"] = new_clock_offsets
                self.locations_data[i]["reference_year"] = new_reference_year
                updated_city_ids.append(self.locations_data[i].get("id"))
                update_count += 1
        # 5. Save and provide feedback
        if rewrite_location_file(self):
            self.rebuild_city_rmse_for_ids(updated_city_ids)
            self.filter_list()
            messagebox.showinfo(
                "Success",
                f"Calculation angles updated for {update_count} cities with country code '{current_country_code}'.",
                parent=self.root,
            )
            if original_data["name"] in affected_cities:
                self.on_city_select(None)
            window.destroy()
        else:
            self.locations_data = original_locations_backup
            messagebox.showerror(
                "Save Failed",
                "Failed to save changes to the file. No cities were updated.",
                parent=window,
            )
    except Exception as e:
        messagebox.showerror(
            "Error", f"An unexpected error occurred: {e}", parent=window
        )


def delete_selected_city(self):
    """Deletes the selected city after confirmation."""
    # (Keep existing code)
    selected_data = self.get_selected_location_data()
    if not selected_data:
        messagebox.showwarning("No Selection", "Please select a city to delete.")
        return
    city_name = selected_data["name"]
    confirm = messagebox.askyesno(
        "Confirm Deletion",
        f"Delete '{city_name}'?\nThis cannot be undone.",
        icon="warning",
    )
    if confirm:
        try:
            original_length = len(self.locations_data)
            self.locations_data = [
                loc for loc in self.locations_data if loc["id"] != selected_data["id"]
            ]
            # Fix comparison bug: check if length decreased
            if len(self.locations_data) == original_length:
                messagebox.showerror(
                    "Error",
                    f"Could not find city '{city_name}' in internal list to remove.",
                )
                return
            removed_city_id = selected_data.get("id")
            if rewrite_location_file(self):
                self.rebuild_city_name_index()
                self.remove_city_rmse_cache_entries([removed_city_id])
                self.city_rmse_index.pop(removed_city_id, None)
                self.city_mae_index.pop(removed_city_id, None)
                self.city_n_index.pop(removed_city_id, None)
                self.populate_listbox(self.search_var.get())
                # Clear selection and prayer times after deletion
                self.city_listbox.selection_clear(0, tk.END)
                self.update_prayer_times_display("Select a city from the list.")
                self.disable_action_buttons()
                messagebox.showinfo("Deleted", f"City '{city_name}' deleted.")
            else:
                messagebox.showerror(
                    "Delete Failed", "Failed to update file. Reloading data."
                )
                self.load_locations()
        except Exception as e:
            messagebox.showerror("Error", f"Error during deletion: {e}")
            self.load_locations()


def _get_reference_file_path(self, location_data):
    """Gets the path to the reference file for a given location and current month."""
    try:
        # Normalize country code and city name for filesystem use
        country_code = location_data["country_code"]
        city_name = location_data["name"].replace(" ", "_").replace(",", "").lower()

        # Use os.path.join for cross-platform path construction
        return os.path.normpath(
            os.path.join("reference", country_code, f"{city_name}.txt")
        )
    except Exception as e:
        print(f"Error constructing reference file path: {e}")
        return None


def _parse_reference_times(self, filepath):
    """Parses the reference times file and returns a dictionary of daily prayer times for all valid dates."""
    reference_times = {}
    selected_month = self.month_var.get() if hasattr(self, "month_var") else None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) != 7:
                    continue
                date_str, fajr, sunrise, dhuhr, asr, maghrib, isha = parts
                try:
                    date_obj = datetime.datetime.strptime(date_str, "%d-%b")
                    if selected_month is not None and date_obj.month != selected_month:
                        continue  # Skip if not the selected month
                    key = (date_obj.month, date_obj.day, date_str)
                    reference_times[key] = {
                        "fajr": fajr,
                        "shurooq": sunrise,
                        "dhuhr": dhuhr,
                        "asr": asr,
                        "maghrib": maghrib,
                        "isha": isha,
                    }
                except ValueError:
                    print(f"Error parsing date: {date_str}")
                    continue
        # Convert to sorted list and rebuild dictionary with original date strings
        sorted_items = sorted(
            reference_times.items(), key=lambda x: (x[0][0], x[0][1])
        )  # Sort by month, then day
        reference_times = {item[0][2]: item[1] for item in sorted_items}
        return reference_times
    except Exception as e:
        print(f"Error parsing reference times: {e}")
        return None


def _calculate_time_difference(self, time_calc, time_ref):
    """Signed difference (time_calc − time_ref) in minutes, handling seconds in time_calc."""
    try:
        # Attempt to parse time_calc with seconds first (assuming raw time from calculator)
        try:
            t_calc = datetime.datetime.strptime(time_calc, "%H:%M:%S")
        except ValueError:
            # If parsing with seconds fails, assume it's HH:MM (from formatted time)
            t_calc = datetime.datetime.strptime(time_calc, "%H:%M")
            # Add seconds = 0 to the datetime object for consistency
            t_calc = t_calc.replace(second=0)

        # Parse reference time (always expected as HH:MM)
        t_ref = datetime.datetime.strptime(time_ref, "%H:%M")
        # Ensure reference time has 0 seconds
        # t_ref = t_ref.replace(second=0)

        diff_seconds = (t_calc - t_ref).total_seconds()

        # Round the difference in seconds to the nearest minute for reporting the difference
        diff_minutes = math.ceil(diff_seconds / 60)

        # apply wrap around for minute difference
        # This handles cases where the difference crosses midnight
        if diff_minutes > 720:
            diff_minutes -= 1440
        elif diff_minutes < -720:
            diff_minutes += 1440

        return diff_minutes
    except ValueError:
        # This will catch errors if time_calc or time_ref are not valid time strings
        return None


def _get_color_for_difference(self, diff):
    """Returns the color code based on the time difference."""
    if diff is None:
        return "black"
    abs_diff = abs(diff)
    if abs_diff <= 2:
        return "#CC7700"  # Dark yellow
    elif abs_diff < 5:
        return "orange"
    else:
        return "red"


def _show_reference_times(self, location_data):
    ref_file = self._get_reference_file_path(location_data)
    print(f"Reference file path: {ref_file}")
    if not ref_file or not os.path.exists(ref_file):
        # Remove open file button if it exists
        if hasattr(self, "_open_ref_file_button") and self._open_ref_file_button:
            self._open_ref_file_button.destroy()
            self._open_ref_file_button = None
        # Hide text widget, show create button and message
        self.ref_times_text.grid_remove()
        for widget in self.ref_button_frame.winfo_children():
            widget.destroy()
        msg_label = ttk.Label(
            self.ref_button_frame,
            text="No reference data found for this location and month.",
            wraplength=300,
            justify=tk.CENTER,
        )
        msg_label.grid(row=0, column=0, pady=(0, 10))
        create_button = ttk.Button(
            self.ref_button_frame,
            text="Create empty reference file",
            command=lambda: self._create_reference_file(ref_file),
        )
        create_button.grid(row=1, column=0)
        self.ref_button_frame.grid()
        return
    else:
        self.ref_button_frame.grid_remove()
        self.ref_times_text.grid()
        ref_times = self._parse_reference_times(ref_file)
        self.ref_times_text.config(state=tk.NORMAL)
        self.ref_times_text.delete("1.0", tk.END)
        if not ref_times:
            # Remove open file button if it exists
            if hasattr(self, "_open_ref_file_button") and self._open_ref_file_button:
                self._open_ref_file_button.destroy()
            self.ref_times_text.insert(tk.END, "Reference file is empty.\n")
            self.ref_times_text.config(state=tk.DISABLED)
            return
        # Only draw the button if the file exists and is not empty
        if hasattr(self, "_open_ref_file_button") and self._open_ref_file_button:
            self._open_ref_file_button.destroy()
        self._open_ref_file_button = ttk.Button(
            self.ref_tab,
            text="Open Reference File",
            command=lambda: self._open_reference_file(ref_file),
        )
        self._open_ref_file_button.grid(row=2, column=0, pady=(5, 0), sticky="ew")
        header = f"Reference Prayer Times from {ref_file}\n"
        header += "-" * 80 + "\n"
        header += "Date      Fajr         Shurooq       Dhuhr        Asr          Maghrib      Isha\n"
        header += "-" * 80 + "\n"
        self.ref_times_text.insert(tk.END, header)
        for date_str, times in sorted(
            ref_times.items(),
            key=lambda x: datetime.datetime.strptime(x[0], "%d-%b").day,
        ):
            line = (
                f"{date_str:<9} "
                f"{times['fajr']:<12} "
                f"{times['shurooq']:<12} "
                f"{times['dhuhr']:<12} "
                f"{times['asr']:<12} "
                f"{times['maghrib']:<12} "
                f"{times['isha']:<12}\n"
            )
            self.ref_times_text.insert(tk.END, line)
        self.ref_times_text.config(state=tk.DISABLED)


def _open_reference_file(self, filepath):
    """Opens the reference file in the default text editor."""
    try:
        if os.name == "nt":  # Windows
            os.startfile(filepath)
        else:
            import subprocess

            subprocess.run(["xdg-open", filepath], check=False)
    except Exception as e:
        messagebox.showerror("Error", f"Failed to open reference file:\n{e}")


def _create_reference_file(self, filepath):
    """Creates an empty reference file and opens it."""
    try:
        # Ensure reference directory exists
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        # Create empty file with headers
        with open(filepath, "w", encoding="utf-8"):
            pass

        # Open the file in the default text editor
        if os.name == "nt":  # Windows
            os.startfile(filepath)
        else:  # Unix-like
            import subprocess

            subprocess.run(["xdg-open", filepath], check=False)

        # Refresh the display
        self.refresh_country_filter()  # <-- Refresh country filter to update '*' marker
        if hasattr(self, "refresh_metrics_for_reference_paths"):
            self.refresh_metrics_for_reference_paths([filepath])
        self.on_tab_changed()
        self.on_city_select(None)  # <-- Ensure Reference Times tab reloads the new file

    except Exception as e:
        messagebox.showerror("Error", f"Failed to create reference file:\n{e}")
