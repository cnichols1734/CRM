from flask import Blueprint, jsonify, request, url_for
from flask_login import login_required, current_user
import openai
from config import Config
from models import Contact, Task, TaskType, TaskSubtype
import re
import json
from pprint import pprint

ai_chat = Blueprint('ai_chat', __name__)

SYSTEM_PROMPT = """You are a virtual assistant integrated into Origen Connect, a specialized CRM platform designed for Origen Realty agents. Origen Connect is a comprehensive real estate CRM that helps agents:

• Track and manage client contacts with detailed profiles
• Create and monitor tasks with specific types (calls, meetings, showings, etc.)
• View a dynamic dashboard showing:
  - Top contacts ranked by potential commission
  - Upcoming tasks and deadlines
  - Client interaction history
  - Performance metrics and goals

Your primary goal is to assist Origen Realty agents with real estate–related questions, especially those concerning the Houston Association of REALTORS® (HAR). You have access to the agent's current view in the CRM, including contact details, tasks, and dashboard data when available.

When responding:
• Address the agent by their first name to make the interaction personal
• Reference relevant CRM features that could help with their query
• Suggest specific actions they can take within Origen Connect
• Provide practical guidance for real estate transactions, listing procedures, and client interactions

Format your responses using markdown-style formatting:
- Use `code` for technical terms or specific values
- Use **bold** for emphasis
- Use bullet points or numbered lists for steps or multiple items
- Use paragraphs to separate different topics

If a question falls outside the scope of real estate or Origen Connect's capabilities, politely decline to answer while suggesting relevant CRM features that might be helpful."""

def get_contact_and_tasks(url):
    """Extract contact data and related tasks if viewing a contact page."""
    # Check if we're on a contact view page
    contact_match = re.search(r'/contact/(\d+)', url)
    if not contact_match:
        return None
    
    contact_id = contact_match.group(1)
    contact = Contact.query.get(contact_id)
    
    if not contact:
        return None
        
    # Get all tasks for this contact
    tasks = Task.query.filter_by(contact_id=contact_id).all()
    
    # Format contact data
    contact_data = {
        "contact": {
            "name": f"{contact.first_name} {contact.last_name}",
            "email": contact.email,
            "phone": contact.phone,
            "address": f"{contact.street_address}, {contact.city}, {contact.state} {contact.zip_code}",
            "notes": contact.notes,
            "potential_commission": float(contact.potential_commission) if contact.potential_commission else None
        },
        "tasks": []
    }
    
    # Format task data
    for task in tasks:
        task_data = {
            "type": task.task_type.name,
            "subtype": task.task_subtype.name,
            "subject": task.subject,
            "description": task.description,
            "status": task.status,
            "priority": task.priority,
            "due_date": task.due_date.strftime("%Y-%m-%d %H:%M") if task.due_date else None,
            "completed_at": task.completed_at.strftime("%Y-%m-%d %H:%M") if task.completed_at else None,
            "outcome": task.outcome,
            "property_address": task.property_address
        }
        contact_data["tasks"].append(task_data)
    
    return contact_data

@ai_chat.route('/api/ai-chat', methods=['POST'])
@login_required
def chat():
    try:
        data = request.json
        user_message = data.get('message')
        page_content = data.get('pageContent')
        current_url = data.get('currentUrl')

        # Get contact and task data if viewing a contact
        contact_data = get_contact_and_tasks(current_url)
        
        # Prepare the context message with agent info
        context_message = f"""Agent Information:
- Name: {current_user.first_name} {current_user.last_name}
- Email: {current_user.email}
- Role: {current_user.role}

Context: User is viewing page: {current_url}

Page Content:
{page_content[:2000]}  # Including first 2000 characters of page content
"""
        
        if contact_data:
            context_message += f"""
Additional Contact Information:
- Name: {contact_data['contact']['name']}
- Email: {contact_data['contact']['email']}
- Phone: {contact_data['contact']['phone']}
- Address: {contact_data['contact']['address']}
- Notes: {contact_data['contact']['notes']}
- Potential Commission: ${contact_data['contact']['potential_commission']}

Related Tasks:
"""
            for task in contact_data['tasks']:
                context_message += f"""
• {task['type']} - {task['subtype']}
  Subject: {task['subject']}
  Description: {task['description']}
  Status: {task['status']}
  Priority: {task['priority']}
  Due Date: {task['due_date']}
  Completed: {task['completed_at'] or 'No'}
  Outcome: {task['outcome'] or 'N/A'}
  Property: {task['property_address'] or 'N/A'}
"""

        # Initialize OpenAI client
        client = openai.OpenAI(api_key=Config.OPENAI_API_KEY)
        
        # Prepare the messages
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"""
{context_message}

User question: {user_message}
"""}
        ]

        # Debug printing
        print("\n" + "="*50)
        print("SENDING TO GPT:")
        print("="*50)
        print("\nSystem Prompt:")
        print("-"*50)
        print(SYSTEM_PROMPT)
        print("\nContext and User Message:")
        print("-"*50)
        print(messages[1]["content"])
        print("="*50 + "\n")

        if contact_data:
            print("\nDetailed Contact Data:")
            print("-"*50)
            pprint(contact_data)
            print("="*50 + "\n")

        # Call GPT-4 Turbo
        response = client.chat.completions.create(
            model="gpt-4-1106-preview",
            messages=messages,
            temperature=0.7,
            max_tokens=500
        )

        # Print GPT's response
        print("\nGPT Response:")
        print("-"*50)
        print(response.choices[0].message.content)
        print("="*50 + "\n")

        return jsonify({
            "response": response.choices[0].message.content
        })

    except Exception as e:
        print(f"\nError in chat route: {str(e)}\n")
        return jsonify({
            "error": str(e)
        }), 500 