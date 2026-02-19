from src.app.infrastructure.prayer_calculator import (
    calculate_prayer_times as native_calculate_prayer_times,
)

from .models import PrayerCalculationRequest, PrayerCalculationResult


def calculate_prayer_times(
    request: PrayerCalculationRequest,
) -> PrayerCalculationResult:
    times, method_used, error = native_calculate_prayer_times(
        **request.to_calculator_kwargs()
    )
    return PrayerCalculationResult(times=times, method_used=method_used, error=error)
