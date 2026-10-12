"""Warranty timeline rules engine (TSD §15)."""

from datetime import date

from app.models.warranty import DurationUnit, StartCondition, TimelineStatus, WarrantyCoverage
from app.warranty.rules.timeline import compute_timeline


def cov(
    value: int | None = 12,
    unit: DurationUnit = DurationUnit.MONTH,
    start: StartCondition = StartCondition.PURCHASE_DATE,
    component: str = "FULL_UNIT",
) -> WarrantyCoverage:
    return WarrantyCoverage(
        policy_id=1,
        component=component,
        duration_value=value,
        duration_unit=unit,
        start_condition=start,
    )


PURCHASE = date(2026, 8, 31)


def test_purchase_anchored_months():
    r = compute_timeline(cov(12, DurationUnit.MONTH), PURCHASE, today=date(2026, 9, 1))
    assert r["start_date"] == PURCHASE
    assert r["expiry_date"] == date(2027, 8, 31)
    assert r["status"] == TimelineStatus.ACTIVE


def test_years_and_weeks_and_days():
    assert compute_timeline(cov(10, DurationUnit.YEAR), PURCHASE, today=date(2026, 9, 1))["expiry_date"] == date(2036, 8, 31)
    assert compute_timeline(cov(4, DurationUnit.WEEK), PURCHASE, today=date(2026, 9, 1))["expiry_date"] == date(2026, 9, 28)
    assert compute_timeline(cov(30, DurationUnit.DAY), PURCHASE, today=date(2026, 9, 1))["expiry_date"] == date(2026, 9, 30)


def test_unlimited_never_expires():
    r = compute_timeline(cov(None, DurationUnit.UNLIMITED), PURCHASE, today=date(2035, 1, 1))
    assert r["expiry_date"] is None
    assert r["status"] == TimelineStatus.ACTIVE


def test_not_started_when_start_in_future():
    r = compute_timeline(cov(12), date(2027, 1, 1), today=date(2026, 9, 1))
    assert r["status"] == TimelineStatus.NOT_STARTED
    assert r["expiry_date"] == date(2028, 1, 1)


def test_expiring_boundary_60_days():
    # 364-day warranty from 2025-01-01 expires exactly 2025-12-31.
    c = cov(364, DurationUnit.DAY)
    start = date(2025, 1, 1)
    assert compute_timeline(c, start, today=date(2025, 11, 2))["status"] == TimelineStatus.EXPIRING  # 59d left
    assert compute_timeline(c, start, today=date(2025, 11, 1))["status"] == TimelineStatus.EXPIRING  # 60d left
    assert compute_timeline(c, start, today=date(2025, 10, 31))["status"] == TimelineStatus.ACTIVE  # 61d left
    assert compute_timeline(c, start, today=date(2025, 12, 31))["status"] == TimelineStatus.EXPIRING  # 0d left
    assert compute_timeline(c, start, today=date(2026, 1, 1))["status"] == TimelineStatus.EXPIRED  # 1d past


def test_month_end_clamping():
    # 2026-01-31 + 1 month = 2026-02-28 (leap-clamped by relativedelta)
    assert compute_timeline(cov(1, DurationUnit.MONTH), date(2026, 1, 31), today=date(2026, 2, 1))["expiry_date"] == date(2026, 2, 28)
    # 2024 is a leap year: 2024-01-31 + 1 month = 2024-02-29
    assert compute_timeline(cov(1, DurationUnit.MONTH), date(2024, 1, 31), today=date(2024, 2, 1))["expiry_date"] == date(2024, 2, 29)


def test_expired_one_day_after():
    r = compute_timeline(cov(12), date(2024, 1, 1), today=date(2025, 1, 2))
    assert r["status"] == TimelineStatus.EXPIRED


def test_expiry_today_is_expiring_not_expired():
    r = compute_timeline(cov(12), date(2025, 8, 31), today=date(2026, 8, 31))
    assert r["expiry_date"] == date(2026, 8, 31)
    assert r["status"] == TimelineStatus.EXPIRING


