from pathlib import Path
import sys
import datetime
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.infrastructure.optimizer.multistage.shared import Stage1Config
from src.app.infrastructure.optimizer.multistage.stage1 import (
    optimize_pure_astronomical_core,
)
from src.app.infrastructure.reference_repository import load_reference_times


class TestMultistageStage1Paris(unittest.TestCase):
    def test_paris_finds_expected_core_angles_and_excluded_ranges(self):
        reference_file = REPO_ROOT / "reference" / "FR" / "france_paris.txt"
        reference_times, available_dates = load_reference_times(reference_file)

        location_data = {
            "name": "France, Paris",
            "latitude": 48.8566,
            "longitude": 2.3522,
            "elevation": 35.0,
            "timezone": "Europe/Paris",
            "temp": 10.0,
            "fajr_angle": 14.45,
            "isha_angle": 13.35,
            "isha_minutes": 0.0,
            "asr_madhab": 0,
            "calculation_method": "angle_based",
        }

        context, diag = optimize_pure_astronomical_core(
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Paris",
            config=Stage1Config(),
        )

        self.assertAlmostEqual(context.fajr_angle, 17.9, delta=1.0)
        self.assertAlmostEqual(context.isha_angle, 16.0, delta=0.8)

        excluded_ranges = list(context.excluded_date_ranges or [])
        self.assertTrue(excluded_ranges)

        excluded_months = set()
        for item in excluded_ranges:
            start_text = str(item.get("start") or "")
            end_text = str(item.get("end") or "")
            try:
                start_date = datetime.date.fromisoformat(start_text)
                end_date = datetime.date.fromisoformat(end_text)
            except ValueError:
                continue
            if end_date < start_date:
                start_date, end_date = end_date, start_date
            cursor = datetime.date(start_date.year, start_date.month, 1)
            end_month = datetime.date(end_date.year, end_date.month, 1)
            while cursor <= end_month:
                excluded_months.add(int(cursor.month))
                if cursor.month == 12:
                    cursor = datetime.date(cursor.year + 1, 1, 1)
                else:
                    cursor = datetime.date(cursor.year, cursor.month + 1, 1)

        expected_problematic = set(range(3, 11))
        self.assertGreaterEqual(len(excluded_months & expected_problematic), 5)
        self.assertIsNotNone(diag.method_comparison)
        method_comparison = diag.method_comparison or {}
        self.assertIn("angles_loss", method_comparison)
        self.assertIn("moonsighting_loss", method_comparison)


if __name__ == "__main__":
    unittest.main()
