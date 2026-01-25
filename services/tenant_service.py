# services/tenant_service.py
"""
Tenant isolation helpers for multi-tenant SaaS.
ALWAYS use org_query() for tenant-scoped models.
RLS is the safety net, but application code should be correct too.
"""

from flask_login import current_user
from functools import wraps
from flask import abort, flash, redirect, url_for


# Models that require org scoping
TENANT_MODELS = [
    'Contact', 'ContactGroup', 'Task', 'Transaction', 'TransactionDocument',
    'TransactionParticipant', 'DocumentSignature', 'ActionPlan',
    'DailyTodoList', 'UserTodo', 'CompanyUpdate', 'ContactFile', 'SendGridTemplate'
]


# =============================================================================
# QUERY HELPERS
# =============================================================================

def org_query(model):
    """
    Return query filtered to current user's organization.
    ALWAYS use this for tenant-scoped models instead of Model.query.
    
    Example:
        contacts = org_query(Contact).filter_by(user_id=current_user.id).all()
    
    Args:
        model: SQLAlchemy model class with organization_id column
        
    Returns:
        Query object filtered to current user's organization
        
    Raises:
        RuntimeError: If called without authenticated user
    """
    if not current_user.is_authenticated:
        raise RuntimeError("org_query() requires authenticated user")
    
    return model.query.filter_by(organization_id=current_user.organization_id)


def org_query_for_id(model, org_id: int):
    """
    Return query filtered to a specific organization.
    Use in background jobs where current_user is not available.
    
    Args:
        model: SQLAlchemy model class with organization_id column
        org_id: Organization ID to filter by
        
    Returns:
        Query object filtered to the specified organization
    """
    return model.query.filter_by(organization_id=org_id)


# =============================================================================
# PERMISSION CHECKS
# =============================================================================

def can_view_all_org_data():
    """Check if user can see all data in their org (owner/admin)."""
    if not current_user.is_authenticated:
        return False
    return current_user.org_role in ('owner', 'admin')


def is_platform_admin():
    """Check if user is Origen super admin."""
    if not current_user.is_authenticated:
        return False
    if not current_user.is_super_admin:
        return False
    org = current_user.organization
    return org and org.is_platform_admin


def is_org_owner():
    """Check if user is owner of their organization."""
    if not current_user.is_authenticated:
        return False
    return current_user.org_role == 'owner'


def is_org_admin():
    """Check if user is admin or owner of their organization."""
    if not current_user.is_authenticated:
        return False
    return current_user.org_role in ('owner', 'admin')


# =============================================================================
# LIMIT CHECKS
# =============================================================================

def org_can_add_user() -> tuple[bool, str]:
    """
    Check if org can add another user.
    
    Returns:
        Tuple of (allowed: bool, message: str)
    """
    org = current_user.organization
    
    if not org.can_invite_users:
        return False, "Your plan does not allow inviting users. Upgrade to Pro."
    
    current_count = org.users.count()
    if org.max_users and current_count >= org.max_users:
        return False, f"User limit reached ({org.max_users}). Contact support to increase."
    
    return True, ""


def org_can_add_contact() -> tuple[bool, str]:
    """
    Check if org can add another contact.
    
    Returns:
        Tuple of (allowed: bool, message: str)
    """
    from models import Contact
    
    org = current_user.organization
    
    if org.max_contacts is None:
        return True, ""  # Unlimited
    
    current_count = org_query(Contact).count()
    if current_count >= org.max_contacts:
        return False, f"Contact limit reached ({org.max_contacts}). Upgrade to Pro for unlimited."
    
    return True, ""


def get_contacts_remaining() -> int | None:
    """
    Get number of contacts remaining before limit.
    
    Returns:
        Number of contacts remaining, or None if unlimited
    """
    from models import Contact
    
    org = current_user.organization
    
    if org.max_contacts is None:
        return None  # Unlimited
    
    current_count = org_query(Contact).count()
    return max(0, org.max_contacts - current_count)


# =============================================================================
# ROLE MANAGEMENT
# =============================================================================

ROLE_HIERARCHY = {'owner': 3, 'admin': 2, 'agent': 1}


def get_role_level(role: str) -> int:
    """Get numeric level for a role."""
    return ROLE_HIERARCHY.get(role, 0)


def can_assign_role(assigner_role: str, target_role: str) -> bool:
    """
    Check if assigner can assign target_role.
    Can only assign roles BELOW your level.
    
    Args:
        assigner_role: Role of the user assigning the role
        target_role: Role being assigned
        
    Returns:
        True if assignment is allowed
    """
    return ROLE_HIERARCHY.get(assigner_role, 0) > ROLE_HIERARCHY.get(target_role, 0)


