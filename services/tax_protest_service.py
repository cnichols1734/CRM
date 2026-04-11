"""
Tax Protest service layer.
Handles address lookup in county tax data, LLM-based subdivision extraction,
comparable property queries, and search result caching for CSV consistency.
"""
import re
import logging
from flask import session
from models import (
    db,
    ChambersProperty,
    HcadProperty,
    HcadBuilding,
    HcadNeighborhoodCode,
    LibertyProperty,
    LibertyCodeProfile,
)

logger = logging.getLogger(__name__)

SUBDIVISION_SYSTEM_PROMPT = (
    "You are a Texas property legal description parser. "
    "Extract ONLY the subdivision or neighborhood name from the given legal description. "
    "Strip lot numbers, block numbers, section numbers, and any other identifiers. "
    "Return ONLY the subdivision name in uppercase, nothing else. "
    "If you cannot identify a subdivision name, return UNKNOWN."
)

SUBDIVISION_EXAMPLES = (
    "Examples:\n"
    "'LOT 21 PINEHURST SEC 2' -> 'PINEHURST'\n"
    "'111 & 112 PINEHURST SEC 2' -> 'PINEHURST'\n"
    "'TR 15 BLK 2 SHADY OAKS' -> 'SHADY OAKS'\n"
    "'LTS 1-3 BLK 4 RIVER BEND SEC 1' -> 'RIVER BEND'\n"
    "'ALL BLK 1 SSBB' -> 'SSBB'\n"
)


def normalize_address(address):
    """Normalize a street address for matching."""
    if not address:
        return ''
    addr = address.upper().strip()
    replacements = {
        ' STREET': ' ST', ' DRIVE': ' DR', ' AVENUE': ' AVE',
        ' BOULEVARD': ' BLVD', ' LANE': ' LN', ' COURT': ' CT',
        ' CIRCLE': ' CIR', ' PLACE': ' PL', ' ROAD': ' RD',
        ' HIGHWAY': ' HWY', ' PARKWAY': ' PKWY',
    }
    for old, new in replacements.items():
        addr = addr.replace(old, new)
    addr = addr.replace('FARM TO MARKET', 'FM')
    addr = re.sub(r'\s*(APT|UNIT|STE|SUITE|#)\s*\S*$', '', addr)
    addr = re.sub(r'\s+', ' ', addr).strip()
    return addr


DIRECTIONAL_PREFIXES = {'N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW',
                         'NORTH', 'SOUTH', 'EAST', 'WEST'}


def _parse_street_parts(address):
    """Extract street number, direction, and street name from a full address.
    Returns (street_num, direction_or_None, street_name).
    """
    addr = normalize_address(address)
    if not addr:
        return None, None, None
    match = re.match(r'^(\d+)\s+(.+)', addr)
    if not match:
        return None, None, addr
    street_num = match.group(1)
    remainder = match.group(2)
    parts = remainder.split()
    if parts and parts[0] in DIRECTIONAL_PREFIXES:
        direction = parts[0]
        street_name = ' '.join(parts[1:]) if len(parts) > 1 else ''
        return street_num, direction, street_name
    return street_num, None, remainder


def find_property_in_tax_data(street_address, city, zip_code):
    """
    Search for a property in Chambers County first, then Harris County, then Liberty County.
    Returns (record_dict, source) or (None, None).
    """
    normalized_address = normalize_address(street_address)
    street_num, direction, street_name = _parse_street_parts(street_address)
    zip_clean = (zip_code or '').strip()[:5]
    city_clean = (city or '').strip().upper()

    # --- Chambers County ---
    chambers_result = _search_chambers(street_num, direction, street_name, zip_clean)
    if chambers_result:
        return chambers_result, 'chambers'

    # --- Harris County (HCAD) ---
    hcad_result = _search_hcad(street_num, direction, street_name, zip_clean)
    if hcad_result:
        return hcad_result, 'hcad'

    # --- Liberty County ---
    liberty_result = _search_liberty(
        normalized_address, street_num, street_name, city_clean, zip_clean
    )
    if liberty_result:
        return liberty_result, 'liberty'

    return None, None


