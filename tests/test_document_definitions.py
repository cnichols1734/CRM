"""
Document Definition Test Harness

Validates all document definitions on every test run.
This ensures configuration errors are caught before deployment.

Run with: python -m pytest tests/test_document_definitions.py -v
"""

import pytest
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.documents import (
    DocumentLoader,
    FieldResolver,
    RoleBuilder,
    DocumentType,
    ConfigurationError
)


class MockUser:
    """Mock User object for testing field resolution."""
    id = 1
    email = "agent@test.com"
    first_name = "Test"
    last_name = "Agent"
    phone = "7135551234"
    license_number = "TX123456"
    licensed_supervisor = "Jane Supervisor"
    licensed_supervisor_license = "TX789012"
    licensed_supervisor_email = "supervisor@test.com"
    licensed_supervisor_phone = "7135555678"
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class MockParticipant:
    """Mock Participant object for testing."""
    def __init__(self, role, name, email, is_primary=False):
        self.role = role
        self.display_name = name
        self.display_email = email
        self.is_primary = is_primary
        self.first_name = name.split()[0] if name else ""
        self.last_name = name.split()[-1] if name and len(name.split()) > 1 else ""
    
    @property
    def name(self):
        return self.display_name
    
    @property
    def email(self):
        return self.display_email


class MockTransaction:
    """Mock Transaction object for testing."""
    id = 1
    property_address = "123 Test St, Houston, TX 77001"
    
    @property
    def primary_seller(self):
        return MockParticipant("seller", "John Seller", "seller@test.com", is_primary=True)
    
    @property
    def sellers(self):
        return [
            self.primary_seller,
            MockParticipant("seller", "Jane Seller", "seller2@test.com")
        ]
    
    @property
    def participants(self):
        return self.sellers


def create_mock_context():
    """Create a mock context with all data sources."""
    return {
        'user': MockUser(),
        'transaction': MockTransaction(),
        'form': {
            'list_price': '500000',
            'commission_rate': '6',
            'property_address': '123 Test St',
        }
    }


class TestDocumentLoader:
    """Test document loading and validation."""
    
    def test_load_all_succeeds(self):
        """All YAML files should load without errors."""
        # This will raise ConfigurationError if any document is invalid
        DocumentLoader.load_all()
        assert DocumentLoader.is_loaded()
    
    def test_at_least_one_document_loaded(self):
        """At least one document definition should be loaded."""
        DocumentLoader.load_all()
        assert len(DocumentLoader.all()) >= 1
    
    def test_all_slugs_are_unique(self):
        """All document slugs should be unique."""
        DocumentLoader.load_all()
        slugs = DocumentLoader.all_slugs()
        assert len(slugs) == len(set(slugs))


class TestDocumentDefinitions:
    """Test each document definition individually."""
    
    @pytest.fixture(autouse=True)
    def load_documents(self):
        """Load all documents before each test."""
        DocumentLoader.clear()
        DocumentLoader.load_all()
    
    def test_iabs_definition_exists(self):
        """IABS document should be loaded."""
        definition = DocumentLoader.get('iabs')
        assert definition is not None
        assert definition.slug == 'iabs'
        assert definition.type == DocumentType.PDF_PREVIEW
    
    def test_iabs_has_required_roles(self):
        """IABS should have agent, broker, seller roles."""
        definition = DocumentLoader.get('iabs')
        role_keys = definition.get_role_keys()
        
        assert 'agent' in role_keys
        assert 'broker' in role_keys
        assert 'seller' in role_keys
    
    def test_iabs_has_agent_fields(self):
        """IABS should have agent/supervisor fields."""
        definition = DocumentLoader.get('iabs')
        agent_fields = definition.get_fields_for_role('agent')
        
        field_keys = [f.field_key for f in agent_fields]
        assert 'agent_name' in field_keys
        assert 'agent_license' in field_keys
        assert 'supervisor_name' in field_keys


