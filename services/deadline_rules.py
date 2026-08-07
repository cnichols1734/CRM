"""
Deadline Rules Service - Phase 1B/2/3

Loads and applies versioned deadline packs (business-day rules, dependencies).
Phase 2: pack selection by transaction side/status (buyer / listing / seller CTC).
Phase 3: lease_tenant pack support and side-filtered requirement listing.
"""
import json
from typing import Dict, List, Optional, Any, Tuple
from datetime import date, datetime, timedelta
from pathlib import Path

# Side/status → preferred pack keys (first loadable wins).
_PACK_PREFERENCE: List[Tuple[str, Optional[frozenset], str]] = [
    # (side_substring, statuses or None=any, pack_key)
    ('landlord', None, 'lease_tenant'),
    ('tenant', None, 'lease_tenant'),
    ('lease', None, 'lease_tenant'),
    ('buyer', frozenset({'under_contract', 'showing', 'pending'}), 'buyer_ctc'),
    ('buyer', None, 'buyer_ctc'),
    ('seller', frozenset({'preparing_to_list', 'active'}), 'listing'),
    ('seller', frozenset({'under_contract', 'pending'}), 'seller_ctc'),
    ('seller', None, 'seller_ctc'),
]


def db_session_has_contract(model, transaction_id: int, organization_id: int) -> bool:
    return model.query.filter_by(
        transaction_id=transaction_id,
        organization_id=organization_id,
        position='primary',
        status='active',
    ).first() is not None


