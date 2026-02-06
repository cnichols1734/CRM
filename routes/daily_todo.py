from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from config import Config
from models import db, Contact, Task, DailyTodoList, User
from feature_flags import feature_required
from sqlalchemy import desc
from sqlalchemy.orm import joinedload
from datetime import datetime, timedelta
from services.ai_service import generate_ai_response

daily_todo = Blueprint('daily_todo', __name__)

SYSTEM_PROMPT = """You are B.O.B. (Business Optimization Buddy), a Houston real estate expert with 15+ years experience (HAR, residential, commercial). Create a personalized daily to-do list from the CRM data provided.

Tone: Direct, professional but not stiff. Use contractions, skip filler. Keep it concise — just the right amount of work for one day.

Respond with a JSON object containing these keys:

"summary": 2-3 sentences addressing the user by first name. Highlight urgent items and potential wins. No marketing ideas here.

"marketing_ideas": 3-5 creative, non-CRM-based ideas. Format: "Channel: Description" (e.g., "Instagram Reels: 30-second video — '3 things buyers miss during showings'"). Vary channels daily. Keep ideas same-day actionable. Localize to Houston. Use current date context — never reference wrong month/year.

"priority_tasks": Array of task objects ordered OVERDUE → TODAY → UPCOMING. Each task:
  {"status": "OVERDUE/TODAY/UPCOMING", "date": "Jan 7", "description": "...", "priority": "HIGH/MEDIUM/LOW"}
  Description format by status:
  - OVERDUE: "[OVERDUE SINCE Jan 1] - Contact Name: task details"
  - TODAY: "[DUE TODAY] - Contact Name: task details"
  - UPCOMING: "Contact Name: task details"
  Always use full contact names, never pronouns.

"follow_ups": 3-5 plain text strings. Include contact info as (Email: x@x.com) or (Phone: 123-456-7890). Add context for why. End with (Added: Jan 4).
  Example: "Follow up with Jane Doe (Email: jane@test.co) about the listing we discussed (Added: Jan 4)"

"opportunities": Sorted by commission (highest first). Include contact full name and brief context. Format commission as "Potential commission: $XX,XXX".
  Example: "Contact John Smith about land purchase - wants acreage outside the city. Potential commission: $27,000"
"""

def get_todo_data(user_id):
    """Gather relevant CRM data for GPT"""
    try:
        today = datetime.utcnow().date()
        
        # Get all pending tasks (eager-load contacts to avoid N+1 queries)
        all_tasks = Task.query.options(joinedload(Task.contact)).filter(
            Task.assigned_to_id == user_id,
            Task.status == 'pending',
            Task.due_date <= datetime.utcnow() + timedelta(days=7)
        ).order_by(Task.due_date).all()

        # Organize tasks by due date
        overdue_tasks = []
        today_tasks = []
        upcoming_tasks = []

        for task in all_tasks:
            # Determine task status based on due date
            status = "UPCOMING"
            if task.due_date:
                if task.due_date.date() < today:
                    status = "OVERDUE"
                elif task.due_date.date() == today:
                    status = "TODAY"

            task_data = {
                "status": status,
                "date": task.due_date.strftime("%Y-%m-%d") if task.due_date else None,
                "description": task.description or "",
                "subject": task.subject or "",
                "priority": task.priority.upper() if task.priority else "MEDIUM",
                "contact_name": f"{task.contact.first_name} {task.contact.last_name}" if task.contact else None
            }
            
            if task.due_date:
                if task.due_date.date() < today:
                    overdue_tasks.append(task_data)
                elif task.due_date.date() == today:
                    today_tasks.append(task_data)
                else:
                    upcoming_tasks.append(task_data)

        # Get recently created contacts (last 10)
        recent_contacts = Contact.query.filter_by(user_id=user_id)\
            .order_by(desc(Contact.created_at))\
            .limit(10).all()

        # Get top contacts by commission
        top_commission_contacts = Contact.query.filter_by(user_id=user_id)\
            .filter(Contact.potential_commission.isnot(None))\
            .order_by(desc(Contact.potential_commission))\
            .limit(5).all()

        # Get current user's first name
        user = db.session.get(User, user_id)
        user_first_name = user.first_name if user else "Agent"

        return {
            "user_first_name": user_first_name,
            "tasks": {
                "overdue": overdue_tasks,
                "today": today_tasks,
                "upcoming": upcoming_tasks
            },
            "recent_contacts": [{
                "name": f"{contact.first_name} {contact.last_name}",
                "created_at": contact.created_at.strftime("%Y-%m-%d") if contact.created_at else None,
                "email": contact.email or "",
                "phone": contact.phone or "",
                "notes": contact.notes or "",
                "days_since_creation": (datetime.utcnow() - contact.created_at).days if contact.created_at else None
            } for contact in recent_contacts],
            "opportunities": [{
                "name": f"{contact.first_name} {contact.last_name}",
                "potential_commission": float(contact.potential_commission or 0),
                "notes": contact.notes or ""
            } for contact in top_commission_contacts]
        }
    except Exception as e:
        print(f"Error in get_todo_data: {str(e)}")
        raise

