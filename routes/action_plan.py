from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from models import db, ActionPlan
from config import Config
from services.ai_service import generate_ai_response
import json
import logging

# Set up logging for action plan generation
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

action_plan_bp = Blueprint('action_plan', __name__)

# System prompt for generating the action plan
ACTION_PLAN_SYSTEM_PROMPT = """Using the agent's questionnaire responses, create a personalized 2026 Lead Generation Plan of Action.

## CRITICAL RESTRICTIONS - NEVER RECOMMEND THESE:
- NEVER suggest doing Facebook Live or any live video streaming
- NEVER suggest doing surveys of past clients
- NEVER suggest referral reward programs or referral incentive programs
- NEVER suggest hosting seminars, workshops, or in-person educational events
- NEVER suggest going "live" on any platform

## Your output must follow these rules:

### 1. Identify the Agent's 3 Core Lead-Gen Pillars
Review their answers and determine:
- Their natural strengths
- Their preferred communication style
- Their time available
- Activities they enjoy or are willing to do
- Strategies that match their desired client type

Choose no more than 3 categories that fit them best.
Label them clearly and explain why these pillars were selected.

### 2. Provide a High-Level Overview
For each pillar, write a short summary of:
- What the pillar is
- Why it aligns with the agent's strengths
- What results this pillar can produce in 2026
- The mindset or approach needed to make that pillar successful

Keep this part inspirational but still practical.

### 3. Create a Detailed Monthly Plan (MONTHLY COMES FIRST)
For each pillar, list specific monthly actions with exact numbers and frequencies.

Be VERY SPECIFIC with quantities and types. Examples:
- "Send 200 postcards to your farm area on the 1st of each month"
- "Complete 10 CMAs for targeted homeowners each month"
- "Record 2 pre-recorded educational videos for social media each month"
- "Send a monthly market update email newsletter to your database"
- "Organize and clean your CRM database on the last Friday of each month"
- "Review and update your drip campaign content monthly"
- "Mail 15 handwritten notes to past clients and sphere each month"

These should be recurring tasks the agent can schedule once per month.

### 4. Create a Detailed Weekly Plan
For each pillar, list specific weekly actions with exact numbers.

Be VERY SPECIFIC with quantities and types. Examples for Social Media:
- "Post 3 social media posts per week including: 1 educational tip, 1 neighborhood spotlight, and 1 market insight or personal branding post"
- "Create 2 short-form videos (Reels/TikToks) per week on topics like home tips, neighborhood tours, or market updates"
- "Spend 15 minutes daily engaging with comments and DMs"

Examples for Sphere of Influence:
- "Make 10 SOI calls each week (2 per day, Monday-Friday)"
- "Send 5 personalized text check-ins to past clients weekly"
- "Schedule 2 coffee or lunch meetings with sphere contacts per week"

Examples for Direct Mail & CMAs:
- "Send 10 personalized CMA letters to targeted homeowners weekly"
- "Drop off 20 door hangers in your farm area each week"
- "Mail 25 'just listed' or 'just sold' postcards to surrounding neighbors weekly"

Examples for Open Houses:
- "Host 1 open house every weekend (or every other weekend)"
- "Follow up with all open house attendees within 24 hours via text"
- "Add all open house visitors to your CRM within 48 hours"

Each action should include specific numbers, frequencies, and be easy to plug directly into a calendar.

### 5. Add Optional High-Impact Bonus Ideas
Include 2-3 creative ideas for each pillar that are OPTIONAL.

Good examples (that follow our restrictions):
- "Record partner interview videos with local lenders or inspectors (pre-recorded, not live)"
- "Create a neighborhood Facebook group and post weekly market insights"
- "Develop a quarterly luxury market report for high-end neighborhoods"
- "Partner with local businesses for co-branded content or giveaways"
- "Create a YouTube channel with pre-recorded neighborhood tour videos"
- "Start a monthly email series on home maintenance tips"

BAD examples (NEVER suggest these):
- NO live Q&As or Facebook Lives
- NO surveys or feedback requests to past clients
- NO referral reward programs
- NO seminars or workshops

### 6. Present the Final Output in This Structure

# 2026 Lead Generation Plan for [Agent Name]

## Your Three Lead-Gen Pillars
- **Pillar #1 Name** — short explanation
- **Pillar #2 Name** — short explanation
- **Pillar #3 Name** — short explanation

---

## High-Level Strategy Overview

### Pillar 1: [Name]
- Why this was chosen
- What this produces
- How the agent should approach it

### Pillar 2: [Name]
- Same structure

### Pillar 3: [Name]
- Same structure

---

## Monthly Action Plan

### Pillar 1 Monthly Tasks
- Specific bullet list with exact numbers and frequencies

### Pillar 2 Monthly Tasks
- Specific bullet list with exact numbers and frequencies

### Pillar 3 Monthly Tasks
- Specific bullet list with exact numbers and frequencies

---

## Weekly Action Plan

### Pillar 1 Weekly Tasks
- Specific bullet list with exact numbers and frequencies

### Pillar 2 Weekly Tasks
- Specific bullet list with exact numbers and frequencies

### Pillar 3 Weekly Tasks
- Specific bullet list with exact numbers and frequencies

---

## Optional High-Impact Bonus Ideas

### Pillar 1
- 2-3 creative ideas (remember: NO live videos, NO surveys, NO referral programs, NO seminars)

### Pillar 2
- 2-3 creative ideas

### Pillar 3
- 2-3 creative ideas

---

## Your Next Steps This Week

Provide a concise checklist the agent can start immediately:
1. Add all monthly recurring tasks to your calendar for the entire year
2. Add all weekly recurring tasks to your calendar
3. Clean and prepare your CRM
4. Start with the easiest pillar first
5. Complete the first week's tasks within 7 days

---

## Tone + Style Requirements
- Supportive and confident
- Clear, structured, and actionable
- Zero fluff — everything should be practical
- Match recommendations to the agent's comfort level and personality
- Avoid overwhelming them
- ALWAYS include specific numbers and frequencies for every task

When writing the agent's plan, use the Origen Realty voice:
- Confident, warm, and supportive
- Practical and focused on simple, repeatable actions
- Direct, clear, and no fluff
- Encouraging and human, with the tone of a real estate coach
- Emphasize consistency, clarity, and sustainability
- Every section should feel like it was written by someone who understands real estate at a deep level and wants the agent to succeed

Always write the final output in this Origen voice.

REMEMBER: Never suggest live videos, surveys, referral programs, or seminars."""


