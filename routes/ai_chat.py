from flask import Blueprint, jsonify, request, url_for, session, Response, stream_with_context
from flask_login import login_required, current_user
from config import Config
from models import Contact, Task, TaskType, TaskSubtype, Transaction
from feature_flags import feature_required
from services.ai_service import generate_chat_response, generate_ai_response
from sqlalchemy import or_, func
import openai
import re
import json
from pprint import pprint
from datetime import datetime, date, timedelta

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
        user_message = data.get('message', '')
        page_content = data.get('pageContent', '')
        current_url = data.get('currentUrl', '')
        clear_history = data.get('clearHistory', False)
        image_data = data.get('image')  # Base64 image data
        mentioned_contact_ids = data.get('mentionedContactIds', [])

        # Initialize or clear session history if requested
        if clear_history or 'chat_history' not in session:
            session['chat_history'] = []

        # Get contact and task data if viewing a contact
        contact_data = get_contact_and_tasks(current_url)
        
        # Get mentioned contacts data
        mentioned_contacts_data = []
        if mentioned_contact_ids:
            for contact_id in mentioned_contact_ids:
                contact = Contact.query.filter_by(
                    id=contact_id,
                    organization_id=current_user.organization_id
                ).first()
                if contact:
                    # Get tasks for this contact
                    tasks = Task.query.filter_by(contact_id=contact_id).limit(5).all()
                    mentioned_contacts_data.append({
                        "name": f"{contact.first_name} {contact.last_name}",
                        "email": contact.email,
                        "phone": contact.phone,
                        "address": f"{contact.street_address or ''}, {contact.city or ''}, {contact.state or ''} {contact.zip_code or ''}".strip(', '),
                        "notes": contact.notes,
                        "potential_commission": float(contact.potential_commission) if contact.potential_commission else None,
                        "tasks": [{"subject": t.subject, "status": t.status, "due_date": t.due_date.strftime("%Y-%m-%d") if t.due_date else None} for t in tasks]
                    })
        
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
        
        # Add mentioned contacts context
        if mentioned_contacts_data:
            context_message += "\n# Mentioned Contacts\n"
            for mc in mentioned_contacts_data:
                context_message += f"""
## {mc['name']}
- Email: {mc['email'] or 'N/A'}
- Phone: {mc['phone'] or 'N/A'}
- Address: {mc['address'] or 'N/A'}
- Potential Commission: ${mc['potential_commission'] or 0:,.0f}
- Notes: {(mc['notes'] or '')[:300]}
- Tasks: {len(mc['tasks'])} recent tasks
"""
        
        if contact_data:
            context_message += f"""
# Contact Details (Current Page)
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
        
        # Add image context if present
        if image_data:
            context_message += "\n# Image Attached\nThe user has attached an image to this message. Please analyze it and incorporate your observations into your response.\n"

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
                
                # Check if we have an image attachment
                if image_data:
                    # Use Chat Completions API with vision for images
                    # Build content array with text and image
                    user_content = [
                        {"type": "text", "text": full_user_prompt}
                    ]
                    
                    # Add image to content
                    user_content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_data}",
                            "detail": "auto"
                        }
                    })
                    
                    # Stream with Chat Completions API (vision-compatible)
                    stream = client.chat.completions.create(
                        model="gpt-5.1",
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_content}
                        ],
                        stream=True
                    )
                    
                    for chunk in stream:
                        if chunk.choices[0].delta.content:
                            content = chunk.choices[0].delta.content
                            full_response += content
                            escaped = content.replace('\n', '\\n').replace('\r', '\\r')
                            yield f"data: {escaped}\n\n"
                else:
                    # Use GPT-5.1 Responses API with streaming (no image)
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
                # Fallback to GPT-4.1-mini with Chat Completions streaming
                try:
                    client = openai.OpenAI(api_key=Config.OPENAI_API_KEY)
                    
                    # Build messages for fallback
                    if image_data:
                        user_content = [
                            {"type": "text", "text": full_user_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}", "detail": "auto"}}
                        ]
                    else:
                        user_content = full_user_prompt
                    
                    stream = client.chat.completions.create(
                        model="gpt-4.1-mini",
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_content}
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


@ai_chat.route('/api/ai-chat/search-contacts', methods=['GET'])
@login_required
def search_contacts():
    """Search contacts for @ mention autocomplete - current user's contacts only"""
    query = request.args.get('q', '').strip()
    
    # Build filter for current user's contacts only
    filters = [
        Contact.user_id == current_user.id
    ]
    
    if query:
        filters.append(
            or_(
                Contact.first_name.ilike(f'{query}%'),
                Contact.last_name.ilike(f'{query}%'),
                func.concat(Contact.first_name, ' ', Contact.last_name).ilike(f'{query}%')
            )
        )
    
    contacts = Contact.query.filter(*filters).limit(10).all()
    
    return jsonify([{
        'id': c.id,
        'name': f'{c.first_name} {c.last_name}',
        'email': c.email or ''
    } for c in contacts])


@ai_chat.route('/api/ai-chat/quick-action', methods=['POST'])
@login_required
@feature_required('AI_CHAT')
def quick_action():
    """Handle quick action requests (summarize tasks, top contacts, pipeline)"""
    try:
        data = request.json
        action = data.get('action')
        
        if action == 'summarize_tasks':
            return _summarize_tasks()
        elif action == 'top_contacts':
            return _top_contacts()
        elif action == 'pipeline_overview':
            return _pipeline_overview()
        else:
            return jsonify({"error": "Unknown action"}), 400
            
    except Exception as e:
        print(f"Quick action error: {str(e)}")
        return jsonify({"error": str(e)}), 500


