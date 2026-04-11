"""
Re-runnable import script for tax protest reference data.

Usage:
    .venv/bin/python3 scripts/import_tax_data.py                 # Import all
    .venv/bin/python3 scripts/import_tax_data.py chambers         # Chambers only
    .venv/bin/python3 scripts/import_tax_data.py hcad             # HCAD only
    .venv/bin/python3 scripts/import_tax_data.py liberty          # Liberty only
    .venv/bin/python3 scripts/import_tax_data.py fort_bend        # Fort Bend only

Data files:
    tax_data/chambers.csv           - Chambers County (CSV, ~43k rows)
    tax_data/real_acct.txt          - Harris County main (TAB-separated, ~1.6M rows)
    tax_data/building_res.txt       - Harris County buildings (TAB-separated, ~1.3M rows)
    tax_data/liberty_county.txt     - Liberty County property file (fixed-width)
    tax_data/fort_bend_county_tax_data/... - Fort Bend County export bundle (CSV)
"""
import csv
import sys
import os
import re
import time
from collections import defaultdict

sys.stdout.reconfigure(line_buffering=True)
csv.field_size_limit(sys.maxsize)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from models import db

BATCH_SIZE = 5000


def clean(val):
    if val is None:
        return None
    cleaned = val.strip().strip('\xa0').strip()
    return cleaned if cleaned else None


def safe_int(val):
    cleaned = clean(val)
    if not cleaned or cleaned in ('Pending',):
        return None
    try:
        return int(float(cleaned.replace(',', '')))
    except (ValueError, TypeError):
        return None


def safe_decimal(val):
    cleaned = clean(val)
    if not cleaned:
        return None
    try:
        return float(cleaned.replace(',', ''))
    except (ValueError, TypeError):
        return None


def safe_decimal_implied(val, scale=4):
    cleaned = clean(val)
    if not cleaned:
        return None
    digits = ''.join(ch for ch in cleaned if ch.isdigit() or ch == '-')
    if not digits:
        return None
    try:
        return int(digits) / (10 ** scale)
    except (ValueError, TypeError):
        return None


def fixed_text(line, start, end):
    return clean(line[start - 1:end])


def fixed_int(line, start, end):
    return safe_int(line[start - 1:end])


def fixed_decimal_implied(line, start, end, scale=4):
    return safe_decimal_implied(line[start - 1:end], scale=scale)


def normalize_address_text(address):
    if not address:
        return None
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
    return addr or None


def build_site_address(street_num, street_prefix, street_name, street_suffix, unit=None):
    parts = [street_num, street_prefix, street_name, street_suffix]
    address = ' '.join(part for part in parts if part).strip()
    if unit:
        address = f"{address} UNIT {unit}".strip()
    return address or None


def resolve_existing_path(*candidates):
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _get_raw_conn(app):
    """Get a raw psycopg2 connection from the SQLAlchemy engine."""
    with app.app_context():
        engine = db.engine
        return engine.raw_connection()


def _bulk_insert(cursor, table, columns, rows):
    """Fast bulk insert using psycopg2 execute_values."""
    from psycopg2.extras import execute_values
    cols = ', '.join(columns)
    template = '(' + ', '.join(['%s'] * len(columns)) + ')'
    sql = f"INSERT INTO {table} ({cols}) VALUES %s"
    if table == 'hcad_properties':
        sql += " ON CONFLICT (acct) DO NOTHING"
    execute_values(cursor, sql, rows, template=template, page_size=BATCH_SIZE)


def _load_liberty_subdivision_lookup(filepath):
    lookup = {}
    with open(filepath, 'r', encoding='utf-8-sig', errors='replace') as f:
        for raw_line in f:
            line = raw_line.rstrip('\r\n')
            code = fixed_text(line, 1, 10)
            if not code:
                continue
            lookup[code] = fixed_text(line, 11, 50)
    return lookup


def _choose_liberty_sq_ft(area_parts):
    main_area = area_parts.get('main_area', 0)
    story_area = area_parts.get('story_area', 0)
    mobile_area = area_parts.get('mobile_area', 0)
    best = max(main_area, story_area, mobile_area)
    return best or None


