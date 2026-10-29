#!/usr/bin/env python3
"""Exercise responsive workflows against a disposable local database.

Run after npm run build. Uses Chromium and WebKit; never contacts a live DB.
Screenshots and measurements are written to tests/artifacts/responsive/.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests.run_onboarding_e2e import ManagedAppServer

SIZES = [(382, 580), (594, 368), (669, 867), (951, 585), (385, 571),
         (320, 568), (375, 667), (390, 844), (430, 932), (844, 390),
         (768, 1024), (1024, 768), (1280, 800), (1440, 900), (1920, 1080)]
ARTIFACTS = ROOT / 'tests/artifacts/responsive'


def seed():
    from tests.browser_test_support import is_local_database_url
    assert is_local_database_url(os.environ.get('DATABASE_URL'))
    from datetime import datetime, timedelta
    from app import create_app
    from models import (db, Organization, User, Contact, Transaction, TransactionType,
                        TransactionDocument, Task, TaskType, TaskSubtype, TransactionParticipant, ClientPortalAccess)
    from services.tenant_service import create_default_groups_for_user, create_default_task_types_for_org
    from flask import url_for
    app = create_app()
    with app.app_context():
        db.create_all()
        org = Organization(name='Responsive Realty', slug='responsive-realty', subscription_tier='pro',
                           status='active', approved_at=datetime.utcnow(), max_contacts=1000, max_users=10)
        db.session.add(org); db.session.flush()
        user = User(organization_id=org.id, username='responsive@example.com', email='responsive@example.com',
                    first_name='Riley', last_name='Agent', role='admin', org_role='owner', last_login=datetime.utcnow())
        user.set_password('responsive-test-password')
        db.session.add(user); db.session.flush()
        create_default_groups_for_user(org.id, user.id, commit=False)
        contact = Contact(organization_id=org.id, user_id=user.id, created_by_id=user.id,
                          first_name='Casey', last_name='Responsive', email='casey@example.com', phone='5125550142')
        db.session.add(contact)
        tt = TransactionType(organization_id=org.id, name='seller', display_name='Seller')
        db.session.add(tt); db.session.flush()
        tx = Transaction(organization_id=org.id, created_by_id=user.id, transaction_type_id=tt.id,
                         street_address='742 Evergreen Terrace', city='Austin', state='TX', zip_code='78701', status='active')
        db.session.add(tx); db.session.flush()
        participant = TransactionParticipant(organization_id=org.id, transaction_id=tx.id, contact_id=contact.id,
                                            role='seller', is_primary=True)
        db.session.add(participant); db.session.flush()
        access = ClientPortalAccess(organization_id=org.id, transaction_id=tx.id, participant_id=participant.id,
                                    token='local-responsive-fixture-only')
        db.session.add(access)
        doc = TransactionDocument(organization_id=org.id, transaction_id=tx.id, template_slug='listing-agreement',
                                  template_name='Responsive contract', signed_original_filename='Responsive contract.pdf',
                                  status='pending', field_data={'sales_price': '450000', 'closing_date': '2026-12-15',
                                  '_meta': {'sales_price': {'page': 1}, 'closing_date': {'page': 12}}})
        db.session.add(doc); db.session.commit()
        create_default_task_types_for_org(org.id)
        kind = TaskType.query.filter_by(organization_id=org.id).first()
        db.session.add(Task(organization_id=org.id, contact_id=contact.id, assigned_to_id=user.id,
                            created_by_id=user.id, type_id=kind.id, subtype_id=TaskSubtype.query.filter_by(task_type_id=kind.id).first().id, subject='Call Casey', priority='high',
                            status='pending', due_date=datetime.utcnow() + timedelta(days=1)))
        db.session.commit()
        with app.test_request_context():
            urls = {name: url_for(endpoint) for name, endpoint in [
                ('dashboard', 'main.dashboard'), ('contacts', 'main.contacts'),
                ('transactions', 'transactions.list_transactions'), ('tasks', 'tasks.tasks'),
                ('contact-form', 'contacts.create_contact'), ('task-form', 'tasks.create_task'),
                ('profile', 'auth.view_user_profile'), ('settings', 'org.settings'),
                ('inbox', 'inbound_email.inbox_home'), ('reports', 'reports.landing')]}
            urls.update(contact=url_for('contacts.view_contact', contact_id=contact.id),
                        transaction=url_for('transactions.view_transaction', id=tx.id),
                        review=url_for('transactions.document_review_workspace', id=tx.id, doc_id=doc.id),
                        pdf=url_for('transactions.view_document_pdf', id=tx.id, doc_id=doc.id),
                        portal=url_for('portal.home', token=access.token))
        print('SEED=' + json.dumps(urls))


def pdf_bytes():
    import fitz
    with fitz.open() as document:
        for number in range(1, 31):
            page = document.new_page(width=612, height=792)
            page.insert_text((72, 90), f'Responsive contract / page {number}', fontsize=20)
            page.insert_text((72, 130), '742 Evergreen Terrace. Test document for layout verification.', fontsize=12)
        return document.tobytes()


def assert_fits(page, label):
    result = page.evaluate('''() => {
      const root = document.documentElement;
      const main = document.querySelector('#mainContent');
      return {width: innerWidth, document: root.scrollWidth,
        main: main ? main.scrollWidth : 0, mainWidth: main ? main.clientWidth : 0, offenders: [...document.querySelectorAll('main *')].filter(el => el.getBoundingClientRect().right > innerWidth + 1).slice(0, 12).map(el => [el.tagName, el.className, el.getBoundingClientRect().width])};
    }''')
    assert result['document'] <= result['width'] + 1, (label, result)
    assert result['main'] <= result['mainWidth'] + 1, (label, result)


def run(browser_names, layouts=True):
    from playwright.sync_api import sync_playwright, expect
    server = ManagedAppServer()
    env = dict(os.environ, DATABASE_URL=server.database_url, SECRET_KEY='responsive-test-secret', FLASK_ENV='testing')
    for key in ('SENDGRID_API_KEY', 'MAIL_PASSWORD', 'MAIL_USERNAME', 'OPENAI_API_KEY', 'RENTCAST_API_KEY',
                'SUPABASE_KEY', 'SUPABASE_URL', 'REPLIERS_API_KEY', 'POSTHOG_PROJECT_TOKEN', 'TELEGRAM_BOT_TOKEN'):
        env[key] = ''
    os.environ.update(env)
    seeded = subprocess.run([sys.executable, __file__, '--seed'], cwd=ROOT, env=env, text=True, capture_output=True)
    if seeded.returncode: raise RuntimeError(seeded.stdout + seeded.stderr)
    urls = json.loads(next(line[5:] for line in seeded.stdout.splitlines() if line.startswith('SEED=')))
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    metrics = []
    try:
        base = server.start()
        with sync_playwright() as pw:
            for name in browser_names:
                browser = getattr(pw, name).launch()
                context = browser.new_context(viewport={'width': 1440, 'height': 900}, has_touch=True,
                                              reduced_motion='reduce')
                context.route('**' + urls['pdf'], lambda route: route.fulfill(content_type='application/pdf', body=pdf_bytes()))
                page = context.new_page(); page.set_default_timeout(12000)
                errors = []
                page.on('pageerror', lambda exc: errors.append(str(exc)))
                try:
                    page.goto(base + '/login')
                    page.locator('input[name="username"]').fill('responsive@example.com')
                    page.locator('input[name="password"]').fill('responsive-test-password')
                    page.locator('form').filter(has=page.locator('input[name="username"]')).locator('button[type="submit"]').click()
                    page.wait_for_url('**/dashboard**')
                    for route_name, path in (urls.items() if layouts else []):
                        if route_name in ('pdf', 'review'): continue
                        response = page.goto(base + path)
                        if route_name == 'reports':
                            assert response.status == 403, 'Reports must remain disabled'
                            continue
                        assert response.status == 200, (route_name, response.status)
                        page.wait_for_timeout(180)
                        for width, height in SIZES:
                            page.set_viewport_size({'width': width, 'height': height})
                            page.wait_for_timeout(100)
                            assert_fits(page, (name, route_name, width))
                        print(f'{name}: {route_name} fits {len(SIZES)} sizes', flush=True)
                    # Search survives switching between both displays and desktop.
                    page.goto(base + urls['contacts'])
                    page.set_viewport_size({'width': 382, 'height': 580})
                    page.locator('#crmMobileSearchToggle').click()
                    page.locator('#crmGlobalSearchInput').fill('Casey')
                    expect(page.locator('#crmGlobalSearchResults')).to_contain_text('Casey Responsive')
                    for width, height in [(951, 585), (669, 867), (1440, 900), (385, 571)]:
                        page.set_viewport_size({'width': width, 'height': height})
                        expect(page.locator('#crmGlobalSearchInput')).to_be_visible()
                        expect(page.locator('#crmGlobalSearchInput')).to_have_value('Casey')
                        assert_fits(page, (name, 'search', width))
                    page.locator('#crmMobileSearchClose').click()
                    # Open preview on desktop, close it after folding, keep the contact selected.
                    page.set_viewport_size({'width': 1440, 'height': 900})
                    page.locator('[data-contacts-page-target="row"]').first.click()
                    expect(page.locator('.crm-rail')).to_contain_text('Casey')
                    page.set_viewport_size({'width': 382, 'height': 580})
                    page.locator('.crm-rail__close').click()
                    expect(page.locator('.crm-contact-cards')).to_be_visible()
                    # Unsaved form values remain unchanged while width and height change.
                    page.goto(base + urls['contact-form'])
                    page.locator('#first_name').fill('Unsaved Riley')
                    for width, height in SIZES:
                        page.set_viewport_size({'width': width, 'height': height})
                        expect(page.locator('#first_name')).to_have_value('Unsaved Riley')
                    # Keep an assistant draft through folding and a short keyboard-sized viewport.
                    page.set_viewport_size({'width': 382, 'height': 580})
                    page.locator('#bob-toggle-mobile').click()
                    page.locator('#bob-textarea').fill('Draft about Casey, not sent')
                    for width, height in [(382, 300), (951, 585), (669, 867), (390, 844)]:
                        page.set_viewport_size({'width': width, 'height': height})
                        expect(page.locator('#bob-textarea')).to_have_value('Draft about Casey, not sent')
                        expect(page.locator('#bob-close-btn')).to_be_in_viewport()
                    page.locator('#bob-close-btn').click()
                    # Exercise the actual PDF controller with a long local PDF.
                    page.goto(base + urls['review'])
                    expect(page.locator('.drw-page')).to_have_count(30)
                    page.wait_for_function("document.querySelector('.drw-page canvas') !== null")
                    pane = page.locator('[data-document-review-workspace-target="pdfPane"]')
                    initial_canvases = page.locator('.drw-page canvas').count()
                    assert initial_canvases < 10, initial_canvases
                    for width, height in SIZES:
                        page.set_viewport_size({'width': width, 'height': height})
                        page.wait_for_timeout(400)
                        assert_fits(page, (name, 'review', width))
                        assert pane.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1'), (name, 'pdf width', width)
                    for _ in range(5): page.get_by_role('button', name='Next page', exact=True).click()
                    page.wait_for_timeout(500)
                    before = page.locator('[data-document-review-workspace-target="pageLabel"]').inner_text()
                    page.set_viewport_size({'width': 382, 'height': 580})
                    page.wait_for_timeout(500)
                    after = page.locator('[data-document-review-workspace-target="pageLabel"]').inner_text()
                    assert before == after, (name, 'reading position', before, after)
                    metrics.append({'browser': name, 'sizes': len(SIZES), 'pdf_pages': 30,
                                    'initial_canvases': initial_canvases, 'retained_page': after})
                    for width, height in [(382, 580), (669, 867), (951, 585), (1440, 900)]:
                        page.set_viewport_size({'width': width, 'height': height}); page.wait_for_timeout(400)
                        page.screenshot(path=str(ARTIFACTS / f'{name}-review-{width}.png'))
                    for theme in ['light', 'dark']:
                        page.goto(base + urls['transactions'])
                        page.evaluate('(theme) => document.documentElement.dataset.theme = theme', theme)
                        for width, height in [(382, 580), (669, 867), (951, 585), (1440, 900)]:
                            page.set_viewport_size({'width': width, 'height': height}); page.wait_for_timeout(150)
                            assert_fits(page, (name, theme, width))
                            page.screenshot(path=str(ARTIFACTS / f'{name}-transactions-{theme}-{width}.png'))
                    # Public navigation stays scrollable in short landscape.
                    page.goto(base + '/logout'); page.goto(base + '/')
                    for width, height in SIZES:
                        page.set_viewport_size({'width': width, 'height': height})
                        page.wait_for_timeout(100)
                        assert_fits(page, (name, 'public homepage', width))
                    page.set_viewport_size({'width': 594, 'height': 368})
                    page.locator('#mobileMenuBtn').click()
                    page.locator('#mobileMenu a').last.scroll_into_view_if_needed()
                    expect(page.locator('#mobileMenu a').last).to_be_in_viewport()
                    assert not errors, errors
                    print(f'{name}: search, contact preview, drafts, PDF and public menu passed', flush=True)
                except Exception:
                    page.screenshot(path=str(ARTIFACTS / f'{name}-failure.png'))
                    (ARTIFACTS / f'{name}-failure.html').write_text(page.content())
                    raise
                finally:
                    context.close(); browser.close()
    finally:
        (ARTIFACTS / 'metrics.json').write_text(json.dumps(metrics, indent=2))
        server.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', action='store_true')
    parser.add_argument('--workflows-only', action='store_true')
    parser.add_argument('--browsers', nargs='+', default=['chromium', 'webkit'])
    args = parser.parse_args()
    if args.seed: seed()
    else: run(args.browsers, layouts=not args.workflows_only)
