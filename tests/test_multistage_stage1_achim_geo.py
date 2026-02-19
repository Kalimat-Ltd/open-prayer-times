from pathlib import Path
import copy
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.infrastructure.optimizer.multistage.shared import Stage1Config
from src.app.infrastructure.optimizer.multistage.stage1 import (
    optimize_pure_astronomical_core,
)
from src.app.infrastructure.reference_repository import load_reference_times


class TestMultistageStage1AchimGeoCalibration(unittest.TestCase):
    def test_achim_geo_calibration_uses_optimized_fields_without_mutating_base_coords(
        self,
    ):
        reference_file = REPO_ROOT / "reference" / "DE" / "germany_achim.txt"
        reference_times, available_dates = load_reference_times(reference_file)

        base_location_data = {
            "name": "Germany, Achim",
            "latitude": 53.0653,
            "longitude": 9.0342,
            "elevation": 0.0,
            "timezone": "Europe/Berlin",
            "temp": 10.0,
            "fajr_angle": 18.0,
            "isha_angle": 16.0,
            "isha_minutes": 0.0,
            "isha_shafaq": "abyad",
            "asr_madhab": 0,
            "calculation_method": "angle_based",
        }

        disabled_input = copy.deepcopy(base_location_data)
        enabled_input = copy.deepcopy(base_location_data)
        disabled_before = copy.deepcopy(disabled_input)
        enabled_before = copy.deepcopy(enabled_input)

        _disabled_ctx, disabled_diag = optimize_pure_astronomical_core(
            location_data=disabled_input,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Berlin",
            config=Stage1Config(enable_geographic_calibration=False),
        )
        enabled_ctx, enabled_diag = optimize_pure_astronomical_core(
            location_data=enabled_input,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Berlin",
            config=Stage1Config(enable_geographic_calibration=True),
        )

        self.assertEqual(disabled_input, disabled_before)
        self.assertEqual(enabled_input, enabled_before)

        self.assertIsNotNone(enabled_ctx.lat)
        self.assertIsNotNone(enabled_ctx.lon)
        self.assertAlmostEqual(enabled_input["latitude"], 53.0653, places=6)
        self.assertAlmostEqual(enabled_input["longitude"], 9.0342, places=6)

        self.assertIsNotNone(enabled_diag.geographic_calibration)
        geo = enabled_diag.geographic_calibration or {}
        self.assertTrue(geo.get("enabled"))
        self.assertFalse(geo.get("skipped"))
        self.assertTrue(geo.get("longitude_success"))
        self.assertTrue(geo.get("latitude_success"))

        self.assertLessEqual(
            float(geo["after_common_mode_mae"]),
            float(geo["before_common_mode_mae"]) + 1e-9,
        )
        self.assertLessEqual(
            float(geo["after_shape_mae"]),
            float(geo["before_shape_mae"]) + 1e-9,
        )

        self.assertIsNotNone(disabled_diag.geographic_calibration)
        disabled_geo = disabled_diag.geographic_calibration or {}
        self.assertFalse(disabled_geo.get("enabled"))


if __name__ == "__main__":
    unittest.main()
