"""Unit tests: warranty region resolution (SRS FR-11a)."""

from app.models.product import Retailer
from app.models.purchase import RegionResolution
from app.models.user import User
from app.services.purchase_service import resolve_region


def _user(country):
    return User(email="u@x.com", password_hash="x", display_name="U", country=country)


def _retailer(country):
    return Retailer(name="R", name_key="r", country=country)


def test_priority_1_explicit_region_wins():
    region, method = resolve_region(explicit_region="de", retailer=_retailer("US"), user=_user("IN"))
    assert region == "DE"
    assert method == RegionResolution.USER_EXPLICIT


def test_priority_2_retailer_country():
    region, method = resolve_region(explicit_region=None, retailer=_retailer("us"), user=_user("IN"))
    assert region == "US"
    assert method == RegionResolution.RETAILER_COUNTRY


def test_priority_3_user_account_country():
    region, method = resolve_region(explicit_region=None, retailer=_retailer(None), user=_user("in"))
    assert region == "IN"
    assert method == RegionResolution.USER_ACCOUNT


def test_unresolved_when_nothing_available():
    region, method = resolve_region(explicit_region=None, retailer=_retailer(None), user=_user(None))
    assert region is None
    assert method == RegionResolution.UNRESOLVED


def test_retailer_none_falls_to_user():
    region, method = resolve_region(explicit_region=None, retailer=None, user=_user("FR"))
    assert region == "FR"
    assert method == RegionResolution.USER_ACCOUNT