class DeadlineRulesService:
    """Service for loading and applying deadline packs."""

    _packs_cache: Dict[str, Dict] = {}

    @staticmethod
    def packs_dir() -> Path:
        return Path(__file__).parent.parent / 'deadline_packs'

    @staticmethod
    def load_pack(pack_key: str, version: str = 'v1') -> Dict[str, Any]:
        """
        Load a deadline pack from JSON.

        Supports builtin packs (seller_ctc_v1, lease_tenant_v1) and clears
        cache key on miss so newly added files are picked up.
        """
        # Allow callers to pass either "seller_ctc" + version="v1" or "seller_ctc_v1".
        key = (pack_key or '').strip()
        ver = (version or 'v1').strip()
        if key.endswith(f'_{ver}'):
            key = key[: -(len(ver) + 1)]
        cache_key = f'{key}_{ver}'
        if cache_key in DeadlineRulesService._packs_cache:
            return DeadlineRulesService._packs_cache[cache_key]

        base_path = DeadlineRulesService.packs_dir()
        pack_path = base_path / f'{key}_{ver}.json'

        if not pack_path.exists():
            raise FileNotFoundError(f'Deadline pack not found: {pack_path}')

        with open(pack_path, 'r') as f:
            pack = json.load(f)

        DeadlineRulesService._validate_pack(pack)
        DeadlineRulesService._packs_cache[cache_key] = pack
        return pack

    @staticmethod
    def clear_cache() -> None:
        DeadlineRulesService._packs_cache.clear()

    @staticmethod
    def list_available_packs() -> List[Dict[str, str]]:
        """List builtin pack files under deadline_packs/."""
        base = DeadlineRulesService.packs_dir()
        if not base.is_dir():
            return []
        found = []
        for path in sorted(base.glob('*_v*.json')):
            # pack_key_version.json — version is last _vN segment
            stem = path.stem
            if '_v' not in stem:
                continue
            pack_key, version = stem.rsplit('_', 1)
            found.append({
                'pack_key': pack_key,
                'version': version,
                'path': str(path.name),
            })
        return found

    @staticmethod
    def _validate_pack(pack: Dict[str, Any]):
        required_keys = ['pack_key', 'version', 'phases', 'requirements']
        for key in required_keys:
            if key not in pack:
                raise ValueError(f'Deadline pack missing required key: {key}')

        if not isinstance(pack['phases'], list):
            raise ValueError('Deadline pack phases must be a list')
        if not isinstance(pack['requirements'], dict):
            raise ValueError('Deadline pack requirements must be a dict')

    @staticmethod
    def get_requirement_definition(
        pack_key: str,
        requirement_key: str,
        version: str = 'v1'
    ) -> Optional[Dict[str, Any]]:
        pack = DeadlineRulesService.load_pack(pack_key, version)
        return pack.get('requirements', {}).get(requirement_key)

    @staticmethod
    def expected_document_slug(
        pack: Dict[str, Any],
        requirement_key: str,
    ) -> Optional[str]:
        """Return the intake document slug declared for a requirement, if any.

        Packs are the source of truth — TransactionRequirement rows do not
        store the slug. Callers pass an already-loaded pack dict.
        """
        req_def = (pack.get('requirements') or {}).get(requirement_key) or {}
        slug = req_def.get('document_slug')
        if slug is None:
            return None
        slug_str = str(slug).strip()
        return slug_str or None

    @staticmethod
    def document_slugs_for_pack(pack: Dict[str, Any]) -> Dict[str, str]:
        """Map requirement_key → document_slug for every pack requirement that declares one."""
        out: Dict[str, str] = {}
        for req_key, req_def in (pack.get('requirements') or {}).items():
            slug = (req_def or {}).get('document_slug')
            if slug is None:
                continue
            slug_str = str(slug).strip()
            if slug_str:
                out[str(req_key)] = slug_str
        return out

    @staticmethod
    def calculate_deadline(
        anchor_date: date,
        deadline_rule: Dict[str, Any],
        *,
        business_day_rules: Optional[Dict[str, Any]] = None,
    ) -> date:
        """
        Calculate a deadline from an anchor date and rule.

        Supports calendar and business day offsets. Business days skip
        weekends and optional holiday ISO dates from the pack.
        """
        offset_days = int(deadline_rule.get('offset_days', 0) or 0)
        unit = deadline_rule.get('unit', 'calendar')

        if unit == 'calendar':
            return anchor_date + timedelta(days=offset_days)

        if unit == 'business':
            holidays = set()
            excluded = {'saturday', 'sunday'}
            rules = business_day_rules or {}
            for h in rules.get('holidays') or []:
                try:
                    holidays.add(date.fromisoformat(str(h)[:10]))
                except ValueError:
                    continue
            for d in rules.get('excluded_days') or ['saturday', 'sunday']:
                excluded.add(str(d).lower())

            weekday_name = {
                0: 'monday', 1: 'tuesday', 2: 'wednesday', 3: 'thursday',
                4: 'friday', 5: 'saturday', 6: 'sunday',
            }
            step = 1 if offset_days >= 0 else -1
            remaining = abs(offset_days)
            current = anchor_date
            # offset 0 → same day (even if weekend — caller can adjust)
            while remaining > 0:
                current = current + timedelta(days=step)
                name = weekday_name[current.weekday()]
                if name in excluded or current in holidays:
                    continue
                remaining -= 1
            return current

        raise ValueError(f'Unknown deadline unit: {unit}')

    @staticmethod
    def list_phases(pack_key: str, version: str = 'v1') -> List[Dict[str, Any]]:
        pack = DeadlineRulesService.load_pack(pack_key, version)
        return pack.get('phases', [])

    @staticmethod
    def list_requirements_in_phase(
        pack_key: str,
        phase_key: str,
        version: str = 'v1'
    ) -> List[str]:
        pack = DeadlineRulesService.load_pack(pack_key, version)
        requirements = pack.get('requirements', {})
        return [
            req_key for req_key, req_def in requirements.items()
            if req_def.get('phase') == phase_key
        ]

    @staticmethod
    def requirements_for_side(
        pack_key: str,
        side: Optional[str],
        version: str = 'v1',
    ) -> Dict[str, Dict[str, Any]]:
        """
        Filter pack requirements by side (landlord/tenant/seller/buyer).

        Requirements without a `sides` list apply to all sides.
        """
        pack = DeadlineRulesService.load_pack(pack_key, version)
        side_norm = (side or '').strip().lower() or None
        out: Dict[str, Dict[str, Any]] = {}
        for req_key, req_def in (pack.get('requirements') or {}).items():
            sides = req_def.get('sides')
            if not sides or side_norm is None or side_norm in [
                str(s).lower() for s in sides
            ]:
                out[req_key] = req_def
        return out

    @staticmethod
    def pack_key_for_transaction_side(side: Optional[str]) -> str:
        """Map transaction side to default deadline pack (status-agnostic)."""
        side_norm = (side or '').strip().lower()
        if side_norm in ('landlord', 'tenant', 'lease'):
            return 'lease_tenant'
        if side_norm == 'buyer':
            return 'buyer_ctc'
        if side_norm == 'seller':
            return 'seller_ctc'
        return 'seller_ctc'

    @staticmethod
    def _has_active_contract(transaction) -> bool:
        """True when an active primary accepted contract exists for this transaction.

        Tolerates non-ORM stand-ins (tests pass plain namespaces) by returning
        False whenever the identifiers or the session are unavailable.
        """
        transaction_id = getattr(transaction, 'id', None)
        organization_id = getattr(transaction, 'organization_id', None)
        if not transaction_id or not organization_id:
            return False
        try:
            from services.controlling_contracts import has_active_primary_contract
            return has_active_primary_contract(transaction_id, organization_id)
        except Exception:
            return False

    @staticmethod
    def resolve_pack_for_transaction(
        transaction,
        *,
        version: str = 'v1',
        side_hint: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Pick the best deadline pack for a transaction's side and status.

        Returns:
            (pack_key, pack_dict)
        """
        side = (side_hint or '').strip().lower()
        if not side:
            type_name = ''
            tx_type = getattr(transaction, 'transaction_type', None)
            if tx_type is not None:
                type_name = (getattr(tx_type, 'name', None) or '').lower()
            side = type_name or 'seller'

        status = (getattr(transaction, 'status', None) or '').strip().lower()

        # An executed contract outranks the status string. Agents routinely leave a
        # listing on 'active' after it goes under contract, and without this the
        # listing pack wins and closing deadlines silently stop recomputing.
        if status not in ('under_contract', 'pending') and \
                DeadlineRulesService._has_active_contract(transaction):
            status = 'under_contract'

        candidates: List[str] = []
        for side_key, statuses, pack_key in _PACK_PREFERENCE:
            if side_key not in side and side not in side_key:
                continue
            if statuses is not None and status not in statuses:
                continue
            if pack_key not in candidates:
                candidates.append(pack_key)

        if 'buyer' in side and 'buyer_ctc' not in candidates:
            candidates.append('buyer_ctc')
        if any(s in side for s in ('landlord', 'tenant', 'lease')):
            if 'lease_tenant' not in candidates:
                candidates.append('lease_tenant')
        if 'seller' in side or not candidates:
            if status in ('preparing_to_list', 'active') and 'listing' not in candidates:
                candidates.append('listing')
            if 'seller_ctc' not in candidates:
                candidates.append('seller_ctc')

        last_err: Optional[Exception] = None
        for pack_key in candidates:
            try:
                pack = DeadlineRulesService.load_pack(pack_key, version)
                return pack_key, pack
            except FileNotFoundError as exc:
                last_err = exc
                continue

        raise FileNotFoundError(
            f'No deadline pack found for side={side!r} status={status!r}'
        ) from last_err

    @staticmethod
    def pack_key_for_transaction(
        transaction,
        *,
        version: str = 'v1',
        side_hint: Optional[str] = None,
    ) -> str:
        """Convenience: return only the resolved pack_key."""
        pack_key, _ = DeadlineRulesService.resolve_pack_for_transaction(
            transaction, version=version, side_hint=side_hint,
        )
        return pack_key

    @staticmethod
    def apply_pack_to_transaction(
        *,
        transaction_id: int,
        organization_id: int,
        pack_key: str,
        anchors: Dict[str, date],
        side: Optional[str] = None,
        version: str = 'v1',
        source: str = 'deadline_pack',
        actor_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Deterministically create requirements from a pack + anchors.

        Skips requirements whose anchor is missing. Never uses AI for dates.
        """
        from services.requirements_service import RequirementsService
        from models import TransactionRequirement

        pack = DeadlineRulesService.load_pack(pack_key, version)
        biz_rules = pack.get('business_day_rules') or {}
        reqs = DeadlineRulesService.requirements_for_side(
            pack_key, side, version=version,
        )

        created = 0
        skipped = 0
        waiting = 0
        ids: List[int] = []

        for req_key, req_def in reqs.items():
            existing = TransactionRequirement.query.filter_by(
                transaction_id=transaction_id,
                requirement_key=req_key,
            ).first()
            if existing:
                skipped += 1
                continue

            rule = req_def.get('deadline_rule') or {}
            anchor_name = rule.get('anchor')
            anchor_date = anchors.get(anchor_name) if anchor_name else None
            due_at = None
            if anchor_date and rule:
                try:
                    due = DeadlineRulesService.calculate_deadline(
                        anchor_date, rule, business_day_rules=biz_rules,
                    )
                    due_at = datetime.combine(due, datetime.min.time())
                except Exception:
                    due_at = None
            elif req_def.get('required'):
                waiting += 1

            req = RequirementsService.create_requirement(
                transaction_id=transaction_id,
                organization_id=organization_id,
                package_key=pack_key,
                phase_key=req_def.get('phase') or 'unknown',
                requirement_key=req_key,
                title=req_def.get('title') or req_key,
                due_at=due_at,
                source=source,
                deadline_rule_version=pack.get('version'),
                responsibility_type=req_def.get('responsibility'),
                assignee_user_id=actor_id,
                work_status='waiting' if due_at is None and req_def.get('required') else 'pending',
            )
            created += 1
            ids.append(req.id)

        return {
            'pack_key': pack_key,
            'version': version,
            'created': created,
            'skipped': skipped,
            'waiting_missing_anchor': waiting,
            'requirement_ids': ids,
        }
