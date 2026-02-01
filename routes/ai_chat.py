from flask import Blueprint, jsonify, request, url_for, session, Response, stream_with_context
from flask_login import login_required, current_user
from config import Config
from models import db, Contact, Task, TaskType, TaskSubtype, Transaction, ChatConversation, ChatMessage
from feature_flags import feature_required
from services.ai_service import generate_chat_response, generate_ai_response
from sqlalchemy import or_, func
from tier_config.tier_limits import get_tier_defaults
import openai
import re
import json
from pprint import pprint
from datetime import datetime, date, timedelta

ai_chat = Blueprint('ai_chat', __name__)


# =============================================================================
# RATE LIMITING FOR FREE TIER
# =============================================================================

def get_daily_message_limit():
    """Get the daily AI chat message limit for the current user's organization."""
    org = current_user.organization
    if not org:
        return 10  # Default to free tier limit
    
    # Platform admin orgs have no limit
    if org.is_platform_admin:
        return None
    
    tier = org.subscription_tier or 'free'
    tier_defaults = get_tier_defaults(tier)
    return tier_defaults.get('daily_ai_chat_messages')


def get_daily_message_count():
    """Count how many messages the user has sent today."""
    today_start = datetime.combine(date.today(), datetime.min.time())
    
    count = ChatMessage.query.join(ChatConversation).filter(
        ChatConversation.user_id == current_user.id,
        ChatMessage.role == 'user',
        ChatMessage.created_at >= today_start
    ).count()
    
    return count


def check_rate_limit():
    """
    Check if user has exceeded their daily AI chat message limit.
    
    Returns:
        tuple: (is_allowed: bool, remaining: int or None, limit: int or None)
    """
    limit = get_daily_message_limit()
    
    # No limit (pro/enterprise/platform admin)
    if limit is None:
        return True, None, None
    
    used = get_daily_message_count()
    remaining = max(0, limit - used)
    
    return remaining > 0, remaining, limit


def get_rate_limit_message():
    """Get a clean upgrade message for users who hit their daily limit."""
    return """You've reached your daily limit of 10 messages with B.O.B. on the free plan.

**Upgrade to Pro** to get unlimited conversations with B.O.B., plus access to:
- AI-powered Daily Action Plans
- Transaction Management
- Document Generation
- And much more!

[Click here to upgrade](/org/upgrade?from=chat) and unlock the full power of B.O.B.

--BOB"""

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

FORMATTING RULES (follow exactly):

Use standard Markdown formatting:

**Text Formatting:**
- Use **bold** for emphasis on key terms, names, or important points
- Use `backticks` for specific values, addresses, prices, or technical terms
- Use *italics* sparingly for subtle emphasis

**Lists (IMPORTANT - follow exactly):**
- Start each bullet with a hyphen and space: "- Item"
- NO blank lines between list items
- For nested lists, indent with 2 spaces before the hyphen
- Keep list items to 1-2 lines maximum
- Example:
  - Main point
    - Sub-point (indented 2 spaces)

**Numbered Lists:**
- Start each item with number, period, space: "1. Item"
- NO blank lines between numbered items

**Structure:**
- Use **Bold headers** instead of # markdown headers for section titles
- Keep paragraphs short (2-4 sentences max)
- Use a single blank line between sections
- NEVER use multiple consecutive blank lines

