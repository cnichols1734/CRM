"""Daily briefing tool shared by B.O.B. and MCP."""
from __future__ import annotations

from services.bob_tools.context import BobContext, ToolResult
from services.daily_briefing import build_briefing_context


def get_daily_briefing(args: dict, ctx: BobContext) -> ToolResult:
    payload = build_briefing_context(ctx.user_id, ctx.organization_id)
    overdue = payload.get('overdue') or []
    today = payload.get('due_today') or payload.get('today') or []
    return ToolResult.success(
        f'Daily briefing: {len(overdue)} overdue, {len(today)} due today.',
        payload,
    )
