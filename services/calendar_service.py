"""
Google Calendar Integration Service

Handles syncing CRM tasks to Google Calendar.
Tasks are synced to the assigned user's calendar.

Usage:
    from services.calendar_service import (
        sync_task_to_calendar,
        update_calendar_event,
        delete_calendar_event,
        mark_event_completed
    )
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pytz

from config import Config
from services.gmail_service import decrypt_token, refresh_access_token, GMAIL_SCOPES

logger = logging.getLogger(__name__)

# User timezone (consistent with tasks.py)
USER_TIMEZONE = 'America/Chicago'


def _get_calendar_service(integration):
    """
    Get authenticated Google Calendar API service.
    
    Args:
        integration: UserEmailIntegration model instance
    
    Returns:
        Google Calendar API service object
    
    Raises:
        Exception if authentication fails
    """
    # Check if calendar sync is enabled
    if not integration or not integration.calendar_sync_enabled:
        return None
    
    # Check if token needs refresh
    if integration.token_expires_at and integration.token_expires_at < datetime.utcnow():
        if not refresh_access_token(integration):
            raise Exception("Failed to refresh access token for calendar sync")
    
    access_token = decrypt_token(integration.access_token_encrypted)
    
    credentials = Credentials(
        token=access_token,
        refresh_token=decrypt_token(integration.refresh_token_encrypted),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=Config.GOOGLE_CLIENT_ID,
        client_secret=Config.GOOGLE_CLIENT_SECRET,
        scopes=GMAIL_SCOPES
    )
    
    return build('calendar', 'v3', credentials=credentials)


def _build_event_body(task, base_url: str = None) -> Dict:
    """
    Convert a Task model to Google Calendar event format.
    
    Args:
        task: Task model instance
        base_url: Base URL for CRM task link (optional)
    
    Returns:
        Dict in Google Calendar event format
    """
    user_tz = pytz.timezone(USER_TIMEZONE)
    
    # Determine start time
    if task.scheduled_time:
        # Use scheduled_time if set
        start_dt = task.scheduled_time
        if start_dt.tzinfo is None:
            start_dt = pytz.utc.localize(start_dt)
        start_dt = start_dt.astimezone(user_tz)
    else:
        # Default to 9 AM on due_date
        due_dt = task.due_date
        if due_dt.tzinfo is None:
            due_dt = pytz.utc.localize(due_dt)
        due_dt = due_dt.astimezone(user_tz)
        start_dt = due_dt.replace(hour=9, minute=0, second=0, microsecond=0)
    
    # End time is 1 hour after start
    end_dt = start_dt + timedelta(hours=1)
    
    # Build description
    description_parts = []
    
    if task.description:
        description_parts.append(task.description)
    
    # Add contact info
    if task.contact:
        contact_name = f"{task.contact.first_name} {task.contact.last_name}"
        description_parts.append(f"\nContact: {contact_name}")
        if task.contact.phone:
            description_parts.append(f"Phone: {task.contact.phone}")
        if task.contact.email:
            description_parts.append(f"Email: {task.contact.email}")
    
    # Add task type info
    if task.task_type:
        description_parts.append(f"\nType: {task.task_type.name}")
        if task.task_subtype:
            description_parts.append(f"Subtype: {task.task_subtype.name}")
    
    # Add CRM link if base_url provided
    if base_url:
        task_url = f"{base_url}/tasks/{task.id}"
        description_parts.append(f"\n---\nView in CRM: {task_url}")
    
    description = "\n".join(description_parts)
    
    # Build event
    event = {
        'summary': task.subject,
        'description': description,
        'start': {
            'dateTime': start_dt.isoformat(),
            'timeZone': USER_TIMEZONE,
        },
        'end': {
            'dateTime': end_dt.isoformat(),
            'timeZone': USER_TIMEZONE,
        },
        # Color based on priority
        'colorId': _get_color_for_priority(task.priority),
        # Add reminders
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'popup', 'minutes': 30},
            ],
        },
    }
    
    # Add location if property address is set
    if task.property_address:
        event['location'] = task.property_address
    
    return event


def _get_color_for_priority(priority: str) -> str:
    """
    Get Google Calendar color ID based on task priority.
    
    Google Calendar color IDs:
    1=Blue, 2=Green, 3=Purple, 4=Red, 5=Yellow, 6=Orange, 7=Turquoise, 
    8=Gray, 9=Bold Blue, 10=Bold Green, 11=Bold Red
    """
    color_map = {
        'high': '11',    # Bold Red
        'medium': '5',   # Yellow
        'low': '10',     # Bold Green
    }
    return color_map.get(priority, '1')


def sync_task_to_calendar(task, base_url: str = None) -> bool:
    """
    Create a Google Calendar event for a task.
    Syncs to the assigned user's calendar.
    
    Args:
        task: Task model instance
        base_url: Base URL for CRM task link
    
    Returns:
        True if sync successful, False otherwise
    """
    from models import db, UserEmailIntegration
    
    try:
        # Get the assigned user's email integration
        integration = UserEmailIntegration.query.filter_by(
            user_id=task.assigned_to_id
        ).first()
        
        if not integration or not integration.calendar_sync_enabled:
            logger.debug(f"Calendar sync not enabled for user {task.assigned_to_id}")
            return False
        
        service = _get_calendar_service(integration)
        if not service:
            return False
        
        # Build event body
        event_body = _build_event_body(task, base_url)
        
        # Create event
        event = service.events().insert(
            calendarId='primary',
            body=event_body
        ).execute()
        
        # Store event ID on task
        task.google_calendar_event_id = event.get('id')
        task.calendar_sync_error = None
        db.session.commit()
        
        logger.info(f"Created calendar event {event.get('id')} for task {task.id}")
        return True
        
    except HttpError as e:
        error_msg = f"Calendar API error: {e.resp.status} - {e.reason}"
        logger.error(f"Failed to create calendar event for task {task.id}: {error_msg}")
        task.calendar_sync_error = error_msg
        db.session.commit()
        return False
        
    except Exception as e:
        error_msg = str(e)
        logger.exception(f"Failed to create calendar event for task {task.id}: {error_msg}")
        task.calendar_sync_error = error_msg
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return False


def update_calendar_event(task, base_url: str = None) -> bool:
    """
    Update an existing Google Calendar event for a task.
    
    Args:
        task: Task model instance with google_calendar_event_id set
        base_url: Base URL for CRM task link
    
    Returns:
        True if update successful, False otherwise
    """
    from models import db, UserEmailIntegration
    
    if not task.google_calendar_event_id:
        # No existing event, create one instead
        return sync_task_to_calendar(task, base_url)
    
    try:
        # Get the assigned user's email integration
        integration = UserEmailIntegration.query.filter_by(
            user_id=task.assigned_to_id
        ).first()
        
        if not integration or not integration.calendar_sync_enabled:
            return False
        
        service = _get_calendar_service(integration)
        if not service:
            return False
        
        # Build updated event body
        event_body = _build_event_body(task, base_url)
        
        # Update event
        event = service.events().update(
            calendarId='primary',
            eventId=task.google_calendar_event_id,
            body=event_body
        ).execute()
        
        task.calendar_sync_error = None
        db.session.commit()
        
        logger.info(f"Updated calendar event {task.google_calendar_event_id} for task {task.id}")
        return True
        
    except HttpError as e:
        if e.resp.status == 404:
            # Event was deleted from calendar, create a new one
            logger.warning(f"Calendar event {task.google_calendar_event_id} not found, creating new")
            task.google_calendar_event_id = None
            return sync_task_to_calendar(task, base_url)
        
        error_msg = f"Calendar API error: {e.resp.status} - {e.reason}"
        logger.error(f"Failed to update calendar event for task {task.id}: {error_msg}")
        task.calendar_sync_error = error_msg
        db.session.commit()
        return False
        
    except Exception as e:
        error_msg = str(e)
        logger.exception(f"Failed to update calendar event for task {task.id}: {error_msg}")
        task.calendar_sync_error = error_msg
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return False


def delete_calendar_event(task) -> bool:
    """
    Delete a Google Calendar event for a task.
    
    Args:
        task: Task model instance with google_calendar_event_id set
    
    Returns:
        True if delete successful, False otherwise
    """
    from models import db, UserEmailIntegration
    
    if not task.google_calendar_event_id:
        return True  # Nothing to delete
    
    try:
        # Get the assigned user's email integration
        integration = UserEmailIntegration.query.filter_by(
            user_id=task.assigned_to_id
        ).first()
        
        if not integration or not integration.calendar_sync_enabled:
            return False
        
        service = _get_calendar_service(integration)
        if not service:
            return False
        
        # Delete event
        service.events().delete(
            calendarId='primary',
            eventId=task.google_calendar_event_id
        ).execute()
        
        logger.info(f"Deleted calendar event {task.google_calendar_event_id} for task {task.id}")
        
        task.google_calendar_event_id = None
        task.calendar_sync_error = None
        db.session.commit()
        
        return True
        
    except HttpError as e:
        if e.resp.status == 404:
            # Already deleted, clear the event ID
            task.google_calendar_event_id = None
            task.calendar_sync_error = None
            db.session.commit()
            return True
        
        error_msg = f"Calendar API error: {e.resp.status} - {e.reason}"
        logger.error(f"Failed to delete calendar event for task {task.id}: {error_msg}")
        task.calendar_sync_error = error_msg
        db.session.commit()
        return False
        
    except Exception as e:
        error_msg = str(e)
        logger.exception(f"Failed to delete calendar event for task {task.id}: {error_msg}")
        task.calendar_sync_error = error_msg
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return False


def mark_event_completed(task) -> bool:
    """
    Mark a calendar event as completed by prefixing title with 'Completed:'.
    
    Args:
        task: Task model instance with google_calendar_event_id set
    
    Returns:
        True if update successful, False otherwise
    """
    from models import db, UserEmailIntegration
    
    if not task.google_calendar_event_id:
        return True  # Nothing to update
    
    try:
        # Get the assigned user's email integration
        integration = UserEmailIntegration.query.filter_by(
            user_id=task.assigned_to_id
        ).first()
        
        if not integration or not integration.calendar_sync_enabled:
            return False
        
        service = _get_calendar_service(integration)
        if not service:
            return False
        
        # Get current event
        event = service.events().get(
            calendarId='primary',
            eventId=task.google_calendar_event_id
        ).execute()
        
        # Update title with completed prefix (avoid double prefix)
        current_summary = event.get('summary', '')
        if not current_summary.startswith('Completed:'):
            event['summary'] = f"Completed: {current_summary}"
        
        # Change color to indicate completed (gray)
        event['colorId'] = '8'  # Gray
        
        # Update event
        service.events().update(
            calendarId='primary',
            eventId=task.google_calendar_event_id,
            body=event
        ).execute()
        
        task.calendar_sync_error = None
        db.session.commit()
        
        logger.info(f"Marked calendar event {task.google_calendar_event_id} as completed")
        return True
        
    except HttpError as e:
        if e.resp.status == 404:
            # Event was deleted, nothing to update
            task.google_calendar_event_id = None
            db.session.commit()
            return True
        
        error_msg = f"Calendar API error: {e.resp.status} - {e.reason}"
        logger.error(f"Failed to mark calendar event completed for task {task.id}: {error_msg}")
        task.calendar_sync_error = error_msg
        db.session.commit()
        return False
        
    except Exception as e:
        error_msg = str(e)
        logger.exception(f"Failed to mark calendar event completed for task {task.id}: {error_msg}")
        task.calendar_sync_error = error_msg
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return False


def check_calendar_scope(integration) -> bool:
    """
    Check if user has granted calendar scope.
    
    Args:
        integration: UserEmailIntegration model instance
    
    Returns:
        True if calendar scope is available, False otherwise
    """
    if not integration or not integration.access_token_encrypted:
        return False
    
    try:
        service = _get_calendar_service(integration)
        if not service:
            return False
        
        # Try a simple API call to check access
        service.calendarList().get(calendarId='primary').execute()
        return True
        
    except HttpError as e:
        if e.resp.status in (401, 403):
            # No access - needs re-auth
            return False
        # Other error, assume scope is OK but API issue
        return True
        
    except Exception:
        return False