**When drafting emails or messages:**
- Put the subject line on its own line with "**Subject:**" prefix
- Separate the email body with a horizontal rule (---)
- Format the signature cleanly at the end

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
        # Check rate limit for free tier users
        is_allowed, remaining, limit = check_rate_limit()
        if not is_allowed:
            return jsonify({
                "response": get_rate_limit_message(),
                "rate_limited": True
            })
        
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
        # Check rate limit for free tier users
        is_allowed, remaining, limit = check_rate_limit()
        if not is_allowed:
            # Return the upgrade message as a streamed response
            def rate_limit_response():
                message = get_rate_limit_message()
                escaped = message.replace('\n', '\\n').replace('\r', '\\r')
                yield f"data: {escaped}\n\n"
                yield f"data: [DONE]\n\n"
                yield f"data: [FULL_RESPONSE]{message}[/FULL_RESPONSE]\n\n"
            
            return Response(
                stream_with_context(rate_limit_response()),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'X-Accel-Buffering': 'no'
                }
            )
        
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
    """Save a message exchange to chat history (session + database)"""
    try:
        data = request.json
        user_message = data.get('userMessage')
        assistant_response = data.get('assistantResponse')
        conversation_id = data.get('conversationId')
        image_data = data.get('imageData')
        mentioned_contact_ids = data.get('mentionedContactIds')
        
        # File attachment data
        file_url = data.get('fileUrl')
        file_name = data.get('fileName')
        file_type = data.get('fileType')
        file_size = data.get('fileSize')
        file_storage_path = data.get('fileStoragePath')
        
        # Session-based history (for context within current session)
        if 'chat_history' not in session:
            session['chat_history'] = []
        
        # Add the exchange to session history
        session['chat_history'].append({
            "role": "user",
            "content": user_message
        })
        session['chat_history'].append({
            "role": "assistant",
            "content": assistant_response
        })
        
        # Keep only the last 10 exchanges (20 messages) in session
        if len(session['chat_history']) > 20:
            session['chat_history'] = session['chat_history'][-20:]
        
        session.modified = True
        
        # Database persistence
        response_data = {"status": "success"}
        
        if conversation_id:
            # Verify conversation belongs to user
            conversation = ChatConversation.query.filter_by(
                id=conversation_id,
                user_id=current_user.id
            ).first()
            
            if conversation:
                # Save user message with optional attachments
                user_msg = ChatMessage(
                    conversation_id=conversation_id,
                    role='user',
                    content=user_message,
                    image_data=image_data,
                    mentioned_contact_ids=mentioned_contact_ids,
                    file_url=file_url,
                    file_name=file_name,
                    file_type=file_type,
                    file_size=file_size,
                    file_storage_path=file_storage_path
                )
                db.session.add(user_msg)
                
                # Save assistant message
                assistant_msg = ChatMessage(
                    conversation_id=conversation_id,
                    role='assistant',
                    content=assistant_response
                )
                db.session.add(assistant_msg)
                
                # Update conversation timestamp
                conversation.updated_at = datetime.utcnow()
                
                # Generate title if this is the first exchange
                if not conversation.title:
                    try:
                        title = _generate_chat_title(user_message)
                        conversation.title = title
                        response_data['title'] = title
                    except Exception as e:
                        print(f"Error generating title: {e}")
                        # Set a fallback title
                        conversation.title = user_message[:50] + ("..." if len(user_message) > 50 else "")
                        response_data['title'] = conversation.title
                
                db.session.commit()
                response_data['conversationId'] = conversation_id
        
        return jsonify(response_data)
    except Exception as e:
        db.session.rollback()
        print(f"Error saving chat history: {e}")
        return jsonify({"error": str(e)}), 500


def _generate_chat_title(first_message):
    """Generate a short title for the conversation using AI"""
    try:
        client = openai.OpenAI(api_key=Config.OPENAI_API_KEY)
        
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Generate a very short title (3-6 words) for this chat conversation. No quotes, no punctuation at the end. Just the title text."
                },
                {
                    "role": "user",
                    "content": f"First message: {first_message[:500]}"
                }
            ],
            max_tokens=20,
            temperature=0.7
        )
        
        title = response.choices[0].message.content.strip()
        # Clean up the title
        title = title.strip('"\'')
        # Limit length
        if len(title) > 100:
            title = title[:97] + "..."
        return title
    except Exception as e:
        print(f"Title generation error: {e}")
        # Fallback: use first few words of message
        words = first_message.split()[:5]
        return " ".join(words) + ("..." if len(first_message.split()) > 5 else "")


