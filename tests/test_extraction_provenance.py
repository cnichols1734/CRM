"""Extraction must record where on the page each value came from."""

from services.document_extractor import (
    META_FIELD_KEY,
    _normalize_meta,
    field_provenance,
    visible_field_data,
)
from services.document_review import _page_hint, _quote_hint


def test_visible_field_data_hides_private_keys():
    field_data = {
        'sales_price': 412000,
        'closing_date': '2026-04-15',
        META_FIELD_KEY: {'sales_price': {'page': 2}},
    }
    assert visible_field_data(field_data) == {
        'sales_price': 412000,
        'closing_date': '2026-04-15',
    }
    assert visible_field_data(None) == {}
    assert visible_field_data('not a dict') == {}


def test_field_provenance_reads_the_meta_block():
    assert field_provenance({META_FIELD_KEY: {'a': {'page': 1}}}) == {'a': {'page': 1}}
    assert field_provenance({'a': 1}) == {}
    assert field_provenance(None) == {}


def test_normalize_meta_keeps_only_extracted_fields_and_clean_values():
    raw = {
        'sales_price': {'page': '3', 'quote': '  Sales Price $412,000  ', 'confidence': 0.9123},
        'closing_date': {'page': 0, 'quote': '', 'confidence': 5},
        'hallucinated_field': {'page': 1, 'quote': 'nope'},
        'bad_shape': 'not a dict',
    }
    normalized = _normalize_meta(raw, {'sales_price', 'closing_date', 'bad_shape'})

    assert normalized['sales_price'] == {
        'page': 3,
        'quote': 'Sales Price $412,000',
        'confidence': 0.912,
    }
    assert 'hallucinated_field' not in normalized
    assert 'bad_shape' not in normalized
    assert 'closing_date' not in normalized


def test_normalize_meta_truncates_long_quotes():
    raw = {'sales_price': {'page': 1, 'quote': 'x' * 400}}
    normalized = _normalize_meta(raw, {'sales_price'})
    assert len(normalized['sales_price']['quote']) == 120


def test_normalize_meta_tolerates_garbage():
    assert _normalize_meta(None, {'a'}) == {}
    assert _normalize_meta('nope', {'a'}) == {}
    assert _normalize_meta({'a': {}}, {'a'}) == {}


def test_review_hints_read_provenance():
    meta = {'closing_date': {'page': 9, 'quote': 'on or before April 15, 2026'}}
    assert _page_hint(meta, 'closing_date') == 9
    assert _quote_hint(meta, 'closing_date') == 'on or before April 15, 2026'
    assert _page_hint(meta, 'missing') is None
    assert _quote_hint(meta, 'missing') is None
    assert _quote_hint({'a': {'quote': '   '}}, 'a') is None
