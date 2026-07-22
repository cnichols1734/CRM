"""
Daily Briefing — context builder, prompt, schema, and generation orchestration.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, date

import pytz
from sqlalchemy import or_, nullsfirst
from sqlalchemy.orm import joinedload

from models import (
    db, User, Contact, Task, Transaction, DailyTodoList,
)
from jobs.base import set_job_org_context
from services.ai_service import generate_structured_response

logger = logging.getLogger(__name__)

USER_TZ = pytz.timezone('America/Chicago')
COLD_DAYS = 30
MAX_TASKS = 25
MAX_COLD = 15
MAX_HOT = 10
MAX_NEW = 10
MAX_TX = 10

BRIEFING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "teaser": {"type": "string"},
        "priorities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "why": {"type": "string"},
                    "action_type": {
                        "type": "string",
                        "enum": ["call", "text", "email", "other"],
                    },
                    "contact_id": {"type": ["integer", "null"]},
                    "task_id": {"type": ["integer", "null"]},
                    "contact_name": {"type": ["string", "null"]},
                    "suggested_script": {"type": ["string", "null"]},
                    "priority": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
                "required": [
                    "id", "title", "why", "action_type", "contact_id",
                    "task_id", "contact_name", "suggested_script", "priority",
                ],
            },
        },
        "reconnect": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "contact_id": {"type": "integer"},
                    "contact_name": {"type": "string"},
                    "days_since_touch": {"type": "integer"},
                    "reason": {"type": "string"},
                    "suggested_message": {"type": "string"},
                    "channel": {
                        "type": "string",
                        "enum": ["call", "text", "email"],
                    },
                },
                "required": [
                    "id", "contact_id", "contact_name", "days_since_touch",
                    "reason", "suggested_message", "channel",
                ],
            },
        },
        "pipeline_watch": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "insight": {"type": "string"},
                    "contact_id": {"type": ["integer", "null"]},
                    "transaction_id": {"type": ["integer", "null"]},
                    "contact_name": {"type": ["string", "null"]},
                    "amount": {"type": ["number", "null"]},
                },
                "required": [
                    "id", "title", "insight", "contact_id",
                    "transaction_id", "contact_name", "amount",
                ],
            },
        },
    },
    "required": ["headline", "teaser", "priorities", "reconnect", "pipeline_watch"],
}

SYSTEM_PROMPT = """You are B.O.B. (Business Optimization Buddy), the agent's sharp daily CRM coach.

Build a focused Daily Briefing from the CRM data provided. This is an operational tool for a real estate agent — not a marketing brainstorm.

