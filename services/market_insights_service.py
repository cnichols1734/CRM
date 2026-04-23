"""Market Insights service: cached, multi-ZIP RentCast `/markets` rollup.

Key responsibilities
--------------------
1. Read the seeded ServiceArea -> list of ZIPs.
2. For each ZIP, return the cached payload if it's fresh; otherwise
   atomically claim a refresh and either run it inline (cold cache)
   or in a background thread (stale-while-revalidate).
3. Roll the per-ZIP payloads up into the area-wide response shape the
   dashboard consumes, applying the requested time window (90d / 180d / 12m).

The atomic claim is the heart of the quota-protection story: the UPDATE's
WHERE clause guarantees that out of N concurrent stale requests, exactly
one wins and actually calls RentCast. The other N-1 read the (briefly stale)
cache and return immediately. The 2-minute guard on `refresh_started_at`
also prevents a crashed refresh from blocking future refreshes forever.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app
from sqlalchemy import text

from config import Config
from models import db, MarketDataCache, RentcastApiLog, ServiceArea
from services.rentcast_service import fetch_market_stats

logger = logging.getLogger(__name__)


WINDOW_TO_MONTHS = {'90d': 3, '180d': 6, '12m': 12}
DEFAULT_WINDOW = '12m'
CLAIM_LOCK_MINUTES = 2

# RentCast's `/markets` endpoint reports stats across *every* listing in a ZIP,
# including Land, Manufactured Homes, Multi-Family, and Commercial. Consumer-
# facing comps (Movoto, Redfin, Realtor.com, Homes.com) default to residential
# resale only, so pooling all property types systematically distorts our
# headline metrics in rural ZIPs that have heavy land inventory. We rebuild
# the headline numbers from the residential subset of `dataByPropertyType`
# to keep the dashboard apples-to-apples with what users see elsewhere.
RESIDENTIAL_PROPERTY_TYPES = frozenset({'Single Family', 'Townhouse', 'Condo'})


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_insights(area_slug: str, window: str = DEFAULT_WINDOW) -> Optional[Dict[str, Any]]:
    """Build the Market Insights response for an area.

    Returns ``None`` if the slug is unknown. Returns a dict with an
    ``error`` key when the area exists but no cache could be produced
    (caller should map that to HTTP 503).
    """
    if window not in WINDOW_TO_MONTHS:
        window = DEFAULT_WINDOW

    area = ServiceArea.query.filter_by(slug=area_slug).first()
    if area is None:
        return None

    zip_codes = list(area.zip_codes or [])
    payloads: List[Dict[str, Any]] = []
    any_stale = False
    last_error: Optional[str] = None

    for zip_code in zip_codes:
        payload, was_stale, err = _resolve_zip_payload(str(zip_code))
        if payload is not None:
            payloads.append(payload)
            any_stale = any_stale or was_stale
        elif err:
            last_error = err

    if not payloads:
        return {
            'area': area.display_name,
            'slug': area.slug,
            'zip_codes': zip_codes,
            'error': last_error or 'No market data available yet for this area.',
        }

    rollup = _build_rollup_response(area, payloads, window)
    rollup['is_stale'] = any_stale
    return rollup


def list_service_areas() -> List[Dict[str, Any]]:
    """Return all configured service areas, sorted for stable display."""
    rows = ServiceArea.query.order_by(ServiceArea.sort_order, ServiceArea.display_name).all()
    return [
        {'slug': r.slug, 'display_name': r.display_name, 'zip_codes': list(r.zip_codes or [])}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Per-ZIP refresh / cache logic
# ---------------------------------------------------------------------------

def _resolve_zip_payload(zip_code: str) -> Tuple[Optional[Dict[str, Any]], bool, Optional[str]]:
    """Return (payload, was_stale, error) for a ZIP.

    - Fresh cache hit: (payload, False, None) -- no API call, no thread.
    - Stale cache + payload exists: (payload, True, None) -- spawn background
      refresh and return the stale payload immediately.
    - Cold cache (no payload yet): block, try to win the claim, call RentCast.
      On failure: (None, False, error).
    """
    row = _get_or_create_cache_row(zip_code)
    refresh_hours = int(getattr(Config, 'MARKET_DATA_REFRESH_HOURS', 168))
    stale_threshold = datetime.utcnow() - timedelta(hours=refresh_hours)

    if row.refreshed_at is not None and row.refreshed_at >= stale_threshold:
        return row.payload, False, None

    if row.payload is not None:
        # Stale-while-revalidate: hand back the stale payload now,
        # kick off the refresh in a daemon thread.
        try:
            app = current_app._get_current_object()
            t = threading.Thread(
                target=_refresh_in_background,
                args=(app, zip_code),
                name=f'market-refresh-{zip_code}',
                daemon=True,
            )
            t.start()
        except Exception:
            logger.exception("Failed to spawn background refresh for zip=%s", zip_code)
        return row.payload, True, None

    # Cold cache -- have to block and refresh inline so the very first
    # ever load returns real data. Lost-claim path polls once and returns
    # whatever the winner wrote; if still empty, surfaces an error.
    if _try_claim_refresh(zip_code):
        ok, err = _perform_refresh(zip_code)
        if ok:
            row = MarketDataCache.query.get(zip_code)
            return (row.payload if row else None), False, None
        return None, False, err
    # Lost the claim: re-read the row.
    row = MarketDataCache.query.get(zip_code)
    if row and row.payload is not None:
        return row.payload, True, None
    return None, False, 'Market data is being fetched. Please refresh in a moment.'


def _get_or_create_cache_row(zip_code: str) -> MarketDataCache:
    """Idempotent INSERT then SELECT. Works on Postgres and SQLite."""
    row = MarketDataCache.query.get(zip_code)
    if row is not None:
        return row
    try:
        db.session.execute(
            text(
                "INSERT INTO market_data_cache (zip_code, updated_at) "
                "VALUES (:zip, CURRENT_TIMESTAMP) "
                "ON CONFLICT (zip_code) DO NOTHING"
            ),
            {'zip': zip_code},
        )
        db.session.commit()
    except Exception:
        # Some SQLite versions don't accept ON CONFLICT on the PK column
        # in this exact form; fall back to a plain INSERT and ignore the dupe.
        db.session.rollback()
        try:
            row = MarketDataCache(zip_code=zip_code)
            db.session.add(row)
            db.session.commit()
        except Exception:
            db.session.rollback()
    return MarketDataCache.query.get(zip_code)


def _try_claim_refresh(zip_code: str) -> bool:
    """Atomically claim the right to refresh this ZIP.

    Returns True if this caller won the claim and should call RentCast,
    False if another worker is already refreshing or just did. Thresholds
    are computed in Python so the SQL is portable across Postgres / SQLite.
    """
    refresh_hours = int(getattr(Config, 'MARKET_DATA_REFRESH_HOURS', 168))
    now = datetime.utcnow()
    stale_threshold = now - timedelta(hours=refresh_hours)
    claim_lock_threshold = now - timedelta(minutes=CLAIM_LOCK_MINUTES)

    result = db.session.execute(
        text(
            """
            UPDATE market_data_cache
            SET refresh_started_at = :now,
                updated_at = :now
            WHERE zip_code = :zip
              AND (refreshed_at IS NULL OR refreshed_at < :stale_threshold)
              AND (refresh_started_at IS NULL OR refresh_started_at < :claim_lock_threshold)
            """
        ),
        {
            'zip': zip_code,
            'now': now,
            'stale_threshold': stale_threshold,
            'claim_lock_threshold': claim_lock_threshold,
        },
    )
    db.session.commit()
    return (result.rowcount or 0) > 0


def _perform_refresh(zip_code: str) -> Tuple[bool, Optional[str]]:
    """Call RentCast for one ZIP and persist the outcome + audit row."""
    result = fetch_market_stats(zip_code)

    log = RentcastApiLog(
        zip_code=zip_code,
        endpoint='/markets',
        status_code=result.get('status_code'),
        latency_ms=result.get('latency_ms'),
        error=None if result.get('success') else (result.get('error') or 'unknown error'),
    )
    db.session.add(log)

    row = MarketDataCache.query.get(zip_code)
    if row is None:
        row = MarketDataCache(zip_code=zip_code)
        db.session.add(row)

    if result.get('success'):
        row.payload = result.get('data')
        row.refreshed_at = datetime.utcnow()
        row.refresh_started_at = None
        row.last_error = None
        db.session.commit()
        return True, None

    # Failure: clear the claim so the next visitor can retry, but DO NOT
    # touch refreshed_at -- the cache is still considered stale and we
    # surface the error.
    row.refresh_started_at = None
    row.last_error = result.get('error') or 'unknown error'
    db.session.commit()
    return False, row.last_error


def _refresh_in_background(app, zip_code: str) -> None:
    """Run :func:`_perform_refresh` in a worker thread with its own context."""
    with app.app_context():
        try:
            if _try_claim_refresh(zip_code):
                _perform_refresh(zip_code)
            # If we lost the claim, another thread/request is on it.
        except Exception:
            logger.exception("Background market refresh failed zip=%s", zip_code)
            try:
                db.session.rollback()
            except Exception:
                pass
        finally:
            try:
                db.session.remove()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Rollup + window slicing
# ---------------------------------------------------------------------------

def _build_rollup_response(area: ServiceArea, payloads: List[Dict[str, Any]], window: str) -> Dict[str, Any]:
    months = WINDOW_TO_MONTHS[window]

    # Filter sale data to residential property types (Single Family, Townhouse,
    # Condo) before any aggregation -- both for the snapshot and every history
    # entry. Rental data + the property-type breakdown stay on the original
    # payloads so users can still inspect the full mix if they want.
    residential_payloads = [_residentialize_payload(p) for p in payloads]

    # Build the unified history (sale + rental + inventory per month) first;
    # headline metrics come from the most recent month so they are always
    # the same regardless of window.
    sale_history = _merged_history(residential_payloads, 'saleData')
    rental_history = _merged_history(payloads, 'rentalData')

    full_history = _zip_histories(sale_history, rental_history)
    sliced_history = full_history[-months:] if full_history else []

    latest = sliced_history[-1] if sliced_history else None
    earliest = sliced_history[0] if sliced_history else None

    median_price = latest['median_price'] if latest else None
    if median_price is None:
        # Fall back to current snapshot fields on saleData (residential subset)
        median_price = _weighted_median(residential_payloads, 'saleData', 'medianPrice', 'totalListings')
    median_dom = latest['median_dom'] if latest else None
    if median_dom is None:
        median_dom = _weighted_avg(residential_payloads, 'saleData', 'medianDaysOnMarket', 'totalListings')
    total_listings = latest['total_listings'] if latest else None
    if total_listings is None:
        total_listings = _sum_field(residential_payloads, 'saleData', 'totalListings')
    new_listings = latest['new_listings'] if latest else None
    if new_listings is None:
        new_listings = _sum_field(residential_payloads, 'saleData', 'newListings')
    median_ppsf = latest['median_ppsf'] if latest else None
    if median_ppsf is None:
        median_ppsf = _weighted_avg(residential_payloads, 'saleData', 'medianPricePerSquareFoot', 'totalListings')
    median_rent = latest['median_rent'] if latest else None
    if median_rent is None:
        median_rent = _weighted_median(payloads, 'rentalData', 'medianRent', 'totalListings')

    price_min = _min_field(residential_payloads, 'saleData', 'minPrice')
    price_max = _max_field(residential_payloads, 'saleData', 'maxPrice')

    yield_pct = None
    if median_rent and median_price and median_price > 0:
        yield_pct = round((median_rent * 12.0) / median_price * 100.0, 2)

    response = {
        'area': area.display_name,
        'slug': area.slug,
        'zip_codes': [str(z) for z in (area.zip_codes or [])],
        'window': window,
        'median_home_price': _delta_pct(median_price, earliest['median_price'] if earliest else None, window),
        'median_price_per_sqft': _delta_pct(median_ppsf, earliest['median_ppsf'] if earliest else None, window),
        'active_inventory': _delta_pct(total_listings, earliest['total_listings'] if earliest else None, window),
        'new_listings': _delta_pct(new_listings, earliest['new_listings'] if earliest else None, window),
        'days_on_market': _delta_days(median_dom, earliest['median_dom'] if earliest else None, window),
        'median_rent': _delta_pct(median_rent, earliest['median_rent'] if earliest else None, window),
        'gross_rental_yield_pct': yield_pct,
        'price_range': {'min': price_min, 'max': price_max},
        'history': sliced_history,
        'history_full': full_history,  # kept so the client can swap windows without a refetch
        'by_property_type': _breakdown(payloads, 'saleData', 'dataByPropertyType', 'propertyType'),
        'by_bedrooms': _breakdown(payloads, 'saleData', 'dataByBedrooms', 'bedrooms'),
        'as_of': _latest_iso(payloads),
        'source': 'RentCast',
        'methodology': {
            'scope': 'Residential resale: Single Family, Townhouse, Condo. '
                     'Land, Manufactured, Multi-Family, and Commercial are excluded.',
            'price': 'Median ASKING price of currently active sale listings (not closed sales).',
            'days_on_market': 'Median DOM of currently active listings (not sold-DOM).',
            'inventory': 'Total listings active at any point in the month (cumulative, not point-in-time).',
            'source': 'RentCast /markets endpoint, refreshed weekly.',
        },
    }
    return response


def _merged_history(payloads: List[Dict[str, Any]], section: str) -> Dict[str, Dict[str, Any]]:
    """Combine the per-ZIP `<section>.history` objects into a single map keyed by `YYYY-MM`.

    Each merged month aggregates across ZIPs using the same rules as the
    snapshot rollup: weighted median for prices, sum for inventory counts,
    listing-count-weighted average for DOM.
    """
    months: Dict[str, List[Dict[str, Any]]] = {}
    for payload in payloads:
        section_data = (payload or {}).get(section) or {}
        history = section_data.get('history') or {}
        for month_key, entry in history.items():
            if not entry:
                continue
            months.setdefault(month_key, []).append(entry)

    merged: Dict[str, Dict[str, Any]] = {}
    for month_key, entries in months.items():
        weights = [_safe_int(e.get('totalListings')) or 0 for e in entries]
        sum_w = sum(weights) or 0

        if section == 'saleData':
            merged[month_key] = {
                'median_price': _weighted_median_values(
                    [e.get('medianPrice') for e in entries], weights
                ),
                'median_ppsf': _weighted_avg_values(
                    [e.get('medianPricePerSquareFoot') for e in entries], weights
                ),
                'median_dom': _weighted_avg_values(
                    [e.get('medianDaysOnMarket') for e in entries], weights
                ),
                'total_listings': sum_w if sum_w else None,
                'new_listings': sum(
                    (_safe_int(e.get('newListings')) or 0) for e in entries
                ) or None,
            }
        else:  # rentalData
            merged[month_key] = {
                'median_rent': _weighted_median_values(
                    [e.get('medianRent') for e in entries], weights
                ),
            }
    return merged


def _zip_histories(
    sale_history: Dict[str, Dict[str, Any]],
    rental_history: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Produce a chronologically sorted list of merged monthly entries."""
    months = sorted(set(sale_history.keys()) | set(rental_history.keys()))
    out: List[Dict[str, Any]] = []
    for m in months:
        s = sale_history.get(m, {})
        r = rental_history.get(m, {})
        # Skip months with no sale data at all -- the chart would be misleading.
        if s.get('median_price') is None and r.get('median_rent') is None:
            continue
        out.append({
            'month': m,
            'median_price': s.get('median_price'),
            'median_ppsf': s.get('median_ppsf'),
            'median_dom': s.get('median_dom'),
            'total_listings': s.get('total_listings'),
            'new_listings': s.get('new_listings'),
            'median_rent': r.get('median_rent'),
        })
    return out