def _search_chambers(street_num, direction, street_name, zip_code):
    """Search Chambers County by address components.
    Matches street number, direction (if present), and first word of street name.
    Tries with zip first, retries without if no match."""
    if not street_num or not street_name:
        return None

    first_word = street_name.split()[0]
    base_filters = [
        ChambersProperty.prop_street_number == street_num,
        ChambersProperty.prop_street.ilike(f'%{first_word}%'),
    ]
    if direction:
        base_filters.append(ChambersProperty.prop_street_dir == direction)

    if zip_code:
        result = ChambersProperty.query.filter(
            ChambersProperty.prop_zip5 == zip_code,
            *base_filters,
        ).first()
        if result:
            return _chambers_to_dict(result)

    result = ChambersProperty.query.filter(
        *base_filters,
    ).first()
    if result:
        return _chambers_to_dict(result)

    return None


def _search_hcad(street_num, direction, street_name, zip_code):
    """Search Harris County by address components.
    Matches street number, direction (via str_sfx_dir), and first word of street name.
    Falls back to relaxed search without zip if initial search misses."""
    if street_num and street_name:
        first_word = street_name.split()[0]
        base_filters = [
            HcadProperty.str_num == street_num,
            HcadProperty.str.ilike(f'%{first_word}%'),
        ]
        if direction:
            base_filters.append(HcadProperty.str_sfx_dir == direction)

        # Try with zip first
        if zip_code:
            result = HcadProperty.query.filter(
                HcadProperty.site_addr_3 == zip_code,
                *base_filters,
            ).first()
            if result:
                return _hcad_found(result)

        # Retry without zip (contact may have wrong/missing zip)
        result = HcadProperty.query.filter(
            *base_filters,
        ).first()
        if result:
            return _hcad_found(result)

    # Last resort: fuzzy match on full site_addr_1
    if street_num and street_name:
        result = HcadProperty.query.filter(
            HcadProperty.site_addr_1.ilike(f'%{street_num}%{street_name.split()[0]}%'),
        ).first()
        if result:
            return _hcad_found(result)

    return None


def _search_liberty(normalized_address, street_num, street_name, city, zip_code):
    """Search Liberty County home records using normalized address, then relaxed fallbacks."""
    base_filter = [LibertyProperty.is_residential_home.is_(True)]

    if normalized_address:
        if zip_code:
            result = LibertyProperty.query.filter(
                LibertyProperty.normalized_site_addr == normalized_address,
                LibertyProperty.situs_zip == zip_code,
                *base_filter,
            ).first()
            if result:
                return _liberty_to_dict(result)

        result = LibertyProperty.query.filter(
            LibertyProperty.normalized_site_addr == normalized_address,
            *base_filter,
        ).first()
        if result:
            return _liberty_to_dict(result)

    if street_name:
        first_word = street_name.split()[0]
        street_filters = [
            LibertyProperty.situs_street.ilike(f'%{first_word}%'),
            *base_filter,
        ]
        if street_num:
            street_filters.append(LibertyProperty.situs_num == street_num)

        if zip_code:
            result = LibertyProperty.query.filter(
                LibertyProperty.situs_zip == zip_code,
                *street_filters,
            ).first()
            if result:
                return _liberty_to_dict(result)

        if city:
            result = LibertyProperty.query.filter(
                db.func.upper(LibertyProperty.situs_city) == city,
                *street_filters,
            ).first()
            if result:
                return _liberty_to_dict(result)

        result = LibertyProperty.query.filter(
            *street_filters,
        ).first()
        if result:
            return _liberty_to_dict(result)

        if street_num:
            result = LibertyProperty.query.filter(
                LibertyProperty.site_addr_1.ilike(f'%{street_num}%{first_word}%'),
                *base_filter,
            ).first()
            if result:
                return _liberty_to_dict(result)

    return None


