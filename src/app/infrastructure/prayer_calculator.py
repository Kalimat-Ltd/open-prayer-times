# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false, reportOptionalMemberAccess=false, reportArgumentType=false
# ruff: noqa: E722, BLE001, F541
# pylint: disable=broad-exception-caught,bare-except
import math
import datetime
import csv
import os
import ephem
from src.app.config import LOC_CSV_PATH
import pytz  # For timezone handling

# --- Constants ---
STANDARD_PRESSURE = 1010.0  # mBar
STANDARD_TEMP = 10.0  # Celsius

# --- Moonsighting Calculation Helper Functions ---


def get_dyy(target_date, latitude):
    """Calculates the day of the year relative to the solstices."""
    year = target_date.year
    if latitude > 0:  # Northern Hemisphere
        solstice_date = datetime.datetime.strptime(f"12-21-{year}", "%m-%d-%Y").date()
    else:  # Southern Hemisphere
        solstice_date = datetime.datetime.strptime(f"06-21-{year}", "%m-%d-%Y").date()

    diff = (target_date - solstice_date).days

    if diff > 0:
        dyy = diff
    else:
        dyy = (
            365 + diff
        )  # Handle leap year not explicitly, but this matches PHP logic for diff

    return dyy


def get_moonsighting_minutes(dyy, a, b, c, d):
    """Calculates the base minutes based on the day of the year and parameters a, b, c, d."""
    if dyy < 91:
        return a + (b - a) / 91 * dyy  # '91 DAYS SPAN
    elif dyy < 137:
        return b + (c - b) / 46 * (dyy - 91)  # '46 DAYS SPAN
    elif dyy < 183:
        return c + (d - c) / 46 * (dyy - 137)  # '46 DAYS SPAN
    elif dyy < 229:
        return d + (c - d) / 46 * (dyy - 183)  # '46 DAYS SPAN
    elif dyy < 275:
        return c + (b - c) / 46 * (dyy - 229)  # '46 DAYS SPAN
    elif dyy >= 275:
        return b + (a - b) / 91 * (dyy - 275)  # ' 91 DAYS SPAN


def calculate_fajr_moonsighting(target_date, latitude):
    """Calculates Fajr minutes before sunrise using the Moonsighting method."""
    dyy = get_dyy(target_date, latitude)

    # Fajr parameters from Fajr.php
    a = 75 + 28.65 / 55 * abs(latitude)
    b = 75 + 19.44 / 55 * abs(latitude)
    c = 75 + 32.74 / 55 * abs(latitude)
    d = 75 + 48.1 / 55 * abs(latitude)

    return round(get_moonsighting_minutes(dyy, a, b, c, d))


def calculate_isha_moonsighting(target_date, latitude, shafaq="general"):
    """Calculates Isha minutes after sunset using the Moonsighting method."""
    dyy = get_dyy(target_date, latitude)

    # Isha parameters from Isha.php based on shafaq
    if shafaq == "ahmer":
        a = 62 + 17.4 / 55.0 * abs(latitude)
        b = 62 - 7.16 / 55.0 * abs(latitude)
        c = 62 + 5.12 / 55.0 * abs(latitude)
        d = 62 + 19.44 / 55.0 * abs(latitude)
    elif shafaq == "abyad":
        a = 75 + 25.6 / 55.0 * abs(latitude)
        b = 75 + 7.16 / 55.0 * abs(latitude)
        c = 75 + 36.84 / 55.0 * abs(latitude)
        d = 75 + 81.84 / 55.0 * abs(latitude)
    else:  # general
        a = 75 + 25.6 / 55.0 * abs(latitude)
        c = 75 - 9.21 / 55.0 * abs(latitude)
        b = 75 + 2.05 / 55.0 * abs(latitude)
        d = 75 + 6.14 / 55.0 * abs(latitude)

    return round(get_moonsighting_minutes(dyy, a, b, c, d))


