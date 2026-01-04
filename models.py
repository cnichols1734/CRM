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
    
    # Relationships for engagement features
    reactions = db.relationship('CompanyUpdateReaction', backref='update', lazy='dynamic', cascade='all, delete-orphan')
    comments = db.relationship('CompanyUpdateComment', backref='update', lazy='dynamic', cascade='all, delete-orphan', order_by='CompanyUpdateComment.created_at')
    views = db.relationship('CompanyUpdateView', backref='update', lazy='dynamic', cascade='all, delete-orphan')
    
    def get_reaction_counts(self):
        """Get count of each reaction type."""
        from sqlalchemy import func
        results = db.session.query(
            CompanyUpdateReaction.reaction_type,
            func.count(CompanyUpdateReaction.id)
        ).filter(CompanyUpdateReaction.update_id == self.id).group_by(CompanyUpdateReaction.reaction_type).all()
        return {r[0]: r[1] for r in results}
    
    def get_user_reactions(self, user_id):
        """Get list of reaction types the user has made on this update."""
        return [r.reaction_type for r in self.reactions.filter_by(user_id=user_id).all()]
    
    def __repr__(self):
        return f'<CompanyUpdate {self.title[:30]}>'


class CompanyUpdateReaction(db.Model):
    """Emoji reactions on company updates (thumbs up, heart, etc.)."""
    __tablename__ = 'company_update_reactions'
    
    # Available reaction types
    REACTION_TYPES = ['thumbs_up', 'heart', 'raised_hands', 'fire', 'clap']
    REACTION_EMOJIS = {
        'thumbs_up': '👍',
        'heart': '❤️',
        'raised_hands': '🙌',
        'fire': '🔥',
        'clap': '👏'
    }
    
    id = db.Column(db.Integer, primary_key=True)
    update_id = db.Column(db.Integer, db.ForeignKey('company_updates.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    reaction_type = db.Column(db.String(20), nullable=False)  # thumbs_up, heart, raised_hands, fire, clap
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Unique constraint: one reaction type per user per update
    __table_args__ = (db.UniqueConstraint('update_id', 'user_id', 'reaction_type', name='unique_user_reaction'),)
    
    # Relationship to user
    user = db.relationship('User', backref=db.backref('update_reactions', lazy=True))
    
    def __repr__(self):
        return f'<Reaction {self.reaction_type} by user {self.user_id}>'


class CompanyUpdateComment(db.Model):
    """Comments on company updates."""
    __tablename__ = 'company_update_comments'
    
    id = db.Column(db.Integer, primary_key=True)
    update_id = db.Column(db.Integer, db.ForeignKey('company_updates.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship to user
    user = db.relationship('User', backref=db.backref('update_comments', lazy=True))
    
    def __repr__(self):
        return f'<Comment by user {self.user_id} on update {self.update_id}>'


class CompanyUpdateView(db.Model):
    """Track which users have viewed each company update (for admin analytics)."""
    __tablename__ = 'company_update_views'
    
    id = db.Column(db.Integer, primary_key=True)
    update_id = db.Column(db.Integer, db.ForeignKey('company_updates.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    viewed_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Unique constraint: one view record per user per update
    __table_args__ = (db.UniqueConstraint('update_id', 'user_id', name='unique_user_view'),)
    
    # Relationship to user
    user = db.relationship('User', backref=db.backref('update_views', lazy=True))
    
    def __repr__(self):
        return f'<View by user {self.user_id} on update {self.update_id}>'


# =============================================================================
# TRANSACTION MANAGEMENT MODELS
# =============================================================================

class TransactionType(db.Model):
    """
    Lookup table for transaction types.
    Values: seller, buyer, landlord, tenant, referral
    """
    __tablename__ = 'transaction_types'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)  # e.g., 'seller'
    display_name = db.Column(db.String(100), nullable=False)  # e.g., 'Seller Representation'
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    
    # Relationships
    transactions = db.relationship('Transaction', backref='transaction_type', lazy='dynamic')
    
    def __repr__(self):
        return f'<TransactionType {self.name}>'


class Transaction(db.Model):
    """
    Represents a real estate transaction (listing, purchase, lease, referral).
    Can have multiple participants (sellers, buyers, agents, etc.).
    """
    __tablename__ = 'transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Who created/owns this transaction
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Transaction type (seller, buyer, landlord, tenant, referral)
    transaction_type_id = db.Column(db.Integer, db.ForeignKey('transaction_types.id'), nullable=False)
    
    # Property details
    street_address = db.Column(db.String(200), nullable=False)
    city = db.Column(db.String(100))
    state = db.Column(db.String(50), default='TX')
    zip_code = db.Column(db.String(20))
    county = db.Column(db.String(100))
    
    # Seller-specific: ownership status (only relevant for seller transactions)
    # Values: conventional, builder, reo, short_sale
    ownership_status = db.Column(db.String(50))
    
    # Transaction status
    # Values: preparing_to_list, active, under_contract, closed, cancelled
    status = db.Column(db.String(50), default='preparing_to_list')
    
    # Key dates
    expected_close_date = db.Column(db.Date)
    actual_close_date = db.Column(db.Date)
    
    # Intake questionnaire answers (JSON)
    intake_data = db.Column(db.JSON, default={})
    
    # Flexible extra data for additional fields
    extra_data = db.Column(db.JSON, default={})
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    created_by = db.relationship('User', backref=db.backref('created_transactions', lazy='dynamic'))
    participants = db.relationship('TransactionParticipant', backref='transaction', 
                                   cascade='all, delete-orphan', lazy='dynamic')
    documents = db.relationship('TransactionDocument', backref='transaction',
                               cascade='all, delete-orphan', lazy='dynamic')
    
    @property
    def full_address(self):
        """Return formatted full address."""
        parts = [self.street_address]
        if self.city:
            parts.append(self.city)
        if self.state:
            parts.append(self.state)
        if self.zip_code:
            parts.append(self.zip_code)
        return ', '.join(parts)
    
    def __repr__(self):
        return f'<Transaction {self.id}: {self.street_address}>'


class TransactionParticipant(db.Model):
    """
    Links contacts/users to transactions with specific roles.
    Supports multiple participants per transaction (e.g., multiple sellers).
    """
    __tablename__ = 'transaction_participants'
    
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False)
    
    # Can link to existing contact or user (both optional for external parties)
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    # Role in the transaction
    # Values: seller, co_seller, buyer, co_buyer, listing_agent, buyers_agent, 
    #         title_company, lender, transaction_coordinator
    role = db.Column(db.String(50), nullable=False)
    
    # For external parties not in contacts/users
    name = db.Column(db.String(200))
    email = db.Column(db.String(200))
    phone = db.Column(db.String(20))
    company = db.Column(db.String(200))
    
    # Is this the primary contact for this role?
    is_primary = db.Column(db.Boolean, default=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    contact = db.relationship('Contact', backref=db.backref('transaction_participations', lazy='dynamic'))
    user = db.relationship('User', backref=db.backref('transaction_participations', lazy='dynamic'))
    
    @property
    def display_name(self):
        """Get the display name from contact, user, or name field."""
        if self.contact:
            return f"{self.contact.first_name} {self.contact.last_name}"
        if self.user:
            return f"{self.user.first_name} {self.user.last_name}"
        return self.name or "Unknown"
    
    @property
    def display_email(self):
        """Get email from contact, user, or email field."""
        if self.contact and self.contact.email:
            return self.contact.email
        if self.user and self.user.email:
            return self.user.email
        return self.email
    
    @property
    def display_phone(self):
        """Get phone from contact, user, or phone field."""
        if self.contact and self.contact.phone:
            return self.contact.phone
        if self.user and self.user.phone:
            return self.user.phone
        return self.phone
    
    def __repr__(self):
        return f'<TransactionParticipant {self.role}: {self.display_name}>'


class TransactionDocument(db.Model):
    """
    A document instance within a transaction.
    Tracks status from draft through signed.
    """
    __tablename__ = 'transaction_documents'
    
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False)
    
    # Document template info
    template_slug = db.Column(db.String(100), nullable=False)  # e.g., 'listing-agreement'
    template_name = db.Column(db.String(200), nullable=False)  # e.g., 'Listing Agreement'
    
    # Status: pending, draft, filled, generated, sent, partially_signed, signed, voided
    status = db.Column(db.String(50), default='pending')
    
    # The actual field data filled in by the agent
    field_data = db.Column(db.JSON, default={})
    
    # Why this document was included in the package (for conditional docs)
    included_reason = db.Column(db.String(500))
    
    # DocuSeal integration fields (nullable, for future use)
    docuseal_template_id = db.Column(db.String(100))  # DocuSeal template ID
    docuseal_submission_id = db.Column(db.String(100))  # DocuSeal submission ID when sent
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    sent_at = db.Column(db.DateTime)  # When sent for signature
    signed_at = db.Column(db.DateTime)  # When all signatures complete
    
    # Relationships
    signatures = db.relationship('DocumentSignature', backref='document',
                                cascade='all, delete-orphan', lazy='dynamic')
    
    def __repr__(self):
        return f'<TransactionDocument {self.template_name} ({self.status})>'


class DocumentSignature(db.Model):
    """
    Tracks each signer on a document.
    Links to TransactionParticipant for prefill and tracking.
    """
    __tablename__ = 'document_signatures'
    
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('transaction_documents.id', ondelete='CASCADE'), nullable=False)
    
    # Link to transaction participant (optional)
    participant_id = db.Column(db.Integer, db.ForeignKey('transaction_participants.id'), nullable=True)
    
    # Signer info (can be populated from participant or entered manually)
    signer_email = db.Column(db.String(200), nullable=False)
    signer_name = db.Column(db.String(200), nullable=False)
    signer_role = db.Column(db.String(50), nullable=False)  # e.g., 'seller', 'listing_agent'
    
    # Status: pending, sent, viewed, signed, declined
    status = db.Column(db.String(50), default='pending')
    
    # Signing order (for sequential signing)
    sign_order = db.Column(db.Integer, default=1)
    
    # DocuSeal integration fields (nullable)
    docuseal_submitter_slug = db.Column(db.String(200))  # For embedded signing
    
    # Timestamps
    sent_at = db.Column(db.DateTime)
    viewed_at = db.Column(db.DateTime)
    signed_at = db.Column(db.DateTime)
    
    # Relationships
    participant = db.relationship('TransactionParticipant')
    
    def __repr__(self):
        return f'<DocumentSignature {self.signer_name} ({self.status})>'