def test_unknown_start_condition_is_unknown():
    for cond in (StartCondition.REGISTRATION_DATE, StartCondition.INSTALLATION_DATE, StartCondition.ACTIVATION_DATE, StartCondition.MANUFACTURE_DATE):
        r = compute_timeline(cov(12, DurationUnit.MONTH, start=cond), PURCHASE, today=date(2026, 9, 1))
        assert r["start_date"] is None
        assert r["expiry_date"] is None
        assert r["status"] == TimelineStatus.UNKNOWN, cond


def test_no_purchase_date_is_unknown():
    r = compute_timeline(cov(12), None, today=date(2026, 9, 1))
    assert r["status"] == TimelineStatus.UNKNOWN


# --- Anchor dates (installation / registration) -----------------------------


def test_installation_date_unknown_until_recorded():
    c = cov(12, DurationUnit.MONTH, start=StartCondition.INSTALLATION_DATE)
    # No installation date recorded -> honestly UNKNOWN (never guessed from purchase).
    r = compute_timeline(c, PURCHASE, today=date(2026, 9, 1))
    assert r["status"] == TimelineStatus.UNKNOWN
    assert r["start_date"] is None


def test_installation_date_start_when_recorded():
    c = cov(12, DurationUnit.MONTH, start=StartCondition.INSTALLATION_DATE)
    install = date(2026, 6, 1)
    r = compute_timeline(
        c,
        PURCHASE,
        today=date(2026, 9, 1),
        anchors={StartCondition.INSTALLATION_DATE: install},
    )
    assert r["start_date"] == install
    assert r["expiry_date"] == date(2027, 6, 1)
    assert r["status"] == TimelineStatus.ACTIVE
    # The purchase date is NOT used as a substitute for the installation date.
    assert r["start_date"] != PURCHASE


def test_registration_required_unknown_until_registered():
    c = WarrantyCoverage(
        policy_id=1,
        component="FULL_UNIT",
        duration_value=12,
        duration_unit=DurationUnit.MONTH,
        start_condition=StartCondition.PURCHASE_DATE,
        registration_required=True,
    )
    # Purchase date known but no registration recorded -> UNKNOWN.
    r = compute_timeline(c, PURCHASE, today=date(2026, 9, 1))
    assert r["status"] == TimelineStatus.UNKNOWN


def test_registration_required_starts_from_registration_date():
    c = WarrantyCoverage(
        policy_id=1,
        component="FULL_UNIT",
        duration_value=12,
        duration_unit=DurationUnit.MONTH,
        start_condition=StartCondition.PURCHASE_DATE,
        registration_required=True,
    )
    reg = date(2026, 7, 15)
    r = compute_timeline(
        c,
        PURCHASE,
        today=date(2026, 9, 1),
        anchors={StartCondition.REGISTRATION_DATE: reg},
    )
    assert r["start_date"] == reg
    assert r["expiry_date"] == date(2027, 7, 15)


def test_registration_date_start_condition_uses_registration_anchor():
    c = cov(24, DurationUnit.MONTH, start=StartCondition.REGISTRATION_DATE)
    reg = date(2026, 5, 10)
    r = compute_timeline(c, PURCHASE, today=date(2026, 9, 1), anchors={StartCondition.REGISTRATION_DATE: reg})
    assert r["start_date"] == reg
    assert r["expiry_date"] == date(2028, 5, 10)


# --- Component-specific warranties (parts/labor/compressor/battery/display/
# --- accessories/other) ------------------------------------------------------


import pytest  # noqa: E402


@pytest.mark.parametrize(
    ("component", "value", "unit", "expected_expiry"),
    [
        ("PARTS", 12, DurationUnit.MONTH, date(2027, 8, 31)),
        ("LABOR", 12, DurationUnit.MONTH, date(2027, 8, 31)),
        ("COMPRESSOR", 60, DurationUnit.MONTH, date(2031, 8, 31)),
        ("BATTERY", 12, DurationUnit.MONTH, date(2027, 8, 31)),
        ("DISPLAY", 12, DurationUnit.MONTH, date(2027, 8, 31)),
        ("ACCESSORIES", 6, DurationUnit.MONTH, date(2027, 2, 28)),
        ("OTHER_PANEL", 24, DurationUnit.MONTH, date(2028, 8, 31)),
    ],
)
def test_component_specific_warranties(component, value, unit, expected_expiry):
    r = compute_timeline(cov(value, unit, component=component), PURCHASE, today=date(2026, 9, 1))
    assert r["start_date"] == PURCHASE
    assert r["expiry_date"] == expected_expiry
    assert r["status"] == TimelineStatus.ACTIVE


