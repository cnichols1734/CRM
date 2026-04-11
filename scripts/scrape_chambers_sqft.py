"""
Scrape square footage from chamberscad.org for Chambers County properties.

Navigates directly to each property's detail page via URL, finds the
Improvement Building table, and sums residential sqft (RES MAS rows).

Usage:
    # Test mode: visible browser, first 3 properties
    python3 scripts/scrape_chambers_sqft.py --limit 3 --visible

    # Full run: headless, all un-scraped properties with improvements
    python3 scripts/scrape_chambers_sqft.py

    # Resume after interruption (only processes sq_ft IS NULL)
    python3 scripts/scrape_chambers_sqft.py

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


def get_properties_to_scrape(app, limit=None):
    """Return list of (id, parcel_id) for Chambers properties needing sqft."""
    from models import db, ChambersProperty

    with app.app_context():
        query = ChambersProperty.query.filter(
            ChambersProperty.sq_ft.is_(None),
            db.or_(
                db.and_(
                    ChambersProperty.improvement_hs_val.isnot(None),
                    ChambersProperty.improvement_hs_val > 0,
                ),
                db.and_(
                    ChambersProperty.improvement_nhs_val.isnot(None),
                    ChambersProperty.improvement_nhs_val > 0,
                ),
            ),
        ).order_by(ChambersProperty.id)

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

    Returns total residential sqft (int), or 0 if no RES MAS rows found.
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
    Parse the Improvement Building table on the detail page.
    Sum the Sqft column for rows where Type starts with "RES MAS".
    Returns int (0 if no residential rows found).
    """
    total = 0

    # The page uses JavaScript to render tables; extract via JS for reliability
    sqft_data = page.evaluate('''() => {
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
                const type = cells[typeIdx].textContent.trim().toUpperCase();
                const sqft = cells[sqftIdx].textContent.trim().replace(/,/g, '');
                if (type.startsWith('RES MAS')) {
                    results.push({type, sqft: parseInt(sqft) || 0});
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
    args = parser.parse_args()

    from app import create_app
    app = create_app()

    properties = get_properties_to_scrape(app, limit=args.limit)
    total = len(properties)
    log.info(f'Found {total} properties to scrape')

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
                    log.info(f'[{idx+1}/{total}] Parcel {parcel_id}: no RES MAS rows (sq_ft=0)')

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
