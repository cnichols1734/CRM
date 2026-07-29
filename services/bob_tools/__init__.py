"""B.O.B.'s CRM tool layer.

Transport-agnostic on purpose: handlers take a ``BobContext`` and never touch
``current_user``, ``request``, or ``session``, so the in-app chat and a future
SMS webhook can share the same tools and the same safety policy.
"""
from services.bob_tools.context import (
    RISK_HIGH_WRITE,
    RISK_LOW_WRITE,
    RISK_READ,
    BobContext,
    ToolResult,
)
from services.bob_tools.registry import (
    TOOLS,
    TOOLS_BY_NAME,
    confirm_action,
    dispatch,
    openai_tool_schemas,
    reject_action,
    undo_action,
)

__all__ = [
    'BobContext',
    'ToolResult',
    'RISK_READ',
    'RISK_LOW_WRITE',
    'RISK_HIGH_WRITE',
    'TOOLS',
    'TOOLS_BY_NAME',
    'dispatch',
    'confirm_action',
    'reject_action',
    'undo_action',
    'openai_tool_schemas',
]
