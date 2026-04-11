"""
Re-runnable import script for tax protest reference data.

Usage:
    .venv/bin/python3 scripts/import_tax_data.py                 # Import all
    .venv/bin/python3 scripts/import_tax_data.py chambers         # Chambers only
    .venv/bin/python3 scripts/import_tax_data.py hcad             # HCAD only

Data files:
    tax_data/chambers.csv           - Chambers County (CSV, ~43k rows)
    tax_data/real_acct.txt          - Harris County main (TAB-separated, ~1.6M rows)
    tax_data/building_res.txt       - Harris County buildings (TAB-separated, ~1.3M rows)
"""
import csv
import sys
import os
import time

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


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else 'all'
    app = create_app()

    if target in ('all', 'chambers'):
        import_chambers(app)
    if target in ('all', 'hcad', 'neighborhoods'):
        import_hcad_neighborhoods(app)
    if target in ('all', 'hcad'):
        import_hcad(app)

    print("\nDone!")