def _summarize_tasks():
    """Summarize open tasks for the current user"""
    # Get all pending tasks for the current user
    tasks = Task.query.filter(
        Task.assigned_to_id == current_user.id,
        Task.status == 'pending'
    ).order_by(Task.due_date.asc()).all()
    
    if not tasks:
        return jsonify({"response": "You don't have any open tasks right now. Great job staying on top of things!\n\n--BOB"})
    
    # Format tasks for AI
    task_data = []
    today = datetime.now()
    overdue_count = 0
    
    for task in tasks:
        is_overdue = task.due_date and task.due_date < today
        if is_overdue:
            overdue_count += 1
        
        task_info = {
            "type": task.task_type.name if task.task_type else "Task",
            "subtype": task.task_subtype.name if task.task_subtype else "",
            "subject": task.subject,
            "priority": task.priority,
            "due_date": task.due_date.strftime("%Y-%m-%d") if task.due_date else "No due date",
            "is_overdue": is_overdue,
            "contact": f"{task.contact.first_name} {task.contact.last_name}" if task.contact else "No contact"
        }
        task_data.append(task_info)
    
    # Create AI prompt
    user_prompt = f"""
{current_user.first_name} has {len(tasks)} open tasks ({overdue_count} overdue). Please provide a concise summary:

Tasks:
{json.dumps(task_data, indent=2)}

Provide:
1. A brief overview of their task load
2. Top 3 priorities they should focus on today
3. Any overdue items that need immediate attention

Keep the response concise and actionable.
"""
    
    response = generate_ai_response(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        reasoning_effort="low"
    )
    
    return jsonify({"response": response})


def _top_contacts():
    """Get top 3 contacts the user should reach out to"""
    # Get all contacts for the current user
    contacts = Contact.query.filter(
        Contact.organization_id == current_user.organization_id,
        Contact.user_id == current_user.id
    ).all()
    
    if not contacts:
        return jsonify({"response": "You don't have any contacts yet. Start building your network!\n\n--BOB"})
    
    today = date.today()
    scored_contacts = []
    
    for contact in contacts:
        # Calculate days since last contact
        if contact.last_contact_date:
            days_since = (today - contact.last_contact_date).days
        else:
            days_since = 999  # Never contacted = high priority
        
        # Calculate priority score: commission potential * days since contact
        commission = float(contact.potential_commission) if contact.potential_commission else 0
        priority_score = commission * min(days_since, 365)  # Cap at 1 year
        
        scored_contacts.append({
            "name": f"{contact.first_name} {contact.last_name}",
            "email": contact.email or "No email",
            "phone": contact.phone or "No phone",
            "days_since_contact": days_since,
            "potential_commission": commission,
            "priority_score": priority_score,
            "notes": (contact.notes or "")[:200]  # Truncate notes
        })
    
    # Sort by priority score (descending) and take top 3
    top_3 = sorted(scored_contacts, key=lambda x: x['priority_score'], reverse=True)[:3]
    
    # Create AI prompt
    user_prompt = f"""
Based on priority scoring (commission potential x days since last contact), here are {current_user.first_name}'s top 3 contacts to reach out to:

{json.dumps(top_3, indent=2)}

For each contact, provide:
1. Why they're a priority (based on the data)
2. A suggested approach or talking point
3. Best time to reach out (based on real estate best practices)

Keep suggestions brief and actionable.
"""
    
    response = generate_ai_response(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        reasoning_effort="medium"
    )
    
    return jsonify({"response": response})


def _pipeline_overview():
    """Get a quick overview of the transaction pipeline"""
    # Get transactions for the organization
    try:
        transactions = Transaction.query.filter(
            Transaction.organization_id == current_user.organization_id
        ).all()
    except:
        transactions = []
    
    if not transactions:
        return jsonify({"response": "No active transactions in your pipeline yet. Let's get some deals going!\n\n--BOB"})
    
    # Group by stage
    stages = {}
    total_value = 0
    
    for txn in transactions:
        stage = txn.stage or 'Unknown'
        if stage not in stages:
            stages[stage] = {"count": 0, "value": 0}
        stages[stage]["count"] += 1
        
        # Try to get the price
        price = 0
        if hasattr(txn, 'sale_price') and txn.sale_price:
            price = float(txn.sale_price)
        elif hasattr(txn, 'list_price') and txn.list_price:
            price = float(txn.list_price)
        
        stages[stage]["value"] += price
        total_value += price
    
    # Create AI prompt
    user_prompt = f"""
Here's {current_user.first_name}'s current transaction pipeline:

Pipeline Summary:
- Total Transactions: {len(transactions)}
- Total Value: ${total_value:,.0f}

By Stage:
{json.dumps(stages, indent=2)}

Provide:
1. A brief overview of pipeline health
2. Key observations (deals stuck, opportunities, etc.)
3. One actionable suggestion to move deals forward

Keep it concise and practical.
"""
    
    response = generate_ai_response(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        reasoning_effort="low"
    )
    
    return jsonify({"response": response})