"""
Learned requirement templates (BOB VTC Phase 3).

Build org-scoped template drafts from approved proposal / completed
requirement history. Apply remains deterministic via RequirementsService —
never AI-apply deadlines.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from models import (
    OrgRequirementTemplate,
    TransactionChangeProposal,
    TransactionRequirement,
    db,
)

logger = logging.getLogger(__name__)


def _requirement_fingerprint(req: TransactionRequirement) -> tuple:
    return (
        req.package_key or '',
        req.phase_key or '',
        req.requirement_key or '',
        req.title or '',
    )


def propose_template_draft(
    organization_id: int,
    *,
    package_key: str = 'learned',
    min_occurrences: int = 3,
    lookback_limit: int = 500,
) -> Dict[str, Any]:
    """
    Propose a template draft from completed requirements + applied proposals.

    Deterministic aggregation only — no LLM.
    """
    completed = (
        TransactionRequirement.query.filter_by(
            organization_id=organization_id,
            work_status='completed',
        )
        .order_by(TransactionRequirement.updated_at.desc())
        .limit(lookback_limit)
        .all()
    )

    applied_proposals = (
        TransactionChangeProposal.query.filter_by(
            organization_id=organization_id,
            status='applied',
        )
        .order_by(TransactionChangeProposal.created_at.desc())
        .limit(lookback_limit)
        .all()
    )

    counts: Counter = Counter()
    samples: Dict[tuple, Dict[str, Any]] = {}

    for req in completed:
        fp = _requirement_fingerprint(req)
        counts[fp] += 1
        samples.setdefault(fp, {
            'package_key': req.package_key,
            'phase_key': req.phase_key,
            'requirement_key': req.requirement_key,
            'title': req.title,
            'responsibility_type': req.responsibility_type,
        })

    # Count requirement_keys suggested inside applied proposal payloads.
    for prop in applied_proposals:
        changes = prop.proposed_changes or {}
        for item in changes.get('requirements') or []:
            if not isinstance(item, dict):
                continue
            key = item.get('requirement_key')
            if not key:
                continue
            fp = (
                item.get('package_key') or package_key,
                item.get('phase_key') or 'learned',
                key,
                item.get('title') or key,
            )
            counts[fp] += 1
            samples.setdefault(fp, {
                'package_key': fp[0],
                'phase_key': fp[1],
                'requirement_key': fp[2],
                'title': fp[3],
                'responsibility_type': item.get('responsibility_type'),
            })

    requirements: Dict[str, Any] = {}
    phases_seen: Dict[str, str] = {}
    for fp, count in counts.most_common():
        if count < min_occurrences:
            continue
        sample = samples[fp]
        req_key = sample['requirement_key']
        # Avoid collisions when same key appears under different packages.
        store_key = req_key if req_key not in requirements else f'{req_key}_{count}'
        requirements[store_key] = {
            'phase': sample['phase_key'] or 'learned',
            'title': sample['title'] or req_key,
            'description': f'Learned from org history ({count} completions/approvals)',
            'responsibility': sample.get('responsibility_type') or 'agent',
            'required': True,
            'source_package_key': sample.get('package_key'),
            'occurrence_count': count,
            # No AI deadline — apply path uses pack rules or explicit due_at only.
            'deadline_rule': None,
        }
        phase = sample['phase_key'] or 'learned'
        phases_seen[phase] = phase.replace('_', ' ').title()

    draft = {
        'pack_key': package_key,
        'version': 'draft',
        'name': f'Learned template ({package_key})',
        'description': (
            f'Org {organization_id} draft from approved/completed history. '
            'Deadlines apply only via RequirementsService / DeadlineRulesService.'
        ),
        'phases': [
            {'phase_key': k, 'name': v, 'description': 'Learned phase'}
            for k, v in sorted(phases_seen.items())
        ],
        'requirements': requirements,
        'learned': True,
        'min_occurrences': min_occurrences,
        'source_counts': {
            'completed_requirements': len(completed),
            'applied_proposals': len(applied_proposals),
        },
    }
    return draft


def save_template_draft(
    organization_id: int,
    draft: Dict[str, Any],
    *,
    created_by_id: Optional[int] = None,
    activate: bool = False,
) -> OrgRequirementTemplate:
    """Persist a versioned org template (draft or active)."""
    pack_key = draft.get('pack_key') or 'learned'
    latest = (
        OrgRequirementTemplate.query.filter_by(
            organization_id=organization_id,
            pack_key=pack_key,
        )
        .order_by(OrgRequirementTemplate.version.desc())
        .first()
    )
    next_version = (latest.version + 1) if latest else 1

    row = OrgRequirementTemplate(
        organization_id=organization_id,
        pack_key=pack_key,
        version=next_version,
        name=draft.get('name') or f'{pack_key} v{next_version}',
        status='active' if activate else 'draft',
        template_json=draft,
        created_by_id=created_by_id,
    )
    db.session.add(row)
    db.session.flush()
    return row


def get_active_template(
    organization_id: int,
    pack_key: str = 'learned',
) -> Optional[OrgRequirementTemplate]:
    return (
        OrgRequirementTemplate.query.filter_by(
            organization_id=organization_id,
            pack_key=pack_key,
            status='active',
        )
        .order_by(OrgRequirementTemplate.version.desc())
        .first()
    )


def apply_learned_template(
    *,
    transaction_id: int,
    organization_id: int,
    pack_key: str = 'learned',
    template: Optional[OrgRequirementTemplate] = None,
    actor_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Deterministically create requirements from an org template.

    Never computes AI deadlines — only creates rows with due_at=None unless
    the template entry already has an explicit due_at (rare).
    """
    from services.requirements_service import RequirementsService

    template = template or get_active_template(organization_id, pack_key)
    if not template:
        raise ValueError(f'No active learned template for pack_key={pack_key}')

    pack = template.template_json or {}
    created_ids: List[int] = []
    skipped = 0

    for req_key, req_def in (pack.get('requirements') or {}).items():
        existing = TransactionRequirement.query.filter_by(
            transaction_id=transaction_id,
            requirement_key=req_key,
        ).first()
        if existing:
            skipped += 1
            continue

        due_raw = req_def.get('due_at')
        due_at = None
        if due_raw:
            try:
                due_at = datetime.fromisoformat(str(due_raw)[:19])
            except ValueError:
                due_at = None

        req = RequirementsService.create_requirement(
            transaction_id=transaction_id,
            organization_id=organization_id,
            package_key=req_def.get('source_package_key') or pack_key,
            phase_key=req_def.get('phase') or 'learned',
            requirement_key=req_key,
            title=req_def.get('title') or req_key,
            due_at=due_at,
            source='learned_template',
            deadline_rule_version=f'org:{template.version}',
            responsibility_type=req_def.get('responsibility'),
            assignee_user_id=actor_id,
        )
        created_ids.append(req.id)

    return {
        'template_id': template.id,
        'pack_key': pack_key,
        'version': template.version,
        'created': len(created_ids),
        'skipped': skipped,
        'requirement_ids': created_ids,
    }


def learn_and_save(
    organization_id: int,
    *,
    created_by_id: Optional[int] = None,
    min_occurrences: int = 3,
    activate: bool = False,
) -> OrgRequirementTemplate:
    """Convenience: propose draft from history and persist."""
    draft = propose_template_draft(
        organization_id, min_occurrences=min_occurrences,
    )
    return save_template_draft(
        organization_id,
        draft,
        created_by_id=created_by_id,
        activate=activate,
    )
