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

    # Update the relationship to use backref
    owner = db.relationship('User', backref=db.backref('contacts', lazy=True))
    groups = db.relationship('ContactGroup',
                           secondary=contact_groups,
                           back_populates='contacts',
                           lazy='joined')

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