"""Shared fixtures for the context tests.

The academic calendar lives on raw.githubusercontent.com. From this machine one
file took 21-64 s, and a full load can exceed the request budget: whichever test
ran first paid minutes for it, or timed out and left nothing cached so the next
one paid again. That made the offline suite slow and flaky (2026-05-01 was even
reported as "no holiday" when the 2026 fetch failed and the 2025 calendar
answered instead — see test_previous_year_calendar_does_not_answer_for_a_spring_date).

Every test in this directory asserts *our* logic on top of the calendar, so it is
pinned here from in-memory payloads. The real fetch is covered separately by
src/test/context/test_context.py::test_holiday_name_comes_from_the_real_calendar,
which is marked live.
"""
from __future__ import annotations

import pytest

from sustech_survival import context as context_module
from sustech_survival.calendar import AcademicCalendar


def _calendar(year: int) -> AcademicCalendar:
    """A calendar for ``year``, built from payloads — no network.

    Mirrors the shape of src/test/calendar/test_calendar.py's fixtures, with the
    holiday names the real 校历 payload carries (Chinese), because that is what
    the holiday tests assert is passed through unchanged. Spring/fall dates match
    the real 2026 calendar (teaching starts 2026-02-25 and 2026-09-07), so week
    numbering agrees with the assertions in test_context_courses.py; summer has no
    teaching_start, so the loader skips it exactly as it does for 2026 (mid-July
    is between semesters).
    """
    general = {
        "holidays": [
            {"name": "劳动节", "start": f"{year}-05-01", "end": f"{year}-05-05"},
            {"name": "国庆节", "start": f"{year}-10-01", "end": f"{year}-10-07"},
        ],
    }
    semesters = {
        "spring_semester": {
            "start": f"{year}-02-23", "end": f"{year}-06-30",
            "sign_in": f"{year}-02-24", "teaching_start": f"{year}-02-25",
            "total_teaching_weeks": 17,
            "midterm": {"start": f"{year}-04-13", "end": f"{year}-04-26",
                        "equivalent_weeks": [8, 9]},
            "final": {"start": f"{year}-06-08", "end": f"{year}-06-18",
                      "equivalent_weeks": [16, 17]},
        },
        "summer_semester": {"start": f"{year}-06-29", "end": f"{year}-08-07"},
        "fall_semester": {
            "start": f"{year}-09-01", "end": f"{year + 1}-01-11",
            "sign_in": f"{year}-09-04", "teaching_start": f"{year}-09-07",
            "total_teaching_weeks": 18,
            "midterm": {"start": f"{year}-10-26", "end": f"{year}-11-08",
                        "equivalent_weeks": [8, 9]},
            "final": {"start": f"{year}-12-28", "end": f"{year + 1}-01-08",
                      "equivalent_weeks": [17, 18]},
        },
    }
    return AcademicCalendar.from_payloads(
        year=year, level="undergraduate",
        undergraduate=semesters, graduate=semesters, general=general,
    )


@pytest.fixture
def make_calendar():
    """Factory for payload-built calendars — ``make_calendar(2025)``."""
    return _calendar


@pytest.fixture(autouse=True)
def pinned_2026_calendar():
    """Pin the 2026 calendar into the process cache for every test in this dir."""
    saved = dict(context_module._CALENDAR_CACHE)
    context_module._CALENDAR_CACHE.clear()
    context_module._CALENDAR_CACHE[2026] = _calendar(2026)
    try:
        yield context_module._CALENDAR_CACHE[2026]
    finally:
        context_module._CALENDAR_CACHE.clear()
        context_module._CALENDAR_CACHE.update(saved)
