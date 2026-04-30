# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from flask import current_app
from itsdangerous import URLSafeTimedSerializer as Serializer
import secrets
import re

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
    
    # Broker/Brokerage information (for document generation)
    broker_name = db.Column(db.String(200))  # e.g., "Origen Realty"
    broker_license_number = db.Column(db.String(50))  # e.g., "9003104"
    broker_address = db.Column(db.String(500))  # Full address string
    
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
    
    # User preferences
    task_window_days = db.Column(db.Integer, nullable=False, default=30)  # Days for upcoming tasks view (7, 30, 60, or 90)
    
    # Onboarding flags
    has_seen_contacts_onboarding = db.Column(db.Boolean, default=False)
    has_seen_dashboard_onboarding = db.Column(db.Boolean, default=False)
    has_seen_inbox_onboarding = db.Column(db.Boolean, default=False)

    # Magic Inbox — per-user forwarding address. The token suffix is the auth.
    # Address format: <slug>-<token>@inbox.origentechnolog.com
    inbox_address = db.Column(db.String(200), unique=True, index=True, nullable=True)
    inbox_token = db.Column(db.String(16), unique=True, nullable=True)

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
        """Update the last_contact_date based on the most recent contact date.
        
        Includes email, text, phone call dates AND the current last_contact_date
        (which may have been set directly by meeting/other activity types).
        """
        # Include current last_contact_date to preserve meeting/other activity dates
        dates = [d for d in [
            self.last_email_date, 
            self.last_text_date, 
            self.last_phone_call_date,
            self.last_contact_date  # Preserve existing value from meeting/other
        ] if d is not None]
        self.last_contact_date = max(dates) if dates else None

    def __repr__(self):
        return f'<Contact {self.first_name} {self.last_name}>'


# =============================================================================
# ORG-WIDE PARTNER DIRECTORY MODELS
# =============================================================================

def normalize_partner_text(value):
    """Normalize partner text for duplicate checks and unique constraints."""
    if not value:
        return None
    normalized = value.strip().lower()
    normalized = re.sub(r'[^\w\s]', '', normalized)
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized or None


def normalize_partner_phone(value):
    """Normalize phone numbers to digits for duplicate detection."""
    if not value:
        return None
    digits = re.sub(r'\D', '', value)
    return digits or None


def normalize_partner_address(street_address, city=None, state=None, zip_code=None):
    """Normalize address parts enough to catch obvious duplicate companies."""
    street = normalize_partner_text(street_address)
    if street:
        replacements = {
            r'\bstreet\b': 'st',
            r'\bavenue\b': 'ave',
            r'\broad\b': 'rd',
            r'\bdrive\b': 'dr',
            r'\blane\b': 'ln',
            r'\bboulevard\b': 'blvd',
            r'\bhighway\b': 'hwy',
            r'\bsuite\b': 'ste',
            r'\bapartment\b': 'apt',
        }
        for pattern, replacement in replacements.items():
            street = re.sub(pattern, replacement, street)

    parts = [
        street,
        normalize_partner_text(city),
        normalize_partner_text(state),
        normalize_partner_text(zip_code),
    ]
    return ' '.join(part for part in parts if part) or None


class PartnerOrganization(db.Model):
    """Org-wide company/vendor record used by transaction participants."""
    __tablename__ = 'partner_organizations'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)

    name = db.Column(db.String(200), nullable=False)
    normalized_name = db.Column(db.String(200), nullable=False)
    partner_type = db.Column(db.String(50), nullable=False, default='other', index=True)

    phone = db.Column(db.String(30))
    normalized_phone = db.Column(db.String(30))
    email = db.Column(db.String(200))
    website = db.Column(db.String(300))
    street_address = db.Column(db.String(200))
    city = db.Column(db.String(100))
    state = db.Column(db.String(50))
    zip_code = db.Column(db.String(20))
    normalized_address = db.Column(db.String(500), index=True)
    notes = db.Column(db.Text)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id], backref=db.backref('created_partner_organizations', lazy='dynamic'))
    updated_by = db.relationship('User', foreign_keys=[updated_by_id], backref=db.backref('updated_partner_organizations', lazy='dynamic'))
    contacts = db.relationship('PartnerContact', back_populates='partner_organization', cascade='all, delete-orphan', lazy='dynamic')
    transaction_participants = db.relationship('TransactionParticipant', back_populates='partner_organization', lazy='dynamic')

    __table_args__ = (
        db.UniqueConstraint('organization_id', 'normalized_name', name='uq_partner_org_normalized_name'),
    )

    @property
    def type_label(self):
        return {
            'brokerage': 'Brokerage',
            'title_company': 'Title Company',
            'lender': 'Lender',
            'attorney': 'Attorney',
            'inspector': 'Inspector',
            'other': 'Other Partner',
        }.get(self.partner_type, self.partner_type.replace('_', ' ').title())

    @property
    def full_address(self):
        parts = [self.street_address, self.city, self.state, self.zip_code]
        return ', '.join(part for part in parts if part)

    def sync_normalized_fields(self):
        self.normalized_name = normalize_partner_text(self.name)
        self.normalized_phone = normalize_partner_phone(self.phone)
        self.normalized_address = normalize_partner_address(
            self.street_address,
            self.city,
            self.state,
            self.zip_code,
        )

    def __repr__(self):
        return f'<PartnerOrganization {self.name}>'


