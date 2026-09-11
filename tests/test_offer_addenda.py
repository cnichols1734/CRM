"""Addendum presence and the Non-Realty Items mapping into offer terms."""

from types import SimpleNamespace

from services import offer_addenda
from services.seller_workflow import (
    _inherited_field_data_for_segment,
    _non_realty_items_text,
    _normalized_supporting_payload,
    apply_offer_terms,
    drop_cleared_offer_terms,
    normalize_offer_terms,
)


def offer(**kwargs):
    kwargs.setdefault('terms_summary', {})
    kwargs.setdefault('offer_documents', [])
    kwargs.setdefault('sale_of_other_property_contingency', None)
    kwargs.setdefault('non_realty_items', None)
    return SimpleNamespace(**kwargs)


# ---------------------------------------------------------------------------
# has_addendum
# ---------------------------------------------------------------------------

def test_nothing_recorded_means_absent():
    assert offer_addenda.has_addendum(offer(), offer_addenda.SALE_OF_OTHER_PROPERTY) is False
    assert offer_addenda.has_addendum(offer(), offer_addenda.NON_REALTY_ITEMS) is False


def test_the_contingency_column_counts():
    assert offer_addenda.has_addendum(
        offer(sale_of_other_property_contingency=True), offer_addenda.SALE_OF_OTHER_PROPERTY,
    ) is True


def test_a_false_contingency_column_does_not_count():
    assert offer_addenda.has_addendum(
        offer(sale_of_other_property_contingency=False), offer_addenda.SALE_OF_OTHER_PROPERTY,
    ) is False


def test_an_addenda_entry_with_fields_counts():
    o = offer(terms_summary={'addenda': {'non_realty_items_addendum': {'items': ['Fridge']}}})
    assert offer_addenda.has_addendum(o, offer_addenda.NON_REALTY_ITEMS) is True


def test_a_null_or_empty_addenda_entry_does_not_count():
    for value in (None, {}, [], '', False, 'null', 'none'):
        o = offer(terms_summary={'addenda': {'sale_of_other_property_addendum': value}})
        assert offer_addenda.has_addendum(o, offer_addenda.SALE_OF_OTHER_PROPERTY) is False, value


def test_an_addenda_entry_that_says_present_false_does_not_count():
    o = offer(terms_summary={'addenda': {'sale_of_other_property_addendum': {'present': False}}})
    assert offer_addenda.has_addendum(o, offer_addenda.SALE_OF_OTHER_PROPERTY) is False


def test_a_detected_document_segment_counts():
    o = offer(terms_summary={
        'detected_documents': [{'document_type': 'non_realty_items', 'start_page': 12, 'end_page': 12}],
    })
    assert offer_addenda.has_addendum(o, offer_addenda.NON_REALTY_ITEMS) is True


def test_a_detected_document_type_label_counts():
    o = offer(terms_summary={'detected_document_types': ['residential_contract', 'non_realty_items_addendum']})
    assert offer_addenda.has_addendum(o, offer_addenda.NON_REALTY_ITEMS) is True


def test_a_supporting_document_entry_counts():
    o = offer(terms_summary={'supporting_documents': {'sale_of_other_property': {'deadline_days': 30}}})
    assert offer_addenda.has_addendum(o, offer_addenda.SALE_OF_OTHER_PROPERTY) is True


def test_a_typed_linked_document_counts():
    o = offer(offer_documents=[SimpleNamespace(document_type='sale_of_other_property')])
    assert offer_addenda.has_addendum(o, offer_addenda.SALE_OF_OTHER_PROPERTY) is True


def test_a_dynamic_relationship_is_read_through_all():
    class Query:
        def all(self):
            return [SimpleNamespace(document_type='non_realty_items')]

    assert offer_addenda.has_addendum(offer(offer_documents=Query()), offer_addenda.NON_REALTY_ITEMS) is True


def test_an_unknown_key_is_never_present():
    assert offer_addenda.has_addendum(offer(sale_of_other_property_contingency=True), 'lead_paint') is False


# ---------------------------------------------------------------------------
# non_realty_items
# ---------------------------------------------------------------------------

