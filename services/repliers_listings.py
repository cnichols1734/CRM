"""Public sandbox listing projection. Repliers credentials stay on the server."""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import math
import re
import threading
import time
from urllib.parse import quote

import requests
from flask import current_app

_cache = OrderedDict()
_lock = threading.Lock()
PAGE_SIZE = 24
ID_PATTERN = re.compile(r'rp-(\d{1,10})-([A-Za-z0-9_-]{1,60})\Z')


class ListingError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


def valid_id(value):
    return isinstance(value, str) and len(value) <= 80 and bool(ID_PATTERN.fullmatch(value))


def _request(path, params):
    key = current_app.config.get('REPLIERS_API_KEY')
    if not key:
        raise ListingError('Home search is being connected. Please try again shortly.')
    cache_key = (hashlib.sha256(key.encode()).hexdigest(), path, repr(sorted(params.items())))
    with _lock:
        cached = _cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            _cache.move_to_end(cache_key)
            return deepcopy(cached[1])
    try:
        response = requests.get('https://api.repliers.io' + path,
            headers={'REPLIERS-API-KEY': key, 'Accept': 'application/json'},
            params=params, timeout=(3, 12), allow_redirects=False)
        if response.status_code == 404:
            raise ListingError('This home is no longer available.', 404)
        if response.status_code != 200:
            raise ListingError('Home search is temporarily unavailable. Please try again.')
        payload = response.json()
        if not isinstance(payload, dict) or payload.get('unrecognizedParams'):
            raise ListingError('Home search could not load these results. Please try again.')
    except (requests.RequestException, ValueError):
        raise ListingError('Home search could not connect. Please try again.') from None
    with _lock:
        _cache[cache_key] = (time.monotonic() + 120, payload)
        _cache.move_to_end(cache_key)
        while len(_cache) > 128:
            _cache.popitem(last=False)
    return deepcopy(payload)


def _number(value):
    try:
        number = float(str(value).replace(',', ''))
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def _facts(pairs):
    return [{'label': label, 'value': str(value)} for label, value in pairs
            if value is not None and str(value).strip()]


def _money(value):
    number = _number(value)
    return f'${number:,.0f}' if number is not None else None


def normalize(record):
    if not isinstance(record, dict):
        return None
    permissions = record.get('permissions') or {}
    if any(permissions.get(key) != 'Y' for key in
           ('displayPublic', 'displayInternetEntireListing', 'displayAddressOnInternet')):
        return None
    details, address = record.get('details') or {}, record.get('address') or {}
    image_paths = [p for p in (record.get('images') or []) if isinstance(p, str) and p.startswith('sample/') and '..' not in p]
    # A key upgraded to a licensed feed must not silently turn this sandbox into IDX.
    if not image_paths and 'SAMPLE DATA' not in str(details.get('description', '')).upper():
        return None
    board, mls = record.get('boardId'), record.get('mlsNumber')
    listing_id = f'rp-{board}-{mls}'
    if not valid_id(listing_id) or _number(record.get('listPrice')) is None:
        return None
    street = ' '.join(str(address[k]) for k in ('streetNumber', 'streetDirectionPrefix', 'streetName', 'streetSuffix', 'streetDirection') if address.get(k))
    if address.get('unitNumber'):
        street += f" #{address['unitNumber']}"
    style = str(details.get('style') or '')
    category = str(details.get('propertyType') or '')
    kind = ('Condo' if 'condo' in (style + category).lower() else
            'Townhouse' if 'town' in (style + category).lower() else
            'Land' if 'land' in category.lower() else
            'House' if 'residential' in category.lower() or 'single' in category.lower() else 'Other')
    lot, taxes, condo = record.get('lot') or {}, record.get('taxes') or {}, record.get('condominium') or {}
    sqft = _number(details.get('sqft'))
    price = _number(record.get('listPrice'))
    agents = record.get('agents') or []
    point = record.get('map') or {}
    try:
        latitude, longitude = float(point['latitude']), float(point['longitude'])
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or permissions.get('displayOnMap') != 'Y':
            latitude = longitude = None
    except (KeyError, ValueError, TypeError):
        latitude = longitude = None
    rooms = []
    for index, room in enumerate(record.get('rooms') or []):
        if not isinstance(room, dict):
            continue
        value = ' · '.join(str(room[k]) for k in ('level', 'length', 'width', 'description') if room.get(k))
        if value:
            rooms.append((f"{room.get('type') or 'Room'} {index + 1}", value))
    return dict(id=listing_id, source='repliers_sandbox', mls_number=str(mls),
        price=int(price), street=street or 'Address not provided', city=address.get('city') or '',
        state=address.get('state') or '', zip=address.get('zip') or '',
        beds=int(_number(details.get('numBedrooms')) or 0), baths=_number(details.get('numBathrooms')) or 0,
        sqft=int(sqft or 0), year_built=int(_number(details.get('yearBuilt')) or 0),
        acres=_number(lot.get('acres')), property_type=kind,
        photo_names=['https://cdn.repliers.io/' + quote(path, safe='/') for path in image_paths],
        listing_agent=agents[0].get('name', '') if agents else '',
        listing_office=(record.get('office') or {}).get('brokerageName') or '',
        remarks=str(details.get('description') or 'No description provided.'),
        map_latitude=latitude, map_longitude=longitude,
        overview=_facts([('Property type', category), ('Style', style), ('Year built', details.get('yearBuilt')),
            ('Living area', f'{sqft:,.0f} sq ft' if sqft else None),
            ('Price per sq ft', _money(price / sqft) if sqft else None),
            ('Lot size', f"{lot['acres']} acres" if lot.get('acres') else None),
            ('Neighborhood', address.get('neighborhood')), ('Parking spaces', details.get('numParkingSpaces')),
            ('Garage', details.get('garage')), ('Listing ID', mls)]),
        rooms=_facts(rooms),
        interior=_facts([('Appliances', details.get('extras')), ('Flooring', details.get('flooringType')),
            ('Fireplaces', details.get('numFireplaces')), ('Basement', details.get('basement1')),
            ('Laundry', details.get('laundryLevel')), ('Furnished', details.get('furnished'))]),
        exterior=_facts([('Construction', details.get('exteriorConstruction1')), ('Roof', details.get('roofMaterial')),
            ('Foundation', details.get('foundationType')), ('Pool', details.get('swimmingPool')),
            ('View', details.get('viewType')), ('Lot features', lot.get('features')), ('Patio', details.get('patio'))]),
        utilities=_facts([('Heating', details.get('heating')), ('Cooling', details.get('airConditioning')),
            ('Water', details.get('waterSource')), ('Sewer', details.get('sewer'))]),
        costs=_facts([('Asking price', _money(price)), ('Annual property tax', _money(taxes.get('annualAmount'))),
            ('Assessment year', taxes.get('assessmentYear')), ('HOA fee', _money(details.get('HOAFee'))),
            ('Maintenance fee', _money((condo.get('fees') or {}).get('maintenance')))]),
        history=_facts([('Listed', str(record['listDate'])[:10] if record.get('listDate') else None),
            ('Days on market', record.get('daysOnMarket')), ('Original price', _money(record.get('originalPrice')))]))


