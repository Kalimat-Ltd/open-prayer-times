"""Lazy dependency loader/cache for optional GUI runtime modules and heavy integrations."""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class _State:
    timezone_finder_cls: Optional[type] = None
    geopy_distance_module: Any = None
    observer_cls: Optional[type] = None
    file_handler_cls: Optional[type] = None
    pytz_module: Any = None
    calculate_prayer_times_fn: Any = None
    optimize_parameters_for_city_fn: Any = None
    open_batch_optimization_dashboard_fn: Any = None


_STATE = _State()


def ensure_loaded():
    if _STATE.timezone_finder_cls is not None:
        return

    from timezonefinder import TimezoneFinder
    import geopy.distance
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    import pytz

    from src.app.infrastructure.prayer_calculator import calculate_prayer_times
    from src.app.infrastructure.optimizer.batch_gui import (
        optimize_parameters_for_city,
        open_batch_optimization_dashboard,
    )

    _STATE.timezone_finder_cls = TimezoneFinder
    _STATE.geopy_distance_module = geopy.distance
    _STATE.observer_cls = Observer
    _STATE.file_handler_cls = FileSystemEventHandler
    _STATE.pytz_module = pytz
    _STATE.calculate_prayer_times_fn = calculate_prayer_times
    _STATE.optimize_parameters_for_city_fn = optimize_parameters_for_city
    _STATE.open_batch_optimization_dashboard_fn = open_batch_optimization_dashboard


def get_timezone_finder_class() -> type:
    ensure_loaded()
    assert _STATE.timezone_finder_cls is not None
    return _STATE.timezone_finder_cls


def get_observer_class() -> type:
    ensure_loaded()
    assert _STATE.observer_cls is not None
    return _STATE.observer_cls


def get_file_handler_class() -> type:
    ensure_loaded()
    assert _STATE.file_handler_cls is not None
    return _STATE.file_handler_cls


def get_pytz_module():
    ensure_loaded()
    return _STATE.pytz_module


def get_geopy_distance_module():
    ensure_loaded()
    return _STATE.geopy_distance_module


def get_calculate_prayer_times_fn():
    ensure_loaded()
    return _STATE.calculate_prayer_times_fn


def get_optimize_parameters_for_city_fn():
    ensure_loaded()
    return _STATE.optimize_parameters_for_city_fn


def get_open_batch_optimization_dashboard_fn():
    ensure_loaded()
    return _STATE.open_batch_optimization_dashboard_fn
