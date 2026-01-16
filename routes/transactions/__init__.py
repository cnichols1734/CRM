# routes/transactions/__init__.py
"""
Transaction Management Routes Package
All routes protected by admin role + TRANSACTIONS_ENABLED feature flag

This package splits the transaction routes into logical modules:
- crud.py: Transaction CRUD operations (list, create, view, edit, delete)
- participants.py: Participant management (add/remove)
- api.py: JSON API endpoints (search, signers, status, rentcast)
- intake.py: Intake questionnaire
- documents.py: Document forms and filling
- signing.py: DocuSeal signing operations
- download.py: Document download and printing
- docuseal_admin.py: Admin and webhook endpoints
- history.py: Audit history
"""

from flask import Blueprint

# Create the blueprint - all sub-modules will register routes on this
transactions_bp = Blueprint('transactions', __name__, url_prefix='/transactions')

# Import all route modules AFTER blueprint creation
# Each module imports transactions_bp and registers routes on it
from . import crud
from . import participants
from . import api
from . import intake
from . import documents
from . import signing
from . import download
from . import docuseal_admin
from . import history
