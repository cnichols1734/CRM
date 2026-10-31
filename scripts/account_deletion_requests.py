"""Inspect requests and record verified manual fulfillment. Never deletes business data."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--organization', type=int, required=True)
    parser.add_argument('--complete', type=int, metavar='REQUEST_ID')
    parser.add_argument('--evidence', type=Path, help='JSON fulfillment evidence; required to complete')
    args = parser.parse_args()
    from app import create_app
    from models import AccountDeletionRequest, User, db
    from routes.agent_api import _set_org_context
    app = create_app()
    with app.app_context():
        _set_org_context(args.organization)
        if args.complete is not None:
            row = AccountDeletionRequest.query.filter_by(id=args.complete, organization_id=args.organization).one()
            if row.completed_at:
                parser.error('This request is already complete.')
            if db.session.get(User, row.user_id) is not None:
                parser.error('The account record still exists. Fulfill deletion before recording completion.')
            if not args.evidence:
                parser.error('--evidence is required.')
            evidence = json.loads(args.evidence.read_text())
            required = ('operator', 'deleted_data', 'external_services', 'retained_records', 'retention_basis_and_expiry', 'confirmation_sent_at')
            if not isinstance(evidence, dict) or any(not isinstance(evidence.get(k), str) or not evidence[k].strip() for k in required):
                parser.error('Evidence must contain nonempty strings for: ' + ', '.join(required))
            row.completion_evidence = json.dumps(evidence)
            row.completed_at = datetime.utcnow()
            db.session.commit()
            _set_org_context(args.organization)
        rows = AccountDeletionRequest.query.filter_by(organization_id=args.organization).order_by(AccountDeletionRequest.due_at).all()
        for row in rows:
            print(json.dumps(dict(id=row.id, user_id=row.user_id, due_at=row.due_at.isoformat(),
                completed_at=row.completed_at.isoformat() if row.completed_at else None,
                notification_sent=row.notified_at is not None,
                overdue=row.completed_at is None and row.due_at < datetime.utcnow())))


if __name__ == '__main__':
    main()
