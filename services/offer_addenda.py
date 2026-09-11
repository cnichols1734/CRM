"""Which addenda ride along with an offer.

The extraction pipeline records an addendum in several places depending on how
the packet arrived: a key under ``terms_summary['addenda']`` when the AI read
it inside a combined PDF, an entry in ``detected_documents`` when the splitter
segmented the packet, a typed ``SellerOfferDocument`` when the agent uploaded
it on its own, and for a few addenda a boolean column of their own. Anything
that wants a Yes/No answer reads all of them through here so the client email
and the offer screen never disagree.
"""

from __future__ import annotations

from typing import Any, Optional

SALE_OF_OTHER_PROPERTY = 'sale_of_other_property'
NON_REALTY_ITEMS = 'non_realty_items'

# Every spelling each pipeline stage uses for the same addendum.
_SIGNALS: dict[str, dict[str, tuple[str, ...]]] = {
    SALE_OF_OTHER_PROPERTY: {
        'addenda_keys': ('sale_of_other_property_addendum', 'sale_of_other_property'),
        'document_types': ('sale_of_other_property', 'sale_of_other_property_addendum'),
        'flags': ('sale_of_other_property_contingency',),
    },
    NON_REALTY_ITEMS: {
        'addenda_keys': ('non_realty_items_addendum', 'non_realty_items'),
        'document_types': ('non_realty_items', 'non_realty_items_addendum'),
        'flags': (),
    },
}

_TRUE_WORDS = frozenset({'1', 'true', 'yes', 'y', 'on', 'present', 'included', 'attached'})
_FALSE_WORDS = frozenset({'0', 'false', 'no', 'n', 'off', 'none', 'null', 'absent', 'not included'})


def has_addendum(offer, key: str) -> bool:
    """True when any stage of the pipeline saw the addendum on this offer."""
    signals = _SIGNALS.get(key)
    if signals is None or offer is None:
        return False

    for column in signals['flags']:
        if _truthy(getattr(offer, column, None)):
            return True

    summary = _summary(offer)
    for column in signals['flags']:
        if _truthy(summary.get(column)):
            return True

    addenda = summary.get('addenda')
    if isinstance(addenda, dict):
        for name in signals['addenda_keys']:
            if _present(addenda.get(name)):
                return True

    supporting = summary.get('supporting_documents')
    if isinstance(supporting, dict):
        for name in signals['document_types']:
            if _present(supporting.get(name)):
                return True

    wanted = set(signals['document_types'])
    for entry in _as_list(summary.get('detected_documents')):
        if isinstance(entry, dict) and (entry.get('document_type') or '') in wanted:
            return True
    for label in _as_list(summary.get('detected_document_types')):
        if isinstance(label, str) and label in wanted:
            return True

    for document in _offer_documents(offer):
        if (getattr(document, 'document_type', None) or '') in wanted:
            return True

    return False


def non_realty_items(offer) -> Optional[str]:
    """What the buyer asked to take with the house, one item per line.

    The column wins because the agent may have corrected it. The JSON bags
    fill in for offers extracted before the column existed.
    """
    if offer is None:
        return None
    direct = _lines(getattr(offer, 'non_realty_items', None))
    if direct:
        return direct

    summary = _summary(offer)
    direct = _lines(summary.get('non_realty_items'))
    if direct:
        return direct

    addenda = summary.get('addenda')
    if isinstance(addenda, dict):
        for name in _SIGNALS[NON_REALTY_ITEMS]['addenda_keys']:
            found = _lines(_items_of(addenda.get(name)))
            if found:
                return found

    supporting = summary.get('supporting_documents')
    if isinstance(supporting, dict):
        for name in _SIGNALS[NON_REALTY_ITEMS]['document_types']:
            found = _lines(_items_of(supporting.get(name)))
            if found:
                return found
    return None


def normalize_items(value: Any) -> Optional[str]:
    """Newline-joined text for the column, from a list, a dict, or prose."""
    return _lines(_items_of(value))


# ---------------------------------------------------------------------------
# Reading the shapes the extractor produces
# ---------------------------------------------------------------------------

def _summary(offer) -> dict:
    summary = getattr(offer, 'terms_summary', None)
    return summary if isinstance(summary, dict) else {}


def _offer_documents(offer) -> list:
    documents = getattr(offer, 'offer_documents', None)
    if documents is None:
        return []
    try:
        if hasattr(documents, 'all'):
            return list(documents.all())
        return list(documents)
    except Exception:
        return []


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _present(value) -> bool:
    """An addendum entry counts when it carries anything at all.

    The extractor returns null for an addendum it did not see, a dict of
    fields for one it did, and occasionally a bare true/false.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        if not value:
            return False
        for flag in ('present', 'included', 'attached'):
            if flag in value:
                return _truthy(value.get(flag))
        return any(item not in (None, '', [], {}) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(item not in (None, '') for item in value)
    return _truthy(value)


def _truthy(value) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in _FALSE_WORDS:
        return False
    if text in _TRUE_WORDS:
        return True
    return False


def _items_of(value):
    """Pull the item list out of whatever shape the addendum came back in."""
    if isinstance(value, dict):
        for name in ('items', 'non_realty_items', 'personal_property', 'list'):
            if name in value and value[name] not in (None, '', [], {}):
                return value[name]
        # A dict with no recognizable list is the addendum's own fields.
        # Nothing there reads as "what the buyer asked for".
        return None
    return value


def _lines(value) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple)):
        parts = [str(item).strip() for item in value if item not in (None, '')]
        parts = [part for part in parts if part]
        return '\n'.join(parts) if parts else None
    if isinstance(value, dict):
        return _lines(_items_of(value))
    text = str(value).strip()
    if not text or text.lower() in _FALSE_WORDS:
        return None
    return text