def format_responses_for_ai(responses):
    """Format the questionnaire responses into a readable format for the AI."""
    formatted = """# Agent Questionnaire Responses

## Section 1: Natural Tendencies
"""
    
    formatted += f"**Name:** {responses.get('name', 'Not provided')}\n"
    
    # Handle communication_preference as array (multi-select)
    comm_pref = responses.get('communication_preference', [])
    if isinstance(comm_pref, list):
        formatted += f"**Preferred communication styles:** {', '.join(comm_pref) if comm_pref else 'Not provided'}\n"
    else:
        formatted += f"**Preferred communication style:** {comm_pref or 'Not provided'}\n"
    
    formatted += f"**Comfort with strangers:** {responses.get('stranger_comfort', 'Not provided')}\n"
    
    # Handle self_description as array (multi-select)
    self_desc = responses.get('self_description', [])
    if isinstance(self_desc, list):
        formatted += f"**Self-descriptions:** {', '.join(self_desc) if self_desc else 'Not provided'}\n"
    else:
        formatted += f"**Self-description:** {self_desc or 'Not provided'}\n"
    
    formatted += "\n## Section 2: Time & Consistency\n"
    formatted += f"**Weekly time commitment:** {responses.get('weekly_time', 'Not provided')}\n"
    
    # Handle leadgen_style as array (multi-select)
    leadgen = responses.get('leadgen_style', [])
    if isinstance(leadgen, list):
        formatted += f"**Preferred lead gen styles:** {', '.join(leadgen) if leadgen else 'Not provided'}\n"
    else:
        formatted += f"**Preferred lead gen style:** {leadgen or 'Not provided'}\n"
    
    formatted += "\n## Section 3: Activities They Enjoy\n"
    activities = responses.get('enjoyed_activities', [])
    if activities:
        for activity in activities:
            formatted += f"- {activity}\n"
    other_activity = responses.get('enjoyed_activities_other', '')
    if other_activity:
        formatted += f"- Other: {other_activity}\n"
    
    formatted += "\n## Section 4: Strengths\n"
    strengths = responses.get('strengths', [])
    if strengths:
        for strength in strengths:
            formatted += f"- {strength}\n"
    formatted += f"\n**Past successful activities:** {responses.get('past_successes', 'Not provided')}\n"
    
    formatted += "\n## Section 5: Lead Source Reality Check\n"
    formatted += f"**High effort but not dreadful activities:** {responses.get('high_effort_activities', 'Not provided')}\n"
    formatted += f"**High dread activities to avoid:** {responses.get('high_dread_activities', 'Not provided')}\n"
    
    formatted += "\n**Target client types:**\n"
    client_types = responses.get('target_clients', [])
    if client_types:
        for client_type in client_types:
            formatted += f"- {client_type}\n"
    other_client = responses.get('target_clients_other', '')
    if other_client:
        formatted += f"- Other: {other_client}\n"
    
    formatted += "\n## Section 6: Success Goals\n"
    formatted += "\n**Success metrics for June 30, 2026:**\n"
    success_metrics = responses.get('success_metrics', [])
    if success_metrics:
        for metric in success_metrics:
            formatted += f"- {metric}\n"
    other_metric = responses.get('success_metrics_other', '')
    if other_metric:
        formatted += f"- Other: {other_metric}\n"
    
    formatted += f"\n**Personal goal for end of 2026:** {responses.get('personal_goal', 'Not provided')}\n"
    
    return formatted