def _is_valid_subdivision(lgl_2):
    """Check if lgl_2 contains a real subdivision name vs. block/plat/utility labels."""
    if not lgl_2 or not lgl_2.strip():
        return False
    val = lgl_2.strip().upper()
    if val.startswith('('):
        return False
    if re.match(r'^BLK\s+\d', val):
        return False
    if re.match(r'^(LTS?\s|LOTS?\s|TRS?\s)', val):
        return False
    if len(val) < 3:
        return False
    return True


def _hcad_found(result):
    """Helper to load sq_ft and return dict for a matched HCAD property."""

    sq_ft = db.session.query(db.func.max(HcadBuilding.im_sq_ft)).filter(
        HcadBuilding.acct == result.acct
    ).scalar()

    return _hcad_to_dict(result, sq_ft)


def extract_subdivision_llm(legal_description):
    """
    Use GPT-4.1-mini to extract a clean subdivision name from a legal description.
    Falls back to regex if the LLM call fails.
    """
    if not legal_description:
        return None

    try:
        from services.ai_service import generate_ai_response
        prompt = f"{SUBDIVISION_EXAMPLES}\nLegal description: '{legal_description}'"
        result = generate_ai_response(
            system_prompt=SUBDIVISION_SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.0,
            reasoning_effort="low",
        )
        cleaned = result.strip().strip("'\"").upper()
        if cleaned and cleaned != 'UNKNOWN' and len(cleaned) > 1:
            return cleaned
    except Exception as e:
        logger.warning(f"LLM subdivision extraction failed, falling back to regex: {e}")

    return extract_subdivision_regex(legal_description)


def extract_subdivision_regex(legal_description):
    """
    Basic regex extraction of subdivision name from legal descriptions.
    Strips lot/tract numbers from the front and SEC/BLK info from the end.
    """
    if not legal_description:
        return None

    text = legal_description.upper().strip()
    text = re.sub(r'^(LOTS?\s+)?[\d\s&,\.]+\s+', '', text)
    text = re.sub(r'^(TRS?\s+)[\d\s&,\.]+\s*', '', text)
    text = re.sub(r'^(ALL\s+)?BLK\s+\d+\s*', '', text)
    text = re.sub(r'\s+SEC(TION)?\s+\d+.*$', '', text)
    text = re.sub(r'\s+BLK\s+\d+.*$', '', text)
    text = re.sub(r'\s+PH(ASE)?\s+\d+.*$', '', text)

    text = text.strip()
    if text and len(text) > 1 and not text.isdigit():
        return text
    return None


def get_neighborhood_name(neighborhood_code):
    """Resolve a neighborhood code to its description from the lookup table."""
    if not neighborhood_code:
        return None
    nc = HcadNeighborhoodCode.query.filter_by(cd=neighborhood_code).first()
    return nc.dscr if nc else None


