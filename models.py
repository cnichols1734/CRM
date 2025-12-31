# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from flask import current_app
from itsdangerous import URLSafeTimedSerializer as Serializer

db = SQLAlchemy()

# Define the association table first, before the models
contact_groups = db.Table('contact_groups',
    db.Column('contact_id', db.Integer, db.ForeignKey('contact.id'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('contact_group.id'), primary_key=True)
)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='agent')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    # New optional profile fields
    phone = db.Column(db.String(20))
    license_number = db.Column(db.String(16))
    licensed_supervisor = db.Column(db.String(120))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_reset_token(self):
        s = Serializer(current_app.config['SECRET_KEY'])
        return s.dumps({'user_id': self.id})

    @staticmethod
    def verify_reset_token(token, expires_sec=1800):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            user_id = s.loads(token, max_age=expires_sec)['user_id']
        except:
            return None
        return User.query.get(user_id)

class ContactGroup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Updated relationship definition using the association table
    contacts = db.relationship('Contact',
                             secondary=contact_groups,
                             back_populates='groups',
                             lazy='dynamic')

class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    street_address = db.Column(db.String(200))
    city = db.Column(db.String(100))
    state = db.Column(db.String(50))
    zip_code = db.Column(db.String(20))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                          onupdate=datetime.utcnow)
    potential_commission = db.Column(db.Numeric(10, 2), nullable=False, default=5000.00)
    
    # New contact date tracking fields
    last_email_date = db.Column(db.Date, nullable=True)
    last_text_date = db.Column(db.Date, nullable=True)
    last_phone_call_date = db.Column(db.Date, nullable=True)
    last_contact_date = db.Column(db.Date, nullable=True)

    # Client objective fields
    current_objective = db.Column(db.Text, nullable=True)
    move_timeline = db.Column(db.Text, nullable=True)
    motivation = db.Column(db.Text, nullable=True)
    financial_status = db.Column(db.Text, nullable=True)
    additional_notes = db.Column(db.Text, nullable=True)

    # Update the relationship to use backref
    owner = db.relationship('User', backref=db.backref('contacts', lazy=True))
    groups = db.relationship('ContactGroup',
                           secondary=contact_groups,
                           back_populates='contacts',
                           lazy='joined')

    def update_last_contact_date(self):
        """Update the last_contact_date based on the most recent contact date"""
        dates = [d for d in [self.last_email_date, self.last_text_date, self.last_phone_call_date] if d is not None]
        self.last_contact_date = max(dates) if dates else None

    def __repr__(self):
        return f'<Contact {self.first_name} {self.last_name}>'

class Interaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    notes = db.Column(db.Text)
    date = db.Column(db.DateTime, nullable=False)
    follow_up_date = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    contact = db.relationship('Contact', backref='interactions')
    user = db.relationship('User', backref='interactions')

class TaskType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)  # e.g., 'Call', 'Email', 'Meeting', 'Showing', 'Follow-up'
    sort_order = db.Column(db.Integer, nullable=False)
    
    # Relationship to subtypes
    subtypes = db.relationship('TaskSubtype', backref='task_type', lazy=True)

class TaskSubtype(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_type_id = db.Column(db.Integer, db.ForeignKey('task_type.id'), nullable=False)
    name = db.Column(db.String(50), nullable=False)  # e.g., for Call: 'Check-in', 'Schedule Showing', 'Discuss Offer'
    sort_order = db.Column(db.Integer, nullable=False)

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=False)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Task Details
    type_id = db.Column(db.Integer, db.ForeignKey('task_type.id'), nullable=False)
    subtype_id = db.Column(db.Integer, db.ForeignKey('task_subtype.id'), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    priority = db.Column(db.String(20), nullable=False, default='medium')  # low, medium, high
    
    # Status
    status = db.Column(db.String(20), nullable=False, default='pending')  # pending, completed, cancelled
    outcome = db.Column(db.Text)  # Notes about what happened when task was completed
    
    # Dates
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    due_date = db.Column(db.DateTime, nullable=False)
    completed_at = db.Column(db.DateTime)
    
    # Optional fields specific to real estate
    property_address = db.Column(db.String(200))  # If task is related to specific property
    scheduled_time = db.Column(db.DateTime)  # For meetings/showings
    reminder_sent = db.Column(db.Boolean, default=False)
    
    # Relationships
    contact = db.relationship('Contact', backref=db.backref('tasks', lazy=True))
    assigned_to = db.relationship('User', foreign_keys=[assigned_to_id], backref='assigned_tasks')
    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_tasks')
    task_type = db.relationship('TaskType')
    task_subtype = db.relationship('TaskSubtype')

class DailyTodoList(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    generated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    todo_content = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship to user
    user = db.relationship('User', backref=db.backref('daily_todos', lazy=True))

    @classmethod
    def get_latest_for_user(cls, user_id):
        """Get the most recent todo list for a user"""
        return cls.query.filter_by(user_id=user_id).order_by(cls.generated_at.desc()).first()

    @classmethod
    def should_generate_new(cls, user_id):
        """Check if we should generate a new todo list (>16 hours since last one)"""
        latest = cls.get_latest_for_user(user_id)
        if not latest:
            return True
        time_since_last = datetime.utcnow() - latest.generated_at
        return time_since_last.total_seconds() > (16 * 3600)  # 16 hours in seconds

class UserTodo(db.Model):
    __tablename__ = 'user_todos'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    text = db.Column(db.String(500), nullable=False)
    completed = db.Column(db.Boolean, default=False, nullable=False)
    order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    user = db.relationship('User', backref=db.backref('todos', lazy=True, cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<UserTodo {self.text[:20]}...>'

class SendGridTemplate(db.Model):
    __tablename__ = 'sendgrid_template'
    
    id = db.Column(db.Integer, primary_key=True)
    sendgrid_id = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(200))
    version = db.Column(db.String(50))
    active_version_id = db.Column(db.String(100))
    preview_url = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)
    last_modified = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<SendGridTemplate {self.name}>'


class ActionPlan(db.Model):
    """Stores the 2026 Lead Generation Action Plan questionnaire and AI-generated plan per user."""
    __tablename__ = 'action_plan'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), unique=True, nullable=False)
    questionnaire_responses = db.Column(db.JSON, nullable=False)  # All form answers as JSON
    ai_generated_plan = db.Column(db.Text, nullable=True)  # The plan generated by OpenAI
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    user = db.relationship('User', backref=db.backref('action_plan', uselist=False, cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<ActionPlan user_id={self.user_id}>'
    
    @classmethod
    def get_for_user(cls, user_id):
        """Get the action plan for a specific user."""
        return cls.query.filter_by(user_id=user_id).first()


class CompanyUpdate(db.Model):
    """Company-wide updates/announcements visible to all users."""
    __tablename__ = 'company_updates'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)  # HTML from Quill.js
    excerpt = db.Column(db.String(500))  # Short preview text
    cover_image_url = db.Column(db.String(500))  # External URL
    author_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship to get author details
    author = db.relationship('User', backref=db.backref('company_updates', lazy=True))
    
    def __repr__(self):
        return f'<CompanyUpdate {self.title[:30]}>'