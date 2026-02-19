import datetime

from src.app.presentation.gui.clock import get_clock_offset_for_date


def test_clock_offset_is_applied_with_inverse_sign_for_runtime():
    payload = '[{"start":"04-24","end":"04-24","offset":60},{"start":"10-30","end":"10-30","offset":-60}]'

    assert get_clock_offset_for_date(payload, datetime.date(2026, 4, 24)) == -60
    assert get_clock_offset_for_date(payload, datetime.date(2026, 10, 30)) == 60
    assert get_clock_offset_for_date(payload, datetime.date(2026, 5, 1)) == 0
