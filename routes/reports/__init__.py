# routes/reports/__init__.py
"""
Reports Module - Premium reporting and analytics for real estate professionals.

Provides pre-built reports and a visual report builder for:
- Transaction pipeline analysis
- Contact engagement health
- Agent performance metrics
- Task completion tracking
- Document signing status
"""

from flask import Blueprint

# Create the blueprint
reports_bp = Blueprint('reports', __name__, url_prefix='/reports')

# Import all route modules AFTER blueprint creation
from . import views
from . import api