class PartnerContact(db.Model):
    """Person associated with an org-wide partner company."""
    __tablename__ = 'partner_contacts'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    partner_organization_id = db.Column(db.Integer, db.ForeignKey('partner_organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)

    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    normalized_full_name = db.Column(db.String(180), nullable=False)
    title = db.Column(db.String(120))
    email = db.Column(db.String(200))
    normalized_email = db.Column(db.String(200))
    phone = db.Column(db.String(30))
    normalized_phone = db.Column(db.String(30))
    notes = db.Column(db.Text)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_primary_contact = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    partner_organization = db.relationship('PartnerOrganization', back_populates='contacts')
    created_by = db.relationship('User', foreign_keys=[created_by_id], backref=db.backref('created_partner_contacts', lazy='dynamic'))
    updated_by = db.relationship('User', foreign_keys=[updated_by_id], backref=db.backref('updated_partner_contacts', lazy='dynamic'))
    transaction_participants = db.relationship('TransactionParticipant', back_populates='partner_contact', lazy='dynamic')

    __table_args__ = (
        db.UniqueConstraint('partner_organization_id', 'normalized_full_name', name='uq_partner_contact_org_full_name'),
    )

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    def sync_normalized_fields(self):
        self.normalized_full_name = normalize_partner_text(self.full_name)
        self.normalized_email = self.email.strip().lower() if self.email else None
        self.normalized_phone = normalize_partner_phone(self.phone)

    def __repr__(self):
        return f'<PartnerContact {self.full_name}>'

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
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='SET NULL'), nullable=True, index=True)
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
    reminder_sent = db.Column(db.Boolean, default=False)  # DEPRECATED: Use specific flags below
    
    # Email reminder tracking (one flag per reminder type)
    two_day_reminder_sent = db.Column(db.Boolean, default=False)  # Sent 48-72 hours before due
    one_day_reminder_sent = db.Column(db.Boolean, default=False)  # Sent 24-48 hours before due
    today_reminder_sent = db.Column(db.Boolean, default=False)  # Sent 0-24 hours before due
    overdue_reminder_sent = db.Column(db.Boolean, default=False)  # Sent after task became overdue
    last_reminder_sent_at = db.Column(db.DateTime)  # Timestamp of most recent reminder
    
    # Google Calendar sync
    google_calendar_event_id = db.Column(db.String(255), nullable=True)  # Calendar event ID
    calendar_sync_error = db.Column(db.Text, nullable=True)  # Last sync error if any
    
    # Auto-checkin flag (system-generated recurring seller check-in tasks)
    is_auto_checkin = db.Column(db.Boolean, default=False, nullable=False)
    
    # Relationships
    contact = db.relationship('Contact', backref=db.backref('tasks', lazy=True))
    transaction = db.relationship('Transaction', backref=db.backref('tasks', lazy='dynamic'))
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
    seller_listing_profile = db.relationship('SellerListingProfile', backref='transaction',
                                             cascade='all, delete-orphan', uselist=False)
    seller_showings = db.relationship('SellerShowing', backref='transaction',
                                      cascade='all, delete-orphan', lazy='dynamic')
    seller_offers = db.relationship('SellerOffer', backref='transaction',
                                    cascade='all, delete-orphan', lazy='dynamic',
                                    foreign_keys='SellerOffer.transaction_id')
    seller_accepted_contracts = db.relationship('SellerAcceptedContract', backref='transaction',
                                                cascade='all, delete-orphan', lazy='dynamic')
    seller_contract_milestones = db.relationship('SellerContractMilestone', backref='transaction',
                                                cascade='all, delete-orphan', lazy='dynamic')
    seller_price_changes = db.relationship('SellerListingPriceChange', backref='transaction',
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
    
    # Can link to existing contact, user, or org-wide partner directory record.
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    partner_organization_id = db.Column(db.Integer, db.ForeignKey('partner_organizations.id', ondelete='SET NULL'), nullable=True)
    partner_contact_id = db.Column(db.Integer, db.ForeignKey('partner_contacts.id', ondelete='SET NULL'), nullable=True)
    
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
    partner_organization = db.relationship('PartnerOrganization', back_populates='transaction_participants')
    partner_contact = db.relationship('PartnerContact', back_populates='transaction_participants')
    
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
    signed_original_filename = db.Column(db.String(255))  # Original filename from upload
    
    # Signing method: 'esign', 'physical', or null (not yet signed)
    signing_method = db.Column(db.String(20), nullable=True)
    
    # Document source: 'template' (our generated), 'external' (uploaded from other party), 
    # 'hybrid' (wet+esign combo), 'static' (uploaded PDF, no signing), 'placeholder' (awaiting content)
    document_source = db.Column(db.String(20), default='template')
    
    # For external/hybrid/static docs: path to the uploaded source PDF in Supabase
    source_file_path = db.Column(db.String(500), nullable=True)
    
    # Manual field placements for ad-hoc signing: [{type, role, page, x, y, w, h, required}]
    field_placements = db.Column(db.JSON, nullable=True)
    
    # Placeholder documents: created by questionnaire as reminders for agent to upload content later
    # (e.g., Special Tax District Notice, Referral Agreement when agent-provided)
    is_placeholder = db.Column(db.Boolean, default=False)
    
    # AI extraction status for uploaded documents (null = not applicable)
    extraction_status = db.Column(db.String(20))  # pending, processing, complete, failed
    extraction_error = db.Column(db.Text)  # error details on failure

    # Lineage for AI-split combined packets. parent_document_id points at the original
    # uploaded packet; page_start/page_end are 1-based pages inside that parent.
    parent_document_id = db.Column(
        db.Integer,
        db.ForeignKey('transaction_documents.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    page_start = db.Column(db.Integer, nullable=True)
    page_end = db.Column(db.Integer, nullable=True)
    split_source = db.Column(db.String(50), nullable=True)  # e.g. 'ai_packet_split'

    # Relationships
    signatures = db.relationship('DocumentSignature', backref='document',
                                cascade='all, delete-orphan', lazy='dynamic')
    sent_by = db.relationship('User', foreign_keys=[sent_by_id], backref='sent_documents')
    split_children = db.relationship(
        'TransactionDocument',
        backref=db.backref('parent_document', remote_side='TransactionDocument.id'),
        foreign_keys=[parent_document_id],
        lazy='dynamic',
    )

    def __repr__(self):
        return f'<TransactionDocument {self.template_name} ({self.status})>'


class SellerListingProfile(db.Model):
    """
    Seller-specific listing controls for a transaction.
    Holds showing access rules, highest-and-best state, and listing operations data.
    """
    __tablename__ = 'seller_listing_profiles'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    # Showing/access setup
    appointment_required = db.Column(db.Boolean, default=True)
    showing_approval_policy = db.Column(db.String(50), default='manual')  # manual, auto_approve, blocked
    access_type = db.Column(db.String(50))  # lockbox, combo, supra, appointment_only, tenant_occupied, other
    lockbox_type = db.Column(db.String(50))
    gate_code = db.Column(db.String(100))
    alarm_notes = db.Column(db.Text)
    pet_notes = db.Column(db.Text)
    occupancy_status = db.Column(db.String(50))  # vacant, owner_occupied, tenant_occupied
    preferred_showing_windows = db.Column(db.JSON, default={})
    restricted_showing_times = db.Column(db.JSON, default={})
    public_showing_instructions = db.Column(db.Text)
    private_showing_notes = db.Column(db.Text)
    showing_service_url = db.Column(db.String(500))
    mls_number = db.Column(db.String(100))

    # Listing operations
    current_list_price = db.Column(db.Numeric(12, 2))
    original_list_price = db.Column(db.Numeric(12, 2))
    go_live_date = db.Column(db.Date)

    # Highest-and-best workflow
    highest_best_enabled = db.Column(db.Boolean, default=False)
    highest_best_deadline_at = db.Column(db.DateTime)
    highest_best_message = db.Column(db.Text)
    highest_best_sent_at = db.Column(db.DateTime)
    highest_best_sent_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    extra_data = db.Column(db.JSON, default={})
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_seller_listing_profiles')
    highest_best_sent_by = db.relationship('User', foreign_keys=[highest_best_sent_by_id])

    def __repr__(self):
        return f'<SellerListingProfile tx={self.transaction_id}>'


class SellerShowing(db.Model):
    """
    A showing request or appointment tied to a seller transaction.
    Also stores feedback and can be linked to resulting offers.
    """
    __tablename__ = 'seller_showings'

    STATUS_PENDING_APPROVAL = 'pending_approval'
    STATUS_APPROVED = 'approved'
    STATUS_SCHEDULED = 'scheduled'
    STATUS_COMPLETED = 'completed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_DECLINED = 'declined'
    STATUS_NO_SHOW = 'no_show'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    showing_agent_name = db.Column(db.String(200), nullable=False)
    showing_agent_email = db.Column(db.String(200))
    showing_agent_phone = db.Column(db.String(50))
    showing_agent_brokerage = db.Column(db.String(200))
    buyer_name = db.Column(db.String(200))
    source = db.Column(db.String(100))  # manual, showing_service, mls, phone, email
    showing_agent_contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=True)
    showing_agent_participant_id = db.Column(db.Integer, db.ForeignKey('transaction_participants.id'), nullable=True)

    requested_start_at = db.Column(db.DateTime)
    scheduled_start_at = db.Column(db.DateTime, nullable=False)
    scheduled_end_at = db.Column(db.DateTime)
    status = db.Column(db.String(50), default=STATUS_SCHEDULED, nullable=False)
    approved_at = db.Column(db.DateTime)
    approved_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    cancellation_reason = db.Column(db.Text)
    showing_service_confirmation = db.Column(db.String(100))

    access_instructions_snapshot = db.Column(db.Text)
    private_notes = db.Column(db.Text)

    feedback_received_at = db.Column(db.DateTime)
    feedback_interest_level = db.Column(db.String(50))  # high, medium, low, none
    feedback_price_opinion = db.Column(db.String(50))  # high, fair, low, unknown
    feedback_condition_comments = db.Column(db.Text)
    feedback_objections = db.Column(db.Text)
    feedback_likelihood = db.Column(db.String(50))
    feedback_follow_up_requested = db.Column(db.Boolean, default=False)
    feedback_notes = db.Column(db.Text)

    extra_data = db.Column(db.JSON, default={})
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_seller_showings')
    approved_by = db.relationship('User', foreign_keys=[approved_by_id])
    showing_agent_contact = db.relationship('Contact', foreign_keys=[showing_agent_contact_id])
    showing_agent_participant = db.relationship('TransactionParticipant', foreign_keys=[showing_agent_participant_id])

    def __repr__(self):
        return f'<SellerShowing {self.showing_agent_name} at {self.scheduled_start_at}>'


class SellerOffer(db.Model):
    """
    A seller-side offer thread. Versions hold uploaded/entered offer documents,
    while this row stores normalized terms used for urgency and comparisons.
    """
    __tablename__ = 'seller_offers'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    source_showing_id = db.Column(db.Integer, db.ForeignKey('seller_showings.id', ondelete='SET NULL'), nullable=True)

    buyer_names = db.Column(db.String(500))
    buyer_agent_name = db.Column(db.String(200))
    buyer_agent_email = db.Column(db.String(200))
    buyer_agent_phone = db.Column(db.String(50))
    buyer_agent_brokerage = db.Column(db.String(200))
    received_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    creation_source = db.Column(db.String(50), default='uploaded_document')  # uploaded_document, manual_entry, verbal, text_email, imported
    status = db.Column(db.String(50), default='new', nullable=False)

    response_deadline_at = db.Column(db.DateTime)
    response_deadline_source = db.Column(db.String(50))  # extracted, manual, extended
    expired_at = db.Column(db.DateTime)
    expiration_warning_sent_at = db.Column(db.DateTime)

    included_in_highest_best = db.Column(db.Boolean, default=False)
    highest_best_requested_at = db.Column(db.DateTime)
    highest_best_response_received_at = db.Column(db.DateTime)
    highest_best_response_status = db.Column(db.String(50))  # updated_before_cutoff, no_update, late_update, withdrawn

    backup_position = db.Column(db.Integer)
    backup_addendum_document_id = db.Column(db.Integer, db.ForeignKey('transaction_documents.id', ondelete='SET NULL'), nullable=True)
    backup_notice_received_at = db.Column(db.DateTime)
    backup_promoted_at = db.Column(db.DateTime)

    # Optional references to versions; intentionally not FK-constrained to avoid circular migration complexity.
    current_version_id = db.Column(db.Integer, index=True)
    accepted_version_id = db.Column(db.Integer, index=True)
    replacement_offer_id = db.Column(db.Integer, db.ForeignKey('seller_offers.id', ondelete='SET NULL'), nullable=True)

    offer_price = db.Column(db.Numeric(12, 2))
    financing_type = db.Column(db.String(100))
    cash_down_payment = db.Column(db.Numeric(12, 2))
    financing_amount = db.Column(db.Numeric(12, 2))
    earnest_money = db.Column(db.Numeric(12, 2))
    additional_earnest_money = db.Column(db.Numeric(12, 2))
    option_fee = db.Column(db.Numeric(12, 2))
    option_period_days = db.Column(db.Integer)
    seller_concessions_amount = db.Column(db.Numeric(12, 2))
    proposed_close_date = db.Column(db.Date)
    possession_type = db.Column(db.String(100))
    leaseback_days = db.Column(db.Integer)
    appraisal_contingency = db.Column(db.Boolean)
    financing_contingency = db.Column(db.Boolean)
    sale_of_other_property_contingency = db.Column(db.Boolean)
    inspection_or_repair_terms_summary = db.Column(db.Text)
    title_policy_payer = db.Column(db.String(50))
    survey_payer = db.Column(db.String(50))
    survey_furnished_by = db.Column(db.Text)
    hoa_resale_certificate_payer = db.Column(db.String(50))
    residential_service_contract = db.Column(db.Text)
    buyer_agent_commission_percent = db.Column(db.Numeric(6, 3))
    buyer_agent_commission_flat = db.Column(db.Numeric(12, 2))
    net_to_seller_estimate = db.Column(db.Numeric(12, 2))

    last_activity_at = db.Column(db.DateTime)
    last_activity_label = db.Column(db.String(200))
    next_action = db.Column(db.String(200))
    next_deadline_at = db.Column(db.DateTime)
    terms_summary = db.Column(db.JSON, default={})
    extra_data = db.Column(db.JSON, default={})

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_seller_offers')
    source_showing = db.relationship('SellerShowing', backref=db.backref('offers', lazy='dynamic'))
    backup_addendum_document = db.relationship('TransactionDocument', foreign_keys=[backup_addendum_document_id])
    replacement_offer = db.relationship('SellerOffer', remote_side=[id], foreign_keys=[replacement_offer_id])
    versions = db.relationship('SellerOfferVersion', backref='offer',
                               cascade='all, delete-orphan', lazy='dynamic',
                               foreign_keys='SellerOfferVersion.offer_id')
    offer_documents = db.relationship('SellerOfferDocument', backref='offer',
                                      cascade='all, delete-orphan', lazy='dynamic')
    activities = db.relationship('SellerOfferActivity', backref='offer',
                                 cascade='all, delete-orphan', lazy='dynamic')

    def __repr__(self):
        return f'<SellerOffer {self.id} tx={self.transaction_id} status={self.status}>'


class SellerOfferVersion(db.Model):
    """One document/manual version inside a seller offer thread."""
    __tablename__ = 'seller_offer_versions'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    offer_id = db.Column(db.Integer, db.ForeignKey('seller_offers.id', ondelete='CASCADE'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    transaction_document_id = db.Column(db.Integer, db.ForeignKey('transaction_documents.id', ondelete='SET NULL'), nullable=True)

    version_number = db.Column(db.Integer, default=1, nullable=False)
    direction = db.Column(db.String(50), nullable=False)  # buyer_offer, seller_counter, buyer_counter, final_acceptance, backup_acceptance
    status = db.Column(db.String(50), default='draft')  # draft, submitted, reviewed, accepted, declined, withdrawn
    submitted_at = db.Column(db.DateTime)
    sent_at = db.Column(db.DateTime)

    terms_data = db.Column(db.JSON, default={})
    extraction_reviewed_at = db.Column(db.DateTime)
    extraction_reviewed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_seller_offer_versions')
    extraction_reviewed_by = db.relationship('User', foreign_keys=[extraction_reviewed_by_id])
    document = db.relationship('TransactionDocument', foreign_keys=[transaction_document_id])

    def __repr__(self):
        return f'<SellerOfferVersion offer={self.offer_id} v{self.version_number}>'


class SellerOfferDocument(db.Model):
    """A PDF that belongs to an offer package, including supporting addenda."""
    __tablename__ = 'seller_offer_documents'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    offer_id = db.Column(db.Integer, db.ForeignKey('seller_offers.id', ondelete='CASCADE'), nullable=False, index=True)
    transaction_document_id = db.Column(db.Integer, db.ForeignKey('transaction_documents.id', ondelete='CASCADE'), nullable=False, index=True)
    offer_version_id = db.Column(db.Integer, db.ForeignKey('seller_offer_versions.id', ondelete='SET NULL'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    document_type = db.Column(db.String(100), nullable=False)
    display_name = db.Column(db.String(200), nullable=False)
    is_primary_terms_document = db.Column(db.Boolean, default=False)
    extraction_summary = db.Column(db.JSON, default={})

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    document = db.relationship('TransactionDocument', foreign_keys=[transaction_document_id])
    offer_version = db.relationship('SellerOfferVersion', foreign_keys=[offer_version_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id])

    def __repr__(self):
        return f'<SellerOfferDocument offer={self.offer_id} type={self.document_type}>'


class SellerContractDocument(db.Model):
    """A PDF that belongs to an accepted seller contract workspace."""
    __tablename__ = 'seller_contract_documents'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    accepted_contract_id = db.Column(db.Integer, db.ForeignKey('seller_accepted_contracts.id', ondelete='CASCADE'), nullable=False, index=True)
    transaction_document_id = db.Column(db.Integer, db.ForeignKey('transaction_documents.id', ondelete='CASCADE'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    document_type = db.Column(db.String(100), nullable=False)
    display_name = db.Column(db.String(200), nullable=False)
    is_primary_contract_document = db.Column(db.Boolean, default=False)
    extraction_summary = db.Column(db.JSON, default={})

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    accepted_contract = db.relationship(
        'SellerAcceptedContract',
        foreign_keys=[accepted_contract_id],
        backref=db.backref('contract_documents', lazy='dynamic'),
    )
    document = db.relationship('TransactionDocument', foreign_keys=[transaction_document_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id])

    def __repr__(self):
        return f'<SellerContractDocument contract={self.accepted_contract_id} type={self.document_type}>'


class SellerOfferActivity(db.Model):
    """Chronological activity log for a seller offer thread."""
    __tablename__ = 'seller_offer_activities'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    offer_id = db.Column(db.Integer, db.ForeignKey('seller_offers.id', ondelete='CASCADE'), nullable=False, index=True)
    version_id = db.Column(db.Integer, db.ForeignKey('seller_offer_versions.id', ondelete='SET NULL'), nullable=True)
    document_id = db.Column(db.Integer, db.ForeignKey('transaction_documents.id', ondelete='SET NULL'), nullable=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)

    event_type = db.Column(db.String(50), nullable=False)
    label = db.Column(db.String(200), nullable=False)
    event_data = db.Column(db.JSON, default={})
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    actor = db.relationship('User', foreign_keys=[actor_id])
    version = db.relationship('SellerOfferVersion', foreign_keys=[version_id])
    document = db.relationship('TransactionDocument', foreign_keys=[document_id])

    def __repr__(self):
        return f'<SellerOfferActivity {self.event_type} offer={self.offer_id}>'


class SellerAcceptedContract(db.Model):
    """Accepted primary or backup contract tied to a seller offer."""
    __tablename__ = 'seller_accepted_contracts'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    offer_id = db.Column(db.Integer, db.ForeignKey('seller_offers.id', ondelete='SET NULL'), nullable=True)
    accepted_version_id = db.Column(db.Integer, db.ForeignKey('seller_offer_versions.id', ondelete='SET NULL'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    status = db.Column(db.String(50), default='active', nullable=False)  # active, terminated, closed
    position = db.Column(db.String(20), default='primary', nullable=False)  # primary, backup
    backup_position = db.Column(db.Integer)
    backup_addendum_document_id = db.Column(db.Integer, db.ForeignKey('transaction_documents.id', ondelete='SET NULL'), nullable=True)
    backup_notice_sent_at = db.Column(db.DateTime)
    backup_notice_received_at = db.Column(db.DateTime)
    backup_promoted_at = db.Column(db.DateTime)

    accepted_price = db.Column(db.Numeric(12, 2))
    effective_date = db.Column(db.Date)
    effective_at = db.Column(db.DateTime)
    closing_date = db.Column(db.Date)
    option_period_days = db.Column(db.Integer)
    financing_approval_deadline = db.Column(db.Date)
    financing_type = db.Column(db.String(100))
    cash_down_payment = db.Column(db.Numeric(12, 2))
    financing_amount = db.Column(db.Numeric(12, 2))
    seller_concessions_amount = db.Column(db.Numeric(12, 2))
    title_company = db.Column(db.String(200))
    escrow_officer = db.Column(db.String(200))
    survey_choice = db.Column(db.Text)
    survey_furnished_by = db.Column(db.Text)
    residential_service_contract = db.Column(db.Text)
    buyer_agent_commission_percent = db.Column(db.Numeric(6, 3))
    buyer_agent_commission_flat = db.Column(db.Numeric(12, 2))
    hoa_applicable = db.Column(db.Boolean)
    seller_disclosure_required = db.Column(db.Boolean)
    seller_disclosure_delivered_at = db.Column(db.DateTime)
    lead_based_paint_required = db.Column(db.Boolean)

    frozen_terms = db.Column(db.JSON, default={})
    addenda_data = db.Column(db.JSON, default={})
    extra_data = db.Column(db.JSON, default={})
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    offer = db.relationship('SellerOffer', foreign_keys=[offer_id], backref=db.backref('accepted_contracts', lazy='dynamic'))
    accepted_version = db.relationship('SellerOfferVersion', foreign_keys=[accepted_version_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_seller_accepted_contracts')
    backup_addendum_document = db.relationship('TransactionDocument', foreign_keys=[backup_addendum_document_id])
    milestones = db.relationship('SellerContractMilestone', backref='accepted_contract',
                                 cascade='all, delete-orphan', lazy='dynamic')
    amendments = db.relationship('SellerContractAmendment', backref='accepted_contract',
                                 cascade='all, delete-orphan', lazy='dynamic')

    def __repr__(self):
        return f'<SellerAcceptedContract {self.position} tx={self.transaction_id} status={self.status}>'


class SellerContractMilestone(db.Model):
    """Deadline or task in the under-contract seller workflow."""
    __tablename__ = 'seller_contract_milestones'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    accepted_contract_id = db.Column(db.Integer, db.ForeignKey('seller_accepted_contracts.id', ondelete='CASCADE'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    milestone_key = db.Column(db.String(100), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    due_at = db.Column(db.DateTime)
    status = db.Column(db.String(50), default='not_started')  # not_started, waiting, due_soon, overdue, completed, not_applicable
    completed_at = db.Column(db.DateTime)
    responsible_party = db.Column(db.String(100))
    source = db.Column(db.String(50), default='calculated')  # calculated, manual, ai_extracted
    notes = db.Column(db.Text)
    source_data = db.Column(db.JSON, default={})

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id])

    def __repr__(self):
        return f'<SellerContractMilestone {self.milestone_key} tx={self.transaction_id}>'


class SellerContractAmendment(db.Model):
    """An amendment negotiation thread under an accepted contract."""
    __tablename__ = 'seller_contract_amendments'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    accepted_contract_id = db.Column(db.Integer, db.ForeignKey('seller_accepted_contracts.id', ondelete='CASCADE'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    current_version_id = db.Column(db.Integer, index=True)
    accepted_version_id = db.Column(db.Integer, index=True)

    amendment_type = db.Column(db.String(100), default='other')
    status = db.Column(db.String(50), default='received')  # received, reviewing, countered, accepted, rejected, withdrawn
    response_deadline_at = db.Column(db.DateTime)
    summary = db.Column(db.Text)
    extra_data = db.Column(db.JSON, default={})

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_seller_contract_amendments')
    versions = db.relationship('SellerContractAmendmentVersion', backref='amendment',
                               cascade='all, delete-orphan', lazy='dynamic')

    def __repr__(self):
        return f'<SellerContractAmendment {self.amendment_type} tx={self.transaction_id}>'


class SellerContractAmendmentVersion(db.Model):
    """One uploaded or manual amendment/counter-amendment version."""
    __tablename__ = 'seller_contract_amendment_versions'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    amendment_id = db.Column(db.Integer, db.ForeignKey('seller_contract_amendments.id', ondelete='CASCADE'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    transaction_document_id = db.Column(db.Integer, db.ForeignKey('transaction_documents.id', ondelete='SET NULL'), nullable=True)

    version_number = db.Column(db.Integer, default=1, nullable=False)
    direction = db.Column(db.String(50), nullable=False)  # buyer_amendment, seller_counter_amendment, buyer_counter_amendment, accepted_amendment
    status = db.Column(db.String(50), default='draft')
    submitted_at = db.Column(db.DateTime)
    terms_data = db.Column(db.JSON, default={})
    reviewed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_seller_contract_amendment_versions')
    reviewed_by = db.relationship('User', foreign_keys=[reviewed_by_id])
    document = db.relationship('TransactionDocument', foreign_keys=[transaction_document_id])

    def __repr__(self):
        return f'<SellerContractAmendmentVersion amendment={self.amendment_id} v{self.version_number}>'


class SellerContractTermination(db.Model):
    """A terminated accepted contract and its seller workflow outcome."""
    __tablename__ = 'seller_contract_terminations'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    accepted_contract_id = db.Column(db.Integer, db.ForeignKey('seller_accepted_contracts.id', ondelete='CASCADE'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    termination_document_id = db.Column(db.Integer, db.ForeignKey('transaction_documents.id', ondelete='SET NULL'), nullable=True)
    promoted_backup_contract_id = db.Column(db.Integer, db.ForeignKey('seller_accepted_contracts.id', ondelete='SET NULL'), nullable=True)

    termination_reason = db.Column(db.String(100), nullable=False)
    terminated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    earnest_money_disposition = db.Column(db.String(200))
    notes = db.Column(db.Text)
    returned_to_active = db.Column(db.Boolean, default=False)
    backup_promoted = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    accepted_contract = db.relationship('SellerAcceptedContract', foreign_keys=[accepted_contract_id],
                                        backref=db.backref('terminations', lazy='dynamic'))
    promoted_backup_contract = db.relationship('SellerAcceptedContract', foreign_keys=[promoted_backup_contract_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_seller_contract_terminations')
    termination_document = db.relationship('TransactionDocument', foreign_keys=[termination_document_id])

    def __repr__(self):
        return f'<SellerContractTermination contract={self.accepted_contract_id} reason={self.termination_reason}>'


class SellerClosingSummary(db.Model):
    """Final closeout details for a seller transaction."""
    __tablename__ = 'seller_closing_summaries'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    accepted_contract_id = db.Column(db.Integer, db.ForeignKey('seller_accepted_contracts.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    final_net_sheet_document_id = db.Column(db.Integer, db.ForeignKey('transaction_documents.id', ondelete='SET NULL'), nullable=True)

    actual_closing_date = db.Column(db.Date)
    funded_recorded_at = db.Column(db.DateTime)
    final_sales_price = db.Column(db.Numeric(12, 2))
    final_seller_concessions = db.Column(db.Numeric(12, 2))
    final_listing_commission = db.Column(db.Numeric(12, 2))
    final_coop_compensation = db.Column(db.Numeric(12, 2))
    final_referral_fee = db.Column(db.Numeric(12, 2))
    final_net_proceeds = db.Column(db.Numeric(12, 2))
    deed_recording_reference = db.Column(db.String(200))
    final_walkthrough_complete = db.Column(db.Boolean, default=False)
    key_access_handoff_complete = db.Column(db.Boolean, default=False)
    possession_status = db.Column(db.String(100))
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    accepted_contract = db.relationship('SellerAcceptedContract', backref=db.backref('closing_summary', uselist=False))
    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_seller_closing_summaries')
    final_net_sheet_document = db.relationship('TransactionDocument', foreign_keys=[final_net_sheet_document_id])

    def __repr__(self):
        return f'<SellerClosingSummary tx={self.transaction_id} closing={self.actual_closing_date}>'


class SellerCommissionTerms(db.Model):
    """Listing commission and representation terms for seller transactions."""
    __tablename__ = 'seller_commission_terms'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    listing_commission_percent = db.Column(db.Numeric(6, 3))
    listing_commission_flat = db.Column(db.Numeric(12, 2))
    coop_compensation_percent = db.Column(db.Numeric(6, 3))
    coop_compensation_flat = db.Column(db.Numeric(12, 2))
    bonus_amount = db.Column(db.Numeric(12, 2))
    referral_fee_percent = db.Column(db.Numeric(6, 3))
    referral_fee_flat = db.Column(db.Numeric(12, 2))
    admin_transaction_fee = db.Column(db.Numeric(12, 2))
    representation_mode = db.Column(db.String(50), default='unknown')  # separate_buyer_agent, intermediary_same_agent, intermediary_different_associates, unknown
    source = db.Column(db.String(50), default='manual')  # listing_agreement_extraction, manual, amendment
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    transaction = db.relationship('Transaction', backref=db.backref('seller_commission_terms', uselist=False))
    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_seller_commission_terms')

    def __repr__(self):
        return f'<SellerCommissionTerms tx={self.transaction_id}>'


class SellerListingPriceChange(db.Model):
    """History of seller listing price changes."""
    __tablename__ = 'seller_listing_price_changes'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    old_price = db.Column(db.Numeric(12, 2))
    new_price = db.Column(db.Numeric(12, 2), nullable=False)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reason = db.Column(db.String(200))
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_seller_listing_price_changes')

    def __repr__(self):
        return f'<SellerListingPriceChange tx={self.transaction_id} {self.old_price}->{self.new_price}>'


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
        size = self.file_size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
    
    @property
    def size_display(self):
        """Alias for human_file_size for API compatibility."""
        return self.human_file_size
    
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


# =============================================================================
# VOICE MEMO MODELS
# =============================================================================

class ContactVoiceMemo(db.Model):
    """
    Voice memos recorded for contacts.
    Stored in Supabase Storage with optional AI transcription.
    """
    __tablename__ = 'contact_voice_memos'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', 
                                ondelete='CASCADE'), nullable=False, index=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id', 
                           ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', 
                        ondelete='SET NULL'), nullable=True)
    
    # Storage info
    storage_path = db.Column(db.String(500), nullable=False)  # Path in Supabase bucket
    file_name = db.Column(db.String(255), nullable=False)
    duration_seconds = db.Column(db.Integer)  # Audio duration
    file_size = db.Column(db.Integer)  # File size in bytes
    
    # Optional AI transcription (via OpenAI Whisper)
    transcription = db.Column(db.Text)
    transcription_status = db.Column(db.String(20), default='pending')  # pending, completed, failed
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    contact = db.relationship('Contact', backref=db.backref('voice_memos', lazy='dynamic', 
                             cascade='all, delete-orphan'))
    user = db.relationship('User', backref=db.backref('voice_memos', lazy='dynamic'))
    organization = db.relationship('Organization', backref=db.backref('voice_memos', lazy='dynamic'))
    
    def __repr__(self):
        return f'<ContactVoiceMemo {self.id} for contact {self.contact_id}>'
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        return {
            'id': self.id,
            'contact_id': self.contact_id,
            'file_name': self.file_name,
            'duration_seconds': self.duration_seconds,
            'transcription': self.transcription,
            'transcription_status': self.transcription_status,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# =============================================================================
# AI CHAT HISTORY MODELS
# =============================================================================

class ChatConversation(db.Model):
    """
    Represents a B.O.B. chat conversation for an agent.
    Each conversation can have multiple messages.
    """
    __tablename__ = 'chat_conversations'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    title = db.Column(db.String(100), nullable=True)  # AI-generated, null until first exchange
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('chat_conversations', lazy='dynamic', cascade='all, delete-orphan'))
    organization = db.relationship('Organization', backref=db.backref('chat_conversations', lazy='dynamic'))
    messages = db.relationship('ChatMessage', backref='conversation', lazy='dynamic', cascade='all, delete-orphan', order_by='ChatMessage.created_at')
    
    def __repr__(self):
        return f'<ChatConversation {self.id}: {self.title or "Untitled"}>'
    
    def to_dict(self, include_messages=False):
        """Convert to dictionary for JSON serialization."""
        data = {
            'id': self.id,
            'title': self.title,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
        if include_messages:
            data['messages'] = [msg.to_dict() for msg in self.messages.all()]
        return data


class ChatMessage(db.Model):
    """
    A single message in a B.O.B. chat conversation.
    """
    __tablename__ = 'chat_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('chat_conversations.id', ondelete='CASCADE'), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)  # 'user' or 'assistant'
    content = db.Column(db.Text, nullable=False)
    image_data = db.Column(db.Text, nullable=True)  # Base64 for image attachments
    mentioned_contact_ids = db.Column(db.JSON, nullable=True)  # Array of contact IDs
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # File attachment fields (for non-image files stored in Supabase Storage)
    file_url = db.Column(db.String(500), nullable=True)  # Supabase signed URL
    file_name = db.Column(db.String(255), nullable=True)  # Original filename
    file_type = db.Column(db.String(100), nullable=True)  # MIME type
    file_size = db.Column(db.Integer, nullable=True)  # Size in bytes
    file_storage_path = db.Column(db.String(500), nullable=True)  # Storage path for cleanup
    
    def __repr__(self):
        return f'<ChatMessage {self.id}: {self.role}>'
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        # Generate fresh signed URL if we have a storage path
        file_url = self.file_url
        if self.file_storage_path and not file_url:
            try:
                from services.supabase_storage import get_signed_url, CHAT_ATTACHMENTS_BUCKET
                file_url = get_signed_url(CHAT_ATTACHMENTS_BUCKET, self.file_storage_path, expires_in=86400)
            except Exception:
                pass
        
        return {
            'id': self.id,
            'role': self.role,
            'content': self.content,
            'image_data': self.image_data,
            'mentioned_contact_ids': self.mentioned_contact_ids,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'file_url': file_url,
            'file_name': self.file_name,
            'file_type': self.file_type,
            'file_size': self.file_size
        }


# =============================================================================
# GMAIL INTEGRATION MODELS
# =============================================================================

class UserEmailIntegration(db.Model):
    """
    Stores OAuth connection for Gmail integration per user.
    Each user can connect their own Gmail account for email sync.
    """
    __tablename__ = 'user_email_integrations'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), 
                        unique=True, nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='CASCADE'), 
                                nullable=False, index=True)
    
    # Provider info
    provider = db.Column(db.String(20), default='gmail')
    connected_email = db.Column(db.String(200))
    
    # Encrypted OAuth tokens (Fernet encryption)
    access_token_encrypted = db.Column(db.Text)
    refresh_token_encrypted = db.Column(db.Text)
    token_expires_at = db.Column(db.DateTime)
    
    # Sync state
    sync_enabled = db.Column(db.Boolean, default=True)
    last_sync_at = db.Column(db.DateTime)
    last_history_id = db.Column(db.String(100))  # Gmail incremental sync checkpoint
    sync_status = db.Column(db.String(20), default='pending')  # pending, syncing, active, error
    sync_error = db.Column(db.Text)
    
    # Google Calendar sync
    calendar_sync_enabled = db.Column(db.Boolean, default=False)  # Toggle for calendar sync
    
    # CRM-based email signature (replaces gmail.settings.basic scope requirement)
    signature_html = db.Column(db.Text)  # HTML content of signature
    signature_images = db.Column(db.JSON)  # Image metadata: [{filename, content_id, mime_type, bytes_b64}]
    
    # OAuth scope version for soft reauth (1=legacy restricted, 2=send-only)
    oauth_scope_version = db.Column(db.Integer, default=2)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('email_integration', uselist=False, 
                          cascade='all, delete-orphan'))
    organization = db.relationship('Organization', backref=db.backref('email_integrations', 
                                  lazy='dynamic'))
    
    @property
    def needs_reauth(self):
        """Check if user needs to reconnect with new scopes."""
        return self.oauth_scope_version is None or self.oauth_scope_version < 2
    
    @property
    def has_signature(self):
        """Check if user has a signature configured."""
        return bool(self.signature_html and self.signature_html.strip())
    
    def get_signature_images_list(self):
        """Get signature images as a list (handles None case)."""
        return self.signature_images or []
    
    def __repr__(self):
        return f'<UserEmailIntegration {self.connected_email} for user {self.user_id}>'


class ContactEmail(db.Model):
    """
    Stores synced email messages linked to contacts.
    Emails are matched to contacts by email address during sync.
    """
    __tablename__ = 'contact_emails'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='CASCADE'), 
                                nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), 
                        nullable=False, index=True)  # Agent who synced this email
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id', ondelete='CASCADE'), 
                          nullable=False, index=True)
    
    # Gmail identifiers (for deduplication and threading)
    # Note: Composite unique on (gmail_message_id, contact_id) allows same email to appear for multiple contacts
    gmail_message_id = db.Column(db.String(100), nullable=False, index=True)
    gmail_thread_id = db.Column(db.String(100), index=True)
    
    # Composite unique constraint: one email per contact (not globally unique)
    __table_args__ = (
        db.UniqueConstraint('gmail_message_id', 'contact_id', name='uq_email_contact'),
    )
    
    # Message content
    subject = db.Column(db.String(500))
    snippet = db.Column(db.String(500))  # Gmail's ~100 char preview
    from_email = db.Column(db.String(200))
    from_name = db.Column(db.String(200))
    to_emails = db.Column(db.JSON)  # List of recipient emails
    cc_emails = db.Column(db.JSON)  # List of CC emails
    
    # Direction relative to agent
    direction = db.Column(db.String(10))  # 'inbound' or 'outbound'
    
    # Timestamp
    sent_at = db.Column(db.DateTime, index=True)
    
    # Metadata
    has_attachments = db.Column(db.Boolean, default=False)
    
    # Full email body for in-CRM viewing
    body_text = db.Column(db.Text)  # Plain text version
    body_html = db.Column(db.Text)  # HTML version (sanitized before display)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    contact = db.relationship('Contact', backref=db.backref('emails', lazy='dynamic',
                             cascade='all, delete-orphan'))
    user = db.relationship('User', backref=db.backref('synced_emails', lazy='dynamic'))
    organization = db.relationship('Organization', backref=db.backref('synced_emails', 
                                  lazy='dynamic'))
    
    def __repr__(self):
        return f'<ContactEmail {self.subject[:30] if self.subject else "No subject"}>'
    
    def to_dict(self):
        """Convert to dictionary for JSON/template serialization."""
        return {
            'id': self.id,
            'gmail_message_id': self.gmail_message_id,
            'gmail_thread_id': self.gmail_thread_id,
            'subject': self.subject,
            'snippet': self.snippet,
            'from_email': self.from_email,
            'from_name': self.from_name,
            'to_emails': self.to_emails,
            'cc_emails': self.cc_emails,
            'direction': self.direction,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'has_attachments': self.has_attachments,
            'body_text': self.body_text,
            'body_html': self.body_html,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# =============================================================================
# TAX PROTEST REFERENCE DATA (shared, no org_id / no RLS)
# =============================================================================

class ChambersProperty(db.Model):
    """Chambers County tax appraisal records."""
    __tablename__ = 'chambers_properties'

    id = db.Column(db.Integer, primary_key=True)
    parcel_id = db.Column(db.String(50), index=True)
    account = db.Column(db.String(50), index=True)
    street = db.Column(db.String(200))
    street_overflow = db.Column(db.String(200))
    city = db.Column(db.String(100))
    zip5 = db.Column(db.String(10))
    prop_street_number = db.Column(db.String(20), index=True)
    prop_street = db.Column(db.String(100), index=True)
    prop_street_dir = db.Column(db.String(10))
    prop_city = db.Column(db.String(100))
    prop_zip5 = db.Column(db.String(10), index=True)
    legal1 = db.Column(db.String(500))
    legal2 = db.Column(db.String(500))
    legal3 = db.Column(db.String(500))
    legal4 = db.Column(db.String(500))
    acres = db.Column(db.Numeric(14, 4))
    market_value = db.Column(db.Integer)
    improvement_hs_val = db.Column(db.Integer)
    improvement_nhs_val = db.Column(db.Integer)
    sq_ft = db.Column(db.Integer)

    def __repr__(self):
        return f'<ChambersProperty {self.prop_street_number} {self.prop_street}>'


class HcadProperty(db.Model):
    """Harris County (HCAD) tax appraisal records."""
    __tablename__ = 'hcad_properties'

    id = db.Column(db.Integer, primary_key=True)
    acct = db.Column(db.String(30), unique=True, index=True)
    str_num = db.Column(db.String(30), index=True)
    str_num_sfx = db.Column(db.String(50))
    str = db.Column(db.String(200), index=True)
    str_sfx = db.Column(db.String(50))
    str_sfx_dir = db.Column(db.String(50))
    str_unit = db.Column(db.String(50))
    site_addr_1 = db.Column(db.String(200))
    site_addr_2 = db.Column(db.String(100))
    site_addr_3 = db.Column(db.String(30), index=True)
    acreage = db.Column(db.Numeric(14, 4))
    assessed_val = db.Column(db.Integer)
    tot_appr_val = db.Column(db.Integer)
    tot_mkt_val = db.Column(db.Integer)
    lgl_1 = db.Column(db.String(500))
    lgl_2 = db.Column(db.String(500))
    lgl_3 = db.Column(db.String(500))
    lgl_4 = db.Column(db.String(500))

    neighborhood_code = db.Column(db.String(20), index=True)

    buildings = db.relationship('HcadBuilding', backref='property', lazy='dynamic')

    def __repr__(self):
        return f'<HcadProperty {self.site_addr_1}>'


class HcadNeighborhoodCode(db.Model):
    """HCAD neighborhood code lookup table."""
    __tablename__ = 'hcad_neighborhood_codes'

    id = db.Column(db.Integer, primary_key=True)
    cd = db.Column(db.String(20), unique=True, index=True)
    grp_cd = db.Column(db.String(20))
    dscr = db.Column(db.String(500))

    def __repr__(self):
        return f'<HcadNeighborhoodCode {self.cd} {self.dscr}>'


class HcadBuilding(db.Model):
    """Harris County building records (sq footage)."""
    __tablename__ = 'hcad_buildings'

    id = db.Column(db.Integer, primary_key=True)
    acct = db.Column(db.String(30), db.ForeignKey('hcad_properties.acct'), nullable=False, index=True)
    im_sq_ft = db.Column(db.Integer)


class LibertyProperty(db.Model):
    """Liberty County home-focused tax appraisal records."""
    __tablename__ = 'liberty_properties'

    id = db.Column(db.Integer, primary_key=True)
    prop_id = db.Column(db.String(20), unique=True, index=True)
    geo_id = db.Column(db.String(50), index=True)
    prop_type_cd = db.Column(db.String(10), index=True)
    situs_num = db.Column(db.String(20), index=True)
    situs_street_prefx = db.Column(db.String(20))
    situs_street = db.Column(db.String(100), index=True)
    situs_street_suffix = db.Column(db.String(20))
    situs_unit = db.Column(db.String(20))
    situs_city = db.Column(db.String(100))
    situs_zip = db.Column(db.String(10), index=True)
    site_addr_1 = db.Column(db.String(200))
    normalized_site_addr = db.Column(db.String(200), index=True)
    legal_desc = db.Column(db.String(500))
    legal_desc2 = db.Column(db.String(500))
    legal_acreage = db.Column(db.Numeric(16, 4))
    abs_subdv_cd = db.Column(db.String(10), index=True)
    abs_subdv_desc = db.Column(db.String(200), index=True)
    appraised_val = db.Column(db.Integer)
    assessed_val = db.Column(db.Integer)
    market_value = db.Column(db.Integer)
    imprv_hstd_val = db.Column(db.Integer)
    imprv_non_hstd_val = db.Column(db.Integer)
    sq_ft = db.Column(db.Integer)
    is_residential_home = db.Column(db.Boolean, nullable=False, default=False, index=True)

    improvements = db.relationship('LibertyImprovement', backref='property', lazy='dynamic')

    def __repr__(self):
        return f'<LibertyProperty {self.site_addr_1 or self.geo_id}>'


class LibertyImprovement(db.Model):
    """Liberty County residential/mobile-home improvements used to classify homes."""
    __tablename__ = 'liberty_improvements'

    id = db.Column(db.Integer, primary_key=True)
    prop_id = db.Column(db.String(20), db.ForeignKey('liberty_properties.prop_id'), nullable=False, index=True)
    imprv_id = db.Column(db.String(20), nullable=False, index=True)
    imprv_type_cd = db.Column(db.String(10))
    imprv_type_desc = db.Column(db.String(50))
    imprv_homesite = db.Column(db.String(1))
    imprv_val = db.Column(db.Integer)
    residential_sq_ft = db.Column(db.Integer)
    is_residential = db.Column(db.Boolean, nullable=False, default=False, index=True)

    __table_args__ = (
        db.UniqueConstraint('prop_id', 'imprv_id', name='uq_liberty_improvement_prop_imprv'),
    )

    def __repr__(self):
        return f'<LibertyImprovement {self.prop_id}/{self.imprv_id}>'


class LibertyCodeProfile(db.Model):
    """Stored strategy metadata for Liberty subdivision/abstract codes."""
    __tablename__ = 'liberty_code_profiles'

    id = db.Column(db.Integer, primary_key=True)
    abs_subdv_cd = db.Column(db.String(10), unique=True, index=True, nullable=False)
    abs_subdv_desc = db.Column(db.String(200), index=True)
    property_count = db.Column(db.Integer, nullable=False, default=0)
    avg_acreage = db.Column(db.Numeric(14, 4))
    median_acreage = db.Column(db.Numeric(14, 4))
    pct_with_situs_num = db.Column(db.Numeric(8, 4))
    pct_with_sq_ft = db.Column(db.Numeric(8, 4))
    distinct_street_count = db.Column(db.Integer)
    distinct_zip_count = db.Column(db.Integer)
    sample_addresses = db.Column(db.JSON)
    sample_legal_descriptions = db.Column(db.JSON)
    bucket = db.Column(db.String(30), index=True)
    strategy = db.Column(db.String(20), index=True)
    confidence = db.Column(db.Numeric(5, 4))
    rationale = db.Column(db.Text)
    model_name = db.Column(db.String(50))
    prompt_version = db.Column(db.String(30))
    classified_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<LibertyCodeProfile {self.abs_subdv_cd} {self.strategy}>'


class FortBendProperty(db.Model):
    """Fort Bend County home-focused tax appraisal records."""
    __tablename__ = 'fort_bend_properties'

    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.String(20), unique=True, index=True)
    quick_ref_id = db.Column(db.String(20), index=True)
    property_number = db.Column(db.String(40), index=True)
    legal_desc = db.Column(db.String(1000))
    legal_location_code = db.Column(db.String(50))
    legal_location_desc = db.Column(db.String(500))
    legal_acres = db.Column(db.Numeric(16, 4))
    market_value = db.Column(db.Integer)
    assessed_value = db.Column(db.Integer)
    land_value = db.Column(db.Integer)
    improvement_value = db.Column(db.Integer)
    sq_ft = db.Column(db.Integer)
    nbhd_code = db.Column(db.String(20), index=True)
    nbhd_desc = db.Column(db.String(500), index=True)
    situs = db.Column(db.String(255))
    site_addr_1 = db.Column(db.String(200))
    normalized_site_addr = db.Column(db.String(200), index=True)
    situs_pre_directional = db.Column(db.String(20))
    situs_street_number = db.Column(db.String(20), index=True)
    situs_street_name = db.Column(db.String(100), index=True)
    situs_street_suffix = db.Column(db.String(20))
    situs_post_directional = db.Column(db.String(20))
    situs_city = db.Column(db.String(100))
    situs_state = db.Column(db.String(10))
    situs_zip = db.Column(db.String(10), index=True)
    acreage = db.Column(db.Numeric(16, 4))
    is_residential_home = db.Column(db.Boolean, nullable=False, default=False, index=True)

    def __repr__(self):
        return f'<FortBendProperty {self.site_addr_1 or self.property_number}>'


# =============================================================================
# IN-APP NOTIFICATIONS
# =============================================================================

class Notification(db.Model):
    """In-app notification delivered to a specific user's bell icon."""
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='RESTRICT'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id',
                        ondelete='CASCADE'), nullable=False, index=True)

    # Category lets the preference system gate delivery per-type
    category = db.Column(db.String(50), nullable=False, index=True)

    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text)
    icon = db.Column(db.String(60), default='fa-bell')
    action_url = db.Column(db.String(500))

    is_read = db.Column(db.Boolean, default=False, nullable=False, index=True)
    read_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = db.relationship('User', backref=db.backref('notifications',
                           lazy='dynamic', order_by='Notification.created_at.desc()'))

    # Rows older than 90 days are safe to prune via a periodic job.
    CATEGORIES = {
        'task_reminder': 'Task Reminders',
        'company_update': 'Company Updates',
        'magic_inbox': 'Magic Inbox',
    }

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = datetime.utcnow()

    def to_dict(self):
        return {
            'id': self.id,
            'category': self.category,
            'title': self.title,
            'body': self.body,
            'icon': self.icon,
            'action_url': self.action_url,
            'is_read': self.is_read,
            'created_at': self.created_at.isoformat() + 'Z' if self.created_at else None,
            'read_at': self.read_at.isoformat() + 'Z' if self.read_at else None,
        }

    def __repr__(self):
        return f'<Notification {self.id} cat={self.category} user={self.user_id}>'


class UserNotificationPreference(db.Model):
    """Per-user opt-in/out for each notification category + channel."""
    __tablename__ = 'user_notification_preferences'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id',
                        ondelete='CASCADE'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='RESTRICT'), nullable=False, index=True)

    category = db.Column(db.String(50), nullable=False)

    # Channels — start with two; add push/sms later
    in_app_enabled = db.Column(db.Boolean, default=True, nullable=False)
    email_enabled = db.Column(db.Boolean, default=True, nullable=False)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    user = db.relationship('User', backref=db.backref('notification_preferences',
                           lazy='dynamic'))

    __table_args__ = (
        db.UniqueConstraint('user_id', 'category',
                            name='uq_user_notification_pref'),
    )

    def __repr__(self):
        return (f'<UserNotificationPreference user={self.user_id} '
                f'cat={self.category} app={self.in_app_enabled} '
                f'email={self.email_enabled}>')


# =============================================================================
# MAGIC INBOX (per-user forwarding address → AI-extracted contacts)
# =============================================================================

class InboundMessage(db.Model):
    """An inbound email to a user's magic inbox.

    One row per inbound message, kept for analytics, debugging, undo, and
    rate-limiting. The raw payload is offloaded to Supabase Storage so this
    table stays small.
    """
    __tablename__ = 'inbound_messages'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id',
                                ondelete='RESTRICT'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id',
                        ondelete='CASCADE'), nullable=False, index=True)

    recipient_address = db.Column(db.String(200), nullable=False, index=True)
    sender_email = db.Column(db.String(200), nullable=True, index=True)
    subject = db.Column(db.String(500), nullable=True)
    plus_alias = db.Column(db.String(100), nullable=True)

    # Where the raw MIME payload lives in Supabase Storage. Kept ~30 days
    # for debugging and abuse review (pruned by a periodic job).
    raw_storage_path = db.Column(db.String(500), nullable=True)

    # vcard | csv | image | text | mixed — set from attachment MIME types so
    # we can see which formats users actually forward, even though the AI
    # path is identical for all of them.
    source_kind = db.Column(db.String(20), nullable=False, default='text', index=True)

    # AI observability so we can see real per-user cost.
    ai_model = db.Column(db.String(60), nullable=True)
    ai_tokens_in = db.Column(db.Integer, nullable=True)
    ai_tokens_out = db.Column(db.Integer, nullable=True)
    ai_cost_cents = db.Column(db.Numeric(10, 4), nullable=True)

    # received | processed | failed | rejected | over_limit
    status = db.Column(db.String(20), nullable=False, default='received', index=True)
    error_message = db.Column(db.Text, nullable=True)

    # JSON list of Contact ids created from this message — drives the undo flow.
    created_contact_ids = db.Column(db.JSON, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    processed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User', backref=db.backref('inbound_messages',
                           lazy='dynamic', order_by='InboundMessage.created_at.desc()'))

    SOURCE_KINDS = {'vcard', 'csv', 'image', 'text', 'mixed'}
    STATUSES = {'received', 'processed', 'failed', 'rejected', 'over_limit'}

    def __repr__(self):
        return (f'<InboundMessage {self.id} status={self.status} '
                f'kind={self.source_kind} user={self.user_id}>')


# =============================================================================
# MARKET INSIGHTS (RentCast-backed, multi-tenant-agnostic lookup data)
# =============================================================================

class ServiceArea(db.Model):
    """A named geographic area composed of one or more ZIP codes.

    Not tenant-scoped: this is global lookup data shared across all orgs.
    """
    __tablename__ = 'service_areas'

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(100), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(200), nullable=False)
    zip_codes = db.Column(db.JSON, nullable=False)  # list[str]
    sort_order = db.Column(db.Integer, nullable=False, default=0, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<ServiceArea {self.slug}>'


class MarketDataCache(db.Model):
    """Per-ZIP cache of the RentCast /markets payload.

    Refresh is gated by an atomic claim (see services/market_insights_service.py)
    so concurrent dashboard loads never duplicate the API call.
    """
    __tablename__ = 'market_data_cache'

    zip_code = db.Column(db.String(10), primary_key=True)
    payload = db.Column(db.JSON, nullable=True)
    refreshed_at = db.Column(db.DateTime, nullable=True, index=True)
    refresh_started_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<MarketDataCache {self.zip_code} refreshed={self.refreshed_at}>'


class RentcastApiLog(db.Model):
    """Audit row written for every outbound RentCast call so the monthly
    quota burn is queryable rather than buried in stdout logs."""
    __tablename__ = 'rentcast_api_log'

    id = db.Column(db.Integer, primary_key=True)
    zip_code = db.Column(db.String(10), nullable=True, index=True)
    endpoint = db.Column(db.String(100), nullable=False)
    status_code = db.Column(db.Integer, nullable=True)
    latency_ms = db.Column(db.Integer, nullable=True)
    error = db.Column(db.Text, nullable=True)
    called_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def __repr__(self):
        return f'<RentcastApiLog {self.endpoint} zip={self.zip_code} status={self.status_code}>'
