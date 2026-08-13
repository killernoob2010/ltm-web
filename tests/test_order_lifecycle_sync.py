from datetime import datetime
from zoneinfo import ZoneInfo

from backend.app.order_lifecycle_sync import due_email_slots, due_wps_slots


SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_wps_slots_are_workday_hours_9_to_18():
    slots = due_wps_slots(datetime(2026, 8, 13, 18, 30, tzinfo=SHANGHAI))
    assert len(slots) == 10
    assert due_wps_slots(datetime(2026, 8, 15, 18, 30, tzinfo=SHANGHAI)) == []


def test_email_slots_are_monday_9_to_11():
    assert len(due_email_slots(datetime(2026, 8, 17, 11, 30, tzinfo=SHANGHAI))) == 3
    assert due_email_slots(datetime(2026, 8, 18, 11, 30, tzinfo=SHANGHAI)) == []
