from pathlib import Path
from typing import Any, Dict, Optional

from src.app.infrastructure.location_repository import CsvLocationRepository
from src.app.infrastructure.optimizer.multistage.pipeline import (
    run_multistage_optimization,
)
from src.app.infrastructure.reference_repository import load_reference_times


def optimize_city_from_reference(
    city_name: str,
    reference_file: Path,
    loc_csv_path: Path,
    timezone_offset_hours: float,
    tz_name: Optional[str] = None,
):
    repository = CsvLocationRepository(loc_csv_path)
    city = repository.get_by_name(city_name)
    reference_times, available_dates = load_reference_times(reference_file)

    location_data: Dict[str, Any] = {
        "name": city.name,
        "latitude": city.latitude,
        "longitude": city.longitude,
        "elevation": city.elevation,
        "timezone": timezone_offset_hours,
        "pressure": city.pressure,
        "temp": city.temp,
        "fajr_angle": city.fajr_angle,
        "isha_angle": city.isha_angle,
        "isha_minutes": city.isha_minutes,
        "asr_madhab": city.asr_madhab,
        "asr_madhab_overrides": city.asr_madhab_overrides or "",
        "high_lat_method": city.high_lat_method,
        "calculation_method": city.calculation_method,
        "isha_shafaq": city.isha_shafaq,
        "isha_harag": city.isha_harag,
        "fajr_offset": city.fajr_offset,
        "shurooq_offset": city.shurooq_offset,
        "dhuhr_offset": city.dhuhr_offset,
        "asr_offset": city.asr_offset,
        "maghrib_offset": city.maghrib_offset,
        "isha_offset": city.isha_offset,
    }

    return run_multistage_optimization(
        location_data=location_data,
        reference_times=reference_times,
        available_dates=available_dates,
        tz_name=tz_name or city.timezone,
        progress_callback=None,
    )