def find_comparables(subdivision, zip_code, market_value, source,
                     main_sq_ft=None, fuzzy_subdivision=False,
                     subdivision_code=None, main_acreage=None):
    """
    Find properties in the same subdivision and zip with lower market value.
    For HCAD: matches on lgl_2 (exact when from structured data, ILIKE when
    from LLM extraction), requires a building, and filters to within 250 sq ft.
    For Chambers: uses ILIKE on legal1 (no sq ft band; HCAD still uses ±250).
    Returns list of property dicts.
    """
    SQ_FT_RANGE = 250

    if source == 'chambers':
        if not subdivision:
            return []

        pattern = f'%{subdivision}%'
        has_improvement = db.or_(
            db.and_(ChambersProperty.improvement_hs_val.isnot(None), ChambersProperty.improvement_hs_val > 0),
            db.and_(ChambersProperty.improvement_nhs_val.isnot(None), ChambersProperty.improvement_nhs_val > 0),
        )
        base_filters = [
            ChambersProperty.legal1.ilike(pattern),
            ChambersProperty.market_value.isnot(None),
            ChambersProperty.market_value > 0,
            ChambersProperty.market_value < market_value,
            ChambersProperty.prop_street_number.isnot(None),
            ChambersProperty.prop_street_number != '0',
            ChambersProperty.prop_street.isnot(None),
            has_improvement,
        ]

        if zip_code:
            results = ChambersProperty.query.filter(
                ChambersProperty.prop_zip5 == zip_code,
                *base_filters,
            ).order_by(ChambersProperty.market_value.asc()).all()
            if results:
                return [_chambers_to_dict(r) for r in results]

        results = ChambersProperty.query.filter(
            *base_filters,
        ).order_by(ChambersProperty.market_value.asc()).all()
        return [_chambers_to_dict(r) for r in results]

    elif source == 'hcad':
        if not subdivision:
            return []

        if fuzzy_subdivision:
            sub_filter = HcadProperty.lgl_2.ilike(f'%{subdivision}%')
        else:
            sub_filter = (HcadProperty.lgl_2 == subdivision)

        def _hcad_query(use_zip):
            q = db.session.query(
                HcadProperty,
                db.func.max(HcadBuilding.im_sq_ft).label('max_sq_ft')
            ).join(
                HcadBuilding, HcadProperty.acct == HcadBuilding.acct
            ).filter(
                sub_filter,
                HcadProperty.tot_mkt_val.isnot(None),
                HcadProperty.tot_mkt_val > 0,
                HcadProperty.tot_mkt_val < market_value,
                HcadProperty.site_addr_1.isnot(None),
                HcadProperty.site_addr_1 != '',
                HcadProperty.str_num.isnot(None),
                HcadProperty.str_num != '0',
                HcadBuilding.im_sq_ft.isnot(None),
                HcadBuilding.im_sq_ft > 0,
            )
            if use_zip and zip_code:
                q = q.filter(HcadProperty.site_addr_3 == zip_code)
            q = q.group_by(HcadProperty.id)
            if main_sq_ft and main_sq_ft > 0:
                q = q.having(
                    db.func.max(HcadBuilding.im_sq_ft).between(
                        main_sq_ft - SQ_FT_RANGE,
                        main_sq_ft + SQ_FT_RANGE,
                    )
                )
            return q.order_by(HcadProperty.tot_mkt_val.asc()).all()

        results = _hcad_query(use_zip=True)
        if not results:
            results = _hcad_query(use_zip=False)

        return [_hcad_to_dict(prop, sq_ft) for prop, sq_ft in results]

    elif source == 'liberty':
        if not subdivision_code:
            return []

        profile = LibertyCodeProfile.query.filter_by(abs_subdv_cd=subdivision_code).first()
        strategy = profile.strategy if profile and profile.strategy else 'strict'
        if strategy == 'reject':
            return []

        base_filters = [
            LibertyProperty.is_residential_home.is_(True),
            LibertyProperty.abs_subdv_cd == subdivision_code,
            LibertyProperty.market_value.isnot(None),
            LibertyProperty.market_value > 0,
            LibertyProperty.market_value < market_value,
            LibertyProperty.site_addr_1.isnot(None),
            LibertyProperty.site_addr_1 != '',
        ]

        def _liberty_query(use_zip):
            q = LibertyProperty.query.filter(*base_filters)
            if use_zip and zip_code:
                q = q.filter(LibertyProperty.situs_zip == zip_code)

            if strategy == 'strict':
                if main_sq_ft and main_sq_ft > 0:
                    q = q.filter(
                        LibertyProperty.sq_ft.isnot(None),
                        LibertyProperty.sq_ft.between(
                            max(0, main_sq_ft - 300),
                            main_sq_ft + 300,
                        )
                    )
                if main_acreage and main_acreage > 0:
                    tol = min(5.0, max(0.25, main_acreage * 0.5))
                    q = q.filter(
                        LibertyProperty.legal_acreage.isnot(None),
                        LibertyProperty.legal_acreage.between(
                            max(0, main_acreage - tol),
                            main_acreage + tol,
                        )
                    )

            return q.order_by(LibertyProperty.market_value.asc()).all()

        results = _liberty_query(use_zip=True)
        if results:
            return [_liberty_to_dict(r) for r in results]

        results = _liberty_query(use_zip=False)
        return [_liberty_to_dict(r) for r in results]

    return []


