"""OAuth scopes and how they map onto B.O.B. tools."""
from __future__ import annotations

from services.bob_tools.context import RISK_HIGH_WRITE, RISK_LOW_WRITE, RISK_READ
from services.bob_tools.registry import TOOLS_BY_NAME

SCOPE_READ = 'read'
SCOPE_WRITE = 'write'
SCOPE_DESTRUCTIVE = 'destructive'
SCOPE_OFFLINE = 'offline_access'

ALL_SCOPES = (SCOPE_READ, SCOPE_WRITE, SCOPE_DESTRUCTIVE, SCOPE_OFFLINE)
DEFAULT_CONSENT_SCOPES = (SCOPE_READ, SCOPE_WRITE, SCOPE_OFFLINE)

SCOPE_LABELS = {
    SCOPE_READ: 'Look up contacts, tasks, todos, agenda, and deals you can already open',
    SCOPE_WRITE: 'Create, update, log, and complete the same records you can in the app',
    SCOPE_DESTRUCTIVE: 'Permanently delete contacts or tasks',
    SCOPE_OFFLINE: 'Stay connected until you revoke access',
}

ATTACHMENT_TOOLS = frozenset({'inspect_attachment', 'import_contacts'})
DESTRUCTIVE_TOOLS = frozenset({'delete_contact', 'delete_task'})
MCP_ONLY_TOOLS = frozenset({'whoami', 'get_capabilities'})


def normalize_scopes(raw) -> list[str]:
    if raw is None:
        return list(DEFAULT_CONSENT_SCOPES)
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.replace(',', ' ').split() if part.strip()]
    else:
        parts = [str(part).strip() for part in raw if str(part).strip()]
    seen = []
    for part in parts:
        if part in ALL_SCOPES and part not in seen:
            seen.append(part)
    if SCOPE_READ not in seen and (SCOPE_WRITE in seen or SCOPE_DESTRUCTIVE in seen):
        seen.insert(0, SCOPE_READ)
    return seen


def scope_for_tool(name: str) -> str:
    if name in MCP_ONLY_TOOLS:
        return SCOPE_READ
    if name in DESTRUCTIVE_TOOLS:
        return SCOPE_DESTRUCTIVE
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return SCOPE_READ
    if tool.risk in (RISK_LOW_WRITE, RISK_HIGH_WRITE):
        return SCOPE_WRITE
    return SCOPE_READ


def tool_allowed(name: str, scopes: list[str] | tuple[str, ...]) -> bool:
    needed = scope_for_tool(name)
    if needed == SCOPE_READ:
        return SCOPE_READ in scopes or SCOPE_WRITE in scopes
    if needed == SCOPE_WRITE:
        return SCOPE_WRITE in scopes
    if needed == SCOPE_DESTRUCTIVE:
        return SCOPE_DESTRUCTIVE in scopes
    return False


def annotations_for_tool(name: str) -> dict:
    tool = TOOLS_BY_NAME.get(name)
    destructive = name in DESTRUCTIVE_TOOLS
    read_only = tool is not None and tool.risk == RISK_READ and name not in {'select_transaction_context'}
    return {
        'readOnlyHint': read_only,
        'destructiveHint': destructive,
        'idempotentHint': read_only,
        'openWorldHint': False,
    }