@ai_chat.route('/api/ai-chat/conversations', methods=['GET'])
@login_required
@feature_required('AI_CHAT')
def list_conversations():
    """List all conversations for the current user"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        
        # Get conversations for current user, ordered by most recent
        conversations = ChatConversation.query.filter_by(
            user_id=current_user.id
        ).order_by(ChatConversation.updated_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return jsonify({
            "conversations": [c.to_dict() for c in conversations.items],
            "total": conversations.total,
            "page": page,
            "per_page": per_page,
            "has_next": conversations.has_next,
            "has_prev": conversations.has_prev
        })
    except Exception as e:
        print(f"Error listing conversations: {e}")
        return jsonify({"error": str(e)}), 500


@ai_chat.route('/api/ai-chat/conversations', methods=['POST'])
@login_required
@feature_required('AI_CHAT')
def create_conversation():
    """Create a new chat conversation"""
    try:
        conversation = ChatConversation(
            user_id=current_user.id,
            organization_id=current_user.organization_id
        )
        db.session.add(conversation)
        db.session.commit()
        
        # Clear session history for new conversation
        session['chat_history'] = []
        session.modified = True
        
        return jsonify(conversation.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        print(f"Error creating conversation: {e}")
        return jsonify({"error": str(e)}), 500


@ai_chat.route('/api/ai-chat/conversations/<int:conversation_id>', methods=['GET'])
@login_required
@feature_required('AI_CHAT')
def get_conversation(conversation_id):
    """Get a single conversation with all its messages"""
    try:
        conversation = ChatConversation.query.filter_by(
            id=conversation_id,
            user_id=current_user.id
        ).first()
        
        if not conversation:
            return jsonify({"error": "Conversation not found"}), 404
        
        # Also load messages into session for context
        session['chat_history'] = []
        for msg in conversation.messages.all():
            session['chat_history'].append({
                "role": msg.role,
                "content": msg.content
            })
        session.modified = True
        
        return jsonify(conversation.to_dict(include_messages=True))
    except Exception as e:
        print(f"Error getting conversation: {e}")
        return jsonify({"error": str(e)}), 500


@ai_chat.route('/api/ai-chat/conversations/<int:conversation_id>', methods=['DELETE'])
@login_required
@feature_required('AI_CHAT')
def delete_conversation(conversation_id):
    """Delete a conversation and all its messages, including stored files"""
    try:
        conversation = ChatConversation.query.filter_by(
            id=conversation_id,
            user_id=current_user.id
        ).first()
        
        if not conversation:
            return jsonify({"error": "Conversation not found"}), 404
        
        # Clean up files from storage before deleting conversation
        try:
            from services.supabase_storage import delete_file, CHAT_ATTACHMENTS_BUCKET
            
            # Get all messages with file attachments
            messages_with_files = ChatMessage.query.filter_by(
                conversation_id=conversation_id
            ).filter(ChatMessage.file_storage_path.isnot(None)).all()
            
            for msg in messages_with_files:
                if msg.file_storage_path:
                    try:
                        delete_file(CHAT_ATTACHMENTS_BUCKET, msg.file_storage_path)
                    except Exception as file_error:
                        print(f"Error deleting file {msg.file_storage_path}: {file_error}")
                        # Continue even if file deletion fails
        except Exception as cleanup_error:
            print(f"Error during file cleanup: {cleanup_error}")
            # Continue with conversation deletion even if cleanup fails
        
        db.session.delete(conversation)
        db.session.commit()
        
        return jsonify({"status": "success"})
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting conversation: {e}")
        return jsonify({"error": str(e)}), 500


@ai_chat.route('/api/ai-chat/clear', methods=['POST'])
@login_required
def clear_chat():
    """Clear the chat history from the session (does not delete database records)"""
    if 'chat_history' in session:
        session.pop('chat_history')
    return jsonify({"status": "success"})


# Allowed file types for chat attachments
ALLOWED_CHAT_FILE_TYPES = {
    'text/csv': '.csv',
    'application/pdf': '.pdf',
    'text/plain': '.txt',
    'application/msword': '.doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.ms-excel': '.xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp'
}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


@ai_chat.route('/api/ai-chat/upload', methods=['POST'])
@login_required
@feature_required('AI_CHAT')
def upload_attachment():
    """Upload a file attachment for chat"""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        
        if not file.filename:
            return jsonify({"error": "No file selected"}), 400
        
        # Check file type
        content_type = file.content_type or 'application/octet-stream'
        if content_type not in ALLOWED_CHAT_FILE_TYPES:
            return jsonify({
                "error": f"File type not allowed. Supported types: CSV, PDF, TXT, DOC, DOCX, XLS, XLSX, and images."
            }), 400
        
        # Read file data to check size
        file_data = file.read()
        file_size = len(file_data)
        
        if file_size > MAX_FILE_SIZE:
            return jsonify({
                "error": f"File too large. Maximum size is 10MB."
            }), 400
        
        # Upload to Supabase Storage
        from services.supabase_storage import (
            get_supabase_client, 
            upload_file, 
            get_signed_url,
            CHAT_ATTACHMENTS_BUCKET
        )
        import uuid
        
        # Generate unique storage path
        ext = ALLOWED_CHAT_FILE_TYPES.get(content_type, '')
        unique_filename = f"{uuid.uuid4().hex}{ext}"
        storage_path = f"user_{current_user.id}/{unique_filename}"
        
        # Upload file
        result = upload_file(
            bucket=CHAT_ATTACHMENTS_BUCKET,
            storage_path=storage_path,
            file_data=file_data,
            original_filename=file.filename,
            content_type=content_type
        )
        
        if 'error' in result:
            return jsonify({"error": f"Upload failed: {result['error']}"}), 500
        
        # Get signed URL for access
        signed_url = get_signed_url(CHAT_ATTACHMENTS_BUCKET, storage_path, expires_in=86400 * 7)  # 7 days
        
        return jsonify({
            "url": signed_url,
            "filename": file.filename,
            "type": content_type,
            "size": file_size,
            "storage_path": storage_path
        })
        
    except Exception as e:
        print(f"Error uploading chat attachment: {e}")
        return jsonify({"error": str(e)}), 500


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