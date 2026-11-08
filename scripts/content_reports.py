"""List content reports or record an investigated report's outcome."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--organization', type=int, required=True)
    parser.add_argument('--resolve', type=int, metavar='REPORT_ID')
    parser.add_argument('--operator')
    parser.add_argument('--outcome')
    args = parser.parse_args()
    if args.resolve and (not args.operator or not args.outcome):
        parser.error('Resolution requires --operator and --outcome documenting the investigation and action.')
    from app import create_app
    from models import AuditEvent, db
    from routes.agent_api import _set_org_context
    with create_app().app_context():
        _set_org_context(args.organization)
        query = AuditEvent.query.filter_by(organization_id=args.organization, event_type='content_report')
        if args.resolve:
            row = query.filter_by(id=args.resolve).with_for_update().one()
            row.event_data = {**row.event_data, 'status': 'resolved', 'operator': args.operator,
                              'outcome': args.outcome, 'resolved_at': datetime.now(timezone.utc).isoformat()}
            db.session.commit()
            _set_org_context(args.organization)
        for row in query.order_by(AuditEvent.created_at).all():
            print(json.dumps({'receipt': f'AF-{row.id}', 'reporter_id': row.actor_id,
                'created_at': row.created_at.isoformat(), **row.event_data}))


if __name__ == '__main__':
    main()