Rules:
- Every item MUST reference a real contact_id, task_id, or transaction_id from the data. Never invent people or deals.
- Keep the day realistic: max 5 priorities, max 5 reconnects, max 4 pipeline insights.
- Prefer overdue tasks and truly cold contacts over filler.
- Write like a capable colleague: direct, specific, no hype, no filler, no Houston-hardcoding.
- suggested_script / suggested_message: ready-to-say or ready-to-send, 1–3 sentences, grounded in the contact's notes/objective/timeline when present.
- headline: 2–3 sentences addressing the agent by first name. Call out the sharpest urgency and the best win.
- teaser: short banner line like "3 priorities · 2 going cold" — counts must match the arrays you return.
- priority ids: "p1", "p2", …  reconnect ids: "r1", "r2", …  pipeline ids: "w1", "w2", …
- If a section has nothing real to say, return an empty array for that section. Never pad with generics.
"""


def today_local() -> date:
    return datetime.now(USER_TZ).date()


def build_briefing_context(user_id: int, org_id: int | None) -> dict:
    """Gather org-scoped CRM slice for the briefing model."""
    user = db.session.get(User, user_id)
    first_name = (user.first_name if user else None) or "Agent"
    today = today_local()
    now_utc = datetime.utcnow()
    week_ago = now_utc - timedelta(days=7)
    cold_cutoff = today - timedelta(days=COLD_DAYS)

    task_q = (
        Task.query
        .options(joinedload(Task.contact), joinedload(Task.task_type))
        .filter(
            Task.assigned_to_id == user_id,
            Task.status == 'pending',
            Task.due_date <= now_utc + timedelta(days=7),
        )
    )
    if org_id:
        task_q = task_q.filter(Task.organization_id == org_id)
    tasks = task_q.order_by(Task.due_date.asc()).limit(MAX_TASKS).all()

    overdue, due_today, upcoming = [], [], []
    for task in tasks:
        contact = task.contact
        contact_name = (
            f"{contact.first_name} {contact.last_name}".strip()
            if contact else None
        )
        due = task.due_date.date() if task.due_date else None
        bucket = "upcoming"
        if due and due < today:
            bucket = "overdue"
        elif due and due == today:
            bucket = "today"

        item = {
            "task_id": task.id,
            "subject": task.subject or "",
            "description": (task.description or "")[:300],
            "priority": task.priority or "medium",
            "due_date": due.isoformat() if due else None,
            "status_bucket": bucket,
            "contact_id": contact.id if contact else None,
            "contact_name": contact_name,
            "task_type": task.task_type.name if task.task_type else None,
        }
        if bucket == "overdue":
            overdue.append(item)
        elif bucket == "today":
            due_today.append(item)
        else:
            upcoming.append(item)

    contact_q = Contact.query.filter_by(user_id=user_id)
    if org_id:
        contact_q = contact_q.filter(Contact.organization_id == org_id)

    # Cold contacts: last_contact_date older than COLD_DAYS or never touched
    cold_candidates = (
        contact_q
        .filter(
            or_(
                Contact.last_contact_date.is_(None),
                Contact.last_contact_date <= cold_cutoff,
            )
        )
        .order_by(
            nullsfirst(Contact.last_contact_date.asc()),
            Contact.potential_commission.desc(),
        )
        .limit(MAX_COLD)
        .all()
    )
    cold_contacts = []
    for c in cold_candidates:
        days = (
            (today - c.last_contact_date).days
            if c.last_contact_date else
            ((today - c.created_at.date()).days if c.created_at else COLD_DAYS)
        )
        cold_contacts.append({
            "contact_id": c.id,
            "name": f"{c.first_name} {c.last_name}".strip(),
            "email": c.email or "",
            "phone": c.phone or "",
            "days_since_touch": days,
            "last_contact_date": (
                c.last_contact_date.isoformat() if c.last_contact_date else None
            ),
            "potential_commission": float(c.potential_commission or 0),
            "current_objective": (c.current_objective or "")[:200],
            "move_timeline": (c.move_timeline or "")[:120],
            "notes": (c.notes or "")[:200],
        })

    hot_contacts = (
        contact_q
        .filter(Contact.potential_commission.isnot(None))
        .order_by(Contact.potential_commission.desc())
        .limit(MAX_HOT)
        .all()
    )
    opportunities = [{
        "contact_id": c.id,
        "name": f"{c.first_name} {c.last_name}".strip(),
        "potential_commission": float(c.potential_commission or 0),
        "current_objective": (c.current_objective or "")[:200],
        "notes": (c.notes or "")[:200],
        "last_contact_date": (
            c.last_contact_date.isoformat() if c.last_contact_date else None
        ),
    } for c in hot_contacts]

    new_contacts = (
        contact_q
        .filter(Contact.created_at >= week_ago)
        .order_by(Contact.created_at.desc())
        .limit(MAX_NEW)
        .all()
    )
    recent = [{
        "contact_id": c.id,
        "name": f"{c.first_name} {c.last_name}".strip(),
        "created_at": c.created_at.strftime("%Y-%m-%d") if c.created_at else None,
        "email": c.email or "",
        "phone": c.phone or "",
        "notes": (c.notes or "")[:200],
        "current_objective": (c.current_objective or "")[:200],
    } for c in new_contacts]

    active_tx = []
    if org_id:
        tx_q = (
            Transaction.query
            .options(joinedload(Transaction.created_by))
            .filter(
                Transaction.organization_id == org_id,
                Transaction.created_by_id == user_id,
                Transaction.status.notin_(['closed', 'cancelled']),
            )
            .order_by(Transaction.updated_at.desc())
            .limit(MAX_TX)
            .all()
        )
        for tx in tx_q:
            tx_type = tx.transaction_type
            active_tx.append({
                "transaction_id": tx.id,
                "address": tx.street_address,
                "city": tx.city or "",
                "status": tx.status,
                "type": (
                    tx_type.display_name if tx_type and tx_type.display_name
                    else (tx_type.name if tx_type else None)
                ),
                "expected_close_date": (
                    tx.expected_close_date.isoformat()
                    if tx.expected_close_date else None
                ),
            })

    return {
        "user_first_name": first_name,
        "today": today.isoformat(),
        "weekday": today.strftime("%A"),
        "tasks": {
            "overdue": overdue,
            "today": due_today,
            "upcoming": upcoming,
        },
        "cold_contacts": cold_contacts,
        "opportunities": opportunities,
        "new_contacts": recent,
        "active_transactions": active_tx,
    }


def generate_briefing_content(user_id: int, org_id: int | None) -> tuple:
    """Build context, call the model, return (content_dict, model_used)."""
    context = build_briefing_context(user_id, org_id)
    user_prompt = (
        "Generate today's Daily Briefing from this CRM data.\n\n"
        f"CRM Data:\n{json.dumps(context, default=str)}"
    )
    content, model_used = generate_structured_response(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=BRIEFING_SCHEMA,
        schema_name="daily_briefing",
        temperature=0.4,
    )
    # Soft-cap arrays in case the model overshoots
    content["priorities"] = (content.get("priorities") or [])[:5]
    content["reconnect"] = (content.get("reconnect") or [])[:5]
    content["pipeline_watch"] = (content.get("pipeline_watch") or [])[:4]
    return content, model_used


def run_briefing_generation(app, briefing_id: int, user_id: int, org_id: int | None):
    """Background worker: fill a generating DailyTodoList row."""
    with app.app_context():
        if org_id:
            set_job_org_context(org_id)
        try:
            row = db.session.get(DailyTodoList, briefing_id)
            if not row:
                logger.error(f"Briefing {briefing_id} not found")
                return
            if row.status == 'ready' and row.todo_content:
                return

            content, model_used = generate_briefing_content(user_id, org_id)
            row.todo_content = content
            row.status = 'ready'
            row.model_used = model_used
            row.error = None
            row.generated_at = datetime.utcnow()
            db.session.commit()
            logger.info(f"Briefing {briefing_id} ready via {model_used}")
        except Exception as e:
            logger.exception(f"Briefing {briefing_id} failed: {e}")
            try:
                db.session.rollback()
                row = db.session.get(DailyTodoList, briefing_id)
                if row:
                    row.status = 'failed'
                    row.error = str(e)[:1000]
                    db.session.commit()
            except Exception:
                db.session.rollback()
        finally:
            db.session.remove()


def serialize_briefing(row: DailyTodoList) -> dict:
    """API-facing payload for a briefing row."""
    content = row.todo_content or {}
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {}

    return {
        "id": row.id,
        "status": row.status,
        "plan_date": row.plan_date.isoformat() if row.plan_date else None,
        "generated_at": (
            row.generated_at.isoformat() + "Z" if row.generated_at else None
        ),
        "viewed_at": (
            row.viewed_at.isoformat() + "Z" if row.viewed_at else None
        ),
        "banner_dismissed": row.banner_dismissed,
        "item_states": row.item_states or {},
        "teaser": content.get("teaser") or "",
        "headline": content.get("headline") or "",
        "priorities": content.get("priorities") or [],
        "reconnect": content.get("reconnect") or [],
        "pipeline_watch": content.get("pipeline_watch") or [],
        "error": row.error,
        "model_used": row.model_used,
    }


CHAT_SYSTEM_PROMPT = """You are B.O.B. (Business Optimization Buddy), helping an agent act on today's Daily Briefing.

You have the full briefing JSON and linked CRM details below. Answer follow-up questions, tighten scripts, pick who to call first, and draft messages. Stay specific to the plan and the real contacts/tasks in it.

Rules:
- Be concise and useful. Prefer short drafts the agent can send as-is.
- Never invent contacts, commissions, or deal facts not present in the briefing/context.
- If asked about something outside the CRM/briefing, say so briefly and redirect.
- Use markdown sparingly (bullets, bold) when it helps scanability.
- Sign off casually as B.O.B. only when the answer is a complete thought, not every short reply.
"""


def build_chat_context(row: DailyTodoList) -> str:
    content = row.todo_content or {}
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {}
    return json.dumps(content, default=str)