class TestFieldResolution:
    """Test field resolution with mock data."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Load documents and create context."""
        DocumentLoader.clear()
        DocumentLoader.load_all()
        self.context = create_mock_context()
    
    def test_resolve_user_email(self):
        """Should resolve user.email from context."""
        value = FieldResolver.resolve_single('user.email', self.context)
        assert value == "agent@test.com"
    
    def test_resolve_user_full_name(self):
        """Should resolve user.full_name from context."""
        value = FieldResolver.resolve_single('user.full_name', self.context)
        assert value == "Test Agent"
    
    def test_resolve_transaction_primary_seller(self):
        """Should resolve transaction.primary_seller.email."""
        value = FieldResolver.resolve_single('transaction.primary_seller.email', self.context)
        assert value == "seller@test.com"
    
    def test_resolve_sellers_array(self):
        """Should resolve transaction.sellers[1].email with bracket notation."""
        value = FieldResolver.resolve_single('transaction.sellers[1].email', self.context)
        assert value == "seller2@test.com"
    
    def test_resolve_form_data(self):
        """Should resolve form.list_price from form data."""
        value = FieldResolver.resolve_single('form.list_price', self.context)
        assert value == "500000"
    
    def test_resolve_null_source_returns_none(self):
        """Null source path should return None."""
        value = FieldResolver.resolve_single(None, self.context)
        assert value is None
    
    def test_resolve_missing_path_returns_none(self):
        """Missing path should return None, not raise."""
        value = FieldResolver.resolve_single('user.nonexistent_field', self.context)
        assert value is None


class TestRoleBuilding:
    """Test role building with resolved fields."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Load documents and create context."""
        DocumentLoader.clear()
        DocumentLoader.load_all()
        self.context = create_mock_context()
    
    def test_build_iabs_submitters(self):
        """Should build submitters for IABS document."""
        definition = DocumentLoader.get('iabs')
        fields = FieldResolver.resolve(definition, self.context)
        submitters = RoleBuilder.build(definition, fields, self.context)
        
        # Should have at least agent, broker, seller (seller_2 is optional)
        assert len(submitters) >= 3
        
        roles = [s.role for s in submitters]
        assert 'Agent' in roles
        assert 'Broker' in roles
        assert 'Seller' in roles
    
    def test_agent_submitter_has_fields(self):
        """Agent submitter should have pre-filled fields."""
        definition = DocumentLoader.get('iabs')
        fields = FieldResolver.resolve(definition, self.context)
        submitters = RoleBuilder.build(definition, fields, self.context)
        
        agent_submitter = next(s for s in submitters if s.role == 'Agent')
        
        # Agent should have fields with values
        assert len(agent_submitter.fields) > 0
        
        # Check a specific field
        field_names = [f['name'] for f in agent_submitter.fields]
        assert 'Sales Agent Name' in field_names
    
    def test_optional_role_skipped_when_no_data(self):
        """Optional roles should be skipped when data is missing."""
        definition = DocumentLoader.get('iabs')
        
        # Create context without second seller
        context = {
            'user': MockUser(),
            'transaction': type('MockTx', (), {
                'primary_seller': MockParticipant("seller", "John", "john@test.com", True),
                'sellers': [MockParticipant("seller", "John", "john@test.com", True)]  # Only one seller
            })(),
            'form': {}
        }
        
        fields = FieldResolver.resolve(definition, context)
        submitters = RoleBuilder.build(definition, fields, context)
        
        # Seller 2 should not be in submitters
        roles = [s.role for s in submitters]
        assert 'Seller 2' not in roles


class TestTransforms:
    """Test field value transforms."""
    
    def test_currency_transform(self):
        """Currency transform should format numbers."""
        from services.documents.transforms import transform_currency
        
        assert transform_currency(500000) == "$500,000.00"
        assert transform_currency("500000") == "$500,000.00"
        assert transform_currency(1234.5) == "$1,234.50"
    
    def test_phone_transform(self):
        """Phone transform should format 10-digit numbers."""
        from services.documents.transforms import transform_phone
        
        assert transform_phone("7135551234") == "(713) 555-1234"
        assert transform_phone(7135551234) == "(713) 555-1234"
        assert transform_phone("(713) 555-1234") == "(713) 555-1234"
    
    def test_percent_transform(self):
        """Percent transform should add % symbol."""
        from services.documents.transforms import transform_percent
        
        assert transform_percent(6) == "6%"
        assert transform_percent("6.5") == "6.5%"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

