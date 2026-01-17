from flask import Blueprint, jsonify, request, url_for, session, Response, stream_with_context
from flask_login import login_required, current_user
from config import Config
from models import Contact, Task, TaskType, TaskSubtype
from feature_flags import feature_required
from services.ai_service import generate_chat_response
import openai
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
- For lists:
  - Use hyphens (-) for unordered lists
  - Use numbers (1.) for ordered lists
  - Indent sublists with exactly 2 spaces
  - Maximum of 2 nesting levels
  - Keep list items concise (1-2 sentences max)
  - Do NOT add blank lines between list items
  - Do NOT add blank lines between a main bullet and its sub-bullet
- Keep paragraphs concise and readable
- NEVER add multiple consecutive blank lines
- Use a single blank line only between major sections

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
    # Filter by organization for multi-tenancy security
    contact = Contact.query.filter_by(
        id=contact_id,
        organization_id=current_user.organization_id
    ).first()
    
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
@feature_required('AI_CHAT')
def chat():
    try:
        data = request.json
        user_message = data.get('message')
        page_content = data.get('pageContent')
        current_url = data.get('currentUrl')
        clear_history = data.get('clearHistory', False)

        # Initialize or clear session history if requested
        if clear_history or 'chat_history' not in session:
            session['chat_history'] = []

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

        # Prepare the messages with history
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        
        # Add conversation history
        messages.extend(session['chat_history'])
        
        # Add current context and user message
        messages.append({
            "role": "user",
            "content": f"""
# Current Context
{context_message}

# User Query
{user_message}
"""
        })

        # Debug printing
        print("\n" + "="*50)
        print("SENDING TO AI (using centralized AI service with fallback chain):")
        print("="*50)
        print("\nSystem Prompt:")
        print("-"*50)
        print(SYSTEM_PROMPT)
        print("\nConversation History:")
        print("-"*50)
        for msg in session['chat_history']:
            print(f"{msg['role'].upper()}: {msg['content']}\n")
        print("\nCurrent Context and User Message:")
        print("-"*50)
        print(messages[-1]["content"])
        print("="*50 + "\n")

        # Call AI using centralized service (GPT-5.1 → GPT-5-mini → GPT-4o fallback)
        assistant_response = generate_chat_response(
            messages=messages,
            temperature=0.8,
            max_tokens=2000
        )

        # Update session history with the new exchange
        session['chat_history'].append({
            "role": "user",
            "content": user_message
        })
        session['chat_history'].append({
            "role": "assistant",
            "content": assistant_response
        })

        # Keep only the last 10 exchanges (20 messages) to prevent session bloat
        if len(session['chat_history']) > 20:
            session['chat_history'] = session['chat_history'][-20:]

        # Make sure to save the session
        session.modified = True

        return jsonify({
            "response": assistant_response
        })

    except Exception as e:
        print(f"\nError in chat route: {str(e)}\n")
        return jsonify({
            "error": str(e)
        }), 500

@ai_chat.route('/api/ai-chat/stream', methods=['POST'])
@login_required
@feature_required('AI_CHAT')
def chat_stream():
    """Stream AI chat response using GPT-5.1 with Server-Sent Events"""
    try:
        data = request.json
        user_message = data.get('message')
        page_content = data.get('pageContent', '')
        current_url = data.get('currentUrl', '')
        clear_history = data.get('clearHistory', False)

        # Initialize or clear session history if requested
        if clear_history or 'chat_history' not in session:
            session['chat_history'] = []

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
            # Add task summary (simplified for streaming context)
            for task in contact_data['tasks'][:5]:  # Limit to 5 tasks for context
                context_message += f"- {task['type']}: {task['subject']} (Due: {task['due_date'] or 'Not set'})\n"

        # Build conversation for the AI
        conversation_history = ""
        for msg in session.get('chat_history', []):
            role = "User" if msg['role'] == 'user' else "BOB"
            conversation_history += f"\n{role}: {msg['content']}\n"

        # Full user prompt with context
        full_user_prompt = f"""
{context_message}

# Conversation History
{conversation_history}

# Current User Message
{user_message}
"""

        def generate():
            """Generator that yields SSE events"""
            full_response = ""
            
            try:
                client = openai.OpenAI(api_key=Config.OPENAI_API_KEY)
                
                # Use GPT-5.1 Responses API with streaming
                stream = client.responses.create(
                    model="gpt-5.1",
                    instructions=SYSTEM_PROMPT,
                    input=full_user_prompt,
                    stream=True
                )
                
                for event in stream:
                    # Handle different event types from Responses API
                    if hasattr(event, 'type'):
                        if event.type == "response.output_text.delta":
                            chunk = event.delta
                            full_response += chunk
                            # Escape newlines for SSE
                            escaped = chunk.replace('\n', '\\n').replace('\r', '\\r')
                            yield f"data: {escaped}\n\n"
                        elif event.type == "response.completed":
                            # Stream completed
                            pass
                    elif hasattr(event, 'delta') and event.delta:
                        # Fallback for different event structure
                        chunk = event.delta
                        full_response += chunk
                        escaped = chunk.replace('\n', '\\n').replace('\r', '\\r')
                        yield f"data: {escaped}\n\n"
                
            except Exception as e:
                print(f"Streaming error with GPT-5.1: {e}")
                # Fallback to GPT-4o with Chat Completions streaming
                try:
                    client = openai.OpenAI(api_key=Config.OPENAI_API_KEY)
                    stream = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": full_user_prompt}
                        ],
                        stream=True
                    )
                    
                    for chunk in stream:
                        if chunk.choices[0].delta.content:
                            content = chunk.choices[0].delta.content
                            full_response += content
                            escaped = content.replace('\n', '\\n').replace('\r', '\\r')
                            yield f"data: {escaped}\n\n"
                            
                except Exception as fallback_error:
                    print(f"Fallback streaming error: {fallback_error}")
                    yield f"data: Sorry, I encountered an error. Please try again.\n\n"
            
            # Signal completion and send the full response for history
            yield f"data: [DONE]\n\n"
            yield f"data: [FULL_RESPONSE]{full_response}[/FULL_RESPONSE]\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no'  # Disable nginx buffering
            }
        )

    except Exception as e:
        print(f"Error in chat_stream: {str(e)}")
        return jsonify({"error": str(e)}), 500


@ai_chat.route('/api/ai-chat/history', methods=['POST'])
@login_required
def save_chat_history():
    """Save a message exchange to chat history (called after streaming completes)"""
    try:
        data = request.json
        user_message = data.get('userMessage')
        assistant_response = data.get('assistantResponse')
        
        if 'chat_history' not in session:
            session['chat_history'] = []
        
        # Add the exchange to history
        session['chat_history'].append({
            "role": "user",
            "content": user_message
        })
        session['chat_history'].append({
            "role": "assistant",
            "content": assistant_response
        })
        
        # Keep only the last 10 exchanges (20 messages)
        if len(session['chat_history']) > 20:
            session['chat_history'] = session['chat_history'][-20:]
        
        session.modified = True
        
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ai_chat.route('/api/ai-chat/clear', methods=['POST'])
@login_required
def clear_chat():
    """Clear the chat history from the session"""
    if 'chat_history' in session:
        session.pop('chat_history')
    return jsonify({"status": "success"})