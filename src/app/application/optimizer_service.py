from pathlib import Path
from typing import Optional, Tuple

from src.app.infrastructure.location_repository import CsvLocationRepository
from src.app.infrastructure.optimizer_estimators import quick_estimate_angles_native
from src.app.infrastructure.reference_repository import load_reference_times


def estimate_city_angles_from_reference(
    city_name: str,
    reference_file: Path,
    loc_csv_path: Path,
    timezone_offset_hours: float,
) -> Optional[Tuple[float, float]]:
    repository = CsvLocationRepository(loc_csv_path)
    city = repository.get_by_name(city_name)
    reference_times, available_dates = load_reference_times(reference_file)

    return quick_estimate_angles_native(
        reference_times=reference_times,
        available_dates=available_dates,
        lat=city.latitude,
        lon=city.longitude,
        elevation=city.elevation,
        timezone=timezone_offset_hours,
        tz_name=city.timezone,
        isha_minutes=city.isha_minutes,
        extra_calc_kwargs={
            "calculation_method": city.calculation_method,
            "isha_shafaq": city.isha_shafaq,
            "asr_madhab": city.asr_madhab,
            "high_lat_method": city.high_lat_method,
            "isha_harag": city.isha_harag,
        },
    )
