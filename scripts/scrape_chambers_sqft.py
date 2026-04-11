"""
Scrape square footage from chamberscad.org for Chambers County properties.

Navigates directly to each property's detail page via URL, finds the
Improvement Building table, and sums sq ft for MLS-style Gross Living Area (GLA):
main structure (RES MAS / RES FRM), upper/second story, half-story, additions,
converted garage, finished attic. Excludes garages, porches, patios, unfinished
attic, etc.

Usage:
    python3 scripts/scrape_chambers_sqft.py --limit 3 --visible
    python3 scripts/scrape_chambers_sqft.py
    # Recompute all improved properties (overwrites existing sq_ft)
    python3 scripts/scrape_chambers_sqft.py --rescan

Requires: playwright (pip install playwright && playwright install chromium)
This is a standalone script -- playwright is NOT an app runtime dependency.
"""
import argparse
import logging
import random
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), 'scrape_chambers.log'),
            mode='a',
        ),
    ],
)
log = logging.getLogger(__name__)

DETAIL_URL = 'https://chamberscad.org/Home/Details?parcelId={parcel_id}'
DELAY_MIN = 0.8
DELAY_MAX = 1.2


def get_properties_to_scrape(app, limit=None, rescan=False):
    """Return list of (id, parcel_id) for Chambers properties to scrape.

    By default only rows with sq_ft IS NULL. With rescan=True, all properties
    with improvements (overwrites prior sq_ft).
    """
    from models import db, ChambersProperty

    with app.app_context():
        has_imp = db.or_(
            db.and_(
                ChambersProperty.improvement_hs_val.isnot(None),
                ChambersProperty.improvement_hs_val > 0,
            ),
            db.and_(
                ChambersProperty.improvement_nhs_val.isnot(None),
                ChambersProperty.improvement_nhs_val > 0,
            ),
        )
        query = ChambersProperty.query.filter(has_imp)
        if not rescan:
            query = query.filter(ChambersProperty.sq_ft.is_(None))
        query = query.order_by(ChambersProperty.id)

        if limit:
            query = query.limit(limit)

        return [(p.id, p.parcel_id) for p in query.all()]


def update_sqft(app, property_id, sq_ft_value):
    """Write sq_ft back to the database."""
    from models import db, ChambersProperty

    with app.app_context():
        db.session.execute(
            db.update(ChambersProperty)
            .where(ChambersProperty.id == property_id)
            .values(sq_ft=sq_ft_value)
        )
        db.session.commit()


def scrape_property(page, parcel_id):
    """
    Navigate directly to a property detail page and extract sqft.

    Returns total GLA sqft (int), or 0 if no matching improvement rows.
    Raises on navigation/scraping failure.
    """
    url = DETAIL_URL.format(parcel_id=parcel_id)
    page.goto(url, wait_until='domcontentloaded', timeout=30000)

    # Wait for the page content to render
    page.wait_for_selector('h2, h3', timeout=15000)
    page.wait_for_timeout(1500)

    # Verify we landed on the right property
    heading = page.locator('h2').first.inner_text()
    if str(parcel_id) not in heading:
        log.warning(f'Parcel {parcel_id}: page heading "{heading}" does not match')
        return 0

    return extract_residential_sqft(page)


