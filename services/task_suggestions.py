import json
import logging
from datetime import datetime, timedelta

from models import (
    db, Contact, Task, Interaction, ContactEmail,
    ContactVoiceMemo, Transaction, TransactionParticipant,
    TaskType, TaskSubtype
)
from services.ai_service import generate_ai_response
from sqlalchemy.orm import joinedload

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are B.O.B., a Houston real estate CRM assistant. Given a contact's CRM profile, suggest exactly 3 specific next-step tasks.

Rules:
- Reference real data from the profile (dates, amounts, objectives, activity gaps). Be specific, not generic.
- If data is stale or missing, suggest re-engagement or data-gathering tasks.
- Pick task_type and task_subtype from the provided list.
- due_in_days: urgent 1-2, standard 3-7, longer up to 14. Order by priority.
- Never fabricate facts. No legal/financial advice.

Return ONLY valid JSON, no markdown:
{"suggestions":[{"subject":"action verb + detail","description":"1-2 sentences why + what","priority":"high|medium|low","due_in_days":3,"task_type":"type name","task_subtype":"subtype name","reason":"one-line insight"}]}"""


def build_contact_context(contact_id, org_id, user_id):
    """Gather relevant CRM data for a contact, optimized for minimal token usage."""
    contact = Contact.query.filter_by(
        id=contact_id, organization_id=org_id
    ).first()

    if not contact:
        return None

    today = datetime.utcnow().date()

    pending_tasks = Task.query.options(
        joinedload(Task.task_type)
    ).filter_by(
        contact_id=contact_id, status='pending'
    ).order_by(Task.due_date.asc()).limit(5).all()

    recent_completed = Task.query.filter(
        Task.contact_id == contact_id,
        Task.status == 'completed',
        Task.completed_at >= datetime.utcnow() - timedelta(days=30)
    ).with_entities(Task.subject, Task.completed_at).order_by(
        Task.completed_at.desc()
    ).limit(3).all()

    recent_interactions = Interaction.query.filter_by(
        contact_id=contact_id
    ).order_by(Interaction.date.desc()).limit(5).all()

    recent_emails = ContactEmail.query.filter_by(
        contact_id=contact_id, user_id=user_id
    ).with_entities(
        ContactEmail.subject, ContactEmail.direction, ContactEmail.sent_at
    ).order_by(ContactEmail.sent_at.desc()).limit(3).all()

    voice_memos = ContactVoiceMemo.query.filter_by(
        contact_id=contact_id, organization_id=org_id,
        transcription_status='completed'
    ).with_entities(ContactVoiceMemo.transcription).order_by(
        ContactVoiceMemo.created_at.desc()
    ).limit(2).all()

    participations = TransactionParticipant.query.filter_by(
        contact_id=contact_id
    ).all()
    tx_ids = [p.transaction_id for p in participations]
    transactions = []
    if tx_ids:
        transactions = Transaction.query.filter(
            Transaction.id.in_(tx_ids)
        ).all()

    task_types = TaskType.query.filter_by(
        organization_id=org_id
    ).options(joinedload(TaskType.subtypes)).order_by(TaskType.sort_order).all()

    def days_since(d):
        if not d:
            return None
        delta = today - d if hasattr(d, 'date') and callable(d.date) else today - d
        return delta.days if hasattr(delta, 'days') else None

    context = {
        "contact": {
            "name": f"{contact.first_name} {contact.last_name}",
            "email": contact.email or "none",
            "phone": contact.phone or "none",
            "commission": float(contact.potential_commission) if contact.potential_commission else None,
            "days_as_contact": (today - contact.created_at.date()).days if contact.created_at else None,
            "last_contact": contact.last_contact_date.strftime("%Y-%m-%d") if contact.last_contact_date else "never",
            "days_silent": days_since(contact.last_contact_date),
            "last_email": contact.last_email_date.strftime("%Y-%m-%d") if contact.last_email_date else None,
            "last_call": contact.last_phone_call_date.strftime("%Y-%m-%d") if contact.last_phone_call_date else None,
            "last_text": contact.last_text_date.strftime("%Y-%m-%d") if contact.last_text_date else None,
            "objective": contact.current_objective or "unknown",
            "timeline": contact.move_timeline or "unknown",
            "motivation": contact.motivation or "unknown",
            "financial": contact.financial_status or "unknown",
            "notes": _truncate(contact.notes, 300),
            "extra_notes": _truncate(contact.additional_notes, 200),
            "groups": [g.name for g in contact.groups] if contact.groups else [],
        },
        "pending_tasks": [{
            "subject": t.subject,
            "type": t.task_type.name if t.task_type else None,
            "priority": t.priority,
            "due": t.due_date.strftime("%Y-%m-%d") if t.due_date else None,
            "overdue": t.due_date.date() < today if t.due_date else False,
        } for t in pending_tasks],
        "done_recently": [
            {"subject": s, "when": c.strftime("%Y-%m-%d") if c else None}
            for s, c in recent_completed
        ],
        "interactions": [{
            "type": i.type,
            "date": i.date.strftime("%Y-%m-%d") if i.date else None,
            "notes": _truncate(i.notes, 120),
        } for i in recent_interactions],
        "emails": [
            {"subject": s or "(none)", "dir": d, "date": dt.strftime("%Y-%m-%d") if dt else None}
            for s, d, dt in recent_emails
        ],
        "voice_notes": [_truncate(t, 150) for t, in voice_memos if t],
        "transactions": [{
            "address": tx.street_address,
            "status": tx.status,
            "type": tx.transaction_type.display_name if tx.transaction_type else None,
            "close": tx.expected_close_date.strftime("%Y-%m-%d") if tx.expected_close_date else None,
            "role": next((p.role for p in participations if p.transaction_id == tx.id), None),
        } for tx in transactions],
        "task_types": [{
            "type": tt.name,
            "subs": [st.name for st in tt.subtypes]
        } for tt in task_types],
    }

    return context


def generate_task_suggestions(contact_id, org_id, user_id):
    """Generate 3 AI-powered task suggestions for a contact."""
    context = build_contact_context(contact_id, org_id, user_id)
    if not context:
        raise ValueError("Contact not found")

    user_prompt = (
        f"Date: {datetime.utcnow().strftime('%Y-%m-%d')} "
        f"({datetime.utcnow().strftime('%A')})\n"
        f"Profile: {json.dumps(context, separators=(',', ':'), default=str)}"
    )

    response_text = generate_ai_response(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.7,
        json_mode=True,
        reasoning_effort="low"
    )

    return _parse_suggestions(response_text, context)


def _parse_suggestions(response_text, context):
    """Parse and validate the AI response into structured suggestions."""
    text = response_text.strip()

    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.error(f"AI returned invalid JSON: {text[:500]}")
        raise ValueError("Could not parse AI response")

    suggestions = data.get("suggestions", [])
    if not isinstance(suggestions, list) or len(suggestions) == 0:
        raise ValueError("AI returned no suggestions")

    valid_types = {
        t["type"]: t["subs"]
        for t in context.get("task_types", [])
    }
    valid_priorities = {"high", "medium", "low"}

    cleaned = []
    for s in suggestions[:3]:
        priority = s.get("priority", "medium").lower()
        if priority not in valid_priorities:
            priority = "medium"

        due_in_days = s.get("due_in_days", 3)
        if not isinstance(due_in_days, int) or due_in_days < 1:
            due_in_days = 3
        if due_in_days > 14:
            due_in_days = 14

        task_type = s.get("task_type", "")
        task_subtype = s.get("task_subtype", "")

        if task_type not in valid_types:
            task_type = list(valid_types.keys())[0] if valid_types else "Call"
            task_subtype = valid_types.get(task_type, ["Follow-up"])[0]
        elif task_subtype not in valid_types.get(task_type, []):
            subtypes = valid_types.get(task_type, [])
            task_subtype = subtypes[0] if subtypes else "Follow-up"

        cleaned.append({
            "subject": s.get("subject", "Follow up")[:200],
            "description": s.get("description", "")[:500],
            "priority": priority,
            "due_in_days": due_in_days,
            "task_type": task_type,
            "task_subtype": task_subtype,
            "reason": s.get("reason", "")[:200],
        })

    return cleaned


def resolve_type_ids(suggestion, org_id):
    """Resolve task_type and task_subtype names to their DB IDs."""
    task_type = TaskType.query.filter_by(
        organization_id=org_id, name=suggestion["task_type"]
    ).first()

    if not task_type:
        task_type = TaskType.query.filter_by(
            organization_id=org_id
        ).order_by(TaskType.sort_order).first()

    task_subtype = None
    if task_type:
        task_subtype = TaskSubtype.query.filter_by(
            task_type_id=task_type.id,
            organization_id=org_id,
            name=suggestion["task_subtype"]
        ).first()

        if not task_subtype:
            task_subtype = TaskSubtype.query.filter_by(
                task_type_id=task_type.id,
                organization_id=org_id
            ).order_by(TaskSubtype.sort_order).first()

    return task_type, task_subtype


def _truncate(text, max_len):
    if not text:
        return None
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
