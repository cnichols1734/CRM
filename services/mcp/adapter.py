"""Map B.O.B. tools onto MCP tools/list and tools/call."""
from __future__ import annotations

from services.bob_tools import (
    CONFIRM_PRECLEARED,
    BobContext,
    dispatch,
    select_tools,
)
from services.bob_tools.context import DEFAULT_TIMEZONE
from services.mcp.instructions import whoami_payload
from services.mcp.scopes import (
    ATTACHMENT_TOOLS,
    annotations_for_tool,
    scope_for_tool,
    tool_allowed,
)

UNTRUSTED_KEYS = frozenset({
    'notes', 'additional_notes', 'note', 'body', 'email_body',
    'description', 'caption', 'text',
})

# In-app B.O.B. previews high-risk writes. MCP executes them (CONFIRM_PRECLEARED).
MCP_DESCRIPTION_OVERRIDES = {
    'append_contact_note': (
        'Add a dated line to a contact\'s notes, keeping everything already '
        'there. This is the right tool for capturing what the agent just '
        'learned, for example "wants a pool" or "pre-approved with Chase". '
        'Applies immediately. Use update_contact only to rewrite or correct '
        'the existing note body.'
    ),
    'update_contact': (
        'Change fields on an existing contact. Applies immediately on this '
        'connector. Only send fields that actually change. To change group '
        'membership use set_contact_groups.'
    ),
    'update_task': (
        'Change an existing task: reschedule it, rename it, change priority, '
        'or cancel it. Applies immediately on this connector. To simply mark '
        'work done, use complete_task instead.'
    ),
    'delete_contact': (
        'Permanently delete a contact along with all of their tasks and '
        'logged activity. Irreversible. Applies immediately if this connection '
        'granted destructive access. Only call this when they clearly asked '
        'to delete the person. For someone who has simply gone quiet, '
        'changing their group is almost always what they actually want.'
    ),
    'delete_task': (
        'Permanently delete a task. Irreversible. Applies immediately if this '
        'connection granted destructive access. If the work happened, '
        'complete_task is the better choice because it preserves the record.'
    ),
}


def mcp_tool_catalog(ctx: BobContext, scopes: list[str]) -> list[dict]:
    tools = []
    tools.extend(_mcp_only_tool_defs())
    for tool in select_tools(ctx):
        if tool.name in ATTACHMENT_TOOLS:
            continue
        if not tool_allowed(tool.name, scopes):
            continue
        tools.append({
            'name': tool.name,
            'title': tool.name.replace('_', ' ').capitalize(),
            'description': MCP_DESCRIPTION_OVERRIDES.get(tool.name, tool.description),
            'inputSchema': tool.parameters or {'type': 'object', 'additionalProperties': False},
            'annotations': annotations_for_tool(tool.name),
        })
    return tools


def grouped_tool_names(ctx: BobContext, scopes: list[str]) -> dict[str, list[str]]:
    groups = {
        'Account': [],
        'Contacts': [],
        'Tasks': [],
        'To-dos': [],
        'Deals': [],
        'Other': [],
    }
    contact_names = {
        'search_contacts', 'count_contacts', 'get_contact', 'list_contact_groups',
        'create_contact', 'update_contact', 'delete_contact', 'set_contact_groups',
        'create_contact_group', 'append_contact_note', 'log_interaction',
    }
    task_names = {
        'get_agenda', 'list_tasks', 'list_task_types', 'create_task',
        'update_task', 'complete_task', 'delete_task',
    }
    todo_names = {'list_todos', 'add_todo', 'complete_todo'}
    for item in mcp_tool_catalog(ctx, scopes):
        name = item['name']
        if name in {'whoami', 'get_capabilities'}:
            groups['Account'].append(name)
        elif name in contact_names:
            groups['Contacts'].append(name)
        elif name in task_names:
            groups['Tasks'].append(name)
        elif name in todo_names:
            groups['To-dos'].append(name)
        elif any(token in name for token in (
            'transaction', 'listing', 'offer', 'requirement', 'deadline',
            'parties', 'documents', 'closing',
        )):
            groups['Deals'].append(name)
        else:
            groups['Other'].append(name)
    return {key: value for key, value in groups.items() if value}


