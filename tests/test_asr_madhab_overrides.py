import datetime
import unittest

from src.app.infrastructure.asr_madhab_overrides import (
    resolve_effective_asr_madhab,
)
from src.app.infrastructure.prayer_calculator import calculate_prayer_times


class TestAsrMadhabOverrides(unittest.TestCase):
    OVERRIDES_JSON = '[{"start":"03-29","end":"10-24","asr_madhab":0}]'

    def test_effective_madhab_switches_inside_and_outside_window(self):
        self.assertEqual(
            resolve_effective_asr_madhab(
                1,
                self.OVERRIDES_JSON,
                datetime.date(2026, 4, 1),
            ),
            0,
        )
        self.assertEqual(
            resolve_effective_asr_madhab(
                1,
                self.OVERRIDES_JSON,
                datetime.date(2026, 10, 25),
            ),
            1,
        )

    def test_calculator_applies_override_for_asr_only(self):
        base_kwargs = {
            "lat_dec": 59.9133,
            "lon_dec": 10.7389,
            "elevation": 0.0,
            "pressure": 1013.25,
            "temp": -28.0,
            "tz_name": "Europe/Oslo",
            "fajr_angle": 16.0,
            "isha_angle": 15.0,
            "isha_minutes": 0.0,
            "rounding": "off",
            "calculation_method": "angle_based",
            "isha_shafaq": "general",
        }

        standard_times, _, _ = calculate_prayer_times(
            **base_kwargs,
            tz_offset_hours=2.0,
            target_date=datetime.date(2026, 4, 1),
            asr_madhab=0,
        )
        hanafi_times, _, _ = calculate_prayer_times(
            **base_kwargs,
            tz_offset_hours=2.0,
            target_date=datetime.date(2026, 4, 1),
            asr_madhab=1,
        )
        override_times, _, _ = calculate_prayer_times(
            **base_kwargs,
            tz_offset_hours=2.0,
            target_date=datetime.date(2026, 4, 1),
            asr_madhab=1,
            asr_madhab_overrides=self.OVERRIDES_JSON,
        )

        self.assertEqual(override_times["asr"], standard_times["asr"])
        self.assertNotEqual(override_times["asr"], hanafi_times["asr"])

        standard_outside, _, _ = calculate_prayer_times(
            **base_kwargs,
            tz_offset_hours=1.0,
            target_date=datetime.date(2026, 10, 25),
            asr_madhab=0,
        )
        hanafi_outside, _, _ = calculate_prayer_times(
            **base_kwargs,
            tz_offset_hours=1.0,
            target_date=datetime.date(2026, 10, 25),
            asr_madhab=1,
        )
        override_outside, _, _ = calculate_prayer_times(
            **base_kwargs,
            tz_offset_hours=1.0,
            target_date=datetime.date(2026, 10, 25),
            asr_madhab=1,
            asr_madhab_overrides=self.OVERRIDES_JSON,
        )

        self.assertEqual(override_outside["asr"], hanafi_outside["asr"])
        self.assertNotEqual(override_outside["asr"], standard_outside["asr"])


if __name__ == "__main__":
    unittest.main()