# --- Never guess a warranty duration -----------------------------------------


def test_finite_duration_without_value_is_unknown():
    """A finite coverage with no recorded duration must be UNKNOWN — the
    expiry must never be guessed (e.g. as start + 0)."""
    c = cov(None, DurationUnit.MONTH)
    r = compute_timeline(c, PURCHASE, today=date(2026, 9, 1))
    assert r["status"] == TimelineStatus.UNKNOWN
    assert r["expiry_date"] is None
    # The start IS known (purchase date) — only the expiry is withheld.
    assert r["start_date"] == PURCHASE
    assert "duration" in (r["note"] or "").lower()


def test_zero_duration_is_unknown():
    c = WarrantyCoverage(
        policy_id=1,
        component="FULL_UNIT",
        duration_value=0,
        duration_unit=DurationUnit.MONTH,
        start_condition=StartCondition.PURCHASE_DATE,
    )
    r = compute_timeline(c, PURCHASE, today=date(2026, 9, 1))
    assert r["status"] == TimelineStatus.UNKNOWN
    assert r["expiry_date"] is None


# --- Extended warranty timelines ----------------------------------------------

from app.warranty.rules.timeline import compute_extended_timeline  # noqa: E402


def test_extended_purchase_anchored():
    r = compute_extended_timeline(
        start_condition=StartCondition.PURCHASE_DATE,
        duration_value=24,
        duration_unit=DurationUnit.MONTH,
        purchase_date=PURCHASE,
        today=date(2026, 9, 1),
    )
    assert r["start_date"] == PURCHASE
    assert r["expiry_date"] == date(2028, 8, 31)
    assert r["status"] == TimelineStatus.ACTIVE


def test_extended_installation_anchored_unknown_until_recorded():
    r = compute_extended_timeline(
        start_condition=StartCondition.INSTALLATION_DATE,
        duration_value=12,
        duration_unit=DurationUnit.MONTH,
        purchase_date=PURCHASE,
        today=date(2026, 9, 1),
    )
    assert r["status"] == TimelineStatus.UNKNOWN
    assert r["start_date"] is None
    assert r["expiry_date"] is None


def test_extended_installation_anchored_when_recorded():
    install = date(2026, 6, 1)
    r = compute_extended_timeline(
        start_condition=StartCondition.INSTALLATION_DATE,
        duration_value=12,
        duration_unit=DurationUnit.MONTH,
        purchase_date=PURCHASE,
        today=date(2026, 9, 1),
        anchors={StartCondition.INSTALLATION_DATE: install},
    )
    assert r["start_date"] == install
    assert r["expiry_date"] == date(2027, 6, 1)
    assert r["start_date"] != PURCHASE  # never substituted by the purchase date


def test_extended_unlimited_never_expires():
    r = compute_extended_timeline(
        start_condition=StartCondition.PURCHASE_DATE,
        duration_value=None,
        duration_unit=DurationUnit.UNLIMITED,
        purchase_date=PURCHASE,
        today=date(2035, 1, 1),
    )
    assert r["expiry_date"] is None
    assert r["status"] == TimelineStatus.ACTIVE


def test_extended_finite_without_duration_is_unknown():
    r = compute_extended_timeline(
        start_condition=StartCondition.PURCHASE_DATE,
        duration_value=None,
        duration_unit=DurationUnit.YEAR,
        purchase_date=PURCHASE,
        today=date(2026, 9, 1),
    )
    assert r["status"] == TimelineStatus.UNKNOWN
    assert r["expiry_date"] is None


def test_extended_not_started_when_anchor_in_future():
    r = compute_extended_timeline(
        start_condition=StartCondition.PURCHASE_DATE,
        duration_value=12,
        duration_unit=DurationUnit.MONTH,
        purchase_date=date(2027, 1, 1),
        today=date(2026, 9, 1),
    )
    assert r["status"] == TimelineStatus.NOT_STARTED
    assert r["expiry_date"] == date(2028, 1, 1)