# ---------------------------------------------------------------------------
# Residential filter (drop Land / Manufactured / Multi-Family / Commercial)
# ---------------------------------------------------------------------------

def _residential_view(section_entry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a *new* dict with top-level stat fields recomputed from the
    residential subset of ``dataByPropertyType``.

    Works on either a snapshot ``saleData`` blob or a single
    ``saleData.history[YYYY-MM]`` entry, since both expose the same shape.
    If ``dataByPropertyType`` is absent or contains no residential rows,
    the original entry is returned unchanged (callers may legitimately
    pass synthesized payloads without the breakdown, and we don't want
    to discard their numbers).
    """
    if not section_entry:
        return section_entry
    pt_rows = section_entry.get('dataByPropertyType') or []
    res_rows = [
        r for r in pt_rows
        if r.get('propertyType') in RESIDENTIAL_PROPERTY_TYPES
    ]
    if not res_rows:
        return section_entry

    weights = [_safe_int(r.get('totalListings')) or 0 for r in res_rows]
    total_inv = sum(weights)
    new_inv = sum((_safe_int(r.get('newListings')) or 0) for r in res_rows)

    mins = [v for v in (_safe_float(r.get('minPrice')) for r in res_rows) if v is not None]
    maxs = [v for v in (_safe_float(r.get('maxPrice')) for r in res_rows) if v is not None]

    out = dict(section_entry)
    out['medianPrice'] = _weighted_avg_values(
        [r.get('medianPrice') for r in res_rows], weights
    )
    out['medianPricePerSquareFoot'] = _weighted_avg_values(
        [r.get('medianPricePerSquareFoot') for r in res_rows], weights
    )
    out['medianDaysOnMarket'] = _weighted_avg_values(
        [r.get('medianDaysOnMarket') for r in res_rows], weights
    )
    out['totalListings'] = total_inv if total_inv else None
    out['newListings'] = new_inv if new_inv else None
    out['minPrice'] = min(mins) if mins else None
    out['maxPrice'] = max(maxs) if maxs else None
    return out


def _residentialize_payload(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a payload-shaped dict where ``saleData`` (and every entry of
    ``saleData.history``) has been filtered to residential property types.

    The original payload is not mutated. ``rentalData`` is passed through
    untouched.
    """
    if not payload:
        return payload
    sd = payload.get('saleData')
    if not sd:
        return payload

    new_sd = dict(_residential_view(sd) or {})

    history = sd.get('history') or {}
    if history:
        new_history = {}
        for month_key, entry in history.items():
            new_history[month_key] = _residential_view(entry)
        new_sd['history'] = new_history

    new_payload = dict(payload)
    new_payload['saleData'] = new_sd
    return new_payload


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _safe_int(v) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _safe_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _weighted_median_values(values, weights) -> Optional[float]:
    """Plain median of provided values (treats weights as a tie-breaker only).

    For v1 we keep median-of-medians for prices since RentCast doesn't expose
    the underlying distribution; weights are still consulted to drop ZIPs
    with zero listings (which would otherwise inject noise).
    """
    pairs = [(_safe_float(v), w) for v, w in zip(values, weights)]
    cleaned = [v for v, w in pairs if v is not None and (w or 0) > 0]
    if not cleaned:
        cleaned = [v for v, _ in pairs if v is not None]
    if not cleaned:
        return None
    return float(median(cleaned))


def _weighted_avg_values(values, weights) -> Optional[float]:
    """Listing-count-weighted average. Falls back to plain mean if no weights."""
    pairs = [(_safe_float(v), _safe_int(w) or 0) for v, w in zip(values, weights)]
    pairs = [(v, w) for v, w in pairs if v is not None]
    if not pairs:
        return None
    total_weight = sum(w for _, w in pairs)
    if total_weight <= 0:
        return sum(v for v, _ in pairs) / len(pairs)
    return sum(v * w for v, w in pairs) / total_weight


def _weighted_median(payloads, section, field, weight_field) -> Optional[float]:
    section_objs = [(p or {}).get(section) or {} for p in payloads]
    return _weighted_median_values(
        [s.get(field) for s in section_objs],
        [s.get(weight_field) for s in section_objs],
    )


def _weighted_avg(payloads, section, field, weight_field) -> Optional[float]:
    section_objs = [(p or {}).get(section) or {} for p in payloads]
    return _weighted_avg_values(
        [s.get(field) for s in section_objs],
        [s.get(weight_field) for s in section_objs],
    )


def _sum_field(payloads, section, field) -> Optional[int]:
    total = 0
    seen = False
    for p in payloads:
        v = _safe_int(((p or {}).get(section) or {}).get(field))
        if v is not None:
            total += v
            seen = True
    return total if seen else None


def _min_field(payloads, section, field) -> Optional[float]:
    vals = [
        _safe_float(((p or {}).get(section) or {}).get(field))
        for p in payloads
    ]
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


def _max_field(payloads, section, field) -> Optional[float]:
    vals = [
        _safe_float(((p or {}).get(section) or {}).get(field))
        for p in payloads
    ]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def _latest_iso(payloads) -> Optional[str]:
    dates = []
    for p in payloads:
        for section in ('saleData', 'rentalData'):
            d = ((p or {}).get(section) or {}).get('lastUpdatedDate')
            if d:
                dates.append(d)
    return max(dates) if dates else None


def _delta_pct(current, previous, window: str) -> Dict[str, Any]:
    out = {'value': _round_for_display(current), 'change_pct': None, 'change_vs': window}
    if current is not None and previous not in (None, 0):
        try:
            pct = ((float(current) - float(previous)) / float(previous)) * 100.0
            out['change_pct'] = round(pct, 1)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return out


def _delta_days(current, previous, window: str) -> Dict[str, Any]:
    out = {'value': _round_for_display(current), 'change_days': None, 'change_vs': window}
    if current is not None and previous is not None:
        try:
            out['change_days'] = int(round(float(current) - float(previous)))
        except (TypeError, ValueError):
            pass
    return out


def _round_for_display(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if abs(f - round(f)) < 1e-6:
        return int(round(f))
    return round(f, 2)


def _breakdown(payloads, section, group_field, key_field) -> List[Dict[str, Any]]:
    """Aggregate the dataByPropertyType / dataByBedrooms arrays across ZIPs.

    Buckets are keyed by ``key_field`` (string for property type, number for
    bedrooms). Within each bucket we use the same weighted rules as the
    headline rollup.
    """
    buckets: Dict[Any, List[Dict[str, Any]]] = {}
    for payload in payloads:
        section_obj = (payload or {}).get(section) or {}
        for entry in section_obj.get(group_field) or []:
            key = entry.get(key_field)
            if key is None:
                continue
            buckets.setdefault(key, []).append(entry)

    out: List[Dict[str, Any]] = []
    for key, entries in buckets.items():
        weights = [_safe_int(e.get('totalListings')) or 0 for e in entries]
        out.append({
            key_field: key,
            'median_price': _weighted_median_values(
                [e.get('medianPrice') for e in entries], weights
            ),
            'median_price_per_sqft': _weighted_avg_values(
                [e.get('medianPricePerSquareFoot') for e in entries], weights
            ),
            'median_dom': _weighted_avg_values(
                [e.get('medianDaysOnMarket') for e in entries], weights
            ),
            'total_listings': sum(weights) or None,
        })

    if key_field == 'bedrooms':
        out.sort(key=lambda r: (r.get('bedrooms') is None, r.get('bedrooms')))
    else:
        out.sort(key=lambda r: -(r.get('total_listings') or 0))
    return out
