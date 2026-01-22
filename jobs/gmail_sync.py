# jobs/gmail_sync.py
"""
Gmail Email Sync Background Job

Syncs emails from Gmail for all users with active integrations.
Runs every 5 minutes via Railway Cron Job.

Usage:
    python jobs/gmail_sync.py
"""

import os
import sys
import logging
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def sync_gmail_for_all_users():
    """
    Sync emails for all users with active Gmail integrations.
    
    For each organization with active integrations:
    1. Set org context for RLS
    2. For each user's integration:
       a. Check if sync is needed (not already syncing)
       b. Refresh token if needed
       c. Fetch new emails via Gmail API
       d. Match to contacts and store
       e. Update sync status and timestamp
    """
    from models import db, Organization, UserEmailIntegration
    from services import gmail_service
    from jobs.base import set_job_org_context
    
    logger.info("Starting Gmail sync job")
    
    total_users_synced = 0
    total_emails_fetched = 0
    total_contacts_matched = 0
    errors = []
    
    # Get all active integrations
    integrations = UserEmailIntegration.query.filter(
        UserEmailIntegration.sync_enabled == True,
        UserEmailIntegration.access_token_encrypted.isnot(None)
    ).all()
    
    logger.info(f"Found {len(integrations)} active Gmail integrations")
    
    for integration in integrations:
        try:
            # Set org context for RLS
            set_job_org_context(integration.organization_id)
            
            # Skip if already syncing (prevent concurrent syncs)
            if integration.sync_status == 'syncing':
                logger.info(f"Skipping user {integration.user_id} - already syncing")
                continue
            
            # Mark as syncing
            integration.sync_status = 'syncing'
            db.session.commit()
            
            logger.info(f"Syncing emails for user {integration.user_id} ({integration.connected_email})")
            
            # Perform incremental sync
            result = gmail_service.fetch_emails_for_user(integration, initial=False)
            
            total_users_synced += 1
            total_emails_fetched += result['emails_fetched']
            total_contacts_matched += result['contacts_matched']
            
            if result['errors']:
                errors.extend(result['errors'])
            
            logger.info(
                f"User {integration.user_id}: "
                f"{result['emails_fetched']} emails, "
                f"{result['contacts_matched']} contacts matched"
            )
            
        except Exception as e:
            logger.exception(f"Error syncing user {integration.user_id}: {e}")
            errors.append(f"User {integration.user_id}: {str(e)}")
            
            # Mark as error
            try:
                integration.sync_status = 'error'
                integration.sync_error = str(e)
                db.session.commit()
            except Exception:
                db.session.rollback()
    
    logger.info(
        f"Gmail sync job completed: "
        f"{total_users_synced} users synced, "
        f"{total_emails_fetched} emails fetched, "
        f"{total_contacts_matched} contacts matched, "
        f"{len(errors)} errors"
    )
    
    return {
        'users_synced': total_users_synced,
        'emails_fetched': total_emails_fetched,
        'contacts_matched': total_contacts_matched,
        'errors': errors
    }


def run_gmail_sync():
    """Entry point for scheduler/cron - creates app context and runs job."""
    from app import create_app
    
    app = create_app()
    with app.app_context():
        sync_gmail_for_all_users()


if __name__ == '__main__':
    run_gmail_sync()
