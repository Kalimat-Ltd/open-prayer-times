import datetime
from pathlib import Path
from typing import Optional, Tuple

from src.app.application.optimizer_service import estimate_city_angles_from_reference
from src.app.application.use_cases import calculate_city_day_from_loc_csv


class PrayerGuiController:
    def __init__(self, loc_csv_path: Path):
        self.loc_csv_path = loc_csv_path

    def calculate_city_day(self, city_name: str, target_date: datetime.date):
        return calculate_city_day_from_loc_csv(
            city_name=city_name,
            target_date=target_date,
            loc_csv_path=self.loc_csv_path,
        )

    def estimate_city_angles(
        self,
        city_name: str,
        reference_file: Path,
        timezone_offset_hours: float,
    ) -> Optional[Tuple[float, float]]:
        return estimate_city_angles_from_reference(
            city_name=city_name,
            reference_file=reference_file,
            loc_csv_path=self.loc_csv_path,
            timezone_offset_hours=timezone_offset_hours,
        )