def test_the_column_wins():
    o = offer(
        non_realty_items='Fridge\nMower',
        terms_summary={'addenda': {'non_realty_items_addendum': {'items': ['Something else']}}},
    )
    assert offer_addenda.non_realty_items(o) == 'Fridge\nMower'


def test_a_top_level_list_is_joined_by_lines():
    o = offer(terms_summary={'non_realty_items': ['Refrigerator', ' Washer and dryer ', '', None]})
    assert offer_addenda.non_realty_items(o) == 'Refrigerator\nWasher and dryer'


def test_the_addenda_bag_fills_in():
    o = offer(terms_summary={'addenda': {'non_realty_items_addendum': {'items': ['Pool equipment'], 'price': '0'}}})
    assert offer_addenda.non_realty_items(o) == 'Pool equipment'


def test_the_supporting_document_bag_fills_in():
    o = offer(terms_summary={'supporting_documents': {'non_realty_items': {'non_realty_items': ['Curtains']}}})
    assert offer_addenda.non_realty_items(o) == 'Curtains'


def test_prose_passes_through():
    o = offer(terms_summary={'non_realty_items': 'Refrigerator and patio furniture'})
    assert offer_addenda.non_realty_items(o) == 'Refrigerator and patio furniture'


def test_nothing_means_none():
    assert offer_addenda.non_realty_items(offer()) is None
    assert offer_addenda.non_realty_items(offer(terms_summary={'non_realty_items': 'none'})) is None
    assert offer_addenda.non_realty_items(offer(terms_summary={'non_realty_items': []})) is None


# ---------------------------------------------------------------------------
# Mapping into the offer
# ---------------------------------------------------------------------------

def test_apply_reads_the_top_level_list():
    assert _non_realty_items_text({'non_realty_items': ['Fridge', 'Mower']}) == 'Fridge\nMower'


def test_apply_reads_the_addenda_bag():
    terms = {'addenda': {'non_realty_items_addendum': {'items': ['Fridge']}}}
    assert _non_realty_items_text(terms) == 'Fridge'


def test_apply_reads_nothing_as_none():
    assert _non_realty_items_text({}) is None
    assert _non_realty_items_text({'non_realty_items': None}) is None


def test_a_standalone_non_realty_upload_lands_in_every_bag():
    payload = _normalized_supporting_payload('non_realty_items', {
        'non_realty_items': ['Refrigerator'],
        'non_realty_items_price': '0',
    })
    assert payload['offer_terms']['non_realty_items'] == ['Refrigerator']
    assert payload['addenda']['non_realty_items_addendum']['items'] == ['Refrigerator']
    assert 'non_realty_items' in payload['supporting_documents']


def test_a_standalone_sale_of_other_property_upload_sets_the_contingency():
    payload = _normalized_supporting_payload('sale_of_other_property', {'deadline_days': 30})
    assert payload['offer_terms']['sale_of_other_property_contingency'] is True
    assert payload['addenda']['sale_of_other_property_addendum'] == {'deadline_days': 30}


def test_a_packaged_sale_addendum_promotes_the_contingency():
    normalized = normalize_offer_terms({
        'addenda': {'sale_of_other_property_addendum': {'deadline_days': 30}},
    })
    assert normalized['sale_of_other_property_contingency'] is True


def test_an_explicit_contingency_answer_is_not_overwritten():
    normalized = normalize_offer_terms({
        'sale_of_other_property_contingency': False,
        'addenda': {'sale_of_other_property_addendum': {'deadline_days': 30}},
    })
    assert normalized['sale_of_other_property_contingency'] is False


def test_present_false_does_not_promote_the_contingency():
    normalized = normalize_offer_terms({
        'addenda': {'sale_of_other_property_addendum': {'present': False}},
    })
    assert normalized.get('sale_of_other_property_contingency') in (None, False)

    from_supporting = normalize_offer_terms({
        'supporting_documents': {'sale_of_other_property': {'present': False}},
    })
    assert from_supporting.get('sale_of_other_property_contingency') in (None, False)


