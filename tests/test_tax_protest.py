"""
Regression tests for the tax protest stats flow.
"""

from types import SimpleNamespace

import feature_flags as feature_flags_module
import routes.tax_protest as tax_protest_route
import services.tax_protest_service as tax_protest_service


class _QueryStub:
    def __init__(self, values):
        self._rows = [(value,) for value in values]

    def filter(self, *args, **kwargs):
        return self

    def with_entities(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


def test_get_subdivision_stats_returns_list_distribution(monkeypatch):
    values = [390000, 420000, 450000, 610000, 615000, 700000, 820000]
    monkeypatch.setattr(
        tax_protest_service.LibertyProperty,
        "query",
        _QueryStub(values),
    )
    monkeypatch.setattr(
        tax_protest_service,
        "_find_liberty_sibling_codes",
        lambda subdivision_code: [],
    )

    stats = tax_protest_service.get_subdivision_stats(
        subdivision="LIBERTY OAKS",
        zip_code="77327",
        market_value=590000,
        source="liberty",
        subdivision_code="007206",
    )

    assert stats["total_homes"] == len(values)
    assert stats["min_value"] == min(values)
    assert stats["max_value"] == max(values)
    assert isinstance(stats["value_distribution"], list)
    assert sum(bucket["count"] for bucket in stats["value_distribution"]) == len(values)


def test_search_property_returns_subdivision_stats(owner_a_client, monkeypatch):
    monkeypatch.setattr(feature_flags_module, "org_has_feature", lambda *args, **kwargs: True)

    fake_contact = SimpleNamespace(
        id=999,
        street_address="156 Maryville Lane",
        city="Cleveland",
        zip_code="77327",
    )
    fake_property = {
        "id": "liberty-1",
        "address": "156 Maryville Lane",
        "full_address": "156 Maryville Lane, Cleveland, TX 77327",
        "city": "Cleveland",
        "zip": "77327",
        "market_value": 591000,
        "sq_ft": 2184,
        "acreage": 0.23,
        "subdivision": "MARYVILLE",
        "subdivision_code": "007206",
        "neighborhood_code": None,
    }
    fake_stats = {
        "total_homes": 21,
        "lower_values": 2,
        "higher_values": 18,
        "percentile": 9.5,
        "value_distribution": [
            {"label": "$362k", "count": 7},
            {"label": "$462k", "count": 7},
            {"label": "$562k", "count": 2},
            {"label": "$662k", "count": 1},
            {"label": "$762k", "count": 0},
            {"label": "$862k", "count": 2},
            {"label": "$962k", "count": 0},
            {"label": "$1062k", "count": 2},
        ],
        "min_value": 362000,
        "max_value": 1162000,
        "median_value": 505000,
    }

    monkeypatch.setattr(
        tax_protest_route,
        "_authorized_contact",
        lambda contact_id: fake_contact,
    )
    monkeypatch.setattr(
        tax_protest_route,
        "find_property_in_tax_data",
        lambda street_address, city, zip_code: (fake_property, "liberty"),
    )
    monkeypatch.setattr(
        tax_protest_route,
        "find_comparables",
        lambda *args, **kwargs: [{"id": "comp-1", "market_value": 420000}],
    )
    monkeypatch.setattr(
        tax_protest_route,
        "cache_search_result",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        tax_protest_route,
        "get_subdivision_stats",
        lambda *args, **kwargs: fake_stats,
    )

    response = owner_a_client.post(
        "/tax-protest/search",
        json={"contact_id": fake_contact.id},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["subdivision"] == "MARYVILLE"
    assert payload["main_property"]["market_value"] == 591000
    assert payload["subdivision_stats"] == fake_stats
