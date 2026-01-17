# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from flask import current_app
from itsdangerous import URLSafeTimedSerializer as Serializer
import secrets

db = SQLAlchemy()

# Define the association table first, before the models
contact_groups = db.Table('contact_groups',
    db.Column('contact_id', db.Integer, db.ForeignKey('contact.id'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('contact_group.id'), primary_key=True)
)


# =============================================================================
# MULTI-TENANT ORGANIZATION MODELS
# =============================================================================

class Organization(db.Model):
    """
    Represents a real estate agency/brokerage.
    All tenant data is scoped to an organization.
    """
    __tablename__ = 'organizations'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    logo_url = db.Column(db.String(500))
    
    # Subscription tier
    subscription_tier = db.Column(db.String(50), default='free')  # free, pro, enterprise
    
    # Configurable limits (easily adjustable per-org)
    max_users = db.Column(db.Integer, default=1)  # Free: 1, Pro: configurable
    max_contacts = db.Column(db.Integer, default=10000)  # Free: 10000, Pro: unlimited (NULL)
    can_invite_users = db.Column(db.Boolean, default=False)  # Free: False, Pro: True
    
    # Per-org feature overrides (beyond tier defaults)
    feature_flags = db.Column(db.JSON, default=dict)
    
    # Platform admin flag (Origen only)
    is_platform_admin = db.Column(db.Boolean, default=False)
    
    # Lifecycle status: pending_approval, active, suspended, pending_deletion
    status = db.Column(db.String(30), default='pending_approval')
    
    deletion_scheduled_at = db.Column(db.DateTime, nullable=True)
    
    # Session invalidation - all sessions created before this time are invalid
    session_invalidated_at = db.Column(db.DateTime, nullable=True)
    
    # Approval tracking
    approved_at = db.Column(db.DateTime, nullable=True)
    approved_by_id = db.Column(db.Integer, nullable=True)  # No FK to avoid circular
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships defined via backref in User model
    
    def upgrade_to_pro(self, max_users=25):
        """Upgrade org to Pro tier with configurable limits."""
        self.subscription_tier = 'pro'
        self.max_users = max_users
        self.max_contacts = None  # Unlimited
        self.can_invite_users = True
    
    # -------------------------------------------------------------------------
    # Limit Check Properties (for template upgrade prompts)
    # -------------------------------------------------------------------------
    
    @property
    def is_at_user_limit(self) -> bool:
        """Check if org has reached user limit. Use in templates for upgrade banners."""
        return self.max_users is not None and self.users.count() >= self.max_users
    
    @property
    def is_at_contact_limit(self) -> bool:
        """Check if org has reached contact limit. Use in templates for upgrade banners."""
        if self.max_contacts is None:
            return False  # Unlimited
        # Import here to avoid circular imports
        return Contact.query.filter_by(organization_id=self.id).count() >= self.max_contacts
    
    @property
    def user_limit_display(self) -> str:
        """Human-readable user limit for UI."""
        current = self.users.count()
        if self.max_users is None:
            return f"{current} users (unlimited)"
        return f"{current} / {self.max_users} users"
    
    @property
    def contact_limit_display(self) -> str:
        """Human-readable contact limit for UI."""
        if self.max_contacts is None:
            return "Unlimited contacts"
        current = Contact.query.filter_by(organization_id=self.id).count()
        return f"{current} / {self.max_contacts} contacts"
    
    def __repr__(self):
        return f'<Organization {self.name}>'


class OrganizationMetrics(db.Model):
    """
    Aggregate metrics ONLY - NO PII.
    This is the ONLY table platform admin routes query for org data.
    Updated by background job every 15-60 minutes.
    """
    __tablename__ = 'organization_metrics'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='CASCADE'), unique=True, nullable=False)
    
    # Counts only - NEVER store PII here
    user_count = db.Column(db.Integer, default=0)
    contact_count = db.Column(db.Integer, default=0)
    task_count = db.Column(db.Integer, default=0)
    transaction_count = db.Column(db.Integer, default=0)
    
    # Activity timestamps (no PII, just timing)
    last_user_login_at = db.Column(db.DateTime)
    last_contact_created_at = db.Column(db.DateTime)
    last_transaction_created_at = db.Column(db.DateTime)
    
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    organization = db.relationship('Organization', backref=db.backref('metrics', uselist=False))
    
    def __repr__(self):
        return f'<OrganizationMetrics org_id={self.organization_id}>'


