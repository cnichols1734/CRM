"""Preview-only edit marks for the template studio.

Real sends never set the editing context, so contenteditable attributes cannot
leak into mail that goes out. The studio sets the context per block while
rendering the preview HTML, then reads the same data attributes back into JSON.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EditState:
    enabled: bool
    index: int


_STATE: contextvars.ContextVar[Optional[EditState]] = contextvars.ContextVar(
    'marketing_edit_state', default=None,
)

EDIT_CSS = '''
    [data-mkt-edit] { cursor: text; }
    [data-mkt-edit]:hover { background-color: rgba(249, 115, 22, 0.08); }
    [data-mkt-edit]:focus { background-color: rgba(249, 115, 22, 0.12); }
    [data-mkt-merge] {
        background-color: #fff7ed !important;
        color: #c2410c !important;
        border: 1px solid #fdba74 !important;
        border-radius: 4px;
        padding: 1px 6px;
        font-weight: 700 !important;
        letter-spacing: 0.01em;
        white-space: nowrap;
        cursor: default;
    }
    .hero-pad [data-mkt-merge] {
        background-color: #f97316 !important;
        color: #ffffff !important;
        border-color: #ea580c !important;
    }
    [data-mkt-merge][data-mkt-filled] {
        background: transparent !important;
        color: inherit !important;
        border: 0 !important;
        padding: 0;
        font-weight: inherit !important;
        letter-spacing: inherit;
    }
'''


def push(enabled: bool, index: int) -> contextvars.Token:
    return _STATE.set(EditState(bool(enabled), int(index)))


def pop(token: contextvars.Token) -> None:
    _STATE.reset(token)


def current() -> Optional[EditState]:
    return _STATE.get()


def mark(
    escaped: str,
    field: str,
    *,
    item: Optional[int] = None,
    key: Optional[str] = None,
) -> str:
    """Wrap already-escaped text so the studio can edit it in place."""
    state = current()
    if not state or not state.enabled:
        return escaped
    extra = ''
    if item is not None:
        extra += f' data-mkt-item="{int(item)}"'
    if key:
        extra += f' data-mkt-key="{key}"'
    return (
        f'<span contenteditable="true" data-mkt-edit="1" '
        f'data-mkt-block="{state.index}" data-mkt-field="{field}"{extra} '
        f'spellcheck="true" style="outline:none;">{escaped}</span>'
    )


def wrap_html(inner_html: str, field: str) -> str:
    """Wrap a block of already-rendered HTML as one editable field."""
    state = current()
    if not state or not state.enabled:
        return inner_html
    return (
        f'<div contenteditable="true" data-mkt-edit="1" '
        f'data-mkt-block="{state.index}" data-mkt-field="{field}" '
        f'spellcheck="true" style="outline:none;">{inner_html}</div>'
    )
