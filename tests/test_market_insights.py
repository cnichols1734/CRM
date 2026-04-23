"""Tests for the Market Insights API + caching/SWR layer.

Covers:
- auth required on both endpoints
- service area listing
- 404 on unknown slug
- fresh cache hit does NOT call RentCast
- stale cache returns immediately and spawns a background refresh
- atomic claim: among concurrent stale callers, only one wins
- cold cache + RentCast failure surfaces 503
- window slicing (90d/180d/12m) with deltas computed against window start
- multi-ZIP rollup uses sum on inventory and weighted-avg DOM
- the rentcast_api_log audit row is written on every call
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from models import db, MarketDataCache, RentcastApiLog, ServiceArea


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def reset_market_tables(app):
    """Wipe market-insight tables before each test for a clean slate."""
    with app.app_context():
        db.session.query(RentcastApiLog).delete()
        db.session.query(MarketDataCache).delete()
        db.session.query(ServiceArea).delete()
        db.session.commit()
    yield
    with app.app_context():
        db.session.query(RentcastApiLog).delete()
        db.session.query(MarketDataCache).delete()
        db.session.query(ServiceArea).delete()
        db.session.commit()


@pytest.fixture
def seeded_areas(app, reset_market_tables):
    with app.app_context():
        db.session.add_all([
            ServiceArea(slug='mont-belvieu', display_name='Mont Belvieu',
                        zip_codes=['77523'], sort_order=0),
            ServiceArea(slug='baytown', display_name='Baytown',
                        zip_codes=['77521', '77520'], sort_order=1),
        ])
        db.session.commit()
    yield


def _months_back(anchor: datetime, n: int) -> datetime:
    """Return the first of the month n months before ``anchor`` (anchor itself for n=0)."""
    year, month = anchor.year, anchor.month
    total = year * 12 + (month - 1) - n
    return datetime(total // 12, (total % 12) + 1, 1)


def _make_payload(zip_code: str, *, base_price=400_000, base_inventory=300,
                  base_dom=30, months: int = 12, base_rent=1500):
    """Build a fake RentCast /markets response with monthly history."""
    today = datetime.utcnow().replace(day=1)
    history = {}
    rental_history = {}
    for i in range(months):
        m = _months_back(today, i)
        key = f"{m.year:04d}-{m.month:02d}"
        history[key] = {
            'date': m.strftime('%Y-%m-01T00:00:00.000Z'),
            'medianPrice': base_price - i * 1000,
            'medianPricePerSquareFoot': 180 - i * 0.5,
            'medianDaysOnMarket': base_dom + i,
            'totalListings': base_inventory - i * 5,
            'newListings': 30 - i,
        }
        rental_history[key] = {
            'date': m.strftime('%Y-%m-01T00:00:00.000Z'),
            'medianRent': base_rent - i * 5,
        }
    return {
        'id': zip_code,
        'zipCode': zip_code,
        'saleData': {
            'lastUpdatedDate': today.strftime('%Y-%m-%dT00:00:00.000Z'),
            'medianPrice': base_price,
            'medianPricePerSquareFoot': 180,
            'medianDaysOnMarket': base_dom,
            'totalListings': base_inventory,
            'newListings': 30,
            'minPrice': 100_000, 'maxPrice': 1_500_000,
            'history': history,
            'dataByPropertyType': [
                {'propertyType': 'Single Family', 'medianPrice': base_price + 10_000,
                 'medianPricePerSquareFoot': 185, 'medianDaysOnMarket': base_dom - 2,
                 'totalListings': int(base_inventory * 0.8)},
                {'propertyType': 'Condo', 'medianPrice': base_price - 80_000,
                 'medianPricePerSquareFoot': 160, 'medianDaysOnMarket': base_dom + 5,
                 'totalListings': int(base_inventory * 0.2)},
            ],
            'dataByBedrooms': [
                {'bedrooms': 3, 'medianPrice': base_price, 'medianPricePerSquareFoot': 180,
                 'medianDaysOnMarket': base_dom, 'totalListings': int(base_inventory * 0.6)},
                {'bedrooms': 4, 'medianPrice': base_price + 50_000, 'medianPricePerSquareFoot': 178,
                 'medianDaysOnMarket': base_dom + 4, 'totalListings': int(base_inventory * 0.4)},
            ],
        },
        'rentalData': {
            'lastUpdatedDate': today.strftime('%Y-%m-%dT00:00:00.000Z'),
            'medianRent': base_rent,
            'history': rental_history,
        },
    }


def _good_result(zip_code, **kwargs):
    return {
        'success': True, 'data': _make_payload(zip_code, **kwargs),
        'status_code': 200, 'latency_ms': 12, 'error': None,
    }


def _bad_result():
    return {
        'success': False, 'data': None, 'status_code': 429,
        'latency_ms': 8, 'error': 'rate limited',
    }


# ---------------------------------------------------------------------------
# Auth + basic routing
# ---------------------------------------------------------------------------

class TestAuth:
    def test_areas_requires_auth(self, client):
        resp = client.get('/api/market-insights/areas')
        assert resp.status_code in (302, 401)

    def test_insights_requires_auth(self, client):
        resp = client.get('/api/market-insights/mont-belvieu')
        assert resp.status_code in (302, 401)


class TestAreasEndpoint:
    def test_lists_seeded_areas(self, owner_a_client, seeded_areas):
        resp = owner_a_client.get('/api/market-insights/areas')
        assert resp.status_code == 200
        body = resp.get_json()
        slugs = [a['slug'] for a in body['areas']]
        assert slugs == ['mont-belvieu', 'baytown']
        assert body['areas'][1]['zip_codes'] == ['77521', '77520']


class TestUnknownSlug:
    def test_404_for_unknown_area(self, owner_a_client, seeded_areas):
        resp = owner_a_client.get('/api/market-insights/no-such-place')
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cache freshness behavior
# ---------------------------------------------------------------------------

class TestCacheBehavior:
    def test_fresh_cache_does_not_call_rentcast(self, app, owner_a_client, seeded_areas):
        with app.app_context():
            db.session.add(MarketDataCache(
                zip_code='77523',
                payload=_make_payload('77523'),
                refreshed_at=datetime.utcnow(),  # fresh
            ))
            db.session.commit()

        with patch('services.market_insights_service.fetch_market_stats') as mock_fetch:
            resp = owner_a_client.get('/api/market-insights/mont-belvieu')
            assert resp.status_code == 200
            mock_fetch.assert_not_called()

        body = resp.get_json()
        assert body['area'] == 'Mont Belvieu'
        assert body['median_home_price']['value'] == 400_000
        assert body['is_stale'] is False

    def test_stale_cache_returns_immediately_and_spawns_refresh(
        self, app, owner_a_client, seeded_areas
    ):
        with app.app_context():
            db.session.add(MarketDataCache(
                zip_code='77523',
                payload=_make_payload('77523'),
                refreshed_at=datetime.utcnow() - timedelta(days=14),  # stale
            ))
            db.session.commit()

        spawned = []

        def fake_thread(*args, **kwargs):
            spawned.append((args, kwargs))
            class _T:
                def start(self_inner): pass
            return _T()

        # Patch threading.Thread inside the service module so we can verify
        # a background refresh was kicked off without actually doing it.
        with patch('services.market_insights_service.threading.Thread', side_effect=fake_thread), \
             patch('services.market_insights_service.fetch_market_stats') as mock_fetch:
            resp = owner_a_client.get('/api/market-insights/mont-belvieu')
            assert resp.status_code == 200
            # Inline call must NOT happen -- only the spawned thread would.
            mock_fetch.assert_not_called()

        body = resp.get_json()
        assert body['is_stale'] is True
        assert len(spawned) == 1, "Expected exactly one background refresh thread"

    def test_first_load_no_cache_returns_503_on_rentcast_error(
        self, app, owner_a_client, seeded_areas
    ):
        # Cache is cold for 77523. RentCast fails. Expect 503.
        with patch('services.market_insights_service.fetch_market_stats',
                   return_value=_bad_result()):
            resp = owner_a_client.get('/api/market-insights/mont-belvieu')
        assert resp.status_code == 503
        body = resp.get_json()
        assert 'error' in body

        with app.app_context():
            row = db.session.get(MarketDataCache, '77523')
            assert row is not None
            assert row.payload is None
            assert row.last_error == 'rate limited'
            assert row.refresh_started_at is None
            log = RentcastApiLog.query.filter_by(zip_code='77523').first()
            assert log is not None
            assert log.status_code == 429

    def test_first_load_no_cache_success_writes_payload_and_log(
        self, app, owner_a_client, seeded_areas
    ):
        with patch('services.market_insights_service.fetch_market_stats',
                   return_value=_good_result('77523')):
            resp = owner_a_client.get('/api/market-insights/mont-belvieu')
        assert resp.status_code == 200

        with app.app_context():
            row = db.session.get(MarketDataCache, '77523')
            assert row is not None
            assert row.payload is not None
            assert row.refreshed_at is not None
            assert row.last_error is None
            assert RentcastApiLog.query.filter_by(zip_code='77523').count() == 1


class TestAtomicClaim:
    def test_only_one_concurrent_caller_wins_the_claim(self, app, seeded_areas):
        """Two stale-but-cached callers should produce exactly one RentCast call.

        We exercise the claim helper directly to avoid threading-related flakes;
        the SQL is the source of truth for this guarantee.
        """
        from services.market_insights_service import _try_claim_refresh

        with app.app_context():
            db.session.add(MarketDataCache(
                zip_code='77523', payload=None,
                refreshed_at=datetime.utcnow() - timedelta(days=14),
            ))
            db.session.commit()

            first = _try_claim_refresh('77523')
            second = _try_claim_refresh('77523')
            assert first is True
            assert second is False, \
                "Second caller must lose the claim while another refresh is in flight"


# ---------------------------------------------------------------------------
# Window slicing + delta computation
# ---------------------------------------------------------------------------

class TestWindowAndDeltas:
    def _seed_fresh(self, app, base_price=400_000):
        with app.app_context():
            db.session.add(MarketDataCache(
                zip_code='77523',
                payload=_make_payload('77523', base_price=base_price, months=12),
                refreshed_at=datetime.utcnow(),
            ))
            db.session.commit()

    def test_window_slices_history(self, app, owner_a_client, seeded_areas):
        self._seed_fresh(app)

        for window, expected_count in (('90d', 3), ('180d', 6), ('12m', 12)):
            resp = owner_a_client.get(
                f'/api/market-insights/mont-belvieu?window={window}'
            )
            assert resp.status_code == 200
            body = resp.get_json()
            assert len(body['history']) == expected_count
            # change_vs label echoes the requested window
            assert body['median_home_price']['change_vs'] == window

    def test_delta_compares_latest_to_first_in_window(self, app, owner_a_client, seeded_areas):
        # Built-in payload: medianPrice for month i (0=current) is 400k - i*1000
        # so 12m delta = (400_000 - 389_000)/389_000 * 100 ~= 2.83%
        self._seed_fresh(app)
        resp = owner_a_client.get('/api/market-insights/mont-belvieu?window=12m')
        body = resp.get_json()
        assert body['median_home_price']['value'] == 400_000
        assert body['median_home_price']['change_pct'] == pytest.approx(2.8, abs=0.2)

        # 90d delta compares to 3 months back (i=2): 400k vs 398k → ~0.5%
        resp_90 = owner_a_client.get('/api/market-insights/mont-belvieu?window=90d')
        body_90 = resp_90.get_json()
        assert body_90['median_home_price']['change_pct'] == pytest.approx(0.5, abs=0.2)

    def test_invalid_window_falls_back_to_default(self, app, owner_a_client, seeded_areas):
        self._seed_fresh(app)
        resp = owner_a_client.get('/api/market-insights/mont-belvieu?window=garbage')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['window'] == '12m'


# ---------------------------------------------------------------------------
# Multi-zip rollup
# ---------------------------------------------------------------------------

class TestMultiZipRollup:
    def test_inventory_summed_and_dom_weighted(self, app, owner_a_client, seeded_areas):
        # Baytown has 77521 + 77520. Give them different inventory and DOM
        # values so we can verify sum (inventory) and weighted average (DOM).
        with app.app_context():
            db.session.add_all([
                MarketDataCache(
                    zip_code='77521',
                    payload=_make_payload('77521', base_price=350_000,
                                          base_inventory=400, base_dom=20),
                    refreshed_at=datetime.utcnow(),
                ),
                MarketDataCache(
                    zip_code='77520',
                    payload=_make_payload('77520', base_price=300_000,
                                          base_inventory=100, base_dom=60),
                    refreshed_at=datetime.utcnow(),
                ),
            ])
            db.session.commit()

        resp = owner_a_client.get('/api/market-insights/baytown')
        assert resp.status_code == 200
        body = resp.get_json()

        # Inventory: 400 + 100 = 500
        assert body['active_inventory']['value'] == 500

        # Weighted DOM: (20*400 + 60*100) / 500 = (8000+6000)/500 = 28
        assert body['days_on_market']['value'] == 28

        # Median of medians for price: median(350k, 300k) = 325k
        assert body['median_home_price']['value'] == 325_000

        # Both ZIPs reflected in the area metadata
        assert body['zip_codes'] == ['77521', '77520']

    def test_breakdowns_are_returned(self, app, owner_a_client, seeded_areas):
        with app.app_context():
            db.session.add(MarketDataCache(
                zip_code='77523',
                payload=_make_payload('77523'),
                refreshed_at=datetime.utcnow(),
            ))
            db.session.commit()

        resp = owner_a_client.get('/api/market-insights/mont-belvieu')
        body = resp.get_json()
        assert any(r['propertyType'] == 'Single Family' for r in body['by_property_type'])
        assert any(r['bedrooms'] == 3 for r in body['by_bedrooms'])

    def test_residential_filter_excludes_land_and_manufactured(
        self, app, owner_a_client, seeded_areas
    ):
        """Headline metrics must be rebuilt from Single Family + Townhouse + Condo
        only, dropping Land / Manufactured / Multi-Family / Commercial which
        skew RentCast's all-property aggregates in rural ZIPs."""
        today = datetime.utcnow().replace(day=1)
        # Build a payload that LOOKS like the all-properties top-level totals
        # are being polluted by land + manufactured. Top-level claims median
        # $300k, 1000 listings, 80 DOM. The residential breakdown shows the
        # truth: Single Family is 800 listings @ $400k, 30 DOM.
        polluted_payload = {
            'id': '77523', 'zipCode': '77523',
            'saleData': {
                'lastUpdatedDate': today.strftime('%Y-%m-%dT00:00:00.000Z'),
                'medianPrice': 300_000,
                'medianPricePerSquareFoot': 130,
                'medianDaysOnMarket': 80,
                'totalListings': 1000,
                'newListings': 200,
                'minPrice': 50_000, 'maxPrice': 5_000_000,
                'dataByPropertyType': [
                    {'propertyType': 'Land', 'medianPrice': 80_000,
                     'medianPricePerSquareFoot': None, 'medianDaysOnMarket': 200,
                     'totalListings': 150, 'newListings': 10,
                     'minPrice': 5_000, 'maxPrice': 500_000},
                    {'propertyType': 'Manufactured', 'medianPrice': 90_000,
                     'medianPricePerSquareFoot': 70, 'medianDaysOnMarket': 180,
                     'totalListings': 50, 'newListings': 2,
                     'minPrice': 30_000, 'maxPrice': 200_000},
                    {'propertyType': 'Single Family', 'medianPrice': 400_000,
                     'medianPricePerSquareFoot': 175, 'medianDaysOnMarket': 30,
                     'totalListings': 800, 'newListings': 188,
                     'minPrice': 100_000, 'maxPrice': 5_000_000},
                ],
                'dataByBedrooms': [],
                'history': {},  # empty history so headline falls back to snapshot
            },
            'rentalData': None,
        }
        with app.app_context():
            db.session.add(MarketDataCache(
                zip_code='77523', payload=polluted_payload,
                refreshed_at=datetime.utcnow(),
            ))
            db.session.commit()

        resp = owner_a_client.get('/api/market-insights/mont-belvieu')
        assert resp.status_code == 200
        body = resp.get_json()

        # Inventory: residential only = 800 (NOT 1000 from raw saleData)
        assert body['active_inventory']['value'] == 800

        # New listings: residential only = 188 (NOT 200)
        assert body['new_listings']['value'] == 188

        # Median price: weighted across residential only ≈ Single Family $400k
        # (the only residential row), NOT the polluted $300k.
        assert body['median_home_price']['value'] == 400_000

        # DOM: 30 (Single Family), NOT 80 (polluted with land + manufactured)
        assert body['days_on_market']['value'] == 30

        # The full property-type breakdown is preserved on the response so
        # users can still inspect Land / Manufactured if they want to.
        types = {r['propertyType'] for r in body['by_property_type']}
        assert {'Single Family', 'Land', 'Manufactured'} <= types

        # Methodology block is exposed for the UI to show as a footer/tooltip.
        assert 'methodology' in body
        assert 'Single Family' in body['methodology']['scope']

    def test_gross_rental_yield_computed(self, app, owner_a_client, seeded_areas):
        with app.app_context():
            db.session.add(MarketDataCache(
                zip_code='77523',
                payload=_make_payload('77523', base_price=400_000, base_rent=2000),
                refreshed_at=datetime.utcnow(),
            ))
            db.session.commit()

        resp = owner_a_client.get('/api/market-insights/mont-belvieu')
        body = resp.get_json()
        # 2000 * 12 / 400_000 = 0.06 -> 6.00%
        assert body['gross_rental_yield_pct'] == pytest.approx(6.0, abs=0.01)