class OrganizationInvite(db.Model):
    """Invites for Pro tier orgs only (free tier cannot invite)."""
    __tablename__ = 'organization_invites'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='CASCADE'), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    invited_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    role = db.Column(db.String(20), default='agent')  # agent or admin only, never owner
    
    # Security: cryptographically random, single-use
    token = db.Column(db.String(64), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)  # 72 hours max
    used_at = db.Column(db.DateTime, nullable=True)  # Set when accepted
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    organization = db.relationship('Organization', backref=db.backref('invites', lazy='dynamic'))
    invited_by = db.relationship('User', foreign_keys=[invited_by_id])
    
    @staticmethod
    def generate_token():
        """Generate a cryptographically secure token."""
        return secrets.token_urlsafe(32)
    
    @property
    def is_valid(self):
        """Check if invite is still valid (not used, not expired)."""
        if self.used_at is not None:
            return False
        if datetime.utcnow() > self.expires_at:
            return False
        return True
    
    def __repr__(self):
        return f'<OrganizationInvite {self.email} to org {self.organization_id}>'


class PlatformAuditLog(db.Model):
    """Logs all platform admin actions on organizations."""
    __tablename__ = 'platform_audit_log'
    
    id = db.Column(db.Integer, primary_key=True)
    admin_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    target_org_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                              ondelete='SET NULL'), nullable=True)
    
    action = db.Column(db.String(100), nullable=False)
    # Actions: org_approved, org_suspended, tier_changed, feature_toggled,
    #          limits_changed, org_deletion_initiated, etc.
    
    details = db.Column(db.JSON, default=dict)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(500))
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    admin = db.relationship('User', foreign_keys=[admin_user_id])
    target_org = db.relationship('Organization', backref=db.backref('audit_logs', lazy='dynamic'))
    
    def __repr__(self):
        return f'<PlatformAuditLog {self.action} on org {self.target_org_id}>'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))  # scrypt hashes can be 160+ chars
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    
    # Legacy role field - kept for backwards compatibility during migration
    # Will be removed after migration completes
    role = db.Column(db.String(20), nullable=False, default='agent')
    
    # Multi-tenant fields
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='RESTRICT'), nullable=True)  # Made NOT NULL after migration
    org_role = db.Column(db.String(20), default='agent')  # owner, admin, agent
    is_super_admin = db.Column(db.Boolean, default=False)  # Origen platform admins only
    
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    # Optional profile fields
    phone = db.Column(db.String(20))
    license_number = db.Column(db.String(16))
    licensed_supervisor = db.Column(db.String(120))
    licensed_supervisor_license = db.Column(db.String(16))
    licensed_supervisor_email = db.Column(db.String(120))
    licensed_supervisor_phone = db.Column(db.String(20))
    
    # Organization relationship
    organization = db.relationship('Organization', backref=db.backref('users', lazy='dynamic'),
                                   foreign_keys=[organization_id])

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
    
    def __repr__(self):
        return f'<User {self.username}>'

class ContactGroup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    # Multi-tenant: organization scoping
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='RESTRICT'), nullable=True, index=True)  # Made NOT NULL after migration
    
    name = db.Column(db.String(100), nullable=False)  # Unique per org, not globally
    category = db.Column(db.String(50), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Relationship definition using the association table
    contacts = db.relationship('Contact',
                             secondary=contact_groups,
                             back_populates='groups',
                             lazy='dynamic')
    
    # Unique constraint: name must be unique within organization
    __table_args__ = (
        db.UniqueConstraint('organization_id', 'name', name='uq_contact_group_org_name'),
    )

class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Multi-tenant: organization scoping
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='RESTRICT'), nullable=True, index=True)  # Made NOT NULL after migration
    
    # Track who created this contact (useful for "my contacts" views)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id',
                              ondelete='SET NULL'), nullable=True)
    
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
    
    # Contact date tracking fields
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

    # Relationships
    owner = db.relationship('User', foreign_keys=[user_id], backref=db.backref('contacts', lazy=True))
    created_by = db.relationship('User', foreign_keys=[created_by_id],
                                 backref=db.backref('created_contacts', lazy='dynamic'))
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
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
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
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    name = db.Column(db.String(50), nullable=False)  # e.g., 'Call', 'Email', 'Meeting', 'Showing', 'Follow-up'
    sort_order = db.Column(db.Integer, nullable=False)
    
    # Relationship to subtypes
    subtypes = db.relationship('TaskSubtype', backref='task_type', lazy=True)

