"""
Link TransactionDocument uploads to TransactionRequirement rows via evidence.

Uses the existing TransactionRequirementEvidence join table — packs declare
which document_slug satisfies each requirement_key.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from models import (
    TransactionDocument,
    TransactionRequirement,
    TransactionRequirementEvidence,
    db,
)
from services.deadline_rules import DeadlineRulesService
from services.requirements_service import CLOSED_WORK_STATUSES


def attach_document(
    requirement: TransactionRequirement,
    document: TransactionDocument,
    *,
    actor_id: Optional[int] = None,
    evidence_type: str = 'document',
    note: Optional[str] = None,
) -> TransactionRequirementEvidence:
    """Attach a document as evidence for a requirement (idempotent)."""
    if requirement.organization_id != document.organization_id:
        raise ValueError(
            'Requirement and document belong to different organizations'
        )
    if requirement.transaction_id != document.transaction_id:
        raise ValueError(
            'Requirement and document belong to different transactions'
        )

    existing = TransactionRequirementEvidence.query.filter_by(
        organization_id=requirement.organization_id,
        requirement_id=requirement.id,
        document_id=document.id,
    ).first()
    if existing:
        return existing

    evidence = TransactionRequirementEvidence(
        organization_id=requirement.organization_id,
        requirement_id=requirement.id,
        evidence_type=evidence_type or 'document',
        document_id=document.id,
        description=note,
        uploaded_by_id=actor_id,
    )
    db.session.add(evidence)
    db.session.flush()
    return evidence


def detach_document(
    requirement: TransactionRequirement,
    document: TransactionDocument,
) -> bool:
    """Remove evidence linking requirement ↔ document. Returns True if a row was deleted."""
    evidence = TransactionRequirementEvidence.query.filter_by(
        organization_id=requirement.organization_id,
        requirement_id=requirement.id,
        document_id=document.id,
    ).first()
    if not evidence:
        return False
    db.session.delete(evidence)
    db.session.flush()
    return True


def evidence_for_requirements(
    transaction_id: int,
    organization_id: int,
    requirement_ids: List[int],
) -> Dict[int, List[TransactionRequirementEvidence]]:
    """Batch-load evidence rows keyed by requirement_id (single query, no N+1)."""
    out: Dict[int, List[TransactionRequirementEvidence]] = {
        rid: [] for rid in requirement_ids
    }
    if not requirement_ids:
        return out

    rows = (
        db.session.query(TransactionRequirementEvidence)
        .join(
            TransactionRequirement,
            TransactionRequirement.id
            == TransactionRequirementEvidence.requirement_id,
        )
        .filter(
            TransactionRequirementEvidence.organization_id == organization_id,
            TransactionRequirement.organization_id == organization_id,
            TransactionRequirement.transaction_id == transaction_id,
            TransactionRequirementEvidence.requirement_id.in_(requirement_ids),
        )
        .all()
    )

    for row in rows:
        out.setdefault(row.requirement_id, []).append(row)
    return out


def _load_pack_for_requirement(requirement: TransactionRequirement):
    """Best-effort pack load from the requirement's package_key."""
    pack_key = (requirement.package_key or '').strip()
    if not pack_key:
        return None
    version = (requirement.deadline_rule_version or 'v1').strip() or 'v1'
    try:
        return DeadlineRulesService.load_pack(pack_key, version)
    except FileNotFoundError:
        try:
            return DeadlineRulesService.load_pack(pack_key, 'v1')
        except FileNotFoundError:
            return None


def auto_attach_for_document(
    document: TransactionDocument,
    *,
    actor_id: Optional[int] = None,
) -> List[TransactionRequirement]:
    """
    Attach this document as evidence to open requirements whose pack
    declares a matching document_slug.

    Uploading a file is evidence that work has started, not that the
    obligation is met. BOB observes and proposes; the agent decides.
    Therefore this never sets work_status to completed — only pending →
    in_progress when a match is attached.
    """
    if not document or not document.id:
        return []

    org_id = document.organization_id
    tx_id = document.transaction_id
    slug = (document.template_slug or '').strip()
    if not slug:
        return []

    requirements = TransactionRequirement.query.filter_by(
        organization_id=org_id,
        transaction_id=tx_id,
    ).all()
    if not requirements:
        return []

    # Cache packs by package_key so we don't re-parse per requirement.
    pack_cache: Dict[str, Optional[dict]] = {}
    touched: List[TransactionRequirement] = []

    for req in requirements:
        if (req.work_status or '').lower() in CLOSED_WORK_STATUSES:
            continue

        pack_key = (req.package_key or '').strip() or '__none__'
        if pack_key not in pack_cache:
            pack_cache[pack_key] = _load_pack_for_requirement(req)
        pack = pack_cache[pack_key]
        if not pack:
            continue

        expected = DeadlineRulesService.expected_document_slug(
            pack, req.requirement_key,
        )
        if not expected or expected != slug:
            continue

        attach_document(
            req,
            document,
            actor_id=actor_id,
            evidence_type='document',
        )

        # Never complete from an upload — agent must mark complete.
        if (req.work_status or '').lower() == 'pending':
            req.work_status = 'in_progress'

        touched.append(req)

    if touched:
        db.session.flush()
    return touched
