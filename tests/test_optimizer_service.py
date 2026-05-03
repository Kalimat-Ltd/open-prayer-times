import json
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.application.optimizer_service import estimate_city_angles_from_reference
from src.app.infrastructure.location_repository import CsvLocationRepository
from src.app.infrastructure.optimizer_estimators import quick_estimate_angles_native
from src.app.infrastructure.reference_repository import load_reference_times


class TestOptimizerService(unittest.TestCase):
    def setUp(self):
        fixture_path = REPO_ROOT / "tests" / "fixtures" / "parity_matrix.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.reference_file = REPO_ROOT / fixture["reference_file"]
        self.locations_csv_path = REPO_ROOT / "resources" / "locations.csv"
        self.city_name = "Russia, Kazan"

    def test_optimizer_service_matches_native_estimator(self):
        service_estimate = estimate_city_angles_from_reference(
            city_name=self.city_name,
            reference_file=self.reference_file,
            loc_csv_path=self.locations_csv_path,
            timezone_offset_hours=3,
        )

        repository = CsvLocationRepository(self.locations_csv_path)
        city = repository.get_by_name(self.city_name)
        reference_times, available_dates = load_reference_times(self.reference_file)

        native_estimate = quick_estimate_angles_native(
            reference_times=reference_times,
            available_dates=available_dates,
            lat=city.latitude,
            lon=city.longitude,
            elevation=city.elevation,
            timezone=3,
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

        self.assertEqual(service_estimate, native_estimate)


if __name__ == "__main__":
    unittest.main()