class TaskSubtype(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    task_type_id = db.Column(db.Integer, db.ForeignKey('task_type.id'), nullable=False)
    name = db.Column(db.String(50), nullable=False)  # e.g., for Call: 'Check-in', 'Schedule Showing', 'Discuss Offer'
    sort_order = db.Column(db.Integer, nullable=False)

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    # Multi-tenant: organization scoping
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='RESTRICT'), nullable=True, index=True)  # Made NOT NULL after migration
    
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
    
    # Multi-tenant: organization scoping
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='RESTRICT'), nullable=True)  # Made NOT NULL after migration
    
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
    
    # Multi-tenant: organization scoping
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='RESTRICT'), nullable=True)  # Made NOT NULL after migration
    
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
    
    # Multi-tenant: organization scoping (future: org-specific email templates)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='RESTRICT'), nullable=True)  # Made NOT NULL after migration
    
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
    
    # Multi-tenant: organization scoping
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='RESTRICT'), nullable=True)  # Made NOT NULL after migration
    
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
    """Organization-wide updates/announcements visible to all users in the org (Team Updates)."""
    __tablename__ = 'company_updates'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Multi-tenant: organization scoping
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='RESTRICT'), nullable=True, index=True)  # Made NOT NULL after migration
    
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
    
    @property
    def cover_image_signed_url(self):
        """Return signed URL for Supabase images, fallback to legacy local URLs."""
        if not self.cover_image_url:
            return None
        
        # Legacy local images already have a full path
        if self.cover_image_url.startswith('/'):
            return self.cover_image_url
        
        try:
            from services import supabase_storage
            return supabase_storage.get_signed_url(
                supabase_storage.COMPANY_UPDATES_BUCKET,
                self.cover_image_url,
                expires_in=3600
            )
        except Exception:
            return self.cover_image_url
    
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
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
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
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
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
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
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
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    name = db.Column(db.String(50), nullable=False)  # e.g., 'seller' - no longer globally unique
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
    
    # Multi-tenant: organization scoping
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='RESTRICT'), nullable=True, index=True)  # Made NOT NULL after migration
    
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
    # Seller statuses: preparing_to_list, active, under_contract, closed, cancelled
    # Buyer statuses: showing, under_contract, closed, cancelled
    status = db.Column(db.String(50), default='preparing_to_list')
    
    # Key dates
    expected_close_date = db.Column(db.Date)
    actual_close_date = db.Column(db.Date)
    
    # Intake questionnaire answers (JSON)
    intake_data = db.Column(db.JSON, default={})
    
    # Flexible extra data for additional fields
    extra_data = db.Column(db.JSON, default={})
    
    # RentCast property intelligence data (buyer transactions)
    rentcast_data = db.Column(db.JSON, default=None)  # Full API response
    rentcast_fetched_at = db.Column(db.DateTime)  # When data was last fetched
    
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
    
    @property
    def primary_seller(self):
        """Get the primary seller participant (for document field resolution)."""
        return next(
            (p for p in self.participants.all() if p.role == 'seller' and p.is_primary),
            None
        )
    
    @property
    def sellers(self):
        """Get all seller participants as a list (for document field resolution)."""
        return [p for p in self.participants.all() if p.role == 'seller']
    
    def __repr__(self):
        return f'<Transaction {self.id}: {self.street_address}>'