def calculate_prayer_times(
    lat_dec,
    lon_dec,
    elevation,
    pressure,
    temp,
    tz_name,
    tz_offset_hours,
    fajr_angle,
    isha_angle,
    isha_minutes,
    target_date,
    asr_madhab=0,  # 0 = Standard (Shafi, Maliki, Hanbali), 1 = Hanafi
    fajr_offset=0.0,
    shurooq_offset=0.0,
    dhuhr_offset=0.0,
    asr_offset=0.0,
    maghrib_offset=0.0,
    isha_offset=0.0,
    high_lat_method=0,  # 0 = angle based, 1 = one seventh, 2 = midnight, 3 = closest city
    skip_fallback=False,
    calculation_method="angle_based",  # 'angle_based' or 'moonsighting'
    isha_shafaq="general",  # 'ahmer', 'abyad', or 'general' for moonsighting method
    high_lat_start_date=None,  # Optional: force high latitude method from this date
    high_lat_end_date=None,  # Optional: force high latitude method until this date
    custom_fajr_angle=None,  # Optional custom fajr angle for angle-based high-lat handling
    custom_isha_angle=None,  # Optional custom isha angle for angle-based high-lat handling
    high_lat_fallback_method=None,  # Optional fallback method for custom-angle failures
    isha_harag=0,  # Haraj support: 0, 1, 2, 3
    rounding="nearest",  # "off", "nearest", "floor", "ceil"
):
    # Coerce optional parameters to their expected defaults when None
    if asr_madhab is None:
        asr_madhab = 0
    if fajr_offset is None:
        fajr_offset = 0.0
    if shurooq_offset is None:
        shurooq_offset = 0.0
    if dhuhr_offset is None:
        dhuhr_offset = 0.0
    if asr_offset is None:
        asr_offset = 0.0
    if maghrib_offset is None:
        maghrib_offset = 0.0
    if isha_offset is None:
        isha_offset = 0.0
    if high_lat_method is None:
        high_lat_method = 0
    if skip_fallback is None:
        skip_fallback = False
    if calculation_method is None:
        calculation_method = "angle_based"
    if isha_shafaq is None:
        isha_shafaq = "general"
    if isha_harag is None:
        isha_harag = 0
    if custom_fajr_angle is not None:
        try:
            custom_fajr_angle = float(custom_fajr_angle)
        except (TypeError, ValueError):
            custom_fajr_angle = None
    if custom_isha_angle is not None:
        try:
            custom_isha_angle = float(custom_isha_angle)
        except (TypeError, ValueError):
            custom_isha_angle = None
    if high_lat_fallback_method is not None:
        try:
            high_lat_fallback_method = int(high_lat_fallback_method)
        except (TypeError, ValueError):
            high_lat_fallback_method = None
    if rounding is None:
        rounding = "nearest"
    obs = ephem.Observer()
    obs.lat = str(lat_dec)
    obs.lon = str(lon_dec)

    # Precompute dip correction in degrees: dip_minutes = 1.76 * sqrt(h)
    dip_minutes = 1.76 * math.sqrt(max(elevation, 0))
    dip_degrees = dip_minutes / 60.0

    if calculation_method != "moonsighting":
        obs.elevation = float(elevation)
        obs.pressure = float(pressure or STANDARD_PRESSURE)
        obs.temp = float(temp or STANDARD_TEMP)

    obs.horizon = "0"

    # Anchor at UTC midnight
    dt_utc = datetime.datetime.combine(
        target_date, datetime.time(0, 0, 0), tzinfo=datetime.timezone.utc
    )
    obs.date = ephem.Date(dt_utc)
    sun = getattr(ephem, "Sun")()
    raw = {}

    fallback_to_closest_city = False

    # Track which method was used for each prayer:
    # 0=angle-based (default), 1=one-seventh, 2=midnight, 3=closest-city, 4=moonsighting
    method_used = {
        "fajr": -1,
        "shurooq": -1,
        "dhuhr": -1,
        "asr": -1,
        "maghrib": -1,
        "isha": -1,
    }

    # Determine if we should force high latitude method for Fajr/Isha
    force_high_lat = False
    if high_lat_start_date and high_lat_end_date:
        if high_lat_start_date <= target_date <= high_lat_end_date:
            force_high_lat = True

    def _apply_high_lat_adjustment(prayer):
        use_custom_angle = False
        if int(high_lat_method) == 0:
            if (
                prayer == "fajr"
                and custom_fajr_angle is not None
                and custom_fajr_angle > 0
            ):
                use_custom_angle = True
            if (
                prayer == "isha"
                and custom_isha_angle is not None
                and custom_isha_angle > 0
            ):
                use_custom_angle = True

        if use_custom_angle:
            custom_fajr = (
                float(custom_fajr_angle)
                if custom_fajr_angle is not None and custom_fajr_angle > 0
                else float(fajr_angle)
            )
            custom_isha = (
                float(custom_isha_angle)
                if custom_isha_angle is not None and custom_isha_angle > 0
                else float(isha_angle)
            )
            adjusted = handle_high_latitudes(
                raw,
                0,
                prayer,
                obs,
                sun,
                custom_fajr,
                custom_isha,
            )
            if adjusted is not None:
                return adjusted, 0

            fallback_method = (
                int(high_lat_fallback_method)
                if high_lat_fallback_method in (1, 2, 3)
                else 1
            )
            if fallback_method == 3:
                return None, 3
            fallback_adjusted = handle_high_latitudes(
                raw,
                fallback_method,
                prayer,
                obs,
                sun,
                float(fajr_angle),
                float(isha_angle),
            )
            return fallback_adjusted, fallback_method

        adjusted = handle_high_latitudes(
            raw,
            int(high_lat_method),
            prayer,
            obs,
            sun,
            float(fajr_angle),
            float(isha_angle),
        )
        return adjusted, int(high_lat_method)

    # 1. Dhuhr (solar transit)
    try:
        t = obs.next_transit(sun)
        raw["dhuhr"] = t.datetime().replace(tzinfo=datetime.timezone.utc)
    except:
        raw["dhuhr"] = None

    # 2. Shurooq (sunrise)
    try:
        obs.horizon = str(dip_degrees)
        obs.date = ephem.Date(dt_utc - datetime.timedelta(hours=12))
        sr = obs.next_rising(sun, use_center=False)
        raw["shurooq"] = sr.datetime().replace(tzinfo=datetime.timezone.utc)
    except:
        raw["shurooq"] = None
        if not skip_fallback:
            # Polar day/night detected: fallback to the closest city with a moderate-latitude
            method_used["shurooq"] = high_lat_method
            fallback_to_closest_city = True

    # 3. Maghrib (sunset)
    try:
        obs.horizon = str(dip_degrees)
        obs.date = ephem.Date(dt_utc)
        ms = obs.next_setting(sun, use_center=False)
        raw["maghrib"] = ms.datetime().replace(tzinfo=datetime.timezone.utc)
    except:
        raw["maghrib"] = None
        if not skip_fallback:
            # Polar day/night detected: fallback to the closest city with a moderate-latitude
            method_used["maghrib"] = high_lat_method
            fallback_to_closest_city = True

        # 4. Isha
    if calculation_method == "moonsighting":
        if raw.get("maghrib"):
            # Moonsighting-based Isha
            isha_ms = raw["maghrib"] + datetime.timedelta(  # type: ignore[operator]
                minutes=calculate_isha_moonsighting(target_date, lat_dec, isha_shafaq)
            )
            isha_ab = None
            isha_one_seventh = None

            if abs(lat_dec) < 55:
                # 18° angle-based Isha
                try:
                    obs.horizon = "-18"
                    obs.date = ephem.Date(raw["maghrib"])
                    ib = obs.next_setting(sun, use_center=True)
                    isha_ab = ib.datetime().replace(tzinfo=datetime.timezone.utc)
                except:
                    isha_ab = None
            elif 55 <= abs(lat_dec) <= 60:
                isha_one_seventh = handle_high_latitudes(
                    raw, 1, "isha", obs, sun, fajr_angle, isha_angle
                )

            # Pick the earliest valid time
            candidates = [t for t in (isha_ms, isha_ab, isha_one_seventh) if t]
            raw["isha"] = min(candidates) if candidates else None
            method_used["isha"] = 1

        else:
            raw["isha"] = None  # Cannot calculate without sunset
    else:
        # 4. Isha (either fixed minutes or angle)
        if isha_minutes is not None and isha_minutes > 0:
            mag = raw.get("maghrib")
            raw["isha"] = (
                (mag + datetime.timedelta(minutes=isha_minutes)) if mag else None
            )
        else:
            try:
                obs.horizon = str(-abs(isha_angle) + dip_degrees)
                obs.date = ephem.Date(raw.get("maghrib") or dt_utc)
                ish = obs.next_setting(sun, use_center=True)
                raw["isha"] = ish.datetime().replace(tzinfo=datetime.timezone.utc)
                # Force high latitude method if in forced period
                if force_high_lat:
                    raw["isha"], method_used["isha"] = _apply_high_lat_adjustment(
                        "isha"
                    )
                    if raw["isha"] is None and method_used["isha"] == 3:
                        fallback_to_closest_city = True
            except:
                if high_lat_method == 3:
                    fallback_to_closest_city = True
                if not fallback_to_closest_city or force_high_lat:
                    raw["isha"], method_used["isha"] = _apply_high_lat_adjustment(
                        "isha"
                    )
                    if raw["isha"] is None and method_used["isha"] == 3:
                        fallback_to_closest_city = True
                else:
                    # Will be handeled later in closest_city_fallback()
                    raw["isha"] = None
                    fallback_to_closest_city = True

    # 5. Fajr
    if calculation_method == "moonsighting":
        if raw.get("shurooq"):
            # Moonsighting-based Fajr
            fajr_ms = raw["shurooq"] - datetime.timedelta(  # type: ignore[operator]
                minutes=calculate_fajr_moonsighting(target_date, lat_dec)
            )
            fajr_ab = None
            fajr_one_seventh = None

            if abs(lat_dec) < 55:
                # 18° angle-based Fajr
                try:
                    obs.horizon = "-18"
                    obs.date = ephem.Date(dt_utc - datetime.timedelta(hours=12))
                    fj = obs.next_rising(sun, use_center=True)
                    fajr_ab = fj.datetime().replace(tzinfo=datetime.timezone.utc)
                except:
                    fajr_ab = None
            elif 55 <= abs(lat_dec) <= 60:
                fajr_one_seventh = handle_high_latitudes(
                    raw, 1, "fajr", obs, sun, fajr_angle, isha_angle
                )

            # Pick the latest valid time
            candidates = [t for t in (fajr_ms, fajr_ab, fajr_one_seventh) if t]
            raw["fajr"] = max(candidates) if candidates else None
            method_used["fajr"] = 1
        else:
            raw["fajr"] = None  # Cannot calculate without sunrise
            fallback_to_closest_city = True
    else:
        try:
            obs.horizon = f"{-abs(fajr_angle)}"
            obs.date = ephem.Date(dt_utc - datetime.timedelta(hours=12))
            fj = obs.next_rising(sun, use_center=True)
            raw["fajr"] = fj.datetime().replace(tzinfo=datetime.timezone.utc)
            # Force high latitude method if in forced period
            if force_high_lat:
                raw["fajr"], method_used["fajr"] = _apply_high_lat_adjustment("fajr")
                if raw["fajr"] is None and method_used["fajr"] == 3:
                    fallback_to_closest_city = True
        except:
            if high_lat_method == 3:
                fallback_to_closest_city = True
            if not fallback_to_closest_city or force_high_lat:
                raw["fajr"], method_used["fajr"] = _apply_high_lat_adjustment("fajr")
                if raw["fajr"] is None and method_used["fajr"] == 3:
                    fallback_to_closest_city = True
            else:
                # Will be handeled later in closest_city_fallback()
                raw["fajr"] = None

    # 6. Asr (Standard or Hanafi)
    if raw.get("dhuhr"):
        try:
            # obs.temp = 10000000.0  # Disable refraction for Asr calculation
            # obs.elevation = 0.0     # Disable dip for Asr calculation
            # obs.pressure = 0.0      # Disable pressure for Asr calculation
            obs.horizon = "0"
            obs.date = ephem.Date(raw["dhuhr"])
            sun.compute(obs)
            # Use float for sun.dec and obs.lat for accurate calculation
            diff_deg = abs(float(obs.lat) - float(sun.dec))

            if asr_madhab == 1:
                # Hanafi: shadow = 2x object height
                alt_asr = math.degrees(math.atan(1.0 / (2.0 + math.tan(diff_deg))))
            else:
                # Standard: shadow = 1x object height
                alt_asr = math.degrees(math.atan(1.0 / (1.0 + math.tan(diff_deg))))
            obs.horizon = f"{alt_asr}"
            obs.date = ephem.Date(raw["dhuhr"])
            a = obs.next_setting(sun, use_center=True)
            raw["asr"] = a.datetime().replace(tzinfo=datetime.timezone.utc)
        except Exception as e:
            raw["asr"] = None
            print(f"Error calculating Asr: {e}")
    else:
        raw["asr"] = None

    def combine_date_and_time(date, time_str):
        if time_str == "N/A":
            return None

        s = 0
        if rounding != "off":
            h, m = map(int, time_str.split(":"))
        else:
            parts = time_str.split(":")
            if len(parts) >= 3:
                h, m, s = map(int, parts[:3])
            else:
                h, m = map(int, parts[:2])

        return datetime.datetime(
            date.year, date.month, date.day, h, m, s, tzinfo=datetime.timezone.utc
        )

    if fallback_to_closest_city:
        # fajr, shurooq, dhuhr, asr, maghrib, isha = closest_city_fallback(lat_dec, lon_dec, target_date)
        raw_times, _, _ = calculate_prayer_times(
            lat_dec=48.0 if calculation_method == "angle_based" else 60,
            lon_dec=lon_dec,
            elevation=elevation,
            pressure=pressure,
            temp=temp,
            tz_name=tz_name,
            tz_offset_hours=tz_offset_hours,
            fajr_angle=fajr_angle,
            isha_angle=isha_angle,
            isha_minutes=isha_minutes,
            target_date=target_date,
            asr_madhab=asr_madhab,
            fajr_offset=fajr_offset,
            shurooq_offset=shurooq_offset,
            dhuhr_offset=dhuhr_offset,
            asr_offset=asr_offset,
            maghrib_offset=maghrib_offset,
            isha_offset=isha_offset,
        )

        # The times from closest_city_fallback are strings, convert them to datetime objects
        raw["fajr"] = combine_date_and_time(target_date, raw_times.get("fajr"))
        raw["isha"] = combine_date_and_time(target_date, raw_times.get("isha"))

        method_used["fajr"] = method_used["isha"] = 3

        if raw["shurooq"] is None or raw["shurooq"] == "N/A":
            raw["shurooq"] = combine_date_and_time(
                target_date, raw_times.get("shurooq")
            )
            method_used["shurooq"] = 3
        if raw["dhuhr"] is None or raw["dhuhr"] == "N/A":
            raw["dhuhr"] = combine_date_and_time(target_date, raw_times.get("dhuhr"))
            method_used["dhuhr"] = 3
        if raw["asr"] is None or raw["asr"] == "N/A":
            raw["asr"] = combine_date_and_time(target_date, raw_times.get("asr"))
            method_used["asr"] = 3
        if raw["maghrib"] is None or raw["maghrib"] == "N/A":
            raw["maghrib"] = combine_date_and_time(
                target_date, raw_times.get("maghrib")
            )
            method_used["maghrib"] = 3
    else:
        if calculation_method == "moonsighting":
            raw["maghrib"] += datetime.timedelta(minutes=float(3))  # type: ignore[operator]
            raw["dhuhr"] += datetime.timedelta(minutes=float(5))  # type: ignore[operator]

        # Apply offsets to raw UTC times
        prayer_offsets = {
            "fajr": fajr_offset,
            "shurooq": shurooq_offset,
            "dhuhr": dhuhr_offset,
            "asr": asr_offset,
            "maghrib": maghrib_offset,
            "isha": isha_offset,
        }

        for prayer, offset in prayer_offsets.items():
            if raw.get(prayer) is not None and offset != 0:
                raw[prayer] += datetime.timedelta(minutes=float(offset))

    # Haraj adjustment for Isha
    if isha_harag != 0 and raw.get("isha"):
        local_isha = convert_to_local_time(raw["isha"], tz_name, tz_offset_hours)
        local_sunset = convert_to_local_time(raw["maghrib"], tz_name, tz_offset_hours)

        if isha_harag == 1:
            year = target_date.year
            solstice = (
                datetime.date(year, 6, 21)
                if lat_dec > 0
                else datetime.date(year, 12, 21)
            )
            obs = ephem.Observer()
            obs.lat, obs.lon = str(lat_dec), str(lon_dec)
            obs.date = ephem.Date(
                datetime.datetime.combine(
                    solstice, datetime.time(12), tzinfo=datetime.timezone.utc
                )
            )
            sol_sunset = (
                obs.next_setting(getattr(ephem, "Sun")())
                .datetime()
                .replace(tzinfo=datetime.timezone.utc)
            )
            local_sol_sunset = convert_to_local_time(
                sol_sunset, tz_name, tz_offset_hours
            )
            if local_sol_sunset is not None:
                local_limit = local_sol_sunset + datetime.timedelta(minutes=65)
                if local_isha > local_limit:
                    local_isha = local_limit

        elif isha_harag == 2:
            obs = ephem.Observer()
            obs.lat, obs.lon = str(lat_dec), str(lon_dec)
            obs.horizon = "-15"
            obs.date = ephem.Date(raw["maghrib"])
            try:
                isha_15 = (
                    obs.next_setting(getattr(ephem, "Sun")(), use_center=True)
                    .datetime()
                    .replace(tzinfo=datetime.timezone.utc)
                )
                local_isha_15 = convert_to_local_time(isha_15, tz_name, tz_offset_hours)
            except:
                local_isha_15 = None
            obs = ephem.Observer()
            obs.lat, obs.lon = str(lat_dec), str(lon_dec)
            obs.date = ephem.Date(raw["maghrib"])
            try:
                next_sr = (
                    obs.next_rising(getattr(ephem, "Sun")(), use_center=False)
                    .datetime()
                    .replace(tzinfo=datetime.timezone.utc)
                )
                night_len = next_sr - raw["maghrib"]
                one7 = local_sunset + night_len / 7
            except:
                one7 = None
            candidates = [t for t in (local_isha_15, one7) if t]
            if candidates:
                local_isha = min(candidates)

        elif isha_harag == 3:
            lock_time = local_isha.replace(hour=23, minute=0, second=0)  # type: ignore[union-attr]
            if local_isha > lock_time:  # type: ignore[operator]
                local_isha = lock_time

        # Convert adjusted local back to UTC without tzinfo conflicts
        try:
            if tz_name:
                tz = pytz.timezone(tz_name)
                # Ensure naive before localize
                if local_isha.tzinfo is not None:  # type: ignore[union-attr]
                    local_naive = local_isha.replace(tzinfo=None)  # type: ignore[union-attr]
                else:
                    local_naive = local_isha
                localized = tz.localize(local_naive)  # type: ignore[arg-type]
                adj_utc = localized.astimezone(datetime.timezone.utc)
            else:
                # Fallback offset
                if local_isha.tzinfo is not None:  # type: ignore[union-attr]
                    # already aware
                    adj_utc = local_isha.astimezone(datetime.timezone.utc)  # type: ignore[union-attr]
                else:
                    adj_utc = local_isha - datetime.timedelta(hours=tz_offset_hours)  # type: ignore[operator]
            raw["isha"] = adj_utc
        except Exception as e:
            print(e)

    # Convert to local strings
    formatted = {}
    for name, utc_dt in raw.items():
        local_dt = convert_to_local_time(utc_dt, tz_name, tz_offset_hours)

        # --- apply rounding ---
        if local_dt and rounding != "off":
            h, m, s = local_dt.hour, local_dt.minute, local_dt.second
            if rounding == "nearest":
                if s >= 30:
                    m += 1
            elif rounding == "ceil":
                if s > 0:
                    m += 1
            # floor (and after nearest/ceil) just drop seconds
            # handle overflow of minutes → hours
            if m >= 60:
                h += 1
                m -= 60
            # rebuild datetime with zero seconds
            local_dt = local_dt.replace(hour=h % 24, minute=m, second=0)

        if local_dt:
            if rounding != "off":
                formatted[name] = local_dt.strftime("%H:%M")
            else:
                formatted[name] = local_dt.strftime("%H:%M:%S")
        else:
            formatted[name] = "N/A"

    return formatted, method_used, None


