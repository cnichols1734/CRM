"""Offer summary email: copy, matrix, overrides, render, and send guard."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from services import offer_summary_email as ose


class FakeType:
    def __init__(self, name):
        self.name = name


class FakeContact:
    def __init__(self, first_name, email=None):
        self.first_name = first_name
        self.last_name = 'Nichols'
        self.email = email


class FakeParticipant:
    def __init__(self, role, name, email=None, is_primary=True, contact=None):
        self.role = role
        self.name = name
        self.email = email
        self.is_primary = is_primary
        self.contact = contact
        self.user = None

    @property
    def display_name(self):
        if self.contact:
            return f'{self.contact.first_name} {self.contact.last_name}'
        return self.name


class FakeTransaction:
    def __init__(self, side='seller', participants=None):
        self.id = 7
        self.transaction_type = FakeType(side)
        self.street_address = '6048 Heritage Creek Dr'
        self.city = 'Katy'
        self.state = 'TX'
        self.zip_code = '77494'
        self.participants = participants or [
            FakeParticipant('seller', 'Cassie Nichols', 'cassie@origenrealty.com'),
        ]

    @property
    def full_address(self):
        return f'{self.street_address}, {self.city}, {self.state} {self.zip_code}'


class FakeOffer:
    _next_id = 1

    def __init__(self, **kwargs):
        self.id = kwargs.pop('id', None) or FakeOffer._next_id
        FakeOffer._next_id = self.id + 1
        self.status = kwargs.pop('status', 'new')
        self.buyer_names = kwargs.pop('buyer_names', None)
        self.buyer_agent_name = kwargs.pop('buyer_agent_name', None)
        self.buyer_agent_brokerage = kwargs.pop('buyer_agent_brokerage', None)
        self.offer_price = kwargs.pop('offer_price', None)
        self.financing_type = kwargs.pop('financing_type', None)
        self.earnest_money = kwargs.pop('earnest_money', None)
        self.option_fee = kwargs.pop('option_fee', None)
        self.option_period_days = kwargs.pop('option_period_days', None)
        self.seller_concessions_amount = kwargs.pop('seller_concessions_amount', None)
        self.proposed_close_date = kwargs.pop('proposed_close_date', None)
        self.terms_summary = kwargs.pop('terms_summary', {})
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeGmail:
    def __init__(self, email='cassie@gmail.com', sync_enabled=True, needs_reauth=False):
        self.connected_email = email
        self.sync_enabled = sync_enabled
        self.needs_reauth = needs_reauth


class FakeAgent:
    first_name = 'Cassie'
    last_name = 'Nichols'
    email = 'cassie@origenrealty.com'
    phone = '(832) 414-0353'
    email_integration = None


class FakeOrg:
    name = 'Origen Realty'
    broker_name = 'Origen Realty'
    broker_license_number = '9003104'
    broker_address = '123 Main St, Katy, TX 77494'
    logo_url = None


class FakeNetSheet:
    def __init__(self, offer_id, estimated_net):
        self.offer_id = offer_id
        self.estimated_net = estimated_net


def full_offer(**overrides):
    defaults = dict(
        buyer_names='Jordan and Riley Vance',
        buyer_agent_name='Dana Reed',
        buyer_agent_brokerage='Keller Williams',
        offer_price=Decimal('425000'),
        financing_type='conventional',
        earnest_money=Decimal('5000'),
        option_fee=Decimal('300'),
        option_period_days=7,
        seller_concessions_amount=Decimal('4000'),
        proposed_close_date=date(2026, 3, 15),
    )
    defaults.update(overrides)
    return FakeOffer(**defaults)


def build(offers, *, side='seller', net_sheets=None, overrides=None, organization=None):
    if not isinstance(offers, (list, tuple)):
        offers = [offers]
    return ose.build_draft(
        FakeTransaction(side=side),
        offers,
        agent=FakeAgent(),
        organization=organization or FakeOrg(),
        side=side,
        net_sheets=net_sheets,
        overrides=overrides,
    )


# ---------------------------------------------------------------------------
# Single offer
# ---------------------------------------------------------------------------

def test_subject_leads_with_the_address_and_price():
    draft = build(full_offer())
    assert draft.subject == 'New offer on 6048 Heritage Creek Dr: $425,000'


def test_price_and_financing_carry_the_headline_not_a_table_row():
    draft = build(full_offer())
    assert draft.headline['value'] == '$425,000'
    assert draft.headline['caption'] == 'Conventional loan'
    keys = [spec['key'] for spec in draft.row_specs]
    assert 'offer_price' not in keys
    assert 'financing_type' not in keys


def test_remaining_terms_read_in_plain_language():
    draft = build(full_offer())
    block = draft.offers[0]
    assert block.value('earnest_money') == '$5,000'
    assert block.value('option_period') == '7 days, $300 fee'
    assert block.value('proposed_close_date') == 'March 15, 2026'


def test_one_option_day_is_singular():
    draft = build(full_offer(option_period_days=1, option_fee=None))
    assert draft.offers[0].value('option_period') == '1 day'


def test_greeting_uses_the_client_first_name():
    draft = build(full_offer())
    assert draft.greeting == 'Hi Cassie,'


def test_greeting_names_both_clients():
    transaction = FakeTransaction(participants=[
        FakeParticipant('seller', 'Cassie Nichols', 'cassie@origenrealty.com'),
        FakeParticipant('co_seller', 'Chris Nichols', 'chris@origenrealty.com',
                        is_primary=False),
    ])
    draft = ose.build_draft(
        transaction, [full_offer()], agent=FakeAgent(), organization=FakeOrg(),
    )
    assert draft.greeting == 'Hi Cassie and Chris,'


def test_a_summary_built_after_a_terms_edit_shows_the_new_number():
    offer = full_offer()
    first = build(offer)
    offer.offer_price = Decimal('432500')
    offer.proposed_close_date = date(2026, 4, 1)
    second = build(offer)

    assert first.headline['value'] == '$425,000'
    assert second.headline['value'] == '$432,500'
    assert second.offers[0].value('proposed_close_date') == 'April 1, 2026'


def test_terms_summary_fills_a_gap_the_column_left_empty():
    offer = full_offer(offer_price=None, terms_summary={'sales_price': '418000'})
    draft = build(offer)
    assert draft.headline['value'] == '$418,000'


def test_a_thin_offer_says_what_is_missing_instead_of_implying_zero():
    draft = build(FakeOffer(buyer_names='Jordan Vance', offer_price=Decimal('400000')))
    assert draft.footnote == ose.MISSING_TERMS_NOTE
    assert draft.headline['value'] == '$400,000'


def test_a_complete_offer_has_no_footnote():
    assert build(full_offer()).footnote is None


def test_no_price_still_produces_a_usable_subject():
    draft = build(full_offer(offer_price=None))
    assert draft.subject == 'New offer on 6048 Heritage Creek Dr'
    assert draft.headline['value'] == 'Price not set yet'


def test_a_missing_cell_reads_not_set():
    block = ose.OfferBlock(offer_id=1, label='A', sublabel=None, status='new')
    assert block.value('earnest_money') == 'Not set'


def test_legacy_dash_override_does_not_reach_the_client():
    offer = full_offer()
    draft = build(offer, overrides={
        'terms': {str(offer.id): {'earnest_money': '\u2014'}},
    })
    assert draft.offers[0].value('earnest_money') == '$5,000'


def test_decimal_from_display_treats_placeholders_as_empty():
    assert ose._decimal_from_display('Not set') is None
    assert ose._decimal_from_display('-') is None
    assert ose._decimal_from_display('') is None
    assert ose._decimal_from_display('   ') is None
    assert ose._decimal_from_display('\u2014') is None
    assert ose._decimal_from_display('\u2013') is None
    assert ose._decimal_from_display('$425,000') == Decimal('425000')


# ---------------------------------------------------------------------------
# Buyer side
# ---------------------------------------------------------------------------

def test_buyer_side_speaks_for_the_buyer():
    draft = build(full_offer(), side='buyer')
    assert draft.subject.startswith('Your offer on')
    assert draft.intro == "Here's the offer we submitted on 6048 Heritage Creek Dr."
    assert 'hear back' in draft.closing


def test_buyer_side_never_shows_a_seller_net():
    draft = build(
        full_offer(), side='buyer',
        net_sheets={1: FakeNetSheet(1, Decimal('390000'))},
    )
    assert draft.net_available is False
    assert draft.include_net is False


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare_set():
    low = full_offer(
        id=101, buyer_names='Alpha Buyer', offer_price=Decimal('410000'),
        proposed_close_date=date(2026, 3, 1), financing_type='fha',
    )
    high = full_offer(
        id=102, buyer_names='Bravo Buyer', offer_price=Decimal('440000'),
        proposed_close_date=date(2026, 4, 10), financing_type='cash',
    )
    return low, high


def test_comparison_orders_the_strongest_price_first():
    low, high = compare_set()
    draft = build([low, high])
    assert draft.mode == 'compare'
    assert [block.label for block in draft.offers] == ['Bravo Buyer', 'Alpha Buyer']


def test_comparison_subject_shows_the_spread():
    low, high = compare_set()
    draft = build([low, high])
    assert draft.subject == (
        '2 offers on 6048 Heritage Creek Dr: $410,000 to $440,000'
    )


def test_comparison_marks_the_best_price_and_the_soonest_close():
    low, high = compare_set()
    draft = build([low, high])
    by_label = {block.label: block for block in draft.offers}

    assert by_label['Bravo Buyer'].cells['offer_price'].wins is True
    assert by_label['Alpha Buyer'].cells['offer_price'].wins is False
    assert by_label['Alpha Buyer'].cells['proposed_close_date'].wins is True
    assert by_label['Bravo Buyer'].cells['proposed_close_date'].wins is False
    assert draft.has_winners is True


def test_identical_figures_are_not_marked_as_a_winner():
    low, high = compare_set()
    high.offer_price = low.offer_price
    draft = build([low, high])
    assert all(
        not block.cells['offer_price'].wins for block in draft.offers
    )


def test_net_row_appears_for_sellers_and_marks_the_best():
    low, high = compare_set()
    draft = build(
        [low, high],
        net_sheets={
            101: FakeNetSheet(101, Decimal('381000')),
            102: FakeNetSheet(102, Decimal('402500')),
        },
    )
    assert draft.include_net is True
    assert {spec['key'] for spec in draft.row_specs} >= {ose.NET_ROW_KEY}
    by_label = {block.label: block for block in draft.offers}
    assert by_label['Bravo Buyer'].cells[ose.NET_ROW_KEY].value == '$402,500'
    assert by_label['Bravo Buyer'].cells[ose.NET_ROW_KEY].wins is True


def test_the_agent_can_leave_the_net_out():
    low, high = compare_set()
    draft = build(
        [low, high],
        net_sheets={
            101: FakeNetSheet(101, Decimal('381000')),
            102: FakeNetSheet(102, Decimal('402500')),
        },
        overrides={'include_net': False},
    )
    assert draft.net_available is True
    assert draft.include_net is False
    assert ose.NET_ROW_KEY not in {spec['key'] for spec in draft.row_specs}


def test_an_empty_row_is_dropped_from_the_matrix():
    low, high = compare_set()
    low.seller_concessions_amount = None
    high.seller_concessions_amount = None
    draft = build([low, high])
    assert 'seller_concessions_amount' not in {
        spec['key'] for spec in draft.row_specs
    }


def test_withdrawn_offers_are_not_offered_for_a_client_email():
    live = full_offer(id=201)
    dead = full_offer(id=202, status='withdrawn')
    assert [o.id for o in ose.selectable_offers([live, dead])] == [201]


# ---------------------------------------------------------------------------
# Agent overrides
# ---------------------------------------------------------------------------

def test_agent_wording_replaces_ours():
    draft = build(full_offer(), overrides={
        'subject': 'Great news on Heritage Creek',
        'intro': 'Read this one twice.',
        'closing': 'Call me tonight.',
        'note': 'I think we counter at 435.',
    })
    assert draft.subject == 'Great news on Heritage Creek'
    assert draft.intro == 'Read this one twice.'
    assert draft.closing == 'Call me tonight.'
    assert draft.note == 'I think we counter at 435.'


def test_blank_overrides_fall_back_to_generated_copy():
    draft = build(full_offer(), overrides={'subject': '   ', 'intro': ''})
    assert draft.subject == 'New offer on 6048 Heritage Creek Dr: $425,000'
    assert draft.intro.startswith('We received an offer')


def test_an_edited_figure_is_kept_and_flagged():
    offer = full_offer()
    draft = build(offer, overrides={
        'terms': {str(offer.id): {'earnest_money': '$7,500'}},
    })
    cell = draft.offers[0].cells['earnest_money']
    assert cell.value == '$7,500'
    assert cell.edited is True


def test_an_edited_figure_never_gets_the_winner_highlight():
    low, high = compare_set()
    draft = build([low, high], overrides={
        'terms': {'102': {'offer_price': '$999,000'}},
    })
    by_label = {block.label: block for block in draft.offers}
    assert by_label['Bravo Buyer'].cells['offer_price'].edited is True
    assert by_label['Bravo Buyer'].cells['offer_price'].wins is False


def test_retyping_the_same_value_is_not_an_edit():
    offer = full_offer()
    draft = build(offer, overrides={
        'terms': {str(offer.id): {'earnest_money': '$5,000'}},
    })
    assert draft.offers[0].cells['earnest_money'].edited is False


def test_at_least_one_offer_is_required():
    with pytest.raises(ValueError):
        build([])


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------

def test_recipients_come_from_the_client_side_participants():
    transaction = FakeTransaction(participants=[
        FakeParticipant('seller', 'Cassie Nichols', 'cassie@origenrealty.com'),
        FakeParticipant('buyers_agent', 'Dana Reed', 'dana@kw.com'),
        FakeParticipant('title_company', 'Katy Title', 'closing@katytitle.com'),
    ])
    recipients = ose.resolve_recipients(transaction, 'seller')
    assert [r.email for r in recipients] == ['cassie@origenrealty.com']


def test_recipient_email_falls_back_to_the_linked_contact():
    transaction = FakeTransaction(participants=[
        FakeParticipant('seller', None, None,
                        contact=FakeContact('Cassie', 'cassie@origenrealty.com')),
    ])
    recipients = ose.resolve_recipients(transaction, 'seller')
    assert recipients[0].email == 'cassie@origenrealty.com'
    assert recipients[0].name == 'Cassie Nichols'


def test_a_participant_without_an_email_is_skipped():
    transaction = FakeTransaction(participants=[
        FakeParticipant('seller', 'Cassie Nichols', None),
    ])
    assert ose.resolve_recipients(transaction, 'seller') == []


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _assert_no_dash_placeholder(text: str) -> None:
    assert '&mdash;' not in text
    assert '&#8212;' not in text
    assert '\u2014' not in text
    assert '\u2013' not in text


def test_single_offer_html_renders(app):
    draft = build(full_offer())
    with app.app_context():
        html = ose.render_html(draft)
    assert '$425,000' in html
    assert 'Conventional loan' in html
    assert '7 days, $300 fee' in html
    assert 'March 15, 2026' in html
    assert 'Origen Realty' in html
    assert 'Brokerage license #9003104' in html
    # Transactional mail must not carry a marketing opt-out.
    assert 'Unsubscribe' not in html
    _assert_no_dash_placeholder(html)


def test_the_brand_marks_are_absolute_urls(app):
    """A relative src is a broken image once the mail leaves us."""
    draft = build(full_offer())
    with app.app_context():
        html = ose.render_html(draft)
    assert draft.brand['mark_url'].startswith('https://')
    assert draft.brand['mark_url'] in html
    assert draft.brand['wordmark_url'] in html


def test_an_organization_logo_replaces_our_mark(app):
    org = FakeOrg()
    org.logo_url = 'https://cdn.example.com/brokerage.png'
    draft = build(full_offer(), organization=org)
    assert draft.brand['mark_url'] == 'https://cdn.example.com/brokerage.png'
    # We have no idea whether their art reads on the slate footer band.
    assert draft.brand['wordmark_url'] is None
    with app.app_context():
        html = ose.render_html(draft)
    assert 'origen-wordmark' not in html


def test_comparison_html_renders_a_column_per_offer(app):
    low, high = compare_set()
    draft = build(
        [low, high],
        net_sheets={
            101: FakeNetSheet(101, Decimal('381000')),
            102: FakeNetSheet(102, Decimal('402500')),
        },
    )
    with app.app_context():
        html = ose.render_html(draft)
    assert 'Alpha Buyer' in html
    assert 'Bravo Buyer' in html
    assert '$440,000' in html
    assert ose.NET_ROW_LABEL in html
    assert ose.NET_CAVEAT in html
    assert 'Offer comparison' in html
    _assert_no_dash_placeholder(html)


def test_comparison_html_writes_not_set_for_a_gap(app):
    low, high = compare_set()
    low.earnest_money = None
    draft = build([low, high])
    assert draft.offers[1].value('earnest_money') == 'Not set'
    with app.app_context():
        html = ose.render_html(draft)
    assert 'Not set' in html
    _assert_no_dash_placeholder(html)


def test_the_agent_note_survives_into_the_html(app):
    draft = build(full_offer(), overrides={
        'note': 'First line.\n\nSecond line.',
    })
    with app.app_context():
        html = ose.render_html(draft)
    assert 'First line.' in html
    assert 'Second line.' in html


def test_html_escapes_a_hostile_buyer_name(app):
    draft = build(full_offer(buyer_names='<script>alert(1)</script>'))
    with app.app_context():
        html = ose.render_html(draft)
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html


def test_text_alternative_lists_every_row():
    draft = build(full_offer())
    text = ose.render_text(draft)
    assert 'Hi Cassie,' in text
    assert 'Earnest money: $5,000' in text
    assert 'Closing date: March 15, 2026' in text
    assert 'Cassie Nichols' in text
    assert 'Origen Realty' in text


def test_text_alternative_covers_each_offer_in_a_comparison():
    low, high = compare_set()
    text = ose.render_text(build([low, high]))
    assert 'Alpha Buyer' in text
    assert 'Bravo Buyer' in text
    assert text.count('Price:') == 2
    _assert_no_dash_placeholder(text)


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def test_send_is_blocked_under_test_so_no_client_is_ever_mailed(app):
    draft = build(full_offer())
    with app.app_context():
        result = ose.send_draft(
            draft,
            to_emails=['seller@example.com'],
            agent=FakeAgent(),
            organization=FakeOrg(),
            transaction_id=7,
        )
    assert result['sent'] is False
    assert result['skipped'] is True


def test_send_refuses_an_empty_recipient_list(app):
    draft = build(full_offer())
    with app.app_context():
        with pytest.raises(ose.OfferEmailError):
            ose.send_draft(draft, to_emails=['   '])


def test_send_refuses_a_blank_subject(app):
    draft = build(full_offer(), overrides={'subject': 'x'})
    draft.subject = '  '
    with app.app_context():
        with pytest.raises(ose.OfferEmailError):
            ose.send_draft(draft, to_emails=['seller@example.com'])


def test_from_name_puts_the_agent_in_front_of_the_brokerage():
    assert ose._from_name(FakeAgent(), FakeOrg()) == 'Cassie Nichols | Origen Realty'


def test_sender_is_the_linked_gmail_when_one_is_connected(app):
    agent = FakeAgent()
    agent.email_integration = FakeGmail()
    with app.app_context():
        sender = ose.resolve_sender(agent)
    assert sender['via'] == 'gmail'
    assert sender['from_email'] == 'cassie@gmail.com'


def test_sender_falls_back_to_the_brokerage_when_gmail_is_off(app):
    with app.app_context():
        sender = ose.resolve_sender(FakeAgent())
    assert sender['via'] == 'sendgrid'
    assert sender['from_email'] == 'info@origenrealty.com'


def test_send_uses_gmail_when_the_agent_has_it_linked(app, monkeypatch):
    draft = build(full_offer())
    agent = FakeAgent()
    agent.email_integration = FakeGmail()
    called = {}

    def fake_gmail(integration, **kwargs):
        called.update(kwargs)
        called['integration'] = integration
        return {'success': True, 'message_id': 'gmail-1'}

    monkeypatch.setattr(ose, 'skip_outbound_send', lambda _to: False)
    monkeypatch.setattr('services.gmail_service.send_email', fake_gmail)

    with app.app_context():
        result = ose.send_draft(
            draft,
            to_emails=['seller@origenrealty.com'],
            agent=agent,
            organization=FakeOrg(),
        )
    assert result['sent'] is True
    assert result['via'] == 'gmail'
    assert result['from_email'] == 'cassie@gmail.com'
    assert called['include_signature'] is False
    assert called['to_emails'] == ['seller@origenrealty.com']
    assert called['body_html'].startswith('<!DOCTYPE html')
