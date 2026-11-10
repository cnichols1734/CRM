"""Recover durable Home Facts jobs when a queue wake-up was missed."""
import logging

from app import create_app
from jobs.home_facts import drain
from models import Organization, db
from services.mobile_org_context import clear_org_context


def main():
    logging.basicConfig(level=logging.INFO)
    with create_app().app_context():
        organization_ids = [row.id for row in Organization.query.filter_by(status='active').all()]
        for organization_id in organization_ids:
            try:
                count = drain(organization_id, limit=100)
                logging.info('Home Facts recovery org=%s processed=%s', organization_id, count)
            except Exception:
                logging.exception('Home Facts recovery failed org=%s', organization_id)
            finally:
                clear_org_context()
                db.session.remove()


if __name__ == '__main__':
    main()
