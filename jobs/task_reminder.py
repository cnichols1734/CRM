# jobs/task_reminder.py
"""
Task Reminder Email Job

Sends batched task reminder emails for all organizations.
Runs daily via Railway Cron Job at 8:00 AM CT.

Each user receives ONE consolidated email with all their tasks organized by urgency:
- Overdue tasks (should have been done already)
- Due tomorrow (24-48 hours from now)
- Due in 2 days (48-72 hours from now)

Usage:
    python jobs/task_reminder.py
"""

import os
import sys
import logging
from datetime import datetime, timedelta
from collections import defaultdict

import pytz

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Central timezone for time calculations
CT = pytz.timezone('America/Chicago')

# Base URL for task links in emails
# Set via environment variable or default to production URL
APP_BASE_URL = os.environ.get('APP_BASE_URL', 'https://www.origentechnolog.com')


def send_task_reminders():
    """
    Send batched task reminder emails for all organizations.
    
    For each organization:
    1. Query all pending tasks needing reminders
    2. Group tasks by user
    3. Send ONE digest email per user with all their tasks
    4. Mark tasks as reminded (only if email succeeds)
    """
    from models import db, Organization, Task, User
    from services.email_service import get_email_service
    from jobs.base import set_job_org_context
    
    now_utc = datetime.utcnow()
    now_ct = datetime.now(CT)
    
    logger.info(f"Starting task reminder job at {now_ct.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    # Calculate time windows (in UTC for database queries)
    # Note: We use UTC for queries since that's how dates are stored
    
    # Overdue: due before now
    overdue_cutoff = now_utc
    
    # Today: due between 0-24 hours from now
    today_start = now_utc
    today_end = now_utc + timedelta(hours=24)
    
    # Tomorrow: due between 24-48 hours from now
    tomorrow_start = now_utc + timedelta(hours=24)
    tomorrow_end = now_utc + timedelta(hours=48)
    
    # Upcoming (2 days): due between 48-72 hours from now
    upcoming_start = now_utc + timedelta(hours=48)
    upcoming_end = now_utc + timedelta(hours=72)
    
    # Get all active orgs
    orgs = Organization.query.filter_by(status='active').all()
    logger.info(f"Processing {len(orgs)} active organizations")
    
    total_emails_sent = 0
    total_tasks_processed = 0
    
    for org in orgs:
        try:
            # Set RLS context for this org
            set_job_org_context(org.id)
            
            # Collect all tasks needing reminders, grouped by user
            user_tasks = defaultdict(lambda: {'overdue': [], 'today': [], 'tomorrow': [], 'upcoming': []})
            
            # Query overdue tasks (always include until completed)
            overdue_tasks = Task.query.filter(
                Task.organization_id == org.id,
                Task.status == 'pending',
                Task.due_date < overdue_cutoff
            ).all()
            
            for task in overdue_tasks:
                user_tasks[task.assigned_to_id]['overdue'].append(task)
            
            # Query today tasks (0-24 hours)
            today_tasks = Task.query.filter(
                Task.organization_id == org.id,
                Task.status == 'pending',
                Task.due_date >= today_start,
                Task.due_date < today_end,
                Task.today_reminder_sent == False
            ).all()
            
            for task in today_tasks:
                user_tasks[task.assigned_to_id]['today'].append(task)
            
            # Query tomorrow tasks (24-48 hours)
            tomorrow_tasks = Task.query.filter(
                Task.organization_id == org.id,
                Task.status == 'pending',
                Task.due_date >= tomorrow_start,
                Task.due_date < tomorrow_end,
                Task.one_day_reminder_sent == False
            ).all()
            
            for task in tomorrow_tasks:
                user_tasks[task.assigned_to_id]['tomorrow'].append(task)
            
            # Query upcoming tasks (48-72 hours)
            upcoming_tasks = Task.query.filter(
                Task.organization_id == org.id,
                Task.status == 'pending',
                Task.due_date >= upcoming_start,
                Task.due_date < upcoming_end,
                Task.two_day_reminder_sent == False
            ).all()
            
            for task in upcoming_tasks:
                user_tasks[task.assigned_to_id]['upcoming'].append(task)
            
            # Send ONE email per user with all their tasks
            email_service = get_email_service()
            
            for user_id, tasks_by_type in user_tasks.items():
                # Skip if no tasks for this user
                total_tasks = (
                    len(tasks_by_type['overdue']) + 
                    len(tasks_by_type['today']) + 
                    len(tasks_by_type['tomorrow']) + 
                    len(tasks_by_type['upcoming'])
                )
                if total_tasks == 0:
                    continue
                
                user = User.query.get(user_id)
                if not user:
                    logger.warning(f"User {user_id} not found, skipping")
                    continue
                
                if not user.email:
                    logger.warning(f"User {user_id} has no email, skipping")
                    continue
                
                try:
                    # Send the batched reminder email
                    success = email_service.send_task_reminder_digest(user, tasks_by_type, base_url=APP_BASE_URL)
                    
                    if success:
                        logger.info(
                            f"Sent reminder to {user.email}: "
                            f"{len(tasks_by_type['overdue'])} overdue, "
                            f"{len(tasks_by_type['today'])} today, "
                            f"{len(tasks_by_type['tomorrow'])} tomorrow, "
                            f"{len(tasks_by_type['upcoming'])} upcoming"
                        )
                        
                        # Mark all tasks as reminded (only after successful send)
                        for task in tasks_by_type['overdue']:
                            task.overdue_reminder_sent = True
                            task.last_reminder_sent_at = now_utc
                            total_tasks_processed += 1
                        
                        for task in tasks_by_type['today']:
                            task.today_reminder_sent = True
                            task.last_reminder_sent_at = now_utc
                            total_tasks_processed += 1
                        
                        for task in tasks_by_type['tomorrow']:
                            task.one_day_reminder_sent = True
                            task.last_reminder_sent_at = now_utc
                            total_tasks_processed += 1
                        
                        for task in tasks_by_type['upcoming']:
                            task.two_day_reminder_sent = True
                            task.last_reminder_sent_at = now_utc
                            total_tasks_processed += 1
                        
                        total_emails_sent += 1
                    else:
                        logger.error(f"Failed to send reminder to {user.email}")
                        
                except Exception as e:
                    logger.exception(f"Error sending reminder to user {user_id}: {e}")
                    # Continue with other users even if one fails
                    continue
            
        except Exception as e:
            logger.exception(f"Error processing org {org.id}: {e}")
            # Continue with other orgs even if one fails
            continue
    
    # Commit all changes
    try:
        db.session.commit()
        logger.info(
            f"Task reminder job completed: "
            f"{total_emails_sent} emails sent, "
            f"{total_tasks_processed} tasks processed"
        )
    except Exception as e:
        logger.exception(f"Error committing changes: {e}")
        db.session.rollback()
        raise


def run_task_reminders():
    """Entry point for scheduler/cron - creates app context and runs job."""
    from app import create_app
    
    app = create_app()
    with app.app_context():
        send_task_reminders()


if __name__ == '__main__':
    run_task_reminders()
