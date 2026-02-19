"""Shared GUI wiring helpers that expose lazy-loaded dependencies and common adapters."""

from typing import Any, cast

from src.app.presentation.gui.clock import get_clock_offset_for_date
from src.app.presentation.gui.constants import FIELD_NAMES
from src.app.presentation.gui.deps import (
    ensure_loaded,
    get_calculate_prayer_times_fn,
    get_file_handler_class,
    get_geopy_distance_module,
    get_observer_class,
    get_open_batch_optimization_dashboard_fn,
    get_optimize_parameters_for_city_fn,
    get_pytz_module,
    get_timezone_finder_class,
)
from src.app.presentation.gui.file_ops import (
    rewrite_location_file as _rewrite_location_file,
)

rewrite_location_file = _rewrite_location_file


def _lazy_imports():
    ensure_loaded()


def _get_pytz() -> Any:
    _lazy_imports()
    return cast(Any, get_pytz_module())


def _get_geopy_distance() -> Any:
    _lazy_imports()
    return cast(Any, get_geopy_distance_module())


def _get_calculate_prayer_times() -> Any:
    _lazy_imports()
    return cast(Any, get_calculate_prayer_times_fn())


def _get_optimize_parameters_for_city() -> Any:
    _lazy_imports()
    return cast(Any, get_optimize_parameters_for_city_fn())


def _get_open_batch_optimization_dashboard() -> Any:
    _lazy_imports()
    return cast(Any, get_open_batch_optimization_dashboard_fn())


def _get_timezone_finder_class() -> type:
    _lazy_imports()
    return cast(type, get_timezone_finder_class())


def _get_observer_class() -> type:
    _lazy_imports()
    return cast(type, get_observer_class())


def _make_reference_folder_handler_class():
    base_handler = cast(type, get_file_handler_class())

    class ReferenceFolderHandler(base_handler):
        def __init__(self, callback):
            super().__init__()
            self._callback = callback

        def on_modified(self, event):
            if not event.is_directory:
                self._callback(event)

        def on_created(self, event):
            if not event.is_directory:
                self._callback(event)

        def on_deleted(self, event):
            if not event.is_directory:
                self._callback(event)

        def on_moved(self, event):
            if not event.is_directory:
                self._callback(event)

    return ReferenceFolderHandler


field_names = FIELD_NAMES
_get_clock_offset_for_date = get_clock_offset_for_date