def _chambers_to_dict(record):
    """Convert a ChambersProperty to a display dict."""
    return {
        'id': record.id,
        'source': 'chambers',
        'address': f"{record.prop_street_number or ''} {record.prop_street or ''}".strip(),
        'full_address': f"{record.prop_street_number or ''} {record.prop_street or ''} {record.prop_street_dir or ''}".strip(),
        'city': record.prop_city,
        'zip': record.prop_zip5,
        'market_value': record.market_value,
        'legal1': record.legal1,
        'legal2': record.legal2,
        'legal3': record.legal3,
        'legal4': record.legal4,
        'sq_ft': record.sq_ft if record.sq_ft and record.sq_ft > 0 else None,
        'acreage': float(record.acres) if record.acres else None,
        'parcel_id': record.parcel_id,
        'account': record.account,
    }


def _hcad_to_dict(record, sq_ft=None):
    """Convert an HcadProperty to a display dict."""
    return {
        'id': record.id,
        'source': 'hcad',
        'address': record.site_addr_1 or f"{record.str_num or ''} {record.str or ''}".strip(),
        'full_address': record.site_addr_1 or '',
        'city': record.site_addr_2,
        'zip': record.site_addr_3,
        'market_value': record.tot_mkt_val,
        'legal1': record.lgl_1,
        'legal2': record.lgl_2,
        'legal3': record.lgl_3,
        'legal4': record.lgl_4,
        'sq_ft': sq_ft,
        'acreage': float(record.acreage) if record.acreage else None,
        'parcel_id': None,
        'account': record.acct,
        'neighborhood_code': record.neighborhood_code,
        'subdivision': record.lgl_2,
    }


def _liberty_to_dict(record):
    """Convert a LibertyProperty to a display dict."""
    return {
        'id': record.id,
        'source': 'liberty',
        'address': record.site_addr_1 or ' '.join(
            part for part in [record.situs_num, record.situs_street_prefx, record.situs_street, record.situs_street_suffix]
            if part
        ).strip(),
        'full_address': record.site_addr_1 or '',
        'city': record.situs_city,
        'zip': record.situs_zip,
        'market_value': record.market_value,
        'legal1': record.legal_desc,
        'legal2': record.abs_subdv_desc,
        'legal3': record.legal_desc2,
        'legal4': None,
        'sq_ft': record.sq_ft if record.sq_ft and record.sq_ft > 0 else None,
        'acreage': float(record.legal_acreage) if record.legal_acreage else None,
        'parcel_id': record.prop_id,
        'account': record.geo_id,
        'subdivision': record.abs_subdv_desc,
        'subdivision_code': record.abs_subdv_cd,
    }


def cache_search_result(source, subdivision, main_property_id, contact_id,
                        zip_code, neighborhood_code=None, main_sq_ft=None,
                        fuzzy_subdivision=False, subdivision_code=None,
                        main_acreage=None):
    """Store search params in Flask session for CSV download consistency."""
    session['tax_protest_result'] = {
        'source': source,
        'subdivision': subdivision,
        'main_property_id': main_property_id,
        'contact_id': contact_id,
        'zip_code': zip_code,
        'neighborhood_code': neighborhood_code,
        'subdivision_code': subdivision_code,
        'main_sq_ft': main_sq_ft,
        'main_acreage': main_acreage,
        'fuzzy_subdivision': fuzzy_subdivision,
    }


def get_cached_search_result():
    """Retrieve cached search params from Flask session."""
    return session.get('tax_protest_result')


def get_main_property_by_id(property_id, source):
    """Load a single property record by its ID and source."""
    if source == 'chambers':
        record = ChambersProperty.query.get(property_id)
        return _chambers_to_dict(record) if record else None
    elif source == 'hcad':
        record = HcadProperty.query.get(property_id)
        if not record:
            return None
        sq_ft = db.session.query(db.func.max(HcadBuilding.im_sq_ft)).filter(
            HcadBuilding.acct == record.acct
        ).scalar()
        return _hcad_to_dict(record, sq_ft)
    elif source == 'liberty':
        record = LibertyProperty.query.get(property_id)
        return _liberty_to_dict(record) if record else None
    return None
