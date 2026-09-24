"""Opt-in browser check: RUN_MARKETING_BROWSER=1 pytest this file."""
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from werkzeug.serving import make_server

from models import MarketingTracking, db
from services.marketing import tracking
from test_marketing_tracking import campaign_with_send, HUMAN

pytestmark = [pytest.mark.usefixtures('marketing_gmail'),
              pytest.mark.skipif(os.environ.get('RUN_MARKETING_BROWSER') != '1', reason='Browser checks are opt-in')]


def test_completed_campaign_refresh_history_filters_and_mobile(app, seed, owner_a_client):
    from playwright.sync_api import sync_playwright, expect
    with app.app_context():
        campaign, send = campaign_with_send(seed)
        cid, sid = campaign.id, send.id
        track = MarketingTracking.query.filter_by(send_id=sid).one()
        open_path = urlsplit(tracking.public_url('open', track.token)).path
        click_path = urlsplit(tracking.public_url('click', track.links[0].token)).path
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    artifacts = Path(__file__).parent / 'artifacts/marketing-tracking'
    artifacts.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width':1440, 'height':1100}, user_agent=HUMAN['User-Agent'])
            cookie = owner_a_client.get_cookie('session')
            context.add_cookies([{'name':'session', 'value':cookie.value, 'url':base}])
            # Keep this check local, including images in previews and analytics.
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'{base}/marketing/campaigns/{cid}')
            expect(page.get_by_role('heading', name='Spring check-in')).to_be_visible()
            expect(page.get_by_role('link', name='0 recorded clicks for tracked@example.com')).to_be_visible()
            page.locator(f'#recipient-preview-{send.contact_id} summary').click()
            preview_id = f'#recipient-preview-{send.contact_id}'
            # Simulate a recipient outside the agent session.
            visitor = playwright.request.new_context(user_agent=HUMAN['User-Agent'])
            assert visitor.get(base + open_path).status == 200
            assert visitor.get(base + click_path, max_redirects=0).status == 302
            expect(page.get_by_role('link', name='1 recorded clicks for tracked@example.com')).to_be_visible(timeout=22000)
            assert page.locator(preview_id).get_attribute('open') is not None
            page.locator(preview_id + ' summary').click()
            page.get_by_role('link', name='1 recorded clicks for tracked@example.com').click()
            expect(page.locator(f'#activity-{sid}')).to_have_attribute('open','')
            expect(page.locator(f'#activity-{sid}').get_by_text('Open recorded')).to_be_visible()
            expect(page.locator(f'#activity-{sid}').get_by_role('link', name='View listing')).to_be_visible()
            page.screenshot(path=str(artifacts/'desktop-dark.png'), full_page=True)
            page.locator("#crmThemeToggle").click()
            page.wait_for_timeout(300)
            page.screenshot(path=str(artifacts/'desktop-light.png'), full_page=True)
            page.get_by_role('navigation', name='Recipient activity filter').get_by_role('link', name='No activity recorded').click()
            expect(page.get_by_text('No recipients match this filter.')).to_be_visible()
            page.get_by_role('navigation', name='Recipient activity filter').get_by_role('link', name='Clicked', exact=True).click()
            expect(page.get_by_role('link', name='1 recorded clicks for tracked@example.com')).to_be_visible()
            page.set_viewport_size({'width':390,'height':844})
            page.locator("#crmThemeToggleMobile").click()
            page.wait_for_timeout(300)
            page.screenshot(path=str(artifacts/'mobile-dark.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
            assert not errors
            visitor.dispose(); context.close(); browser.close()
    finally:
        server.shutdown(); thread.join(timeout=5)