@action_plan_bp.route('/action-plan')
@login_required
def action_plan():
    """Main page - shows form if no plan exists, shows plan if exists."""
    existing_plan = ActionPlan.get_for_user(current_user.id)
    return render_template('action_plan.html', existing_plan=existing_plan)


@action_plan_bp.route('/api/action-plan/submit', methods=['POST'])
@login_required
def submit_action_plan():
    """Save questionnaire responses and generate AI plan."""
    try:
        data = request.get_json()
        responses = data.get('responses', {})
        
        if not responses:
            return jsonify({'error': 'No responses provided'}), 400
        
        # Format responses for OpenAI
        formatted_responses = format_responses_for_ai(responses)
        
        # User prompt for the AI
        user_prompt = f"Create a personalized 2026 Lead Generation Plan of Action for this agent based on their questionnaire responses:\n\n{formatted_responses}"
        
        # Generate the action plan using centralized AI service with fallback mechanism
        generated_plan = generate_ai_response(
            system_prompt=ACTION_PLAN_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.7,
            reasoning_effort="medium"
        )
        
        # Check if plan exists and update, or create new
        existing_plan = ActionPlan.get_for_user(current_user.id)
        
        if existing_plan:
            existing_plan.questionnaire_responses = responses
            existing_plan.ai_generated_plan = generated_plan
        else:
            new_plan = ActionPlan(
                user_id=current_user.id,
                questionnaire_responses=responses,
                ai_generated_plan=generated_plan
            )
            db.session.add(new_plan)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'plan': generated_plan
        })
        
    except Exception as e:
        print(f"Error generating action plan: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@action_plan_bp.route('/api/action-plan/retake', methods=['POST'])
@login_required
def retake_action_plan():
    """Clear existing plan to allow retaking the questionnaire."""
    try:
        existing_plan = ActionPlan.get_for_user(current_user.id)
        
        if existing_plan:
            db.session.delete(existing_plan)
            db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"Error clearing action plan: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@action_plan_bp.route('/api/action-plan/get')
@login_required
def get_action_plan():
    """Get the current user's action plan if it exists."""
    existing_plan = ActionPlan.get_for_user(current_user.id)
    
    if existing_plan:
        return jsonify({
            'exists': True,
            'plan': existing_plan.ai_generated_plan,
            'responses': existing_plan.questionnaire_responses,
            'created_at': existing_plan.created_at.isoformat() if existing_plan.created_at else None,
            'updated_at': existing_plan.updated_at.isoformat() if existing_plan.updated_at else None
        })
    
    return jsonify({'exists': False})