@daily_todo.route('/api/daily-todo/generate', methods=['POST'])
@login_required
@feature_required('AI_DAILY_TODO')
def generate_todo():
    """Generate a new daily todo list using GPT"""
    try:
        # Get force parameter from request
        force = request.json.get('force', False)
        
        # Check if we need to generate a new list, unless force is True
        if not force and not DailyTodoList.should_generate_new(current_user.id):
            latest = DailyTodoList.get_latest_for_user(current_user.id)
            return jsonify({"todo": latest.todo_content, "generated_at": latest.generated_at})

        # Gather CRM data
        try:
            todo_data = get_todo_data(current_user.id)
        except Exception as e:
            print("Error gathering todo data:", str(e))
            return jsonify({"error": f"Error gathering todo data: {str(e)}"}), 500

        # Call AI with the data (using centralized AI service with fallback chain)
        try:
            user_prompt = f"Generate a daily to-do list based on the CRM data and the current-date context.\n\nCRM Data: {todo_data}\n\nContext: {{'current_date_utc': '{datetime.utcnow().strftime('%Y-%m-%d')}', 'current_month_name': '{datetime.utcnow().strftime('%B')}', 'current_year': '{datetime.utcnow().strftime('%Y')}', 'weekday_name': '{datetime.utcnow().strftime('%A')}', 'timezone_hint': 'America/Chicago (Houston)'}}"
            
            todo_content = generate_ai_response(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.7,
                json_mode=True,
                reasoning_effort="low"
            )
        except Exception as e:
            print("Error calling AI service:", str(e))
            return jsonify({"error": f"Error calling AI service: {str(e)}"}), 500
        
        # Create new todo list in database
        try:
            new_todo = DailyTodoList(
                user_id=current_user.id,
                organization_id=current_user.organization_id,
                todo_content=todo_content
            )
            db.session.add(new_todo)
            db.session.commit()
        except Exception as e:
            print("Error saving todo list:", str(e))
            return jsonify({"error": f"Error saving todo list: {str(e)}"}), 500

        return jsonify({
            "todo": todo_content,
            "generated_at": new_todo.generated_at
        })

    except Exception as e:
        print("Unexpected error in generate_todo:", str(e))
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@daily_todo.route('/api/daily-todo/latest', methods=['GET'])
@login_required
@feature_required('AI_DAILY_TODO')
def get_latest_todo():
    """Get the most recent todo list for the current user"""
    latest = DailyTodoList.get_latest_for_user(current_user.id)
    if not latest:
        return jsonify({"error": "No todo list found"}), 404
    
    return jsonify({
        "todo": latest.todo_content,
        "generated_at": latest.generated_at
    }) 