from datetime import timedelta, timezone

import pytest

from factories import T0
from store.keys import (
    code_key,
    item_key,
    scan_log_item_pk,
    scan_log_key,
    shop_key,
    staff_key,
    station_key,
    tenant_from_pk,
)


def test_shop_records_are_keyed_by_tenant() -> None:
    assert shop_key("cafe-01") == {"pk": "TENANT#cafe-01"}
    assert item_key("cafe-01", "lids_12oz") == {"pk": "TENANT#cafe-01", "sk": "ITEM#lids_12oz"}
    assert code_key("cafe-01", "INV-LID12-1BOX") == {
        "pk": "TENANT#cafe-01",
        "sk": "CODE#INV-LID12-1BOX",
    }
    assert staff_key("cafe-01", "STAFF-001") == {"pk": "TENANT#cafe-01", "sk": "STAFF#STAFF-001"}


def test_station_lookup_is_keyed_by_api_key_only() -> None:
    # This is how the API finds the tenant, so it can't need the tenant itself.
    assert station_key("abc123") == {"pk": "APIKEY#abc123"}


def test_scan_log_key_is_station_plus_utc_time() -> None:
    assert scan_log_key("cafe-01", "door-1", T0) == {
        "pk": "TENANT#cafe-01#STATION#door-1",
        "sk": "2026-10-17T17:00:00.000000+00:00",
    }


def test_resent_scan_gets_the_same_log_key_in_any_time_zone() -> None:
    same_moment = T0.astimezone(timezone(timedelta(hours=-7)))
    assert scan_log_key("cafe-01", "door-1", same_moment) == scan_log_key("cafe-01", "door-1", T0)


def test_by_item_index_key() -> None:
    assert scan_log_item_pk("cafe-01", "lids_12oz") == "TENANT#cafe-01#ITEM#lids_12oz"


def test_two_shops_never_share_a_key() -> None:
    assert item_key("cafe-01", "lids") != item_key("cafe-02", "lids")


@pytest.mark.parametrize("bad", ["", "cafe#01"])
def test_key_parts_must_be_non_empty_and_without_hash(bad: str) -> None:
    with pytest.raises(ValueError):
        item_key(bad, "lids")
    with pytest.raises(ValueError):
        item_key("cafe-01", bad)


def test_tenant_comes_back_out_of_a_key() -> None:
    assert tenant_from_pk("TENANT#cafe-01") == "cafe-01"
    assert tenant_from_pk("TENANT#cafe-01#STATION#door-1") == "cafe-01"
    with pytest.raises(ValueError):
        tenant_from_pk("APIKEY#abc")