def can_modify_user(modifier, target_user) -> bool:
    """
    Check if modifier can change target_user's role or remove them.
    
    Args:
        modifier: User attempting the modification
        target_user: User being modified
        
    Returns:
        True if modification is allowed
    """
    if modifier.id == target_user.id:
        return False  # Cannot modify self
    if modifier.organization_id != target_user.organization_id:
        return False  # Must be same org
    return ROLE_HIERARCHY.get(modifier.org_role, 0) > ROLE_HIERARCHY.get(target_user.org_role, 0)


def validate_last_owner(org, user_being_modified, new_role=None, removing=False):
    """
    Prevent removing or demoting the last owner.
    
    Args:
        org: Organization model instance
        user_being_modified: User being changed
        new_role: New role being assigned (if demoting)
        removing: True if user is being removed from org
        
    Raises:
        ValueError: If modification would leave org without an owner
    """
    if user_being_modified.org_role != 'owner':
        return  # Not an owner, no restriction
    
    owner_count = org.users.filter_by(org_role='owner').count()
    
    if owner_count <= 1:
        if removing:
            raise ValueError("Cannot remove the last owner of the organization.")
        if new_role and new_role != 'owner':
            raise ValueError("Cannot demote the last owner. Promote someone else to owner first.")


# =============================================================================
# DECORATORS
# =============================================================================

