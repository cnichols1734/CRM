"""Save, version, and look up marketing templates."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from models import MarketingTemplate, MarketingTemplateVersion, db
from services.marketing import blocks as blockmod
from services.marketing import compliance
from services.marketing import merge_fields as mf
from services.marketing.blocks import (
    BlockError, find_placeholders, missing_button_url_message, validate_blocks,
)
from services.marketing.context import shell_for
from services.marketing.render import render


class TemplateError(ValueError):
    """A template cannot be saved or used. Message is shown to the agent."""


def visible_to(organization_id: int, user_id: int, *, include_archived: bool = False):
    """Templates this agent can pick: theirs, plus org-shared, plus system."""
    query = MarketingTemplate.query.filter(
        MarketingTemplate.organization_id == organization_id,
    )
    if not include_archived:
        query = query.filter(MarketingTemplate.status != 'archived')
    query = query.filter(
        db.or_(
            MarketingTemplate.created_by_id == user_id,
            MarketingTemplate.visibility == 'org',
            MarketingTemplate.source == 'system',
        )
    )
    return query.order_by(
        MarketingTemplate.last_used_at.desc().nullslast(),
        MarketingTemplate.updated_at.desc(),
    )


def get_visible(organization_id: int, user_id: int, template_id: int) -> MarketingTemplate:
    template = visible_to(organization_id, user_id, include_archived=True).filter(
        MarketingTemplate.id == template_id,
    ).first()
    if template is None:
        raise TemplateError('That template is not available.')
    return template


def _copy_blob(subject: str, preheader: Optional[str], blocks: list[dict]) -> str:
    parts = [subject or '', preheader or '']
    for block in blocks:
        for key, value in block.items():
            if isinstance(value, str):
                parts.append(value)
            elif key == 'items' and isinstance(value, list):
                parts.extend(str(v) for v in value)
            elif key in ('stats', 'steps') and isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict):
                        parts.extend(str(v) for v in entry.values() if v)
    return '\n'.join(parts)


def scan_template(subject: str, preheader: Optional[str], blocks: list[dict]) -> list:
    findings = compliance.scan_blocks(blocks)
    findings += compliance.scan_text(subject or '', field='subject')
    findings += compliance.scan_text(preheader or '', field='preheader')
    return findings


def prepare(
    subject: str,
    preheader: Optional[str],
    raw_blocks,
    *,
    acknowledge_warnings: bool = False,
) -> dict[str, Any]:
    """Validate, lint, and decide sendability. Does not write."""
    subject = (subject or '').strip()
    preheader = (preheader or '').strip() or None
    if not subject:
        raise TemplateError('An email needs a subject line.')
    if len(subject) > 300:
        raise TemplateError('Subject is too long.')

    try:
        blocks = validate_blocks(raw_blocks)
    except BlockError as exc:
        raise TemplateError(str(exc)) from exc

    try:
        mf.validate_text(subject, where='The subject')
        if preheader:
            mf.validate_text(preheader, where='The preview line')
        mf.validate_text(_copy_blob(subject, preheader, blocks), where='This template')
    except mf.MergeFieldError as exc:
        raise TemplateError(str(exc)) from exc

    findings = scan_template(subject, preheader, blocks)
    state = compliance.state_for(findings)
    if state == 'blocked':
        status = 'draft'
    elif state == 'warn' and not acknowledge_warnings:
        status = 'draft'
    else:
        status = 'ready'

    used = sorted(mf.extract_keys(_copy_blob(subject, preheader, blocks)))
    return {
        'subject': subject,
        'preheader': preheader,
        'blocks': blocks,
        'findings': [f.to_dict() if hasattr(f, 'to_dict') else {
            'severity': f.severity, 'field': f.field,
            'matched_text': getattr(f, 'matched_text', getattr(f, 'matched', None)),
            'message': f.message, 'protected_class': getattr(f, 'protected_class', None),
            'block_index': getattr(f, 'block_index', None),
        } for f in findings],
        'compliance_state': state,
        'status': status,
        'merge_fields_used': used,
        'placeholders': find_placeholders(blocks, subject, preheader or ''),
    }


def _finding_payload(findings) -> list[dict]:
    out = []
    for finding in findings:
        if hasattr(finding, 'to_dict'):
            out.append(finding.to_dict())
            continue
        out.append({
            'severity': finding.severity,
            'field': finding.field,
            'matched_text': getattr(finding, 'matched_text', getattr(finding, 'matched', None)),
            'message': finding.message,
            'protected_class': getattr(finding, 'protected_class', None),
            'block_index': getattr(finding, 'block_index', None),
        })
    return out


def cache_render(template: MarketingTemplate, org, agent=None) -> None:
    ctx = shell_for(org, agent, preheader=template.preheader)
    rendered = render(template.blocks or [], ctx, validate=False)
    template.html_cached = rendered.html
    template.text_cached = rendered.text


def snapshot(
    template: MarketingTemplate,
    *,
    created_by_id: Optional[int],
    change_note: Optional[str] = None,
    generated_by_ai: bool = False,
    prompt: Optional[str] = None,
) -> MarketingTemplateVersion:
    row = MarketingTemplateVersion(
        organization_id=template.organization_id,
        template_id=template.id,
        version=template.version,
        subject=template.subject,
        preheader=template.preheader,
        blocks=template.blocks,
        html=template.html_cached,
        created_by_id=created_by_id,
        change_note=change_note,
        generated_by_ai=generated_by_ai,
        prompt=prompt,
    )
    db.session.add(row)
    return row


def save(
    *,
    organization_id: int,
    user_id: int,
    org,
    agent,
    name: str,
    subject: str,
    blocks,
    preheader: Optional[str] = None,
    description: Optional[str] = None,
    category: str = 'other',
    visibility: str = 'private',
    template: Optional[MarketingTemplate] = None,
    acknowledge_warnings: bool = False,
    generated_by_ai: bool = False,
    prompt: Optional[str] = None,
    change_note: Optional[str] = None,
    source: Optional[str] = None,
    commit: bool = True,
) -> MarketingTemplate:
    name = (name or '').strip()
    if not name:
        raise TemplateError('Give the template a name.')
    if category not in MarketingTemplate.CATEGORIES:
        category = 'other'
    if visibility not in MarketingTemplate.VISIBILITIES:
        visibility = 'private'

    prepared = prepare(
        subject, preheader, blocks,
        acknowledge_warnings=acknowledge_warnings,
    )
    button_error = missing_button_url_message(prepared['blocks'])
    if button_error:
        raise TemplateError(button_error)

    creating = template is None
    if creating:
        template = MarketingTemplate(
            organization_id=organization_id,
            created_by_id=user_id,
            source=source or ('ai' if generated_by_ai else 'manual'),
            version=1,
        )
        db.session.add(template)
    else:
        if template.organization_id != organization_id:
            raise TemplateError('That template is not available.')
        if (
            template.created_by_id not in (None, user_id)
            and template.source != 'system'
            and visibility != template.visibility
        ):
            # Editing someone else's org-shared template is allowed; stealing
            # it private is not.
            pass
        template.version = (template.version or 1) + 1

    template.name = name[:200]
    template.description = (description or '')[:500] or None
    template.category = category
    template.subject = prepared['subject']
    template.preheader = prepared['preheader']
    template.blocks = prepared['blocks']
    template.visibility = visibility
    template.status = prepared['status']
    template.compliance_state = prepared['compliance_state']
    template.compliance_findings = _finding_payload(
        scan_template(prepared['subject'], prepared['preheader'], prepared['blocks'])
    )
    template.merge_fields_used = prepared['merge_fields_used']
    template.updated_at = datetime.utcnow()

    if acknowledge_warnings and prepared['compliance_state'] == 'warn':
        template.compliance_ack_by_id = user_id
        template.compliance_ack_at = datetime.utcnow()
        template.status = 'ready'
    elif prepared['compliance_state'] != 'warn':
        template.compliance_ack_by_id = None
        template.compliance_ack_at = None

    cache_render(template, org, agent)
    db.session.flush()
    snapshot(
        template,
        created_by_id=user_id,
        change_note=change_note,
        generated_by_ai=generated_by_ai,
        prompt=prompt,
    )

    if commit:
        db.session.commit()
    return template


def mark_used(template: MarketingTemplate) -> None:
    template.last_used_at = datetime.utcnow()