def test_apply_reads_the_supporting_document_bag():
    terms = {'supporting_documents': {'non_realty_items': {'non_realty_items': ['Curtains']}}}
    assert _non_realty_items_text(terms) == 'Curtains'


def test_a_blank_form_value_does_not_fall_back_to_the_addenda_bag():
    terms = {
        'non_realty_items': '',
        'addenda': {'non_realty_items_addendum': {'items': ['Fridge']}},
        'supporting_documents': {'non_realty_items': {'non_realty_items': ['Curtains']}},
    }
    assert _non_realty_items_text(terms) is None


def test_an_empty_column_is_an_explicit_clear():
    o = offer(
        non_realty_items='',
        terms_summary={'addenda': {'non_realty_items_addendum': {'items': ['Fridge']}}},
    )
    assert offer_addenda.non_realty_items(o) is None


def test_drop_cleared_offer_terms_removes_a_blank_title_policy_payer():
    merged = {
        'offer_price': '440000',
        'title_policy_payer': 'Seller',
    }
    drop_cleared_offer_terms(merged, {'title_policy_payer': ''})
    assert 'title_policy_payer' not in merged
    assert merged['offer_price'] == '440000'

    untouched = {'title_policy_payer': 'Seller'}
    drop_cleared_offer_terms(untouched, {'offer_price': '440000'})
    assert untouched['title_policy_payer'] == 'Seller'


def test_apply_offer_terms_clears_title_policy_payer_when_blank():
    offer_row = SimpleNamespace(
        terms_summary={'title_policy_payer': 'Seller'},
        response_deadline_at=None,
        title_policy_payer='Seller',
        non_realty_items=None,
    )
    apply_offer_terms(offer_row, {
        'title_policy_payer': '',
        'offer_price': '440000',
    })
    assert offer_row.title_policy_payer is None
    assert 'title_policy_payer' not in (offer_row.terms_summary or {})


def test_apply_offer_terms_clears_when_the_form_sends_a_blank():
    offer_row = SimpleNamespace(
        terms_summary={'addenda': {'non_realty_items_addendum': {'items': ['Fridge']}}},
        response_deadline_at=None,
        non_realty_items='Fridge',
    )
    apply_offer_terms(offer_row, {
        'non_realty_items': '',
        'addenda': {'non_realty_items_addendum': {'items': ['Fridge']}},
    })
    assert offer_row.non_realty_items == ''
    assert offer_addenda.non_realty_items(offer_row) is None


def test_apply_offer_terms_still_reads_legacy_bags_when_untouched():
    offer_row = SimpleNamespace(
        terms_summary={},
        response_deadline_at=None,
        non_realty_items=None,
    )
    apply_offer_terms(offer_row, {
        'addenda': {'non_realty_items_addendum': {'items': ['Fridge']}},
    })
    assert offer_row.non_realty_items == 'Fridge'

    offer_row.non_realty_items = None
    apply_offer_terms(offer_row, {
        'supporting_documents': {'non_realty_items': {'items': ['Mower']}},
    })
    assert offer_row.non_realty_items == 'Mower'


def test_split_children_inherit_the_new_addenda_payloads():
    parent = {
        'addenda': {
            'non_realty_items_addendum': {'items': ['Fridge'], 'price': '0'},
            'sale_of_other_property_addendum': {'deadline_days': 30},
        },
        'supporting_documents': {
            'non_realty_items': {'non_realty_items': ['Should not win']},
        },
    }
    non_realty = _inherited_field_data_for_segment('non_realty_items', parent)
    sale = _inherited_field_data_for_segment('sale_of_other_property', parent)
    assert non_realty['items'] == ['Fridge']
    assert sale['deadline_days'] == 30

    from_supporting = _inherited_field_data_for_segment('non_realty_items', {
        'supporting_documents': {'non_realty_items': {'non_realty_items': ['Curtains']}},
    })
    assert from_supporting['non_realty_items'] == ['Curtains']

    from_top_level = _inherited_field_data_for_segment('non_realty_items', {
        'non_realty_items': ['Washer'],
        'non_realty_items_price': '0',
    })
    assert from_top_level['non_realty_items'] == ['Washer']
    assert from_top_level['non_realty_items_price'] == '0'
