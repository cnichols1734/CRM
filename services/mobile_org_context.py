"""Keep mobile tenant context local to each database transaction."""
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from models import db


@event.listens_for(Session, 'after_begin')
def _apply_context(session, transaction, connection):
    org_id = session.info.get('mobile_org_id')
    if org_id is not None and connection.dialect.name == 'postgresql':
        connection.execute(text("SELECT set_config('app.current_org_id', :org_id, true)"), {'org_id': str(org_id)})


def set_org_context(org_id):
    db.session.info['mobile_org_id'] = org_id
    connection = db.session.connection()
    if connection.dialect.name == 'postgresql':
        connection.execute(text("SELECT set_config('app.current_org_id', :org_id, true)"), {'org_id': str(org_id)})


def clear_org_context():
    db.session.info.pop('mobile_org_id', None)
    db.session.rollback()