def _load_liberty_home_improvements(info_path, detail_path):
    improvements = {}

    print("Parsing Liberty improvement info...")
    with open(info_path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for raw_line in f:
            line = raw_line.rstrip('\r\n')
            prop_id = fixed_text(line, 1, 12)
            imprv_id = fixed_text(line, 17, 28)
            if not prop_id or not imprv_id:
                continue

            imprv_type_cd = fixed_text(line, 29, 38)
            imprv_type_desc = fixed_text(line, 39, 63)
            is_residential = imprv_type_cd in {'R', 'M'} or imprv_type_desc in {'RESIDENTIAL', 'MOBILE HOME'}
            if not is_residential:
                continue

            key = (prop_id, imprv_id)
            improvements[key] = {
                'prop_id': prop_id,
                'imprv_id': imprv_id,
                'imprv_type_cd': imprv_type_cd,
                'imprv_type_desc': imprv_type_desc,
                'imprv_homesite': fixed_text(line, 69, 69),
                'imprv_val': fixed_int(line, 70, 83),
                'residential_sq_ft': None,
                'is_residential': True,
            }

    print("Parsing Liberty improvement detail...")
    detail_area_by_key = {}
    with open(detail_path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for raw_line in f:
            line = raw_line.rstrip('\r\n')
            prop_id = fixed_text(line, 1, 12)
            imprv_id = fixed_text(line, 17, 28)
            key = (prop_id, imprv_id)
            if key not in improvements:
                continue

            detail_type = fixed_text(line, 41, 50) or ''
            detail_desc = fixed_text(line, 51, 75) or ''
            area = fixed_int(line, 94, 108)
            if not area or area <= 0:
                continue

            buckets = detail_area_by_key.setdefault(key, {
                'main_area': 0,
                'story_area': 0,
                'mobile_area': 0,
            })
            if 'M HOME' in detail_desc:
                buckets['mobile_area'] += area
            elif detail_type == 'MA':
                buckets['main_area'] += area
            elif detail_type.startswith('MA'):
                buckets['story_area'] += area

    improvement_rows = []
    property_sq_ft = {}
    home_prop_ids = set()

    for key, info in improvements.items():
        home_prop_ids.add(info['prop_id'])
        residential_sq_ft = _choose_liberty_sq_ft(detail_area_by_key.get(key, {}))
        info['residential_sq_ft'] = residential_sq_ft
        if residential_sq_ft:
            property_sq_ft[info['prop_id']] = max(property_sq_ft.get(info['prop_id']) or 0, residential_sq_ft)
        improvement_rows.append((
            info['prop_id'],
            info['imprv_id'],
            info['imprv_type_cd'],
            info['imprv_type_desc'],
            info['imprv_homesite'],
            info['imprv_val'],
            residential_sq_ft,
            True,
        ))

    return home_prop_ids, property_sq_ft, improvement_rows


def _load_fort_bend_home_property_ids(filepath):
    home_prop_ids = set()
    with open(filepath, 'r', newline='', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            prop_id = clean(row.get('PropertyID'))
            if not prop_id:
                continue
            if clean(row.get('Type')) in {'R', 'M'}:
                home_prop_ids.add(prop_id)
    return home_prop_ids


def _load_fort_bend_acreage(filepath):
    homesite_acres = defaultdict(float)
    total_acres = defaultdict(float)

    with open(filepath, 'r', newline='', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            prop_id = clean(row.get('PropertyID'))
            if not prop_id:
                continue

            acreage = safe_decimal(row.get('Acres'))
            square_feet = safe_decimal(row.get('SquareFeet'))
            area_acres = acreage if acreage and acreage > 0 else (
                (square_feet / 43560.0) if square_feet and square_feet > 0 else None
            )
            if not area_acres or area_acres <= 0:
                continue

            total_acres[prop_id] += area_acres
            homesite_flag = safe_decimal(row.get('HomesiteFlag'))
            if homesite_flag and homesite_flag > 0:
                homesite_acres[prop_id] += area_acres

    acreage_by_prop = {}
    prop_ids = set(total_acres) | set(homesite_acres)
    for prop_id in prop_ids:
        acreage_by_prop[prop_id] = homesite_acres.get(prop_id) or total_acres.get(prop_id)
    return acreage_by_prop


def _fort_bend_site_addr(row):
    situs = clean(row.get('Situs'))
    if situs:
        first_part = clean(situs.split(',')[0])
        if first_part:
            return first_part

    return build_site_address(
        clean(row.get('SitusStreetNumber')),
        clean(row.get('SitusPreDirectional')),
        clean(row.get('SitusStreetName')),
        clean(row.get('SitusStreetSuffix')),
    )


def import_chambers(app):
    filepath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tax_data', 'chambers.csv')
    if not os.path.exists(filepath):
        print(f"ERROR: {filepath} not found")
        return

    print("=== Importing Chambers County data ===")
    start = time.time()

    conn = _get_raw_conn(app)
    cursor = conn.cursor()

    print("Truncating chambers_properties...")
    cursor.execute("DELETE FROM chambers_properties")
    conn.commit()

    columns = [
        'parcel_id', 'account', 'street', 'street_overflow', 'city', 'zip5',
        'prop_street_number', 'prop_street', 'prop_street_dir', 'prop_city', 'prop_zip5',
        'legal1', 'legal2', 'legal3', 'legal4', 'acres', 'market_value',
        'improvement_hs_val', 'improvement_nhs_val',
    ]

    batch = []
    count = 0

    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            batch.append((
                clean(row.get('Parcel_ID')),
                clean(row.get('Account')),
                clean(row.get('Street')),
                clean(row.get('Street_Overflow')),
                clean(row.get('City')),
                clean(row.get('Zip5')),
                clean(row.get('Prop_Street_Number')),
                clean(row.get('Prop_Street')),
                clean(row.get('Prop_Street_Dir')),
                clean(row.get('Prop_City')),
                clean(row.get('Prop_Zip5')),
                clean(row.get('Legal1')),
                clean(row.get('Legal2')),
                clean(row.get('Legal3')),
                clean(row.get('Legal4')),
                safe_decimal(row.get('Acres')),
                safe_int(row.get('Market_Value')),
                safe_int(row.get('Improvement_Hs')),
                safe_int(row.get('Improvement_Nhs')),
            ))

            if len(batch) >= BATCH_SIZE:
                _bulk_insert(cursor, 'chambers_properties', columns, batch)
                conn.commit()
                count += len(batch)
                print(f"  Chambers: {count:,} rows...")
                batch = []

    if batch:
        _bulk_insert(cursor, 'chambers_properties', columns, batch)
        conn.commit()
        count += len(batch)

    cursor.close()
    conn.close()

    elapsed = time.time() - start
    print(f"  Chambers complete: {count:,} rows in {elapsed:.1f}s")


def import_hcad_neighborhoods(app):
    """Import the HCAD neighborhood code lookup table."""
    nc_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tax_data', 'real_neighborhood_code.txt')
    if not os.path.exists(nc_path):
        print(f"ERROR: {nc_path} not found")
        return

    print("=== Importing HCAD neighborhood codes ===")
    conn = _get_raw_conn(app)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM hcad_neighborhood_codes")
    conn.commit()

    cols = ['cd', 'grp_cd', 'dscr']
    batch = []
    count = 0

    with open(nc_path, 'r', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            cd = clean(row.get('cd'))
            if not cd:
                continue
            batch.append((cd, clean(row.get('grp_cd')), clean(row.get('dscr'))))

            if len(batch) >= BATCH_SIZE:
                _bulk_insert(cursor, 'hcad_neighborhood_codes', cols, batch)
                conn.commit()
                count += len(batch)
                batch = []

    if batch:
        _bulk_insert(cursor, 'hcad_neighborhood_codes', cols, batch)
        conn.commit()
        count += len(batch)

    cursor.close()
    conn.close()
    print(f"  Neighborhood codes complete: {count:,} rows")


def import_hcad(app):
    main_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tax_data', 'real_acct.txt')
    bld_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tax_data', 'building_res.txt')

    if not os.path.exists(main_path):
        print(f"ERROR: {main_path} not found")
        return
    if not os.path.exists(bld_path):
        print(f"ERROR: {bld_path} not found")
        return

    print("=== Importing Harris County (HCAD) data ===")
    start = time.time()

    conn = _get_raw_conn(app)
    cursor = conn.cursor()

    print("Truncating hcad_buildings and hcad_properties...")
    cursor.execute("DELETE FROM hcad_buildings")
    cursor.execute("DELETE FROM hcad_properties")
    conn.commit()


    # --- Main properties ---
    print("Importing HCAD main properties (real_acct.txt)...")
    main_cols = [
        'acct', 'str_num', 'str_num_sfx', 'str', 'str_sfx', 'str_sfx_dir', 'str_unit',
        'site_addr_1', 'site_addr_2', 'site_addr_3',
        'acreage', 'assessed_val', 'tot_appr_val', 'tot_mkt_val',
        'lgl_1', 'lgl_2', 'lgl_3', 'lgl_4',
        'neighborhood_code',
    ]

    batch = []
    count = 0

    with open(main_path, 'r', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            acct = clean(row.get('acct'))
            if not acct:
                continue

            batch.append((
                acct,
                clean(row.get('str_num')),
                clean(row.get('str_num_sfx')),
                clean(row.get('str')),
                clean(row.get('str_sfx')),
                clean(row.get('str_sfx_dir')),
                clean(row.get('str_unit')),
                clean(row.get('site_addr_1')),
                clean(row.get('site_addr_2')),
                clean(row.get('site_addr_3')),
                safe_decimal(row.get('acreage')),
                safe_int(row.get('assessed_val')),
                safe_int(row.get('tot_appr_val')),
                safe_int(row.get('tot_mkt_val')),
                clean(row.get('lgl_1')),
                clean(row.get('lgl_2')),
                clean(row.get('lgl_3')),
                clean(row.get('lgl_4')),
                clean(row.get('Neighborhood_Code')),
            ))

            if len(batch) >= BATCH_SIZE:
                _bulk_insert(cursor, 'hcad_properties', main_cols, batch)
                conn.commit()
                count += len(batch)
                if count % 50000 < BATCH_SIZE:
                    elapsed = time.time() - start
                    print(f"  HCAD main: {count:,} rows... ({elapsed:.0f}s)")
                batch = []

    if batch:
        _bulk_insert(cursor, 'hcad_properties', main_cols, batch)
        conn.commit()
        count += len(batch)

    main_elapsed = time.time() - start
    print(f"  HCAD main complete: {count:,} rows in {main_elapsed:.1f}s")

    # --- Buildings ---
    print("Importing HCAD buildings (building_res.txt)...")
    bld_start = time.time()
    bld_cols = ['acct', 'im_sq_ft']
    batch = []
    bld_count = 0
    bld_skipped = 0

    with open(bld_path, 'r', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            acct = clean(row.get('acct'))
            if not acct:
                bld_skipped += 1
                continue

            batch.append((acct, safe_int(row.get('im_sq_ft'))))

            if len(batch) >= BATCH_SIZE:
                try:
                    _bulk_insert(cursor, 'hcad_buildings', bld_cols, batch)
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    print(f"  Warning: batch insert failed ({e}), inserting individually...")
                    for item in batch:
                        try:
                            cursor.execute(
                                "INSERT INTO hcad_buildings (acct, im_sq_ft) VALUES (%s, %s)",
                                item,
                            )
                            conn.commit()
                        except Exception:
                            conn.rollback()
                            bld_skipped += 1
                bld_count += len(batch)
                if bld_count % 50000 < BATCH_SIZE:
                    elapsed = time.time() - bld_start
                    print(f"  HCAD buildings: {bld_count:,} rows... ({elapsed:.0f}s)")
                batch = []

    if batch:
        try:
            _bulk_insert(cursor, 'hcad_buildings', bld_cols, batch)
            conn.commit()
        except Exception:
            conn.rollback()
            for item in batch:
                try:
                    cursor.execute(
                        "INSERT INTO hcad_buildings (acct, im_sq_ft) VALUES (%s, %s)",
                        item,
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    bld_skipped += 1
        bld_count += len(batch)

    bld_elapsed = time.time() - bld_start
    print(f"  HCAD buildings complete: {bld_count:,} rows in {bld_elapsed:.1f}s (skipped {bld_skipped:,})")

    cursor.close()
    conn.close()

    total_elapsed = time.time() - start
    print(f"=== HCAD import complete: {count:,} properties + {bld_count:,} buildings in {total_elapsed:.1f}s ===")


def import_liberty(app):
    repo_root = os.path.dirname(os.path.dirname(__file__))
    downloads_dir = os.path.join(os.path.expanduser('~'), 'Downloads', '2026 PRELIMARY APPRAISAL ROLL')

    property_path = resolve_existing_path(
        os.path.join(repo_root, 'tax_data', 'liberty_county.txt'),
        os.path.join(downloads_dir, '2026-03-31_002100_APPRAISAL_ENTITY_INFO.TXT'),
    )
    subdv_path = resolve_existing_path(
        os.path.join(repo_root, 'tax_data', '2026-03-31_002100_APPRAISAL_ABSTRACT_SUBDV.TXT'),
        os.path.join(downloads_dir, '2026-03-31_002100_APPRAISAL_ABSTRACT_SUBDV.TXT'),
    )
    imprv_info_path = resolve_existing_path(
        os.path.join(repo_root, 'tax_data', '2026-03-31_002100_APPRAISAL_IMPROVEMENT_INFO.TXT'),
        os.path.join(downloads_dir, '2026-03-31_002100_APPRAISAL_IMPROVEMENT_INFO.TXT'),
    )
    imprv_detail_path = resolve_existing_path(
        os.path.join(repo_root, 'tax_data', '2026-03-31_002100_APPRAISAL_IMPROVEMENT_DETAIL.TXT'),
        os.path.join(downloads_dir, '2026-03-31_002100_APPRAISAL_IMPROVEMENT_DETAIL.TXT'),
    )

    missing = [
        path for path in [property_path, subdv_path, imprv_info_path, imprv_detail_path]
        if not path
    ]
    if missing:
        print("ERROR: Liberty County source files not found")
        return

    print("=== Importing Liberty County home data ===")
    start = time.time()

    subdv_lookup = _load_liberty_subdivision_lookup(subdv_path)
    home_prop_ids, property_sq_ft, improvement_rows = _load_liberty_home_improvements(
        imprv_info_path, imprv_detail_path
    )

    print(f"  Liberty home properties identified: {len(home_prop_ids):,}")
    print(f"  Liberty residential/mobile improvements: {len(improvement_rows):,}")

    conn = _get_raw_conn(app)
    cursor = conn.cursor()

    print("Truncating liberty_improvements and liberty_properties...")
    cursor.execute("DELETE FROM liberty_improvements")
    cursor.execute("DELETE FROM liberty_properties")
    conn.commit()

    prop_cols = [
        'prop_id', 'geo_id', 'prop_type_cd',
        'situs_num', 'situs_street_prefx', 'situs_street', 'situs_street_suffix', 'situs_unit',
        'situs_city', 'situs_zip', 'site_addr_1', 'normalized_site_addr',
        'legal_desc', 'legal_desc2', 'legal_acreage',
        'abs_subdv_cd', 'abs_subdv_desc',
        'appraised_val', 'assessed_val', 'market_value',
        'imprv_hstd_val', 'imprv_non_hstd_val',
        'sq_ft', 'is_residential_home',
    ]

    batch = []
    count = 0
    imported_prop_ids = set()
    with open(property_path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for raw_line in f:
            line = raw_line.rstrip('\r\n')
            prop_id = fixed_text(line, 1, 12)
            if not prop_id or prop_id not in home_prop_ids:
                continue
            imported_prop_ids.add(prop_id)

            situs_num = fixed_text(line, 4460, 4474)
            situs_prefx = fixed_text(line, 1040, 1049)
            situs_street = fixed_text(line, 1050, 1099)
            situs_suffix = fixed_text(line, 1100, 1109)
            situs_unit = fixed_text(line, 4475, 4479)
            site_addr_1 = build_site_address(situs_num, situs_prefx, situs_street, situs_suffix, situs_unit)

            abs_subdv_cd = fixed_text(line, 1676, 1685)
            abs_subdv_desc = subdv_lookup.get(abs_subdv_cd) if abs_subdv_cd else None

            batch.append((
                prop_id,
                fixed_text(line, 547, 596),
                fixed_text(line, 13, 17),
                situs_num,
                situs_prefx,
                situs_street,
                situs_suffix,
                situs_unit,
                fixed_text(line, 1110, 1139),
                fixed_text(line, 1140, 1149),
                site_addr_1,
                normalize_address_text(site_addr_1),
                fixed_text(line, 1150, 1404),
                fixed_text(line, 1405, 1659),
                fixed_decimal_implied(line, 1660, 1675, scale=4),
                abs_subdv_cd,
                abs_subdv_desc,
                fixed_int(line, 1916, 1930),
                fixed_int(line, 1946, 1960),
                fixed_int(line, 4214, 4227),
                fixed_int(line, 1826, 1840),
                fixed_int(line, 1841, 1855),
                property_sq_ft.get(prop_id),
                True,
            ))

            if len(batch) >= BATCH_SIZE:
                _bulk_insert(cursor, 'liberty_properties', prop_cols, batch)
                conn.commit()
                count += len(batch)
                if count % 25000 < BATCH_SIZE:
                    elapsed = time.time() - start
                    print(f"  Liberty properties: {count:,} rows... ({elapsed:.0f}s)")
                batch = []

    if batch:
        _bulk_insert(cursor, 'liberty_properties', prop_cols, batch)
        conn.commit()
        count += len(batch)

    print(f"  Liberty properties complete: {count:,} rows")

    imprv_cols = [
        'prop_id', 'imprv_id', 'imprv_type_cd', 'imprv_type_desc',
        'imprv_homesite', 'imprv_val', 'residential_sq_ft', 'is_residential',
    ]
    batch = []
    imprv_count = 0
    skipped_improvements = 0
    for row in improvement_rows:
        if row[0] not in imported_prop_ids:
            skipped_improvements += 1
            continue
        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            _bulk_insert(cursor, 'liberty_improvements', imprv_cols, batch)
            conn.commit()
            imprv_count += len(batch)
            batch = []

    if batch:
        _bulk_insert(cursor, 'liberty_improvements', imprv_cols, batch)
        conn.commit()
        imprv_count += len(batch)

    cursor.close()
    conn.close()

    elapsed = time.time() - start
    print(
        f"=== Liberty import complete: {count:,} properties + {imprv_count:,} improvements "
        f"in {elapsed:.1f}s (skipped {skipped_improvements:,} orphan improvements) ==="
    )


def import_fort_bend(app):
    repo_root = os.path.dirname(os.path.dirname(__file__))
    base_dir = os.path.join(repo_root, 'tax_data', 'fort_bend_county_tax_data')

    property_path = os.path.join(base_dir, 'PropertyProperty-E', 'PropertyDataExport4558080.txt')
    improvement_path = os.path.join(base_dir, 'PropertyImprovement-E', 'PropertyDataExport4558083.txt')
    land_path = os.path.join(base_dir, 'PropertyLand-E', 'PropertyDataExport4558082.txt')

    missing = [path for path in [property_path, improvement_path, land_path] if not os.path.exists(path)]
    if missing:
        print("ERROR: Fort Bend County source files not found")
        return

    print("=== Importing Fort Bend County home data ===")
    start = time.time()

    home_prop_ids = _load_fort_bend_home_property_ids(improvement_path)
    acreage_by_prop = _load_fort_bend_acreage(land_path)

    print(f"  Fort Bend home properties identified: {len(home_prop_ids):,}")
    print(f"  Fort Bend properties with acreage: {len(acreage_by_prop):,}")

    conn = _get_raw_conn(app)
    cursor = conn.cursor()

    print("Truncating fort_bend_properties...")
    cursor.execute("DELETE FROM fort_bend_properties")
    conn.commit()

    prop_cols = [
        'property_id', 'quick_ref_id', 'property_number',
        'legal_desc', 'legal_location_code', 'legal_location_desc', 'legal_acres',
        'market_value', 'assessed_value', 'land_value', 'improvement_value',
        'sq_ft', 'nbhd_code', 'nbhd_desc',
        'situs', 'site_addr_1', 'normalized_site_addr',
        'situs_pre_directional', 'situs_street_number', 'situs_street_name',
        'situs_street_suffix', 'situs_post_directional',
        'situs_city', 'situs_state', 'situs_zip',
        'acreage', 'is_residential_home',
    ]

    batch = []
    count = 0

    with open(property_path, 'r', newline='', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            property_id = clean(row.get('PropertyID'))
            if not property_id or property_id not in home_prop_ids:
                continue

            site_addr_1 = _fort_bend_site_addr(row)
            acreage = acreage_by_prop.get(property_id)
            if acreage is None:
                acreage = safe_decimal(row.get('LegalAcres'))

            market_value = safe_int(row.get('CurrMarketValue'))
            if market_value is None:
                market_value = safe_int(row.get('MarketValue'))

            assessed_value = safe_int(row.get('CurrAssessedValue'))
            if assessed_value is None:
                assessed_value = safe_int(row.get('AssessedValue'))

            land_value = safe_int(row.get('CurrLandValue'))
            if land_value is None:
                land_value = safe_int(row.get('LandValue'))

            improvement_value = safe_int(row.get('CurrImprovmentValue'))
            if improvement_value is None:
                improvement_value = safe_int(row.get('ImprovmentValue'))

            batch.append((
                property_id,
                clean(row.get('QuickRefID')),
                clean(row.get('PropertyNumber')),
                clean(row.get('LegalDesc')),
                clean(row.get('LegalLocationCode')),
                clean(row.get('LegalLocationDesc')),
                safe_decimal(row.get('LegalAcres')),
                market_value,
                assessed_value,
                land_value,
                improvement_value,
                safe_int(row.get('SquareFootage')),
                clean(row.get('NbhdCode')),
                clean(row.get('NbhdDesc')),
                clean(row.get('Situs')),
                site_addr_1,
                normalize_address_text(site_addr_1),
                clean(row.get('SitusPreDirectional')),
                clean(row.get('SitusStreetNumber')),
                clean(row.get('SitusStreetName')),
                clean(row.get('SitusStreetSuffix')),
                clean(row.get('SitusPostDirectional')),
                clean(row.get('SitusCity')),
                clean(row.get('SitusState')),
                clean(row.get('SitusZip')),
                acreage,
                True,
            ))

            if len(batch) >= BATCH_SIZE:
                _bulk_insert(cursor, 'fort_bend_properties', prop_cols, batch)
                conn.commit()
                count += len(batch)
                if count % 25000 < BATCH_SIZE:
                    elapsed = time.time() - start
                    print(f"  Fort Bend properties: {count:,} rows... ({elapsed:.0f}s)")
                batch = []

    if batch:
        _bulk_insert(cursor, 'fort_bend_properties', prop_cols, batch)
        conn.commit()
        count += len(batch)

    cursor.close()
    conn.close()

    elapsed = time.time() - start
    print(f"=== Fort Bend import complete: {count:,} properties in {elapsed:.1f}s ===")


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else 'all'
    app = create_app()

    if target in ('all', 'chambers'):
        import_chambers(app)
    if target in ('all', 'hcad', 'neighborhoods'):
        import_hcad_neighborhoods(app)
    if target in ('all', 'hcad'):
        import_hcad(app)
    if target in ('all', 'liberty'):
        import_liberty(app)
    if target in ('all', 'fort_bend', 'fortbend'):
        import_fort_bend(app)

    print("\nDone!")