class TransactionParticipant(db.Model):
    """
    Links contacts/users to transactions with specific roles.
    Supports multiple participants per transaction (e.g., multiple sellers).
    """
    __tablename__ = 'transaction_participants'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
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
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
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

    # Who sent this document for signature
    sent_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    sent_at = db.Column(db.DateTime)  # When sent for signature
    signed_at = db.Column(db.DateTime)  # When all signatures complete
    
    # Signed document storage (Supabase)
    signed_file_path = db.Column(db.String(500))  # Path in Supabase storage
    signed_file_size = db.Column(db.Integer)  # Size in bytes
    signed_file_downloaded_at = db.Column(db.DateTime)  # When downloaded from DocuSeal
    
    # Signing method: 'esign', 'physical', or null (not yet signed)
    signing_method = db.Column(db.String(20), nullable=True)
    
    # Document source: 'template' (our generated), 'external' (uploaded from other party), 'hybrid' (wet+esign combo)
    document_source = db.Column(db.String(20), default='template')
    
    # For external/hybrid docs: path to the uploaded source PDF in Supabase
    source_file_path = db.Column(db.String(500), nullable=True)
    
    # Manual field placements for ad-hoc signing: [{type, role, page, x, y, w, h, required}]
    field_placements = db.Column(db.JSON, nullable=True)
    
    # Relationships
    signatures = db.relationship('DocumentSignature', backref='document',
                                cascade='all, delete-orphan', lazy='dynamic')
    sent_by = db.relationship('User', foreign_keys=[sent_by_id], backref='sent_documents')

    def __repr__(self):
        return f'<TransactionDocument {self.template_name} ({self.status})>'


class DocumentSignature(db.Model):
    """
    Tracks each signer on a document.
    Links to TransactionParticipant for prefill and tracking.
    """
    __tablename__ = 'document_signatures'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
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


class ContactFile(db.Model):
    """
    Files uploaded and attached to contacts.
    Stored in Supabase Storage with metadata in this table.
    """
    __tablename__ = 'contact_files'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    
    # File metadata
    filename = db.Column(db.String(255), nullable=False)  # Stored filename (UUID-based)
    original_filename = db.Column(db.String(255), nullable=False)  # Original upload name
    file_type = db.Column(db.String(100))  # MIME type
    file_size = db.Column(db.Integer)  # Size in bytes
    storage_path = db.Column(db.String(500), nullable=False)  # Full path in Supabase Storage
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    contact = db.relationship('Contact', backref=db.backref('files', lazy='dynamic', cascade='all, delete-orphan'))
    uploaded_by = db.relationship('User', backref=db.backref('uploaded_files', lazy='dynamic'))
    
    # Allowed file extensions
    ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'pdf', 'doc', 'docx', 'csv', 'xlsx', 'xls'}
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
    
    @property
    def file_extension(self):
        """Get file extension from original filename."""
        if '.' in self.original_filename:
            return self.original_filename.rsplit('.', 1)[1].lower()
        return ''
    
    @property
    def is_image(self):
        """Check if file is an image."""
        return self.file_extension in {'jpg', 'jpeg', 'png', 'gif'}
    
    @property
    def human_file_size(self):
        """Return human-readable file size."""
        if not self.file_size:
            return 'Unknown'
        for unit in ['B', 'KB', 'MB', 'GB']:
            if self.file_size < 1024:
                return f"{self.file_size:.1f} {unit}"
            self.file_size /= 1024
        return f"{self.file_size:.1f} TB"
    
    @classmethod
    def allowed_file(cls, filename):
        """Check if filename has an allowed extension."""
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in cls.ALLOWED_EXTENSIONS
    
    def __repr__(self):
        return f'<ContactFile {self.original_filename} for contact {self.contact_id}>'


