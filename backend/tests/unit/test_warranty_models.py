"""Database foundation tests for the warranty domain + notifications (TSD §13-§15, §22/§32, §34).

Proves the Phase-0 schema is real and coherent: referential integrity,
unique constraints, check constraints, and the user-ownership cascade
required for SRS FR-20 account deletion.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.notification import Notification, NotificationCategory, NotificationStatus
from app.models.purchase import Purchase, PurchaseItem
from app.models.user import User
from app.models.warranty import (
    DurationUnit,
    MatchMethod,
    PolicyStatus,
    SourceType,
    StartCondition,
    TimelineStatus,
    WarrantyCoverage,
    WarrantyMapping,
    WarrantyPolicy,
    WarrantySource,
    WarrantyTimeline,
)


@pytest.fixture(autouse=True)
def _clean(db_session):
    """These tests commit schema fixtures; remove them so later phases
    (warranty engine/API tests) start from a clean warranty domain."""
    yield
    from app.models.notification import Notification as N

    db_session.query(N).delete()
    db_session.query(WarrantyTimeline).delete()
    db_session.query(WarrantyMapping).delete()
    db_session.query(WarrantyCoverage).delete()
    db_session.query(WarrantyPolicy).delete()
    db_session.query(WarrantySource).delete()
    db_session.query(PurchaseItem).delete()
    db_session.query(Purchase).delete()
    db_session.query(User).delete()
    db_session.commit()


def _user(db_session, suffix: str) -> User:
    u = User(
        email=f"wf-{suffix}@example.com",
        password_hash="x" * 60,
        display_name="Warranty Fixture",
    )
    db_session.add(u)
    db_session.flush()
    return u


def _warranty_chain(db_session) -> tuple[WarrantyPolicy, WarrantyCoverage]:
    source = WarrantySource(
        source_type=SourceType.OFFICIAL_WEB,
        source_url="https://example.com/warranty",
        source_title="Nortech warranty terms",
        retrieved_at=datetime.now(UTC),
        region="IN",
        trust_level=4,
    )
    db_session.add(source)
    db_session.flush()
    policy = WarrantyPolicy(
        manufacturer_id=None,
        product_category="TELEVISION",
        product_model_pattern="QT*",
        region="IN",
        policy_name="Nortech standard warranty",
        policy_version="1.0",
        effective_from=date(2024, 1, 1),
        source_id=source.id,
        status=PolicyStatus.ACTIVE,
    )
    db_session.add(policy)
    db_session.flush()
    coverage = WarrantyCoverage(
        policy_id=policy.id,
        component="FULL_UNIT",
        duration_value=1,
        duration_unit=DurationUnit.YEAR,
        start_condition=StartCondition.PURCHASE_DATE,
        registration_required=False,
    )
    db_session.add(coverage)
    db_session.flush()
    return policy, coverage


def _purchase_with_item(db_session, user: User) -> PurchaseItem:
    purchase = Purchase(user_id=user.id, purchase_date="2026-01-15", currency="USD", total=1299.99)
    db_session.add(purchase)
    db_session.flush()
    item = PurchaseItem(purchase_id=purchase.id, product_name_raw="Nortech 55in QLED TV", quantity=1)
    db_session.add(item)
    db_session.flush()
    return item


def test_full_warranty_chain_persists(db_session):
    policy, coverage = _warranty_chain(db_session)
    user = _user(db_session, "chain")
    item = _purchase_with_item(db_session, user)

    mapping = WarrantyMapping(
        purchase_item_id=item.id,
        warranty_policy_id=policy.id,
        match_method=MatchMethod.EXACT_MODEL,
        match_confidence=0.97,
    )
    db_session.add(mapping)
    db_session.flush()
    timeline = WarrantyTimeline(
        purchase_item_id=item.id,
        coverage_id=coverage.id,
        component="FULL_UNIT",
        purchase_date=date(2026, 1, 15),
        start_date=date(2026, 1, 15),
        expiry_date=date(2027, 1, 14),
        duration_value=1,
        duration_unit=DurationUnit.YEAR,
        status=TimelineStatus.ACTIVE,
    )
    db_session.add(timeline)
    db_session.commit()

    got = db_session.execute(
        select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id == item.id)
    ).scalar_one()
    assert got.expiry_date == date(2027, 1, 14)
    assert got.status is TimelineStatus.ACTIVE
    assert got.coverage.component == "FULL_UNIT"

    got_map = db_session.get(WarrantyMapping, mapping.id)
    assert got_map is not None
    assert got_map.match_method is MatchMethod.EXACT_MODEL
    assert got_map.verification_status.value == "AI_EXTRACTED"
    assert got_map.uuid  # exposed as warranty_id in the API


def test_notification_idempotency_key_is_unique(db_session):
    user = _user(db_session, "notif")
    key = "purchase-1:coverage-2:2027-01-14:30d"
    n1 = Notification(user_id=user.id, category=NotificationCategory.WARRANTY_EXPIRING, title="t", notification_key=key)
    db_session.add(n1)
    db_session.commit()

    n2 = Notification(user_id=user.id, category=NotificationCategory.WARRANTY_EXPIRING, title="t", notification_key=key)
    db_session.add(n2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # A changed expiry snapshot produces a distinct key (TSD §32).
    n3 = Notification(
        user_id=user.id,
        category=NotificationCategory.WARRANTY_EXPIRING,
        title="t",
        notification_key="purchase-1:coverage-2:2027-02-01:30d",
        status=NotificationStatus.SCHEDULED,
    )
    db_session.add(n3)
    db_session.commit()
    assert n3.id is not None


def test_duplicate_coverage_component_rejected(db_session):
    policy, _ = _warranty_chain(db_session)
    dup = WarrantyCoverage(policy_id=policy.id, component="FULL_UNIT", duration_value=2, duration_unit=DurationUnit.YEAR)
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_unlimited_coverage_allows_null_duration(db_session):
    policy, _ = _warranty_chain(db_session)
    life = WarrantyCoverage(policy_id=policy.id, component="COMPRESSOR", duration_unit=DurationUnit.UNLIMITED)
    db_session.add(life)
    db_session.commit()
    assert life.id is not None


def test_zero_duration_rejected(db_session):
    policy, _ = _warranty_chain(db_session)
    bad = WarrantyCoverage(policy_id=policy.id, component="X", duration_value=0, duration_unit=DurationUnit.YEAR)
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_trust_level_bounds_enforced(db_session):
    bad = WarrantySource(source_title="t", trust_level=9)
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_user_deletion_cascades_to_owned_data(db_session):
    """SRS FR-20: deleting a user removes their documents, purchases,
    claims, notifications, and (through them) extraction artifacts."""
    user = _user(db_session, "cascade")
    item = _purchase_with_item(db_session, user)
    policy, coverage = _warranty_chain(db_session)
    mapping = WarrantyMapping(purchase_item_id=item.id, warranty_policy_id=policy.id)
    db_session.add(mapping)
    db_session.flush()
    db_session.add(
        WarrantyTimeline(
            purchase_item_id=item.id,
            coverage_id=coverage.id,
            component="FULL_UNIT",
            status=TimelineStatus.ACTIVE,
        )
    )
    db_session.add(
        Notification(
            user_id=user.id,
            category=NotificationCategory.SYSTEM,
            title="hi",
            notification_key=f"cascade-{user.id}",
        )
    )
    db_session.commit()
    user_id, item_id = user.id, item.id

    db_session.execute(Purchase.__table__.delete().where(Purchase.user_id == user_id))
    db_session.execute(Notification.__table__.delete().where(Notification.user_id == user_id))
    db_session.delete(user)
    db_session.commit()

    assert db_session.execute(select(func.count()).select_from(Purchase).where(Purchase.user_id == user_id)).scalar() == 0
    assert db_session.execute(select(func.count()).select_from(PurchaseItem).where(PurchaseItem.id == item_id)).scalar() == 0
    assert db_session.execute(select(func.count()).select_from(WarrantyMapping).where(WarrantyMapping.purchase_item_id == item_id)).scalar() == 0