def convert_to_local_time(utc_dt, tz_name, tz_offset_hours):
    """
    Convert a UTC datetime to local time.
    - If tz_name is valid, use pytz; otherwise add offset hours.
    """
    if utc_dt is None:
        return None
    if tz_name:
        try:
            tz = pytz.timezone(tz_name)
            if utc_dt.tzinfo is None:
                utc_dt = utc_dt.replace(tzinfo=datetime.timezone.utc)
            return utc_dt.astimezone(tz)
        except (pytz.UnknownTimeZoneError, Exception):
            pass
    # Fallback to offset if timezone name is invalid or not provided
    return utc_dt + datetime.timedelta(hours=tz_offset_hours)


def closest_city_fallback(
    lat_dec, lon_dec, target_date, target_lat=45.0, lat_band=10.0
):
    """
    Fallback to the city near 45° latitude (default: 35°–55°) whose longitude is closest to the target location.
    Prints the chosen city and its coordinates.
    Returns (fajr, shurooq, dhuhr, asr, maghrib, isha) as UTC time strings (HH:MM:SS) or raises if none found.
    """
    loc_file = str(LOC_CSV_PATH)
    if not os.path.exists(loc_file):
        raise RuntimeError("locations.csv not found, cannot fallback to a city.")
    preferred = []
    all_cities = []
    with open(loc_file, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                city_lat = float(row.get("optimized_lat") or row.get("latitude") or "")
                city_lon = float(row.get("optimized_lon") or row.get("longitude") or "")
            except Exception:
                continue
            if city_lat is None or city_lon is None:
                continue
            # Skip if this is the same city (within 0.01 deg)
            if abs(city_lat - lat_dec) < 0.01 and abs(city_lon - lon_dec) < 0.01:
                continue
            # Prefer cities near target_lat latitude band
            if abs(city_lat - target_lat) <= lat_band:
                preferred.append((row, city_lat, city_lon))
            all_cities.append((row, city_lat, city_lon))

    # Try preferred cities first (within latitude band, closest longitude)
    city_list = []
    if preferred:
        city_list = [
            (row, city_lat, city_lon, abs(city_lon - lon_dec))
            for (row, city_lat, city_lon) in preferred
        ]
        city_list.sort(key=lambda x: x[3])
    else:
        # Fallback: globally closest to target_lat latitude, then closest longitude
        city_list = [
            (
                row,
                city_lat,
                city_lon,
                abs(city_lat - target_lat),
                abs(city_lon - lon_dec),
            )
            for (row, city_lat, city_lon) in all_cities
        ]
        city_list.sort(key=lambda x: (x[3], x[4]))
    for entry in city_list:
        if preferred:
            row, city_lat, city_lon, *_ = entry
        else:
            row, city_lat, city_lon, *_ = entry
        try:
            elevation = float(row.get("elevation") or 0)
            pressure = float(row.get("pressure") or STANDARD_PRESSURE)
            temp = float(row.get("temp") or STANDARD_TEMP)
            tz_name = row.get("timezone")
            tz_offset_hours = 0
            try:
                tz = pytz.timezone(tz_name)
                dt_obj = datetime.datetime(
                    target_date.year, target_date.month, target_date.day, 12, 0, 0
                )
                tz_offset_hours = tz.utcoffset(dt_obj).total_seconds() / 3600.0
            except Exception:
                tz_offset_hours = 0
            fajr_angle = float(row.get("fajr_angle") or 18)
            isha_angle = float(row.get("isha_angle") or 17)
            isha_minutes = (
                float(row.get("isha_minutes")) if row.get("isha_minutes") else None
            )
            asr_madhab = int(row.get("asr_madhab") or 0)
            fajr_offset = float(row.get("fajr_offset") or 0)
            shurooq_offset = float(row.get("shurooq_offset") or 0)
            dhuhr_offset = float(row.get("dhuhr_offset") or 0)
            asr_offset = float(row.get("asr_offset") or 0)
            maghrib_offset = float(row.get("maghrib_offset") or 0)
            isha_offset = float(row.get("isha_offset") or 0)
            times, _, _ = calculate_prayer_times(
                lat_dec=city_lat,
                lon_dec=city_lon,
                elevation=elevation,
                pressure=pressure,
                temp=temp,
                tz_name=tz_name,
                tz_offset_hours=tz_offset_hours,
                fajr_angle=fajr_angle,
                isha_angle=isha_angle,
                isha_minutes=isha_minutes,
                target_date=target_date,
                asr_madhab=asr_madhab,
                fajr_offset=fajr_offset,
                shurooq_offset=shurooq_offset,
                dhuhr_offset=dhuhr_offset,
                asr_offset=asr_offset,
                maghrib_offset=maghrib_offset,
                isha_offset=isha_offset,
                high_lat_method=0,  # angle_based = 0
                skip_fallback=True,
            )
            if times["shurooq"] != "N/A" and times["maghrib"] != "N/A":
                raw_times, _, _ = calculate_prayer_times(
                    lat_dec=city_lat,
                    lon_dec=city_lon,
                    elevation=elevation,
                    pressure=pressure,
                    temp=temp,
                    tz_name=None,
                    tz_offset_hours=0,
                    fajr_angle=fajr_angle,
                    isha_angle=isha_angle,
                    isha_minutes=isha_minutes,
                    target_date=target_date,
                    asr_madhab=asr_madhab,
                    fajr_offset=fajr_offset,
                    shurooq_offset=shurooq_offset,
                    dhuhr_offset=dhuhr_offset,
                    asr_offset=asr_offset,
                    maghrib_offset=maghrib_offset,
                    isha_offset=isha_offset,
                    high_lat_method=0,  # angle_based = 0
                    skip_fallback=True,
                )
                city_name = row.get("city") or row.get("name") or "Unknown"
                country = row.get("country") or ""
                print(
                    f"[Fallback] Using city: {city_name}, {country} (lat={city_lat}, lon={city_lon})"
                )
                return (
                    raw_times.get("fajr"),
                    raw_times.get("shurooq"),
                    raw_times.get("dhuhr"),
                    raw_times.get("asr"),
                    raw_times.get("maghrib"),
                    raw_times.get("isha"),
                )
        except Exception:
            continue
    raise RuntimeError("No fallback city found with valid sunrise/sunset events.")


def compute_adjusted(t_start, night_len, method, angle, prayer):
    """
    Return the adjusted UTC datetime for Fajr or Isha:
    - 0 = Angle Based:   fraction angle/60 of the night from sunset for Isha; from sunrise backwards for Fajr
    - 1 = One Seventh:   1/7th after sunset for Isha; 1/7th before sunrise for Fajr
    - 2 = Midnight:      midpoint of the night
    """

    # Angle Based method:
    if method == 0:
        # fraction = |angle| / 60
        frac = abs(angle) / 60.0
        if prayer == "isha":
            return t_start + night_len * frac
        else:  # fajr
            return t_start + night_len * (1.0 - frac)

    # One Seventh method:
    if method == 1:
        # Divide night into 7 parts
        part = night_len / 7
        if prayer == "isha":
            return t_start + part
        else:  # fajr
            return t_start + night_len - part

    # Midnight method:
    if method == 2:
        # Midpoint of the night
        return t_start + night_len / 2

    # Unknown method
    return None


def fallback_event(observer, sun, base_time, find_fn, nudge):
    """
    Generic: nudge = +timedelta for previous_setting, -timedelta for next_rising
    find_fn = observer.previous_setting or .next_rising
    """
    # 1) nudge the date
    observer.date = ephem.Date(base_time + nudge)
    observer.horizon = "0"
    try:
        evt = find_fn(sun, use_center=False)
    except ephem.AlwaysUpError:
        # sun still above horizon: nudge further or abort
        observer.date = ephem.Date(base_time + nudge * 2)
        evt = find_fn(sun, use_center=False)
    except ephem.NeverUpError:
        # sun never sets/rises: truly polar—return None
        return None
    return evt.datetime().replace(tzinfo=datetime.timezone.utc)


def handle_high_latitudes(raw, method, prayer, obs, sun, fajr_angle, isha_angle):
    # assume obs and sun are in outer scope
    if prayer == "isha":
        mag = raw.get("maghrib")
        if not mag:
            return None
        # try to find previous sunset after maghrib+5min
        t = fallback_event(
            obs, sun, mag, obs.previous_setting, datetime.timedelta(minutes=5)
        )
        if not t:
            return None
        # Need next_rising for night length
        try:
            night_end = (
                obs.next_rising(sun, use_center=False)
                .datetime()
                .replace(tzinfo=datetime.timezone.utc)
            )
        except:
            # If next_rising fails, try next_setting as a fallback for the end of the "night"
            try:
                night_end = (
                    obs.next_setting(sun, use_center=False)
                    .datetime()
                    .replace(tzinfo=datetime.timezone.utc)
                )
            except:
                return None  # Cannot determine night length

        if night_end <= t:  # Ensure night_end is after t
            # This can happen near the poles, where sunrise might be before sunset
            return None

        night_len = night_end - t
        # apply midnight/one_seventh/angle_based to t and night_end...
        return compute_adjusted(t, night_len, method, isha_angle, prayer)
    else:  # fajr
        sh = raw.get("shurooq")
        if not sh:
            return None
        # find next sunrise before shurooq-5min
        t = fallback_event(
            obs, sun, sh, obs.next_rising, datetime.timedelta(minutes=-5)
        )
        if not t:
            return None
        # Need previous_setting for night length
        try:
            prev_ss = (
                obs.previous_setting(sun, use_center=False)
                .datetime()
                .replace(tzinfo=datetime.timezone.utc)
            )
        except:
            # If previous_setting fails, try previous_rising as a fallback for the start of the "night"
            try:
                prev_ss = (
                    obs.previous_rising(sun, use_center=False)
                    .datetime()
                    .replace(tzinfo=datetime.timezone.utc)
                )
            except:
                return None  # Cannot determine night length

        if t <= prev_ss:  # Ensure t is after prev_ss
            # This can happen near the poles, where sunrise might be before sunset
            return None

        night_len = t - prev_ss
        return compute_adjusted(prev_ss, night_len, method, fajr_angle, prayer)
