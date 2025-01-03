from flask import Blueprint, jsonify, request, url_for
from flask_login import login_required, current_user
import openai
from config import Config
from models import Contact, Task, TaskType, TaskSubtype
import re
import json
from pprint import pprint
from datetime import datetime

ai_chat = Blueprint('ai_chat', __name__)

SYSTEM_PROMPT = """You are B.O.B. (Business Optimization Buddy), an experienced real estate professional with deep expertise in the Houston market and HAR (Houston Association of REALTORS®) procedures. Think of yourself as a knowledgeable, supportive colleague who's always ready to share insights and practical advice.

Communication Style:
- Be direct and genuine - skip phrases like "I hope this message finds you well"
- Keep a professional tone without being overly formal
- Use natural language and contractions
- Acknowledge mistakes directly without over-apologizing
- Skip unnecessary words that don't add value
- Find the middle ground between casual and corporate

Your background includes:
- 15+ years of real estate experience in Houston
- Extensive knowledge of HAR procedures and best practices
- Deep understanding of market trends and property valuation
- Expert negotiation and client relationship skills
- Experience with both residential and commercial properties

When interacting with agents:
- Be professional but personable
- Share real-world examples and practical experiences
- Provide actionable advice based on industry best practices
- Focus on solving real estate challenges first, mentioning CRM features only when naturally relevant
- Address agents by their first name
- Keep conversations efficient but friendly
- Close all conversations with "--BOB"

Your expertise covers:
- Market analysis and property valuation
- Client relationship management and communication
- Contract negotiations and transaction procedures
- Marketing strategies and lead generation
- HAR regulations and compliance
- Property showing best practices
- Closing procedures and documentation

When giving advice:
- Be practical and straightforward
- Share what works without unnecessary elaboration
- Keep it concise but complete
- Address urgent matters directly
- Draw from real estate best practices and market knowledge
- Share practical tips that have worked in similar situations
- Consider both immediate needs and long-term strategy
- Be supportive while staying professional
- Suggest CRM features only when they naturally fit the conversation

Format your responses using markdown-style formatting:
- Use `code` for specific values or technical terms
- Use **bold** for emphasis
- Use bullet points or numbered lists for steps
- Keep paragraphs concise and readable

Email/Message Format:
- Get to the point quickly
- Skip unnecessary formal phrases
- Keep apologies sincere but brief
- End naturally but professionally
- Use simple signatures
- Match formality to the situation and relationship

If a question falls outside your real estate expertise, politely acknowledge your limitations while redirecting to areas where you can provide valuable insights."""

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
        context_message = f"""
# Agent Context
- **Name**: {current_user.first_name} {current_user.last_name}
- **Email**: {current_user.email}
- **Role**: {current_user.role}
- **Current View**: {current_url}

# Page Context
{page_content[:2000]}
"""
        
        if contact_data:
            context_message += f"""
# Contact Details
- **Full Name**: {contact_data['contact']['name']}
- **Email**: {contact_data['contact']['email']}
- **Phone**: {contact_data['contact']['phone']}
- **Location**: {contact_data['contact']['address']}
- **Potential Commission**: ${contact_data['contact']['potential_commission']}

# Contact Notes
{contact_data['contact']['notes']}

# Related Tasks ({len(contact_data['tasks'])} total)
"""
            # Group tasks by status
            tasks_by_status = {}
            
            # Sort tasks into status groups
            for task in contact_data['tasks']:
                status = task['status'].capitalize()  # Normalize status case
                if status not in tasks_by_status:
                    tasks_by_status[status] = []
                
                # Check for overdue tasks
                if task['due_date']:
                    task_date = datetime.strptime(task['due_date'], "%Y-%m-%d %H:%M")
                    if task_date < datetime.now() and status != 'Completed':
                        if 'Overdue' not in tasks_by_status:
                            tasks_by_status['Overdue'] = []
                        tasks_by_status['Overdue'].append(task)
                    else:
                        tasks_by_status[status].append(task)
                else:
                    tasks_by_status[status].append(task)

            # Add tasks to context message by status group
            for status, tasks in tasks_by_status.items():
                if tasks:  # Only show status groups that have tasks
                    context_message += f"\n## {status} Tasks ({len(tasks)})\n"
                    for task in tasks:
                        context_message += f"""
- **{task['type']} - {task['subtype']}**
  - Subject: {task['subject']}
  - Description: {task['description']}
  - Priority: {task['priority']}
  - Due: {task['due_date'] or 'Not set'}
  - Property: {task['property_address'] or 'N/A'}
  - Outcome: {task['outcome'] or 'Pending'}
"""

        # Initialize OpenAI client
        client = openai.OpenAI(api_key=Config.OPENAI_API_KEY)
        
        # Prepare the messages
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"""
# Current Context
{context_message}

# User Query
{user_message}
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
            max_tokens=1000
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