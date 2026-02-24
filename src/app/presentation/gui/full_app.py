"""Thin PrayerApp method binder/orchestrator that composes the split gui method modules."""

# pyright: reportAttributeAccessIssue=false
# ruff: noqa: BLE001, ARG001, F841, SLF001, B603
# pylint: disable=broad-exception-caught,global-statement,protected-access,redefined-outer-name,unused-argument,unused-variable
import multiprocessing
import tkinter as tk

from src.app.presentation.gui.shared import _lazy_imports
from src.app.presentation.gui import app_shell_and_loading as _app_shell
from src.app.presentation.gui import city_list_and_calculations as _city_list
from src.app.presentation.gui import city_form_workflows as _city_form
from src.app.presentation.gui import (
    city_data_and_reference_actions as _city_data_ref,
)
from src.app.presentation.gui import summary_and_status_views as _summary_status

_PARTS = (_app_shell, _city_list, _city_form, _city_data_ref, _summary_status)


def _resolve_method(name: str):
    for module in _PARTS:
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(f"Method not found in parts: {name}")


class PrayerApp:
    """Main application class for the Prayer Times GUI."""

    def __init__(self, root): ...


PrayerApp.__init__ = _resolve_method("__init__")
PrayerApp.on_closing = _resolve_method("on_closing")
PrayerApp.create_widgets = _resolve_method("create_widgets")
PrayerApp.refresh_country_filter = _resolve_method("refresh_country_filter")
PrayerApp.on_tab_changed = _resolve_method("on_tab_changed")
PrayerApp.process_pending_reference_changes = _resolve_method(
    "process_pending_reference_changes"
)
PrayerApp._parse_location_row = _resolve_method("_parse_location_row")
PrayerApp.load_locations = _resolve_method("load_locations")
PrayerApp._format_location_for_file = _resolve_method("_format_location_for_file")
PrayerApp._distance_km = _resolve_method("_distance_km")
PrayerApp._distance_color = _resolve_method("_distance_color")
PrayerApp.rebuild_city_name_index = _resolve_method("rebuild_city_name_index")
PrayerApp._ensure_city_name_index = _resolve_method("_ensure_city_name_index")
PrayerApp.rebuild_city_rmse_index = _resolve_method("rebuild_city_rmse_index")
PrayerApp._ensure_city_rmse_index = _resolve_method("_ensure_city_rmse_index")
PrayerApp.rebuild_city_rmse_for_ids = _resolve_method("rebuild_city_rmse_for_ids")
PrayerApp.remove_city_rmse_cache_entries = _resolve_method(
    "remove_city_rmse_cache_entries"
)
PrayerApp.get_city_id_from_reference_path = _resolve_method(
    "get_city_id_from_reference_path"
)
PrayerApp.refresh_metrics_for_reference_paths = _resolve_method(
    "refresh_metrics_for_reference_paths"
)
PrayerApp.populate_listbox = _resolve_method("populate_listbox")
PrayerApp.filter_list = _resolve_method("filter_list")
PrayerApp.get_selected_location_data = _resolve_method("get_selected_location_data")
PrayerApp.disable_action_buttons = _resolve_method("disable_action_buttons")
PrayerApp.enable_action_buttons = _resolve_method("enable_action_buttons")
PrayerApp.update_prayer_times_display = _resolve_method("update_prayer_times_display")
PrayerApp.on_city_select = _resolve_method("on_city_select")
PrayerApp.calculate_and_display_prayer_times = _resolve_method(
    "calculate_and_display_prayer_times"
)
PrayerApp.refresh_prayer_times = _resolve_method("refresh_prayer_times")
PrayerApp.run_selected_city_optimizer = _resolve_method("run_selected_city_optimizer")
PrayerApp.on_month_select = _resolve_method("on_month_select")
PrayerApp.copy_times_to_clipboard = _resolve_method("copy_times_to_clipboard")
PrayerApp.create_city_form = _resolve_method("create_city_form")
PrayerApp.open_add_city_window = _resolve_method("open_add_city_window")
PrayerApp._validate_and_get_form_data = _resolve_method("_validate_and_get_form_data")
PrayerApp.save_new_city = _resolve_method("save_new_city")
PrayerApp.open_modify_city_window = _resolve_method("open_modify_city_window")
PrayerApp.save_modified_city = _resolve_method("save_modified_city")
PrayerApp.apply_to_country = _resolve_method("apply_to_country")
PrayerApp.delete_selected_city = _resolve_method("delete_selected_city")
PrayerApp._get_reference_file_path = _resolve_method("_get_reference_file_path")
PrayerApp._parse_reference_times = _resolve_method("_parse_reference_times")
PrayerApp._calculate_time_difference = _resolve_method("_calculate_time_difference")
PrayerApp._get_color_for_difference = _resolve_method("_get_color_for_difference")
PrayerApp._show_reference_times = _resolve_method("_show_reference_times")
PrayerApp._open_reference_file = _resolve_method("_open_reference_file")
PrayerApp._create_reference_file = _resolve_method("_create_reference_file")
PrayerApp.show_conclusion_summary = _resolve_method("show_conclusion_summary")
PrayerApp.update_status_bar = _resolve_method("update_status_bar")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _lazy_imports()
    root = tk.Tk()
    app = PrayerApp(root)
    root.mainloop()
