import datetime
from unittest import TestCase
from unittest.mock import patch

from src.app.infrastructure.prayer_calculator import calculate_prayer_times


class TestHighLatCustomIndicator(TestCase):
    def test_angle_based_custom_fallback_updates_method_used(self):
        target_date = datetime.date(2026, 1, 15)

        def fake_handle(raw, method, prayer, _obs, _sun, fajr_angle, isha_angle):
            if (
                method == 0
                and prayer == "fajr"
                and abs(float(fajr_angle) - 16.5) < 1e-6
            ):
                return None
            if (
                method == 0
                and prayer == "isha"
                and abs(float(isha_angle) - 14.5) < 1e-6
            ):
                return None
            if method == 1:
                anchor = raw.get("shurooq") if prayer == "fajr" else raw.get("maghrib")
                if anchor is None:
                    return None
                delta = -90 if prayer == "fajr" else 90
                return anchor + datetime.timedelta(minutes=delta)
            return None

        with patch(
            "src.app.infrastructure.prayer_calculator.handle_high_latitudes",
            side_effect=fake_handle,
        ):
            _times, method_used, _error = calculate_prayer_times(
                lat_dec=24.7136,
                lon_dec=46.6753,
                elevation=612.0,
                pressure=1005.0,
                temp=20.0,
                tz_name="Asia/Riyadh",
                tz_offset_hours=3.0,
                fajr_angle=18.0,
                isha_angle=17.0,
                isha_minutes=0.0,
                target_date=target_date,
                high_lat_method=0,
                high_lat_start_date=target_date,
                high_lat_end_date=target_date,
                custom_fajr_angle=16.5,
                custom_isha_angle=14.5,
                high_lat_fallback_method=1,
                rounding="nearest",
            )

        self.assertEqual(method_used.get("fajr"), 1)
        self.assertEqual(method_used.get("isha"), 1)

    def test_angle_based_without_custom_uses_angle_indicator(self):
        target_date = datetime.date(2026, 1, 16)

        def fake_handle(raw, _method, prayer, _obs, _sun, _fajr_angle, _isha_angle):
            anchor = raw.get("shurooq") if prayer == "fajr" else raw.get("maghrib")
            if anchor is None:
                return None
            delta = -80 if prayer == "fajr" else 80
            return anchor + datetime.timedelta(minutes=delta)

        with patch(
            "src.app.infrastructure.prayer_calculator.handle_high_latitudes",
            side_effect=fake_handle,
        ):
            _times, method_used, _error = calculate_prayer_times(
                lat_dec=24.7136,
                lon_dec=46.6753,
                elevation=612.0,
                pressure=1005.0,
                temp=20.0,
                tz_name="Asia/Riyadh",
                tz_offset_hours=3.0,
                fajr_angle=18.0,
                isha_angle=17.0,
                isha_minutes=0.0,
                target_date=target_date,
                high_lat_method=0,
                high_lat_start_date=target_date,
                high_lat_end_date=target_date,
                custom_fajr_angle=None,
                custom_isha_angle=None,
                high_lat_fallback_method=1,
                rounding="nearest",
            )

        self.assertEqual(method_used.get("fajr"), 0)
        self.assertEqual(method_used.get("isha"), 0)
