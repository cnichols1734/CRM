#!/usr/bin/env python3
"""
CRM Integration Test Suite

A standalone test runner using Playwright for browser-based UI/integration testing.
Run with: python tests/run_tests.py

Options:
    --headed    Run with browser visible (default: headless)
    --base-url  Override base URL (default: http://127.0.0.1:5007)
    --slow      Add delays between actions for debugging
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, expect

# Load environment variables from project root .env
load_dotenv(project_root / '.env')


class Colors:
    """ANSI color codes for console output."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'


class TestResult:
    """Track test results."""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
    
    def add_pass(self):
        self.passed += 1
    
    def add_fail(self, test_name: str, error: str):
        self.failed += 1
        self.errors.append((test_name, error))
    
    def summary(self) -> str:
        status = "PASSED" if self.failed == 0 else "FAILED"
        color = Colors.GREEN if self.failed == 0 else Colors.RED
        return f"{color}{Colors.BOLD}[RESULT] {self.passed} passed, {self.failed} failed - {status}{Colors.END}"


class CRMTestSuite:
    """
    Integration test suite for CRM application.
    
    Covers:
    - Authentication (login, logout)
    - Contact management (create, edit, delete)
    - Task management (create, edit, complete, delete)
    - User profile operations
    """
    
    def __init__(self, base_url: str, headless: bool = True, slow_mo: int = 0):
        self.base_url = base_url.rstrip('/')
        self.headless = headless
        self.slow_mo = slow_mo
        self.results = TestResult()
        
        # Cleanup registry - tracks created items for cleanup
        self.cleanup_registry = {
            'tasks': [],      # Task IDs to delete
            'contacts': [],   # Contact IDs to delete
        }
        
        # Test credentials from environment
        self.test_username = os.getenv('TEST_USERNAME')
        self.test_password = os.getenv('TEST_PASSWORD')
        
        if not self.test_username or not self.test_password:
            raise ValueError("TEST_USERNAME and TEST_PASSWORD must be set in .env file")
        
        # Playwright objects
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        
        # Test data
        self.test_contact_id = None
        self.test_task_id = None
    
    def log(self, message: str, level: str = "info"):
        """Log a message with color coding."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        if level == "test":
            print(f"\n{Colors.BLUE}{Colors.BOLD}[TEST]{Colors.END} {message}")
        elif level == "step":
            print(f"  {Colors.CYAN}→{Colors.END} {message}", end="")
        elif level == "ok":
            print(f" {Colors.GREEN}OK{Colors.END}")
        elif level == "pass":
            print(f"  {Colors.GREEN}{Colors.BOLD}✓ PASS{Colors.END}")
        elif level == "fail":
            print(f" {Colors.RED}FAIL{Colors.END}")
            print(f"  {Colors.RED}{Colors.BOLD}✗ FAIL{Colors.END}")
        elif level == "error":
            print(f"\n  {Colors.RED}Error: {message}{Colors.END}")
        elif level == "cleanup":
            print(f"\n{Colors.YELLOW}[CLEANUP]{Colors.END} {message}")
        elif level == "header":
            print(f"\n{Colors.BOLD}{'=' * 50}{Colors.END}")
            print(f"{Colors.BOLD}{message}{Colors.END}")
            print(f"{Colors.BOLD}{'=' * 50}{Colors.END}")
        else:
            print(f"[{timestamp}] {message}")
    
    def setup(self):
        """Initialize Playwright browser."""
        self.log("CRM Integration Test Suite", "header")
        self.log(f"Base URL: {self.base_url}")
        self.log(f"Mode: {'Headed' if not self.headless else 'Headless'}")
        self.log(f"Test User: {self.test_username}")
        
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo
        )
        self.context = self.browser.new_context(
            viewport={'width': 1280, 'height': 800}
        )
        self.page = self.context.new_page()
        
        # Set reasonable timeouts
        self.page.set_default_timeout(10000)
        self.page.set_default_navigation_timeout(15000)
    
    def teardown(self):
        """Clean up Playwright resources."""
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
    
    # ==================== AUTHENTICATION TESTS ====================
    
    def test_login(self):
        """Test login with valid credentials."""
        test_name = "Login with valid credentials"
        self.log(test_name, "test")
        
        try:
            # Navigate to login page
            self.log("Navigating to login page...", "step")
            self.page.goto(f"{self.base_url}/login")
            self.log("", "ok")
            
            # Fill credentials
            self.log("Filling credentials...", "step")
            self.page.fill('input[name="username"]', self.test_username)
            self.page.fill('input[name="password"]', self.test_password)
            self.log("", "ok")
            
            # Submit form
            self.log("Submitting form...", "step")
            self.page.click('input[type="submit"]')
            self.log("", "ok")
            
            # Wait for navigation and verify dashboard
            self.log("Verifying dashboard redirect...", "step")
            self.page.wait_for_url("**/dashboard**", timeout=10000)
            expect(self.page).to_have_url(f"{self.base_url}/dashboard")
            self.log("", "ok")
            
            self.log("", "pass")
            self.results.add_pass()
            
        except Exception as e:
            self.log(str(e), "error")
            self.log("", "fail")
            self.results.add_fail(test_name, str(e))
    
    def test_dashboard_access(self):
        """Test that dashboard is accessible after login."""
        test_name = "Dashboard access after login"
        self.log(test_name, "test")
        
        try:
            # Verify we're on dashboard
            self.log("Checking dashboard content...", "step")
            expect(self.page.locator("body")).to_contain_text("Dashboard")
            self.log("", "ok")
            
            # Check for key dashboard elements
            self.log("Verifying dashboard elements...", "step")
            # Dashboard should have contact stats or task info
            body_text = self.page.locator("body").inner_text()
            assert "Contact" in body_text or "Task" in body_text or "Commission" in body_text
            self.log("", "ok")
            
            self.log("", "pass")
            self.results.add_pass()
            
        except Exception as e:
            self.log(str(e), "error")
            self.log("", "fail")
            self.results.add_fail(test_name, str(e))
    
    # ==================== CONTACT TESTS ====================
    
    def test_create_contact(self):
        """Test creating a new contact."""
        test_name = "Create contact"
        self.log(test_name, "test")
        
        try:
            # Navigate to create contact page
            self.log("Navigating to create contact page...", "step")
            self.page.goto(f"{self.base_url}/contacts/create")
            self.page.wait_for_load_state("networkidle")
            self.log("", "ok")
            
            # Generate unique test data
            timestamp = int(time.time())
            test_first_name = f"TestContact{timestamp}"
            test_last_name = "AutoTest"
            test_email = f"test{timestamp}@example.com"
            test_phone = "5551234567"
            
            # Fill contact form
            self.log("Filling contact form...", "step")
            self.page.fill('input[name="first_name"]', test_first_name)
            self.page.fill('input[name="last_name"]', test_last_name)
            self.page.fill('input[name="email"]', test_email)
            self.page.fill('input[name="phone"]', test_phone)
            self.page.fill('input[name="street_address"]', "123 Test Street")
            self.page.fill('input[name="city"]', "Austin")
            self.page.fill('input[name="state"]', "TX")
            self.page.fill('input[name="zip_code"]', "78701")
            
            # Select at least one group (required by form validation)
            group_checkboxes = self.page.locator('input[name="group_ids"]')
            group_count = group_checkboxes.count()
            if group_count > 0:
                group_checkboxes.first.check()
                self.log("", "ok")
            else:
                self.log("", "fail")
                raise Exception("No contact groups found in database - cannot create contact without a group")
            
            # Submit form (uses button, not input)
            self.log("Submitting form...", "step")
            self.page.click('button[type="submit"]')
            self.page.wait_for_load_state("networkidle")
            self.page.wait_for_timeout(2000)
            
            # Check if we were redirected (success) or stayed on form (validation failed)
            current_url = self.page.url
            if '/contacts/create' in current_url:
                # Still on form - validation failed
                body_text = self.page.locator("body").inner_text()
                self.log("", "fail")
                raise Exception(f"Form submission failed - still on create page. Check for validation errors.")
            self.log("", "ok")
            
            # Verify success - should redirect to index with flash message
            self.log("Verifying contact created...", "step")
            
            # Check if we got the success flash message or are on index page
            body_text = self.page.locator("body").inner_text()
            
            # If we're on the index page after redirect, the contact might already be visible
            if test_first_name in body_text:
                self.log("", "ok")
            else:
                # Navigate to contacts list and search for our contact
                self.page.goto(f"{self.base_url}/?q={test_first_name}")
                self.page.wait_for_load_state("networkidle")
                self.page.wait_for_timeout(1000)
                
                body_text = self.page.locator("body").inner_text()
                
                # If still not found, try without search filter
                if test_first_name not in body_text:
                    self.page.goto(f"{self.base_url}/")
                    self.page.wait_for_load_state("networkidle")
                    self.page.wait_for_timeout(1000)
                    body_text = self.page.locator("body").inner_text()
                
                if test_first_name not in body_text:
                    # Debug: print current URL and page content snippet
                    current_url = self.page.url
                    self.log(f"Debug - URL: {current_url}", "error")
                    self.log(f"Debug - Looking for: {test_first_name}", "error")
                    # Check if there's an error message
                    if "error" in body_text.lower() or "Error" in body_text:
                        self.log("Debug - Page contains error message", "error")
                assert test_first_name in body_text, f"Contact {test_first_name} not found in contacts list"
                self.log("", "ok")
            
            # Extract contact ID from the page for cleanup
            # Find the link to view this contact
            contact_link = self.page.locator(f'a:has-text("{test_first_name}")').first
            if contact_link.count() > 0:
                href = contact_link.get_attribute('href')
                if href and '/contact/' in href:
                    # Extract contact ID, handling query params
                    contact_path = href.split('/contact/')[-1]
                    contact_id_str = contact_path.split('/')[0].split('?')[0]
                    self.test_contact_id = int(contact_id_str)
                    self.cleanup_registry['contacts'].append(self.test_contact_id)
                    self.log(f"Contact ID: {self.test_contact_id}")
            
            self.log("", "pass")
            self.results.add_pass()
            
        except Exception as e:
            self.log(str(e), "error")
            self.log("", "fail")
            self.results.add_fail(test_name, str(e))
    
    def test_view_contact(self):
        """Test viewing a contact's details."""
        test_name = "View contact details"
        self.log(test_name, "test")
        
        if not self.test_contact_id:
            self.log("Skipping - no contact created", "error")
            self.results.add_fail(test_name, "No contact ID available")
            return
        
        try:
            # Navigate to contact view page
            self.log("Navigating to contact view page...", "step")
            self.page.goto(f"{self.base_url}/contact/{self.test_contact_id}")
            self.page.wait_for_load_state("networkidle")
            self.log("", "ok")
            
            # Verify contact details are displayed
            self.log("Verifying contact details...", "step")
            body_text = self.page.locator("body").inner_text()
            assert "TestContact" in body_text or "AutoTest" in body_text
            self.log("", "ok")
            
            self.log("", "pass")
            self.results.add_pass()
            
        except Exception as e:
            self.log(str(e), "error")
            self.log("", "fail")
            self.results.add_fail(test_name, str(e))
    
    def test_edit_contact(self):
        """Test editing a contact."""
        test_name = "Edit contact"
        self.log(test_name, "test")
        
        if not self.test_contact_id:
            self.log("Skipping - no contact created", "error")
            self.results.add_fail(test_name, "No contact ID available")
            return
        
        try:
            # Navigate to contact view page
            self.log("Navigating to contact page...", "step")
            self.page.goto(f"{self.base_url}/contact/{self.test_contact_id}")
            self.page.wait_for_load_state("networkidle")
            self.log("", "ok")
            
            # Find and click edit button or edit the form inline
            self.log("Editing contact notes...", "step")
            
            # The edit is done via POST to /contacts/<id>/edit
            # We'll make a direct API call since the UI might use modals/AJAX
            updated_notes = f"Updated by test at {datetime.now().isoformat()}"
            
            # Use page.evaluate to make a fetch request
            response = self.page.evaluate(f'''
                async () => {{
                    const formData = new FormData();
                    formData.append('first_name', 'TestContactEdited');
                    formData.append('last_name', 'AutoTest');
                    formData.append('email', 'edited@example.com');
                    formData.append('notes', '{updated_notes}');
                    
                    const response = await fetch('/contacts/{self.test_contact_id}/edit', {{
                        method: 'POST',
                        body: formData
                    }});
                    return {{ status: response.status, ok: response.ok }};
                }}
            ''')
            
            assert response['ok'], f"Edit failed with status {response['status']}"
            self.log("", "ok")
            
            # Verify edit by reloading
            self.log("Verifying edit...", "step")
            self.page.reload()
            self.page.wait_for_load_state("networkidle")
            body_text = self.page.locator("body").inner_text()
            assert "TestContactEdited" in body_text or updated_notes in body_text
            self.log("", "ok")
            
            self.log("", "pass")
            self.results.add_pass()
            
        except Exception as e:
            self.log(str(e), "error")
            self.log("", "fail")
            self.results.add_fail(test_name, str(e))
    
    # ==================== TASK TESTS ====================
    
    def test_create_task(self):
        """Test creating a new task."""
        test_name = "Create task"
        self.log(test_name, "test")
        
        if not self.test_contact_id:
            self.log("Skipping - no contact created", "error")
            self.results.add_fail(test_name, "No contact ID available")
            return
        
        try:
            # Navigate to create task page
            self.log("Navigating to create task page...", "step")
            self.page.goto(f"{self.base_url}/tasks/new")
            self.page.wait_for_load_state("networkidle")
            self.log("", "ok")
            
            # Fill task form
            self.log("Filling task form...", "step")
            
            # Select contact - the contact dropdown
            contact_select = self.page.locator('select[name="contact_id"]')
            if contact_select.count() > 0:
                # Select by value (contact ID)
                contact_select.select_option(value=str(self.test_contact_id))
            
            # Select task type - first available option
            type_select = self.page.locator('select[name="type_id"]')
            if type_select.count() > 0:
                # Get first option value
                first_option = self.page.locator('select[name="type_id"] option').nth(1)
                if first_option.count() > 0:
                    type_value = first_option.get_attribute('value')
                    type_select.select_option(value=type_value)
                    # Wait for subtypes to load
                    self.page.wait_for_timeout(500)
            
            # Select subtype - first available option
            subtype_select = self.page.locator('select[name="subtype_id"]')
            if subtype_select.count() > 0:
                self.page.wait_for_timeout(500)
                first_subtype = self.page.locator('select[name="subtype_id"] option').nth(1)
                if first_subtype.count() > 0:
                    subtype_value = first_subtype.get_attribute('value')
                    if subtype_value:
                        subtype_select.select_option(value=subtype_value)
            
            # Fill subject
            timestamp = int(time.time())
            test_subject = f"Test Task {timestamp}"
            self.page.fill('input[name="subject"]', test_subject)
            
            # Fill description
            self.page.fill('textarea[name="description"]', "Test task created by automated test")
            
            # Set due date to tomorrow
            tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
            self.page.fill('input[name="due_date"]', tomorrow)
            
            # Set priority
            priority_select = self.page.locator('select[name="priority"]')
            if priority_select.count() > 0:
                priority_select.select_option(value="medium")
            
            self.log("", "ok")
            
            # Submit form (uses button, not input)
            self.log("Submitting form...", "step")
            self.page.click('button[type="submit"]')
            self.page.wait_for_load_state("networkidle")
            self.log("", "ok")
            
            # Verify task was created by checking tasks page
            self.log("Verifying task created...", "step")
            self.page.goto(f"{self.base_url}/tasks")
            self.page.wait_for_load_state("networkidle")
            
            body_text = self.page.locator("body").inner_text()
            assert test_subject in body_text, f"Task '{test_subject}' not found in tasks list"
            self.log("", "ok")
            
            # Try to extract task ID from the page
            # Look for task links containing our subject
            task_links = self.page.locator(f'a[href*="/tasks/"]:has-text("{test_subject}")')
            if task_links.count() == 0:
                # Try finding any row containing our subject
                task_row = self.page.locator(f'tr:has-text("{test_subject}") a[href*="/tasks/"]').first
                if task_row.count() > 0:
                    href = task_row.get_attribute('href')
                    if href:
                        # Extract task ID, handling query params (e.g., /tasks/5?ref=tasks)
                        task_path = href.split('/tasks/')[-1]
                        task_id_str = task_path.split('/')[0].split('?')[0]
                        self.test_task_id = int(task_id_str)
            else:
                href = task_links.first.get_attribute('href')
                if href:
                    # Extract task ID, handling query params (e.g., /tasks/5?ref=tasks)
                    task_path = href.split('/tasks/')[-1]
                    task_id_str = task_path.split('/')[0].split('?')[0]
                    self.test_task_id = int(task_id_str)
            
            if self.test_task_id:
                self.cleanup_registry['tasks'].append(self.test_task_id)
                self.log(f"Task ID: {self.test_task_id}")
            
            self.log("", "pass")
            self.results.add_pass()
            
        except Exception as e:
            self.log(str(e), "error")
            self.log("", "fail")
            self.results.add_fail(test_name, str(e))
    
    def test_view_task(self):
        """Test viewing a task's details."""
        test_name = "View task details"
        self.log(test_name, "test")
        
        if not self.test_task_id:
            self.log("Skipping - no task created", "error")
            self.results.add_fail(test_name, "No task ID available")
            return
        
        try:
            # Navigate to task view page
            self.log("Navigating to task view page...", "step")
            self.page.goto(f"{self.base_url}/tasks/{self.test_task_id}")
            self.page.wait_for_load_state("networkidle")
            self.log("", "ok")
            
            # Verify task details are displayed
            self.log("Verifying task details...", "step")
            body_text = self.page.locator("body").inner_text()
            assert "Test Task" in body_text or "pending" in body_text.lower() or "medium" in body_text.lower()
            self.log("", "ok")
            
            self.log("", "pass")
            self.results.add_pass()
            
        except Exception as e:
            self.log(str(e), "error")
            self.log("", "fail")
            self.results.add_fail(test_name, str(e))
    
    def test_edit_task_priority(self):
        """Test changing task priority."""
        test_name = "Edit task priority"
        self.log(test_name, "test")
        
        if not self.test_task_id:
            self.log("Skipping - no task created", "error")
            self.results.add_fail(test_name, "No task ID available")
            return
        
        try:
            # Use quick-update API to change priority
            self.log("Changing task priority to high...", "step")
            
            response = self.page.evaluate(f'''
                async () => {{
                    const formData = new FormData();
                    formData.append('priority', 'high');
                    
                    const response = await fetch('/tasks/{self.test_task_id}/quick-update', {{
                        method: 'POST',
                        body: formData
                    }});
                    return {{ status: response.status, ok: response.ok }};
                }}
            ''')
            
            assert response['ok'], f"Priority update failed with status {response['status']}"
            self.log("", "ok")
            
            self.log("", "pass")
            self.results.add_pass()
            
        except Exception as e:
            self.log(str(e), "error")
            self.log("", "fail")
            self.results.add_fail(test_name, str(e))
    
    def test_complete_task(self):
        """Test marking a task as complete."""
        test_name = "Complete task"
        self.log(test_name, "test")
        
        if not self.test_task_id:
            self.log("Skipping - no task created", "error")
            self.results.add_fail(test_name, "No task ID available")
            return
        
        try:
            # Use quick-update API to mark complete
            self.log("Marking task as completed...", "step")
            
            response = self.page.evaluate(f'''
                async () => {{
                    const formData = new FormData();
                    formData.append('status', 'completed');
                    
                    const response = await fetch('/tasks/{self.test_task_id}/quick-update', {{
                        method: 'POST',
                        body: formData
                    }});
                    return {{ status: response.status, ok: response.ok }};
                }}
            ''')
            
            assert response['ok'], f"Complete task failed with status {response['status']}"
            self.log("", "ok")
            
            # Verify by checking tasks with status=completed
            self.log("Verifying task is completed...", "step")
            self.page.goto(f"{self.base_url}/tasks?status=completed")
            self.page.wait_for_load_state("networkidle")
            body_text = self.page.locator("body").inner_text()
            assert "Test Task" in body_text
            self.log("", "ok")
            
            self.log("", "pass")
            self.results.add_pass()
            
        except Exception as e:
            self.log(str(e), "error")
            self.log("", "fail")
            self.results.add_fail(test_name, str(e))
    
    # ==================== USER PROFILE TESTS ====================
    
    def test_view_profile(self):
        """Test viewing user profile."""
        test_name = "View user profile"
        self.log(test_name, "test")
        
        try:
            # Navigate to profile page
            self.log("Navigating to profile page...", "step")
            self.page.goto(f"{self.base_url}/profile")
            self.page.wait_for_load_state("networkidle")
            self.log("", "ok")
            
            # Verify profile content
            self.log("Verifying profile content...", "step")
            body_text = self.page.locator("body").inner_text()
            # Should contain user info fields
            assert "Email" in body_text or "Profile" in body_text or "Name" in body_text
            self.log("", "ok")
            
            self.log("", "pass")
            self.results.add_pass()
            
        except Exception as e:
            self.log(str(e), "error")
            self.log("", "fail")
            self.results.add_fail(test_name, str(e))
    
    # ==================== NAVIGATION TESTS ====================
    
    def test_navigation_contacts(self):
        """Test navigating to contacts list."""
        test_name = "Navigate to contacts list"
        self.log(test_name, "test")
        
        try:
            self.log("Navigating to contacts list...", "step")
            self.page.goto(f"{self.base_url}/")
            self.page.wait_for_load_state("networkidle")
            self.log("", "ok")
            
            self.log("Verifying contacts page...", "step")
            # The index page shows contacts
            body_text = self.page.locator("body").inner_text()
            assert "Contact" in body_text or "Name" in body_text
            self.log("", "ok")
            
            self.log("", "pass")
            self.results.add_pass()
            
        except Exception as e:
            self.log(str(e), "error")
            self.log("", "fail")
            self.results.add_fail(test_name, str(e))
    
    def test_navigation_tasks(self):
        """Test navigating to tasks list."""
        test_name = "Navigate to tasks list"
        self.log(test_name, "test")
        
        try:
            self.log("Navigating to tasks list...", "step")
            self.page.goto(f"{self.base_url}/tasks")
            self.page.wait_for_load_state("networkidle")
            self.log("", "ok")
            
            self.log("Verifying tasks page...", "step")
            body_text = self.page.locator("body").inner_text()
            assert "Task" in body_text or "Due" in body_text
            self.log("", "ok")
            
            self.log("", "pass")
            self.results.add_pass()
            
        except Exception as e:
            self.log(str(e), "error")
            self.log("", "fail")
            self.results.add_fail(test_name, str(e))
    
    # ==================== CLEANUP ====================
    
    def cleanup(self):
        """Delete all test data created during tests."""
        self.log("Removing test data...", "cleanup")
        
        # Delete tasks first (they reference contacts)
        for task_id in self.cleanup_registry['tasks']:
            try:
                self.log(f"  → Deleting task ID: {task_id}...", "step")
                response = self.page.evaluate(f'''
                    async () => {{
                        const response = await fetch('/tasks/{task_id}/delete', {{
                            method: 'POST'
                        }});
                        return {{ status: response.status, ok: response.ok }};
                    }}
                ''')
                if response['ok']:
                    print(f" {Colors.GREEN}OK{Colors.END}")
                else:
                    print(f" {Colors.YELLOW}SKIPPED (status {response['status']}){Colors.END}")
            except Exception as e:
                print(f" {Colors.RED}ERROR: {e}{Colors.END}")
        
        # Delete contacts
        for contact_id in self.cleanup_registry['contacts']:
            try:
                self.log(f"  → Deleting contact ID: {contact_id}...", "step")
                response = self.page.evaluate(f'''
                    async () => {{
                        const response = await fetch('/contacts/{contact_id}/delete', {{
                            method: 'POST'
                        }});
                        return {{ status: response.status, ok: response.ok }};
                    }}
                ''')
                if response['ok']:
                    print(f" {Colors.GREEN}OK{Colors.END}")
                else:
                    print(f" {Colors.YELLOW}SKIPPED (status {response['status']}){Colors.END}")
            except Exception as e:
                print(f" {Colors.RED}ERROR: {e}{Colors.END}")
        
        self.log("Cleanup complete.")
    
    # ==================== MAIN RUNNER ====================
    
    def run_all(self):
        """Run all tests with setup, execution, and cleanup."""
        try:
            self.setup()
            
            # Authentication tests
            self.test_login()
            self.test_dashboard_access()
            
            # Navigation tests
            self.test_navigation_contacts()
            self.test_navigation_tasks()
            
            # Contact tests
            self.test_create_contact()
            self.test_view_contact()
            self.test_edit_contact()
            
            # Task tests
            self.test_create_task()
            self.test_view_task()
            self.test_edit_task_priority()
            self.test_complete_task()
            
            # Profile tests
            self.test_view_profile()
            
        finally:
            # Always run cleanup, even if tests fail
            self.cleanup()
            
            # Print summary
            print(f"\n{'=' * 50}")
            print(self.results.summary())
            
            if self.results.errors:
                print(f"\n{Colors.RED}Failed tests:{Colors.END}")
                for test_name, error in self.results.errors:
                    print(f"  - {test_name}: {error}")
            
            self.teardown()
        
        # Return exit code
        return 0 if self.results.failed == 0 else 1


def main():
    """Main entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        description='CRM Integration Test Suite',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python tests/run_tests.py                    # Run headless
  python tests/run_tests.py --headed           # Run with visible browser
  python tests/run_tests.py --headed --slow    # Run slowly for debugging
        '''
    )
    
    parser.add_argument(
        '--headed',
        action='store_true',
        help='Run with browser visible (default: headless)'
    )
    
    parser.add_argument(
        '--base-url',
        default=os.getenv('BASE_URL', 'http://127.0.0.1:5006'),
        help='Base URL for the CRM application (default: http://127.0.0.1:5006)'
    )
    
    parser.add_argument(
        '--slow',
        action='store_true',
        help='Add 500ms delay between actions for debugging'
    )
    
    args = parser.parse_args()
    
    try:
        suite = CRMTestSuite(
            base_url=args.base_url,
            headless=not args.headed,
            slow_mo=500 if args.slow else 0
        )
        exit_code = suite.run_all()
        sys.exit(exit_code)
        
    except ValueError as e:
        print(f"{Colors.RED}Configuration error: {e}{Colors.END}")
        sys.exit(1)
    except Exception as e:
        print(f"{Colors.RED}Fatal error: {e}{Colors.END}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

