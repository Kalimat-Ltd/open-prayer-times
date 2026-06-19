from pathlib import Path
import json
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.infrastructure.reference_repository import load_reference_times
from src.app.infrastructure.optimizer.multistage.shared import Stage1Config
from src.app.infrastructure.optimizer.multistage.stage1 import (
    optimize_pure_astronomical_core,
)


class TestMultistageStage1AsrOverrideOslo(unittest.TestCase):
    def test_oslo_detects_hanafi_base_and_standard_override_window(self):
        reference_file = REPO_ROOT / "reference" / "NO" / "norway_oslo.txt"
        reference_times, available_dates = load_reference_times(
            reference_file, year=2026
        )

        location_data = {
            "id": 31386,
            "country_code": "NO",
            "name": "Norway, Oslo",
            "latitude": 59.9133,
            "longitude": 10.7389,
            "optimized_lat": 59.94344592596073,
            "optimized_lon": 10.918535023853137,
            "timezone": "Europe/Oslo",
            "elevation": 0.0,
            "pressure": 1013.2499961689455,
            "temp": -27.998007256605547,
            "calculation_method": "angle_based",
            "fajr_angle": 16.0,
            "isha_angle": 15.0,
            "isha_minutes": 0.0,
            "isha_shafaq": "general",
            "high_lat_method": 0,
            "asr_madhab": 0,
            "isha_harag": 0,
            "fajr_offset": 0.0,
            "shurooq_offset": 0.0,
            "dhuhr_offset": 0.0,
            "asr_offset": 0.0,
            "maghrib_offset": 0.0,
            "isha_offset": 0.0,
        }

        context, diag = optimize_pure_astronomical_core(
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Oslo",
            config=Stage1Config(),
        )

        self.assertEqual(context.asr_madhab, 1)  # 1 = hanafi

        override_blocks = json.loads(context.asr_madhab_overrides)
        self.assertEqual(
            override_blocks,
            [{"start": "03-29", "end": "10-24", "asr_madhab": 0}],
        )

        detection = diag.asr_madhab_detection or {}
        self.assertTrue(detection.get("enabled"))
        self.assertEqual(detection.get("selected"), "hanafi")
        self.assertEqual(detection.get("reason"), "hanafi_reduces_asr_mae")
        self.assertEqual(
            detection.get("seasonal_override_reason"),
            "override_detected",
        )
        self.assertEqual(detection.get("seasonal_override_madhab"), "standard")

        ranges = detection.get("seasonal_override_ranges") or []
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0].get("start_month_day"), "03-29")
        self.assertEqual(ranges[0].get("end_month_day"), "10-24")
        self.assertGreater(float(ranges[0].get("base_mae", 0.0)), 30.0)
        self.assertLess(float(ranges[0].get("alt_mae", 999.0)), 2.0)


if __name__ == "__main__":
    unittest.main()