def extract_residential_sqft(page):
    """
    Sum Improvement Building sq ft for MLS-style GLA (finished living area).

    Includes: RES MAS / RES FRM, UPPER / 2ND FLR, 1/2 STRY, ADD / ADDN,
    CONV GAR, FIN ATTIC. Excludes garages (non-converted), porches, patios,
    unfinished attic, storage, etc.
    """
    total = 0

    # The page uses JavaScript to render tables; extract via JS for reliability
    sqft_data = page.evaluate('''() => {
        function norm(s) {
            return s.replace(/\\s+/g, ' ').trim().toUpperCase();
        }
        /** CAD "Type" cell → counts toward gross living area (MLS-style). */
        function isGlaType(typeRaw) {
            const t = norm(typeRaw);
            if (!t) return false;

            // Converted garage counts as living area; check before generic GAR exclude
            if (t.includes('CONV') && t.includes('GAR')) return true;

            // Main structure
            if (t.startsWith('RES MAS') || t.startsWith('RES FRM')) return true;

            // Additional finished stories / levels
            if (t === 'UPPER' || t.startsWith('UPPER ')) return true;
            if (t.includes('2ND') && (t.includes('FLR') || t.includes('FLOOR'))) return true;
            if ((t.includes('1/2') || t.includes('HALF')) && t.includes('STRY')) return true;

            // Finished additions (whole-word ADD / ADDN)
            if (/\\bADDN?\\b/.test(t)) return true;

            // Finished attic
            if ((t.includes('FIN') && t.includes('ATTIC')) || t.startsWith('FIN ATT')) return true;

            // Non-GLA — do not count
            if (t.includes('UNF') && t.includes('ATTIC')) return false;
            if (/GARAGE|ATT\\s*GAR|DET\\s*GAR|\\bGAR\\b/i.test(t)) return false;
            if (/POR\\s*CH|PORCH|OPEN\\s*PORCH|CVRD\\s*PCH|CVRD\\s*PORCH/i.test(t)) return false;
            if (/\\bPATIO\\b|\\bDECK\\b|STORAGE|SHED|OUTBLDG|OUT\\s*BLDG/i.test(t)) return false;

            return false;
        }

        const results = [];
        const tables = document.querySelectorAll('table');
        for (const table of tables) {
            const headers = Array.from(table.querySelectorAll('thead th, tr:first-child th'))
                .map(h => h.textContent.trim().toUpperCase());
            const typeIdx = headers.indexOf('TYPE');
            const sqftIdx = headers.findIndex(h => h.includes('SQFT') || h.includes('SQ FT'));
            if (typeIdx === -1 || sqftIdx === -1) continue;

            const rows = table.querySelectorAll('tbody tr');
            for (const row of rows) {
                const cells = row.querySelectorAll('td');
                if (cells.length <= Math.max(typeIdx, sqftIdx)) continue;
                const type = cells[typeIdx].textContent.trim();
                const sqft = cells[sqftIdx].textContent.trim().replace(/,/g, '');
                if (isGlaType(type)) {
                    results.push({type: norm(type), sqft: parseInt(sqft, 10) || 0});
                }
            }
        }
        return results;
    }''')

    for row in sqft_data:
        total += row['sqft']

    return total


def main():
    parser = argparse.ArgumentParser(description='Scrape Chambers CAD sqft')
    parser.add_argument('--limit', type=int, default=None,
                        help='Max properties to scrape (default: all)')
    parser.add_argument('--visible', action='store_true',
                        help='Run browser in visible (non-headless) mode')
    parser.add_argument('--rescan', action='store_true',
                        help='Re-scrape all improved properties (overwrite existing sq_ft)')
    args = parser.parse_args()

    from app import create_app
    app = create_app()

    properties = get_properties_to_scrape(
        app, limit=args.limit, rescan=args.rescan)
    total = len(properties)
    log.info(f'Found {total} properties to scrape')
    if args.rescan:
        log.info('Rescan mode: existing sq_ft values will be overwritten')

    if total == 0:
        log.info('Nothing to do')
        return

    scraped = 0
    failed = 0
    start = time.time()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.visible)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120.0.0.0 Safari/537.36',
        )
        page = context.new_page()

        for idx, (prop_id, parcel_id) in enumerate(properties):
            try:
                sqft = scrape_property(page, parcel_id)
                update_sqft(app, prop_id, sqft)
                scraped += 1

                if sqft > 0:
                    log.info(f'[{idx+1}/{total}] Parcel {parcel_id}: {sqft} sqft')
                else:
                    log.info(f'[{idx+1}/{total}] Parcel {parcel_id}: no GLA rows matched (sq_ft=0)')

            except (PlaywrightTimeout, Exception) as e:
                failed += 1
                log.error(f'[{idx+1}/{total}] Parcel {parcel_id}: FAILED - {e}')

            if idx % 100 == 99:
                elapsed = time.time() - start
                rate = (idx + 1) / elapsed * 3600
                log.info(f'Progress: {idx+1}/{total} ({scraped} ok, {failed} failed, '
                         f'{rate:.0f}/hr, {elapsed:.0f}s elapsed)')

            delay = random.uniform(DELAY_MIN, DELAY_MAX)
            time.sleep(delay)

        browser.close()

    elapsed = time.time() - start
    log.info(f'Done: {scraped} scraped, {failed} failed, {elapsed:.0f}s total')


if __name__ == '__main__':
    sys.stdout.reconfigure(line_buffering=True)
    main()