def org_owner_required(f):
    """Decorator to require org owner role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if not is_org_owner():
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def org_admin_required(f):
    """Decorator to require org admin or owner role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if not is_org_admin():
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def platform_admin_required(f):
    """Decorator to require Origen platform super admin."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if not is_platform_admin():
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


# =============================================================================
# VALIDATION HELPERS
# =============================================================================

def validate_org_member(user_id: int) -> bool:
    """
    Validate that a user belongs to the current user's organization.
    
    Args:
        user_id: User ID to check
        
    Returns:
        True if user is in the same organization
    """
    from models import User
    
    user = User.query.get(user_id)
    if not user:
        return False
    return user.organization_id == current_user.organization_id


def validate_org_resource(model, resource_id: int) -> bool:
    """
    Validate that a resource belongs to the current user's organization.
    
    Args:
        model: SQLAlchemy model class
        resource_id: ID of the resource to check
        
    Returns:
        True if resource belongs to user's organization
    """
    resource = model.query.get(resource_id)
    if not resource:
        return False
    if hasattr(resource, 'organization_id'):
        return resource.organization_id == current_user.organization_id
    return False


def get_or_404_org(model, resource_id: int):
    """
    Get a resource by ID or abort 404 if not found or wrong org.
    
    Args:
        model: SQLAlchemy model class
        resource_id: ID of the resource
        
    Returns:
        The resource if found and belongs to user's org
        
    Raises:
        404: If resource not found or belongs to different org
    """
    resource = model.query.get(resource_id)
    if not resource:
        abort(404)
    if hasattr(resource, 'organization_id'):
        if resource.organization_id != current_user.organization_id:
            abort(404)
    return resource


# =============================================================================
# ORGANIZATION SETUP HELPERS
# =============================================================================

def create_default_groups_for_org(org_id: int):
    """
    Create default contact groups for a new organization.
    Called when an organization is approved.
    Idempotent - safe to call multiple times.
    
    Args:
        org_id: The organization ID to create groups for
        
    Returns:
        List of created ContactGroup objects
    """
    from models import db, ContactGroup
    
    # Check if any groups already exist for this org
    existing_count = ContactGroup.query.filter_by(organization_id=org_id).count()
    if existing_count > 0:
        # Already created, return existing groups
        return ContactGroup.query.filter_by(organization_id=org_id).all()
    
    # Default groups for real estate CRM
    default_groups = [
        # Buyer pipeline
        {'name': 'Buyer - New Potential Client', 'category': 'Status', 'sort_order': 1},
        {'name': 'Buyer - Actively Showing Homes', 'category': 'Status', 'sort_order': 2},
        {'name': 'Buyer - Under Contract', 'category': 'Status', 'sort_order': 3},
        {'name': 'Buyer - Previous Client', 'category': 'Status', 'sort_order': 4},
        # Seller pipeline
        {'name': 'Seller - New Potential Client', 'category': 'Status', 'sort_order': 5},
        {'name': 'Seller - Active Listing', 'category': 'Status', 'sort_order': 6},
        {'name': 'Seller - Under Contract', 'category': 'Status', 'sort_order': 7},
        {'name': 'Seller - Previous Client', 'category': 'Status', 'sort_order': 8},
        # Priority groups
        {'name': 'A', 'category': 'Priority', 'sort_order': 9},
        {'name': 'B', 'category': 'Priority', 'sort_order': 10},
        {'name': 'C', 'category': 'Priority', 'sort_order': 11},
        {'name': 'D', 'category': 'Priority', 'sort_order': 12},
        # Relationship groups
        {'name': 'Family', 'category': 'Relationship', 'sort_order': 13},
        {'name': 'Friend', 'category': 'Relationship', 'sort_order': 14},
        # Professional groups
        {'name': 'Real Estate Agent', 'category': 'Professional', 'sort_order': 15},
        {'name': 'Lender', 'category': 'Professional', 'sort_order': 16},
        {'name': 'Inspector', 'category': 'Professional', 'sort_order': 17},
        {'name': 'Insurance Broker', 'category': 'Professional', 'sort_order': 18},
    ]
    
    created_groups = []
    for group_data in default_groups:
        group = ContactGroup(
            organization_id=org_id,
            name=group_data['name'],
            category=group_data.get('category', 'Status'),
            sort_order=group_data.get('sort_order', 0)
        )
        db.session.add(group)
        created_groups.append(group)
    
    db.session.commit()
    return created_groups


def create_default_task_types_for_org(org_id: int):
    """
    Create default task types and subtypes for a new organization.
    Called when an organization is approved.
    Idempotent - safe to call multiple times.
    
    Args:
        org_id: The organization ID to create task types for
        
    Returns:
        List of created TaskType objects
    """
    from models import db, TaskType, TaskSubtype
    
    # Check if any task types already exist for this org
    existing_count = TaskType.query.filter_by(organization_id=org_id).count()
    if existing_count > 0:
        # Already created, return existing types
        return TaskType.query.filter_by(organization_id=org_id).all()
    
    # Default task types with their subtypes for real estate CRM
    default_task_types = [
        {
            'name': 'Call',
            'sort_order': 10,
            'subtypes': [
                {'name': 'Check-in', 'sort_order': 1},
                {'name': 'Schedule Showing', 'sort_order': 2},
                {'name': 'Discuss Offer', 'sort_order': 3},
                {'name': 'Follow-up', 'sort_order': 4}
            ]
        },
        {
            'name': 'Meeting',
            'sort_order': 20,
            'subtypes': [
                {'name': 'Initial Consultation', 'sort_order': 1},
                {'name': 'Property Showing', 'sort_order': 2},
                {'name': 'Contract Review', 'sort_order': 3},
                {'name': 'Home Inspection', 'sort_order': 4}
            ]
        },
        {
            'name': 'Email',
            'sort_order': 30,
            'subtypes': [
                {'name': 'Send Listings', 'sort_order': 1},
                {'name': 'Send Documents', 'sort_order': 2},
                {'name': 'Market Update', 'sort_order': 3},
                {'name': 'General Follow-up', 'sort_order': 4}
            ]
        },
        {
            'name': 'Document',
            'sort_order': 40,
            'subtypes': [
                {'name': 'Prepare Contract', 'sort_order': 1},
                {'name': 'Review Documents', 'sort_order': 2},
                {'name': 'Submit Offer', 'sort_order': 3},
                {'name': 'Process Paperwork', 'sort_order': 4}
            ]
        }
    ]
    
    created_types = []
    for type_data in default_task_types:
        # Create the task type
        task_type = TaskType(
            organization_id=org_id,
            name=type_data['name'],
            sort_order=type_data['sort_order']
        )
        db.session.add(task_type)
        db.session.flush()  # Get the task_type.id
        
        # Create subtypes for this type
        for subtype_data in type_data.get('subtypes', []):
            subtype = TaskSubtype(
                organization_id=org_id,
                task_type_id=task_type.id,
                name=subtype_data['name'],
                sort_order=subtype_data['sort_order']
            )
            db.session.add(subtype)
        
        created_types.append(task_type)
    
    db.session.commit()
    return created_types


def create_default_transaction_types_for_org(org_id: int):
    """
    Create default transaction types for a new organization.
    Called when an organization is approved.
    Idempotent - safe to call multiple times.
    
    Args:
        org_id: The organization ID to create transaction types for
        
    Returns:
        List of created TransactionType objects
    """
    from models import db, TransactionType
    
    # Default transaction types for real estate CRM
    default_transaction_types = [
        {'name': 'seller', 'display_name': 'Seller Representation', 'sort_order': 1},
        {'name': 'buyer', 'display_name': 'Buyer Representation', 'sort_order': 2},
        {'name': 'landlord', 'display_name': 'Landlord Representation', 'sort_order': 3},
        {'name': 'tenant', 'display_name': 'Tenant Representation', 'sort_order': 4},
        {'name': 'referral', 'display_name': 'Referral', 'sort_order': 5},
    ]
    
    # Check if any transaction types already exist for this org
    existing_count = TransactionType.query.filter_by(organization_id=org_id).count()
    if existing_count > 0:
        # Already created, return existing types
        return TransactionType.query.filter_by(organization_id=org_id).all()
    
    created_types = []
    for type_data in default_transaction_types:
        tx_type = TransactionType(
            organization_id=org_id,
            name=type_data['name'],
            display_name=type_data['display_name'],
            sort_order=type_data['sort_order'],
            is_active=True
        )
        db.session.add(tx_type)
        created_types.append(tx_type)
    
    db.session.commit()
    return created_types
