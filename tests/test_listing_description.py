"""Listing description prompt, copy cleanup, and file-fact wiring."""
from types import SimpleNamespace

from services.ai_service import _web_search_tools
from services.listing_description import (
    LISTING_DESCRIPTION_SYSTEM_PROMPT,
    build_listing_description_user_prompt,
    collect_listing_description_facts,
    sanitize_listing_copy,
    web_search_location,
)


def test_listing_prompt_bans_ai_slop_and_requires_file_then_search():
    prompt = LISTING_DESCRIPTION_SYSTEM_PROMPT
    assert 'listing agreement' in prompt.lower()
    assert 'supporting documents' in prompt.lower()
    assert 'web search' in prompt.lower()
    assert 'File facts win' in prompt
    assert 'em dashes' in prompt
    assert 'nestled' in prompt
    assert 'stunning' in prompt
    assert '120 to 180' in prompt
    assert 'THE HOUSE COMES FIRST' in prompt
    assert 'Never mention financing' in prompt
    assert 'this property pairs' in prompt
    assert 'designated flood hazard area' in prompt
    assert 'mandatory homeowners association' in prompt
    assert 'If I removed the subdivision name and city' in prompt


def test_user_prompt_puts_file_facts_ahead_of_web_search():
    facts = {
        'address': '6004 Lakeside Ct',
        'city': 'Houston',
        'state': 'TX',
        'listing_agreement': {'special_provisions': 'Seller to provide existing survey.'},
        'property': {'bedrooms': 4, 'bathrooms': 3},
    }
    prompt = build_listing_description_user_prompt(facts)
    assert 'Write MLS public remarks for 6004 Lakeside Ct' in prompt
    assert 'listing agreement' in prompt.lower()
    assert 'supporting documents' in prompt
    assert 'File facts win' in prompt
    assert 'The house comes first' in prompt
    assert 'listing_agreement.special_provisions: Seller to provide existing survey.' in prompt
    assert 'property.bedrooms: 4' in prompt
    assert 'city: Houston' in prompt
    assert 'financing_types' not in prompt


def test_sanitize_listing_copy_strips_dashes_and_markup():
    raw = '"**Welcome home** — 4 beds – 3 baths,  nestled  on the lot"'
    cleaned = sanitize_listing_copy(raw)
    assert '—' not in cleaned
    assert '–' not in cleaned
    assert '**' not in cleaned
    assert cleaned.startswith('Welcome home')
    assert '4 beds - 3 baths' in cleaned
    assert '  ' not in cleaned


def test_collect_facts_prefers_full_address_and_rentcast():
    tx = SimpleNamespace(
        full_address='6004 Lakeside Ct, Houston, TX 77080',
        street_address='6004 Lakeside Ct',
        city='Houston',
        state='TX',
        zip_code='77080',
        intake_data={'notes': 'pool'},
        rentcast_data={'bedrooms': 4, 'squareFootage': 2400, 'features': []},
    )
    facts = collect_listing_description_facts(tx, {'list_price': 450000, 'has_hoa': True})
    assert facts['address'].startswith('6004 Lakeside')
    assert facts['listing']['list_price'] == 450000
    assert facts['listing']['has_hoa'] is True
    assert facts['property']['bedrooms'] == 4
    assert 'features' not in facts['property']
    loc = web_search_location(facts)
    assert loc == {'country': 'US', 'city': 'Houston', 'state': 'TX'}


def test_collect_facts_includes_listing_agreement_and_supporting_docs():
    tx = SimpleNamespace(
        full_address='14046 Wolftrap Lane, Conroe, TX 77384',
        street_address='14046 Wolftrap Lane',
        city='Conroe',
        state='TX',
        zip_code='77384',
        intake_data={'has_hoa': True},
        rentcast_data=None,
    )
    listing_doc = SimpleNamespace(
        template_slug='listing-agreement',
        template_name='Listing Agreement',
        is_placeholder=False,
        field_data={
            'financing_types': 'Conventional, VA, FHA, Cash',
            'flood_hazard': True,
            'special_provisions': 'Seller to provide existing survey.',
            'detected_documents': [{'document_type': 'listing_agreement'}],
            'total_commission': '6',
        },
    )
    disclosure = SimpleNamespace(
        template_slug='sellers-disclosure',
        template_name="Seller's Disclosure",
        is_placeholder=False,
        field_data={
            'roof_type': 'Composition',
            'previous_flooding': False,
            'seller_names': 'Jane Seller',
        },
    )
    skip_other = SimpleNamespace(
        template_slug='iabs',
        template_name='IABS',
        is_placeholder=False,
        field_data={'form_date': '2026-01-01'},
    )
    facts = collect_listing_description_facts(
        tx,
        {'list_price': '$525,000', 'financing_types': 'Conventional, VA, FHA, Cash'},
        documents=[listing_doc, disclosure, skip_other],
    )
    assert 'financing_types' not in facts.get('listing', {})
    assert 'financing_types' not in facts['listing_agreement']
    assert facts['listing_agreement']['flood_hazard'] is True
    assert facts['listing_agreement']['special_provisions'] == 'Seller to provide existing survey.'
    assert 'detected_documents' not in facts['listing_agreement']
    assert 'total_commission' not in facts['listing_agreement']
    assert facts['supporting_documents']["Seller's Disclosure"]['roof_type'] == 'Composition'
    assert 'seller_names' not in facts['supporting_documents']["Seller's Disclosure"]
    assert 'IABS' not in facts['supporting_documents']
    prompt = build_listing_description_user_prompt(facts)
    assert 'listing_agreement.financing_types' not in prompt
    assert 'Conventional, VA, FHA, Cash' not in prompt
    assert 'Composition' in prompt


def test_web_search_tools_include_location():
    tools = _web_search_tools({'city': 'Houston', 'state': 'TX'})
    assert tools == [{
        'type': 'web_search',
        'user_location': {
            'type': 'approximate',
            'country': 'US',
            'city': 'Houston',
            'region': 'TX',
        },
    }]