def call_mcp_tool(name: str, arguments: dict, ctx: BobContext, scopes: list[str]) -> dict:
    if name == 'whoami':
        user = ctx.load_user()
        org = user.organization if user else None
        data = whoami_payload(user, org, scopes, timezone=ctx.timezone)
        return _ok(data, summary=f"Acting as {data['name']} in {data.get('organization') or 'AgentFlow'}.")

    if name == 'get_capabilities':
        catalog = mcp_tool_catalog(ctx, scopes)
        data = {
            'scopes': list(scopes),
            'tools': [
                {
                    'name': item['name'],
                    'title': item['title'],
                    'scope': scope_for_tool(item['name']),
                    'description': item['description'][:240],
                }
                for item in catalog
            ],
        }
        return _ok(data, summary=f'{len(catalog)} tools available on this connection.')

    if name in ATTACHMENT_TOOLS:
        return _error('File import is not available over MCP.')
    if not tool_allowed(name, scopes):
        return _error(f'This connection does not include the {scope_for_tool(name)} permission needed for {name}.')

    if name == 'select_transaction_context':
        # Stateless MCP: validate the id by reading the deal, then tell the
        # caller to pass transaction_id on later tools. Do not write Telegram.
        result = dispatch('get_transaction_summary', arguments, ctx)
        payload = mark_untrusted(result.for_model())
        if result.ok:
            payload['message'] = (
                'Transaction is available. Pass this transaction_id on later deal tools.'
            )
        return _from_tool_result(result, payload)

    result = dispatch(
        name, arguments or {}, ctx,
        confirmation=CONFIRM_PRECLEARED,
    )
    return _from_tool_result(result, mark_untrusted(result.for_model()))


def build_context(user, *, timezone: str | None = None) -> BobContext:
    return BobContext.from_user(
        user,
        surface='mcp',
        timezone=timezone or getattr(user, 'timezone', None) or DEFAULT_TIMEZONE,
    )


def mark_untrusted(value):
    if isinstance(value, dict):
        out = {}
        held = {}
        for key, inner in value.items():
            if key in UNTRUSTED_KEYS and isinstance(inner, str) and inner.strip():
                held[key] = inner
            else:
                out[key] = mark_untrusted(inner)
        if held:
            existing = out.get('untrusted_user_content')
            if isinstance(existing, dict):
                existing.update(held)
            else:
                out['untrusted_user_content'] = held
        return out
    if isinstance(value, list):
        return [mark_untrusted(item) for item in value]
    return value


def _mcp_only_tool_defs() -> list[dict]:
    return [
        {
            'name': 'whoami',
            'title': 'Who am I',
            'description': (
                'Return the signed-in AgentFlow user, organization, role, '
                'timezone, granted scopes, and enabled features. Call this '
                'when you need to know who you are acting as.'
            ),
            'inputSchema': {'type': 'object', 'additionalProperties': False},
            'annotations': {
                'readOnlyHint': True,
                'destructiveHint': False,
                'idempotentHint': True,
                'openWorldHint': False,
            },
        },
        {
            'name': 'get_capabilities',
            'title': 'What can I do',
            'description': (
                'List the MCP tools this connection actually granted, after '
                'role, feature flags, and OAuth scopes. Use when a tool seems '
                'missing.'
            ),
            'inputSchema': {'type': 'object', 'additionalProperties': False},
            'annotations': {
                'readOnlyHint': True,
                'destructiveHint': False,
                'idempotentHint': True,
                'openWorldHint': False,
            },
        },
    ]


def _ok(data: dict, *, summary: str) -> dict:
    payload = {'status': 'ok', 'summary': summary, **data}
    return {
        'isError': False,
        'content': [{'type': 'text', 'text': _as_text(payload)}],
        'structuredContent': payload,
    }


def _error(message: str) -> dict:
    payload = {'status': 'error', 'error': message}
    return {
        'isError': True,
        'content': [{'type': 'text', 'text': _as_text(payload)}],
        'structuredContent': payload,
    }


def _from_tool_result(result, payload: dict) -> dict:
    if result.record_url and 'record_url' not in payload:
        payload['record_url'] = result.record_url
    return {
        'isError': not result.ok,
        'content': [{'type': 'text', 'text': _as_text(payload)}],
        'structuredContent': payload,
    }


def _as_text(payload: dict) -> str:
    import json
    return json.dumps(payload, default=str)
