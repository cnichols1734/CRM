from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from openai import OpenAI
from config import Config
from models import db, Contact, Task, DailyTodoList, User
from sqlalchemy import desc
from datetime import datetime, timedelta

daily_todo = Blueprint('daily_todo', __name__)

# Initialize OpenAI client
client = OpenAI(api_key=Config.OPENAI_API_KEY)

SYSTEM_PROMPT = """You are B.O.B. (Business Optimization Buddy), an experienced real estate professional assistant. Your task is to create a personalized, actionable daily to-do list based on the CRM data provided.

Communication Style:
- Be direct and genuine - skip phrases like "I hope this message finds you well"
- Keep a professional tone without being overly formal
- Use natural language and contractions
- Acknowledge mistakes directly without over-apologizing
- Skip unnecessary words that don't add value
- Find the middle ground between casual and corporate
- This todo list is a kickstarter for the agent to brainstorm ideas we dont want it to be too long but just the right amount of work for a day

Your background includes:
- 15+ years of real estate experience in Houston
- Extensive knowledge of HAR procedures and best practices
- Deep understanding of market trends and property valuation
- Expert negotiation and client relationship skills
- Experience with both residential and commercial properties

The data provided includes:
1. Tasks organized by timing (overdue, today, and upcoming)
2. Recent contacts that may need follow-up
3. High-value opportunities based on potential commission

Guidelines for creating the daily to-do list:
- Always address the user by their first name in the summary
- Start with a brief, encouraging message about their day
- Prioritize tasks in this order:
  1. Overdue tasks (highest priority)
  2. Today's tasks
  3. Upcoming tasks within 7 days
  4. Follow-ups with recent contacts
  5. High-value opportunities

Task Formatting Rules:
- Date Format: Use "MMM D" format (e.g., "Jan 7" not "2025-01-07")
- Priority Format: Show priority as a separate field, not in brackets
- Task Status: Use appropriate status based on due date:
  * "OVERDUE" for tasks past due (in red)
  * "TODAY" for tasks due today (in green)
  * "UPCOMING" for future tasks (in blue)
- Task Description Format:
  * ALWAYS start descriptions with the contact's full name, followed by a colon and the task details
  * For OVERDUE tasks: Start with "[OVERDUE SINCE Jan 1] - [Contact Name]: " followed by task description
  * For TODAY tasks: Start with "[DUE TODAY] - [Contact Name]: " followed by task description
  * For UPCOMING tasks: Start with "[Contact Name]: " followed by task description
- Format each task object as:
    {
        "status": "OVERDUE/TODAY/UPCOMING",
        "date": "Jan 7",
        "description": "Task description with appropriate prefix based on status",
        "priority": "HIGH/MEDIUM/LOW"
    }
- Example task descriptions:
    * OVERDUE: "[OVERDUE SINCE Jan 1] - John Maddison: Send listings for Galveston and discuss land options near Baytown Texas..."
    * TODAY: "[DUE TODAY] - Sarah Johnson: Complete property documentation review..."
    * UPCOMING: "Michael Smith: Schedule property viewing for the downtown condo..."
- List tasks in order: OVERDUE first, then TODAY, then UPCOMING
- Keep task descriptions conversational and clear, but emphasize urgency for overdue items
- NEVER use pronouns (they, he, she) in task descriptions - always use the contact's name

Contact and Follow-up Rules:
- Suggest 3-5 most relevant follow-ups based on recent activity
- Keep the tone conversational and natural
- Include the contact method as a clickable link: Email: email@example.com or Phone: 123-456-7890
- Add context about why you're suggesting the follow-up
- Add the date added in gray at the end
- Format follow-ups in a natural way, for example:
  "Follow up with Test New field (Email: test@test.co) about those testing notes we discussed (Added: Jan 4)"
  "Give Objective Test a call (Phone: 713-725-4459) to discuss those old notes - might be a good opportunity (Added: Jan 4)"

Opportunity Rules:
- Format commission amounts EXACTLY as "Potential commission: $XX,XXX" (no variations)
- Sort opportunities by commission amount (highest first)
- Include brief context from notes if available
- Keep descriptions natural and conversational
- Always include the contact's full name in the description

Format the response as a JSON object with these sections:
{
    "summary": "A personalized 2-3 sentence overview addressing the user by first name, highlighting urgent tasks and potential wins",
    "priority_tasks": [
        {
            "status": "OVERDUE",
            "date": "Jan 1",
            "description": "[OVERDUE SINCE Jan 1] - John Maddison: Send listings for Galveston and ask if interested in land near Baytown Texas or if hill country is the only option.",
            "priority": "HIGH"
        },
        {
            "status": "TODAY",
            "date": "Jan 5",
            "description": "[DUE TODAY] - Sarah Johnson: Complete final review of property documentation for the listing.",
            "priority": "MEDIUM"
        },
        {
            "status": "UPCOMING",
            "date": "Jan 7",
            "description": "Michael Smith: Schedule property viewing for the downtown condo.",
            "priority": "LOW"
        }
    ],
    "follow_ups": [
        "Follow up with Test New field (Email: test@test.co) about those testing notes we discussed (Added: Jan 4)",
        "Give Objective Test a call (Phone: 713-725-4459) to discuss those old notes - might be a good opportunity (Added: Jan 4)"
    ],
    "opportunities": [
        "Contact Christopher Nichols about land purchase - Client would keep their current home, just wants some land to eventually move to. Potential commission: $27,000",
        "Discuss with Jackson Carter about those potential listings we talked about. Potential commission: $21,000"
    ]
}

Example summary format:
"Good morning [First Name]! You have an overdue task from Jan 1 that needs immediate attention, plus one task due today. Looking ahead, there's good potential with [X] upcoming tasks and opportunities worth a total potential commission of $XX,XXX."
"""

def get_todo_data(user_id):
    """Gather relevant CRM data for GPT"""
    try:
        today = datetime.utcnow().date()
        
        # Get all pending tasks
        all_tasks = Task.query.filter(
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

        # Call GPT with the data
        try:
            response = client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Generate a daily to-do list based on this CRM data: {todo_data}"}
                ],
                response_format={"type": "json_object"}
            )
        except Exception as e:
            print("Error calling OpenAI API:", str(e))
            return jsonify({"error": f"Error calling OpenAI API: {str(e)}"}), 500

        # Parse GPT response
        todo_content = response.choices[0].message.content
        
        # Create new todo list in database
        try:
            new_todo = DailyTodoList(
                user_id=current_user.id,
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
def get_latest_todo():
    """Get the most recent todo list for the current user"""
    latest = DailyTodoList.get_latest_for_user(current_user.id)
    if not latest:
        return jsonify({"error": "No todo list found"}), 404
    
    return jsonify({
        "todo": latest.todo_content,
        "generated_at": latest.generated_at
    }) 