class AuditEvent(db.Model):
    """
    Comprehensive audit trail for transactions and documents.
    Tracks all significant actions for compliance and reporting.
    """
    __tablename__ = 'audit_events'

    id = db.Column(db.Integer, primary_key=True)

    # What was affected
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=True)
    document_id = db.Column(db.Integer, db.ForeignKey('transaction_documents.id', ondelete='SET NULL'), nullable=True)
    signature_id = db.Column(db.Integer, db.ForeignKey('document_signatures.id', ondelete='SET NULL'), nullable=True)

    # Who performed the action (null for system/webhook events)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)

    # What happened
    event_type = db.Column(db.String(50), nullable=False)
    # Event types:
    # Transaction: transaction_created, transaction_updated, transaction_deleted, transaction_status_changed
    # Participants: participant_added, participant_removed, participant_updated
    # Documents: document_added, document_removed, document_filled, document_generated
    # E-Sign: document_sent, document_resent, document_voided, document_viewed, document_signed
    # Webhook: webhook_received

    # Human-readable description
    description = db.Column(db.String(500))

    # Detailed event data (JSON) - stores context-specific data
    # Examples:
    # - For status changes: {"old_status": "pending", "new_status": "sent"}
    # - For field changes: {"changed_fields": ["seller_name", "price"]}
    # - For webhooks: {"raw_payload": {...}, "ip_address": "..."}
    # - For signatures: {"signer_email": "...", "signer_role": "Seller"}
    event_data = db.Column(db.JSON, default={})

    # Source of the event
    source = db.Column(db.String(50), default='app')  # app, webhook, system, api

    # IP address for web requests (useful for compliance)
    ip_address = db.Column(db.String(45))  # Supports IPv6

    # User agent for web requests
    user_agent = db.Column(db.String(500))

    # Timestamp
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Relationships
    transaction = db.relationship('Transaction', backref=db.backref('audit_events', lazy='dynamic', cascade='all, delete-orphan'))
    document = db.relationship('TransactionDocument', backref=db.backref('audit_events', lazy='dynamic'))
    signature = db.relationship('DocumentSignature', backref=db.backref('audit_events', lazy='dynamic'))
    actor = db.relationship('User', backref=db.backref('audit_actions', lazy='dynamic'))

    # Event type constants for consistency
    # Transaction events
    TRANSACTION_CREATED = 'transaction_created'
    TRANSACTION_UPDATED = 'transaction_updated'
    TRANSACTION_DELETED = 'transaction_deleted'
    TRANSACTION_STATUS_CHANGED = 'transaction_status_changed'

    # Participant events
    PARTICIPANT_ADDED = 'participant_added'
    PARTICIPANT_REMOVED = 'participant_removed'
    PARTICIPANT_UPDATED = 'participant_updated'

    # Document lifecycle events
    DOCUMENT_ADDED = 'document_added'
    DOCUMENT_REMOVED = 'document_removed'
    DOCUMENT_FILLED = 'document_filled'
    DOCUMENT_GENERATED = 'document_generated'
    DOCUMENT_PACKAGE_GENERATED = 'document_package_generated'
    DOCUMENT_PACKAGE_SYNCED = 'document_package_synced'

    # E-signature events
    DOCUMENT_SENT = 'document_sent'
    DOCUMENT_RESENT = 'document_resent'
    DOCUMENT_VOIDED = 'document_voided'
    DOCUMENT_VIEWED = 'document_viewed'
    DOCUMENT_SIGNED = 'document_signed'
    DOCUMENT_SIGNED_PHYSICAL = 'document_signed_physical'
    DOCUMENT_UPLOADED_EXTERNAL = 'document_uploaded_external'
    DOCUMENT_SENT_ADHOC = 'document_sent_adhoc'
    DOCUMENT_CONVERTED_HYBRID = 'document_converted_hybrid'
    DOCUMENT_DECLINED = 'document_declined'
    ENVELOPE_SENT = 'envelope_sent'

    # System events
    WEBHOOK_RECEIVED = 'webhook_received'
    INTAKE_SAVED = 'intake_saved'

    @classmethod
    def log(cls, event_type, transaction_id=None, document_id=None, signature_id=None,
            actor_id=None, description=None, event_data=None, source='app',
            ip_address=None, user_agent=None):
        """
        Convenience method to create and save an audit event.
        Returns the created event.
        """
        event = cls(
            event_type=event_type,
            transaction_id=transaction_id,
            document_id=document_id,
            signature_id=signature_id,
            actor_id=actor_id,
            description=description,
            event_data=event_data or {},
            source=source,
            ip_address=ip_address,
            user_agent=user_agent
        )
        db.session.add(event)
        return event

    def __repr__(self):
        return f'<AuditEvent {self.event_type} tx={self.transaction_id} at {self.created_at}>'


# =============================================================================
# AGENT RESOURCES (Per-Organization)
# =============================================================================

class AgentResource(db.Model):
    """
    External resource links for agents within an organization.
    Each org can configure their own set of useful links.
    """
    __tablename__ = 'agent_resources'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    
    label = db.Column(db.String(100), nullable=False)  # Display name
    url = db.Column(db.String(2000), nullable=False)   # Resource URL
    sort_order = db.Column(db.Integer, default=0)      # For custom ordering
    is_active = db.Column(db.Boolean, default=True)    # Can disable without deleting
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', backref=db.backref('agent_resources', lazy='dynamic'))
    created_by = db.relationship('User', backref=db.backref('created_resources', lazy='dynamic'))
    
    def __repr__(self):
        return f'<AgentResource {self.label} org={self.organization_id}>'
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        return {
            'id': self.id,
            'label': self.label,
            'url': self.url,
            'sort_order': self.sort_order,
            'is_active': self.is_active
        }