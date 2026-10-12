"""Warranty timeline computation (TSD §15).

Rules engine for coverage windows. The start date depends on the
manufacturer-specific start condition:

- PURCHASE_DATE — anchored to the receipt's purchase date.
- INSTALLATION_DATE — anchored to the user-recorded installation date;
  UNKNOWN until the user records one (never guessed from the purchase).
- REGISTRATION_DATE / registration_required — anchored to the
  user-recorded registration date; a coverage that requires product
  registration is UNKNOWN until a registration date exists, even when the
  nominal start condition is the purchase date.
- MANUFACTURE_DATE / ACTIVATION_DATE — not captured in the MVP; UNKNOWN.

When the required anchor is not known the timeline is honestly UNKNOWN
rather than guessed (SRS FR-11a spirit: never default silently).
"""

from datetime import date

from dateutil.relativedelta import relativedelta

from app.models.warranty import DurationUnit, StartCondition, TimelineStatus, WarrantyCoverage

# Expiry is flagged EXPIRING this many days ahead (SRS FR-15 default window
# is 60/30/7 for alerts; the timeline uses the outer 60-day band).
EXPIRING_WINDOW_DAYS = 60


def _add_duration(start: date, value: int | None, unit: DurationUnit) -> date | None:
    """Add a calendar duration. Returns None when the duration is not
    knowable — the caller must then report UNKNOWN, never a guessed date."""
    if value is None or value <= 0:
        return None
    if unit == DurationUnit.DAY:
        return start + relativedelta(days=value)
    if unit == DurationUnit.WEEK:
        return start + relativedelta(weeks=value)
    if unit == DurationUnit.MONTH:
        return start + relativedelta(months=value)
    if unit == DurationUnit.YEAR:
        return start + relativedelta(years=value)
    raise ValueError(f"unit {unit} has no finite duration")


def compute_timeline(
    coverage: WarrantyCoverage,
    purchase_date: date | None = None,
    today: date | None = None,
    *,
    anchors: dict[StartCondition, date | None] | None = None,
) -> dict:
    """Compute (start_date, expiry_date, status) for one coverage.

    ``anchors`` maps a StartCondition to the user-supplied anchor date
    (purchase / installation / registration). Callers that only know the
    purchase date may use the positional ``purchase_date`` argument.
    Returns a dict so callers can store or serialize directly.

    Honesty contract ("never guess a warranty duration"):
    - unknown anchor date           -> start None, status UNKNOWN
    - finite duration with no value -> expiry None, status UNKNOWN
    - UNLIMITED unit                -> expiry None (no expiry by definition),
                                       status reflects the start only
    """
    today = today or date.today()
    start = _start_date(coverage, purchase_date, anchors or {})
    if start is None:
        return {
            "start_date": None,
            "expiry_date": None,
            "status": TimelineStatus.UNKNOWN,
            "note": "start anchor date not recorded",
        }

    unlimited = coverage.duration_unit == DurationUnit.UNLIMITED
    if unlimited:
        expiry = None
        status = TimelineStatus.NOT_STARTED if today < start else TimelineStatus.ACTIVE  # in force, no expiry to watch
        return {
            "start_date": start,
            "expiry_date": None,
            "status": status,
            "note": "unlimited duration — no expiry date",
        }

    expiry = _add_duration(start, coverage.duration_value, coverage.duration_unit)
    if expiry is None:
        # A finite coverage whose duration is not recorded: the expiry
        # cannot be computed. Report UNKNOWN rather than a guessed date.
        return {
            "start_date": start,
            "expiry_date": None,
            "status": TimelineStatus.UNKNOWN,
            "note": "duration not recorded — expiry not computed",
        }

    if today < start:
        status = TimelineStatus.NOT_STARTED
    elif today > expiry:
        status = TimelineStatus.EXPIRED
    elif (expiry - today).days <= EXPIRING_WINDOW_DAYS:
        status = TimelineStatus.EXPIRING
    else:
        status = TimelineStatus.ACTIVE
    return {"start_date": start, "expiry_date": expiry, "status": status, "note": None}


def _start_from_condition(
    condition: StartCondition,
    anchors: dict[StartCondition, date | None],
    purchase_fallback: date | None = None,
) -> date | None:
    """Resolve the anchor date for a start condition.

    Honesty rules (no silent defaults): a condition whose anchor date is
    not recorded -> None (the caller reports UNKNOWN, never guesses).
    """
    if condition == StartCondition.PURCHASE_DATE:
        return anchors.get(StartCondition.PURCHASE_DATE, purchase_fallback)
    if condition == StartCondition.INSTALLATION_DATE:
        return anchors.get(StartCondition.INSTALLATION_DATE)
    if condition == StartCondition.REGISTRATION_DATE:
        return anchors.get(StartCondition.REGISTRATION_DATE)
    # MANUFACTURE_DATE / ACTIVATION_DATE are not captured in the MVP.
    return None


def _start_date(
    coverage: WarrantyCoverage,
    purchase_date: date | None,
    anchors: dict[StartCondition, date | None],
) -> date | None:
    """Anchor the coverage start to the condition the manufacturer defines."""
    if coverage.registration_required:
        # Registration is required: without a recorded registration date
        # the coverage clock has not demonstrably started.
        return anchors.get(StartCondition.REGISTRATION_DATE)
    return _start_from_condition(coverage.start_condition, anchors, purchase_fallback=purchase_date)


def compute_extended_timeline(
    *,
    start_condition: StartCondition,
    duration_value: int | None,
    duration_unit: DurationUnit,
    purchase_date: date | None = None,
    today: date | None = None,
    anchors: dict[StartCondition, date | None] | None = None,
) -> dict:
    """Compute (start_date, expiry_date, status) for an extended warranty.

    Same honesty contract as ``compute_timeline``: unknown anchor ->
    UNKNOWN; finite duration with no value -> UNKNOWN (never guess);
    UNLIMITED -> no expiry by definition.
    """
    today = today or date.today()
    anchors = anchors or {}
    start = _start_from_condition(start_condition, anchors, purchase_fallback=purchase_date)
    if start is None:
        return {
            "start_date": None,
            "expiry_date": None,
            "status": TimelineStatus.UNKNOWN,
            "note": "start anchor date not recorded",
        }

    if duration_unit == DurationUnit.UNLIMITED:
        status = TimelineStatus.NOT_STARTED if today < start else TimelineStatus.ACTIVE
        return {
            "start_date": start,
            "expiry_date": None,
            "status": status,
            "note": "unlimited duration — no expiry date",
        }

    expiry = _add_duration(start, duration_value, duration_unit)
    if expiry is None:
        return {
            "start_date": start,
            "expiry_date": None,
            "status": TimelineStatus.UNKNOWN,
            "note": "duration not recorded — expiry not computed",
        }

    if today < start:
        status = TimelineStatus.NOT_STARTED
    elif today > expiry:
        status = TimelineStatus.EXPIRED
    elif (expiry - today).days <= EXPIRING_WINDOW_DAYS:
        status = TimelineStatus.EXPIRING
    else:
        status = TimelineStatus.ACTIVE
    return {"start_date": start, "expiry_date": expiry, "status": status, "note": None}