def search(args):
    params = dict(status='A', type='sale', resultsPerPage=PAGE_SIZE, sortBy='updatedOnDesc',
                  hasImages='true', displayPublic='Y', displayInternetEntireListing='Y',
                  displayAddressOnInternet='Y', state='TX', aggregates='address.city')
    for key, provider, minimum, maximum in [('page', 'pageNum', 1, 2000),
            ('max_price', 'maxPrice', 1, 100000000), ('min_beds', 'minBeds', 0, 20),
            ('min_baths', 'minBaths', 0, 20)]:
        value = args.get(key)
        if value is not None:
            try:
                value = int(value)
                if not minimum <= value <= maximum:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ListingError('Choose valid home search filters.', 400)
            params[provider] = value
    city = args.get('city', '').strip()
    if len(city) > 100:
        raise ListingError('Choose a valid city.', 400)
    if city:
        params['city'] = city
    property_type = args.get('property_type')
    types = {'House': 'Single Family Residence', 'Townhouse': 'Townhouse', 'Condo': 'Condominium', 'Land': 'Land'}
    if property_type:
        if property_type not in types:
            raise ListingError('Choose a valid property type.', 400)
        params['propertyTypeOrStyle'] = types[property_type]
    data = _request('/listings', params)
    listings = [item for raw in data.get('listings', []) if (item := normalize(raw)) is not None]
    cities = (((data.get('aggregates') or {}).get('address') or {}).get('city') or {})
    return dict(listings=listings, page=data.get('page', 1), total_pages=data.get('numPages', 1),
                total=data.get('count', len(listings)), cities=sorted(cities), source='repliers_sandbox')


def get_listing(listing_id):
    if not valid_id(listing_id):
        raise ListingError('This listing was not found.', 404)
    board, mls = ID_PATTERN.fullmatch(listing_id).groups()
    data = _request('/listings/' + quote(mls, safe=''), {'boardId': board})
    listing = normalize(data)
    if listing is None or listing['id'] != listing_id:
        raise ListingError('This listing is not available in the sandbox.', 404)
    return listing


def get_many(ids):
    if not ids or len(ids) > 40 or any(not valid_id(value) for value in ids):
        raise ListingError('Choose up to 40 valid homes.', 400)
    groups = {}
    for value in ids:
        board, mls = ID_PATTERN.fullmatch(value).groups()
        groups.setdefault(board, []).append(mls)
    if len(groups) > 4:
        raise ListingError('Choose homes from at most four listing boards.', 400)
    wanted = set(ids)
    found = {}
    for board, numbers in groups.items():
        data = _request('/listings', {'boardId': board, 'mlsNumber': numbers,
            'resultsPerPage': 40, 'displayPublic': 'Y', 'displayInternetEntireListing': 'Y',
            'displayAddressOnInternet': 'Y'})
        for record in data.get('listings', []):
            listing = normalize(record)
            if listing and listing['id'] in wanted:
                found[listing['id']] = listing
    return [found[value] for value in ids if value in found]
