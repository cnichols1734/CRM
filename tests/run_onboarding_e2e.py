#!/usr/bin/env python3
"""
New-User Activation E2E Test (Playwright)
=========================================

This walks the exact path a brand-new signup takes, entirely through the UI:

    1. Sign up a fresh account at /register
    2. Land on the dashboard (auto-logged-in)
    3. Create the first contact
    4. View that contact
    5. Create a task tied to that contact
    6. Log out and log back in with the new credentials
    7. Confirm the contact persisted

Why this exists: our activation funnel starts at signup, but the legacy browser
tests log in as a pre-seeded user and never exercise registration. This test
covers the real first-run journey so we know it ALWAYS works.

Reliability features:
  * Self-contained. Boots its own Flask server against a throwaway SQLite
    database. No externally running app and no pre-seeded data required.
  * Hermetic. SendGrid / OpenAI keys are blanked so signup never makes a
    network call or hangs.
  * Diagnosable. On any failure it writes a screenshot + page HTML to
    tests/artifacts/ and prints the tail of the server log.
  * Safe. Refuses to run against any non-local database or base URL.

Usage:
    .venv/bin/python tests/run_onboarding_e2e.py                 # headless, self-managed server
    .venv/bin/python tests/run_onboarding_e2e.py --headed --slow # watch it run
    .venv/bin/python tests/run_onboarding_e2e.py --base-url http://127.0.0.1:5011
                                                                 # use an already-running local server
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeoutError
from tests.browser_test_support import ensure_local_base_url, is_local_database_url

ARTIFACTS_DIR = PROJECT_ROOT / "tests" / "artifacts"


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"


# ---------------------------------------------------------------------------
# Managed app server (subprocess + throwaway SQLite database)
# ---------------------------------------------------------------------------

class ManagedAppServer:
    """Boots the Flask app as a subprocess against a disposable SQLite DB."""

    def __init__(self, keep_db: bool = False):
        self.keep_db = keep_db
        self.process: subprocess.Popen | None = None
        self.port = self._free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._db_dir = tempfile.mkdtemp(prefix="crm_e2e_")
        self.db_path = os.path.join(self._db_dir, "onboarding_e2e.db")
        self.database_url = f"sqlite:///{self.db_path}"
        self.log_path = os.path.join(self._db_dir, "server.log")
        self._log_file = None

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def start(self) -> str:
        if not is_local_database_url(self.database_url):
            raise RuntimeError(f"Refusing non-local test DB: {self.database_url}")

        env = dict(os.environ)
        env["DATABASE_URL"] = self.database_url
        env["E2E_PORT"] = str(self.port)
        env.setdefault("SECRET_KEY", "onboarding-e2e-secret-key")
        env["FLASK_ENV"] = "testing"
        # Keep the run hermetic and fast: no outbound email / AI calls.
        # Empty (but present) values stop python-dotenv from re-injecting real
        # keys from .env, because load_dotenv(override=False) skips existing keys.
        for noisy_key in ("SENDGRID_API_KEY", "OPENAI_API_KEY", "RENTCAST_API_KEY"):
            env[noisy_key] = ""

        self._log_file = open(self.log_path, "w")
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(PROJECT_ROOT / "tests" / "_e2e_app_server.py")],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )
        self._wait_until_ready()
        return self.base_url

    def _wait_until_ready(self, timeout: float = 40.0) -> None:
        deadline = time.time() + timeout
        health_url = f"{self.base_url}/health"
        last_error = None
        while time.time() < deadline:
            if self.process and self.process.poll() is not None:
                raise RuntimeError(
                    f"Server process exited early (code {self.process.returncode}).\n"
                    + self.read_log_tail()
                )
            try:
                with urllib.request.urlopen(health_url, timeout=3) as resp:
                    if resp.status == 200:
                        return
            except Exception as e:  # noqa: BLE001 - polling, any failure means "not ready yet"
                last_error = e
            time.sleep(0.4)
        raise RuntimeError(
            f"Server did not become healthy within {timeout}s "
            f"(last error: {last_error}).\n" + self.read_log_tail()
        )

    def read_log_tail(self, lines: int = 40) -> str:
        try:
            with open(self.log_path, "r") as f:
                content = f.read().splitlines()
            return "--- server log (tail) ---\n" + "\n".join(content[-lines:])
        except Exception:
            return "(no server log available)"

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self._log_file:
            self._log_file.close()
        if not self.keep_db:
            try:
                if os.path.exists(self.db_path):
                    os.remove(self.db_path)
                if os.path.exists(self.log_path):
                    os.remove(self.log_path)
                os.rmdir(self._db_dir)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# The test journey
# ---------------------------------------------------------------------------

class OnboardingJourney:
    def __init__(self, base_url: str, headless: bool, slow_mo: int,
                 server: ManagedAppServer | None):
        self.base_url = base_url.rstrip("/")
        ensure_local_base_url(self.base_url)
        self.headless = headless
        self.slow_mo = slow_mo
        self.server = server

        stamp = int(time.time())
        self.email = f"e2e+{stamp}@example.com"
        self.password = "Activate123!"
        self.first_name = "Riley"
        self.last_name = f"Newuser{stamp}"
        self.company_name = f"Newuser {stamp} Realty"

        self.quick_add_last = f"Quickadd{stamp}"
        self.quick_add_name = f"Casey {self.quick_add_last}"

        self.contact_last = f"Prospect{stamp}"
        self.contact_first = "Jordan"
        self.contact_id: int | None = None
        self.task_subject = f"First follow-up call {stamp}"

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

        self.server_errors: list[str] = []
        self.passed = 0

    # ---- lifecycle ----------------------------------------------------------

    def setup(self):
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless, slow_mo=self.slow_mo)
        self.context = self.browser.new_context(viewport={"width": 1366, "height": 900})
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)
        self.page.set_default_navigation_timeout(30000)
        # Diagnostics: record uncaught JS errors and any 5xx responses.
        self.page.on("pageerror", lambda exc: self.server_errors.append(f"pageerror: {exc}"))
        self.page.on("response", self._note_bad_response)

    def _note_bad_response(self, response):
        try:
            if response.status >= 500:
                self.server_errors.append(f"HTTP {response.status} {response.url}")
        except Exception:
            pass

    def teardown(self):
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    # ---- helpers ------------------------------------------------------------

    def log(self, message: str, level: str = "info"):
        if level == "step":
            print(f"  {Colors.CYAN}->{Colors.END} {message}")
        elif level == "ok":
            print(f"     {Colors.GREEN}OK{Colors.END}")
        elif level == "header":
            print(f"\n{Colors.BOLD}{'=' * 60}{Colors.END}")
            print(f"{Colors.BOLD}{message}{Colors.END}")
            print(f"{Colors.BOLD}{'=' * 60}{Colors.END}")
        else:
            print(message)

    def dismiss_welcome_overlay(self):
        """Dismiss the first-run dashboard welcome modal if it is showing.

        Clicks the real "Look around first" button so the server-side
        has_seen flag persists and the modal does not reappear on reload.
        """
        overlay = self.page.locator("#welcomeOverlay")
        if overlay.count() == 0:
            return
        dismiss_btn = self.page.locator("#welcomeDismiss")
        if dismiss_btn.count() > 0:
            try:
                dismiss_btn.click(timeout=5000)
            except Exception:
                pass
        try:
            overlay.wait_for(state="detached", timeout=5000)
        except Exception:
            # Belt and suspenders: force-remove if the animation/remove lagged.
            self.page.evaluate(
                """() => {
                    const o = document.getElementById('welcomeOverlay');
                    if (o) o.remove();
                    document.body.style.overflow = '';
                }"""
            )

    def dismiss_ai_chat_panel(self):
        """Hide the BOB/AI overlay so it cannot intercept form clicks."""
        try:
            self.page.evaluate(
                """() => {
                    const overlay = document.getElementById('bob-overlay');
                    if (overlay) overlay.classList.remove('visible');
                    const panel = document.getElementById('bob-panel');
                    if (panel) panel.classList.remove('open', 'modal');
                    document.body.classList.remove('bob-fullscreen-open');
                    document.body.style.overflow = '';
                }"""
            )
        except Exception:
            pass

    def run_step(self, name: str, fn):
        self.log(name, "step")
        try:
            fn()
            self.log("", "ok")
            self.passed += 1
        except Exception as e:
            self._capture_failure(name)
            raise AssertionError(f"Step failed: {name}\n  {e}") from e

    def _capture_failure(self, step_name: str):
        slug = "".join(c if c.isalnum() else "_" for c in step_name)[:50]
        stamp = datetime.now().strftime("%H%M%S")
        try:
            shot = ARTIFACTS_DIR / f"FAIL_{slug}_{stamp}.png"
            self.page.screenshot(path=str(shot), full_page=True)
            print(f"     {Colors.YELLOW}screenshot: {shot}{Colors.END}")
        except Exception:
            pass
        try:
            html = ARTIFACTS_DIR / f"FAIL_{slug}_{stamp}.html"
            html.write_text(self.page.content())
            print(f"     {Colors.YELLOW}page html:  {html}{Colors.END}")
        except Exception:
            pass
        print(f"     {Colors.YELLOW}current url: {self.page.url}{Colors.END}")
        if self.server_errors:
            print(f"     {Colors.RED}server-side errors seen:{Colors.END}")
            for err in self.server_errors[-10:]:
                print(f"       - {err}")
        if self.server:
            print(self.server.read_log_tail(25))

    def _body_text(self) -> str:
        return self.page.locator("body").inner_text()

    # ---- steps --------------------------------------------------------------

    def step_signup(self):
        self.page.goto(f"{self.base_url}/register")
        self.page.wait_for_selector('input[name="company_name"]', state="visible")
        self.page.fill('input[name="company_name"]', self.company_name)
        self.page.fill("#first_name", self.first_name)
        self.page.fill("#last_name", self.last_name)
        self.page.fill("#email", self.email)
        self.page.fill("#password", self.password)
        self.page.fill("#confirm_password", self.password)
        # Honeypot must stay empty; assert we are not accidentally filling it.
        assert self.page.input_value("#referral_code") == "", "Honeypot field unexpectedly populated"

        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard**", timeout=20000)
        body = self._body_text()
        assert "Dashboard" in body, "Dashboard heading not found after signup"

    def step_dashboard_empty_state(self):
        # Free-tier, transaction-disabled accounts get one focused workspace.
        self.page.goto(f"{self.base_url}/dashboard")
        self.page.wait_for_load_state("networkidle")
        body = self._body_text()
        assert "How do you want to start?" in body
        assert "transaction pipeline" not in body.lower()
        assert self.page.locator("#welcomeOverlay").count() == 0, (
            "Blocking welcome overlay still renders for a brand-new user"
        )

    def step_dashboard_quick_add(self):
        # Complete contact + dated follow-up without leaving the dashboard.
        self.page.goto(f"{self.base_url}/dashboard")
        self.page.wait_for_load_state("networkidle")
        self.page.click('[data-analytics-path="manual"]')
        self.page.wait_for_selector("#activation-name", state="visible")
        self.page.fill("#activation-name", self.quick_add_name)
        self.page.fill("#activation-phone", "5125550199")
        self.page.get_by_role("button", name="Choose a follow-up day").click()
        self.page.locator('input[name="follow_up"][value="tomorrow"]').check()
        self.page.click("#activation-submit")

        success = self.page.locator('[data-dashboard-page-target="activationSuccess"]')
        expect(success).to_contain_text("Your next follow-up", timeout=10000)
        expect(success).to_contain_text(self.quick_add_name, timeout=10000)
        expect(success).to_contain_text("Due", timeout=10000)

        # And it really persisted.
        self.page.goto(f"{self.base_url}/contacts?q={self.quick_add_last}")
        self.page.wait_for_load_state("networkidle")
        assert self.quick_add_last in self._body_text(), (
            "Quick-added contact did not show up in the contact list"
        )

    def step_create_contact(self):
        self.page.goto(f"{self.base_url}/contacts/create")
        self.page.wait_for_load_state("networkidle")
        self.dismiss_ai_chat_panel()
        self.page.wait_for_selector("#first_name", state="visible")

        self.page.fill("#first_name", self.contact_first)
        self.page.fill("#last_name", self.contact_last)
        self.page.fill("#email", f"{self.contact_last.lower()}@example.com")
        self.page.fill("#phone", "5125550142")
        self.page.fill("#street_address", "742 Evergreen Terrace")
        self.page.fill("#city", "Austin")
        self.page.fill("#state", "TX")
        self.page.fill("#zip_code", "78701")

        # Groups are available but optional during activation and later entry.
        groups = self.page.locator('input[name="group_ids"]')
        assert groups.count() > 0, "No contact groups were seeded for the new org"

        self.dismiss_ai_chat_panel()
        self.page.click('form button[type="submit"]')
        self.page.wait_for_load_state("networkidle")
        # Should have left the create form (success redirect to /contacts).
        assert "/contacts/create" not in self.page.url, (
            "Still on the create form after submit -- validation failed"
        )

        body = self._body_text()
        if self.contact_last not in body:
            self.page.goto(f"{self.base_url}/contacts?q={self.contact_last}")
            self.page.wait_for_load_state("networkidle")
            body = self._body_text()
        assert self.contact_last in body, f"New contact {self.contact_last} not found in list"

        link = self.page.locator('a[href*="/contact/"]').filter(has_text=self.contact_last).first
        if link.count() == 0:
            link = self.page.locator('a[href*="/contact/"]').first
        href = link.get_attribute("href")
        if href and "/contact/" in href:
            self.contact_id = int(href.split("/contact/")[-1].split("/")[0].split("?")[0])

    def step_view_contact(self):
        target = (f"{self.base_url}/contact/{self.contact_id}"
                  if self.contact_id else f"{self.base_url}/contacts?q={self.contact_last}")
        self.page.goto(target)
        self.page.wait_for_load_state("networkidle")
        body = self._body_text()
        assert self.contact_first in body or self.contact_last in body, (
            "Contact details did not render"
        )

    def step_create_task(self):
        self.page.goto(f"{self.base_url}/tasks/new")
        self.page.wait_for_load_state("networkidle")
        self.dismiss_ai_chat_panel()

        contact_select = self.page.locator('select[name="contact_id"]')
        if contact_select.count() > 0 and self.contact_id:
            contact_select.select_option(value=str(self.contact_id))

        type_select = self.page.locator('select[name="type_id"]')
        if type_select.count() > 0:
            first_type = self.page.locator('select[name="type_id"] option').nth(1)
            if first_type.count() > 0:
                type_select.select_option(value=first_type.get_attribute("value"))
                self.page.wait_for_timeout(600)

        subtype_select = self.page.locator('select[name="subtype_id"]')
        if subtype_select.count() > 0:
            first_subtype = self.page.locator('select[name="subtype_id"] option').nth(1)
            if first_subtype.count() > 0:
                value = first_subtype.get_attribute("value")
                if value:
                    subtype_select.select_option(value=value)

        self.page.fill('input[name="subject"]', self.task_subject)
        description = self.page.locator('textarea[name="description"]')
        if description.count() > 0:
            description.fill("Created by the onboarding E2E test.")
        due = self.page.locator('input[name="due_date"]')
        if due.count() > 0:
            due.fill((datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"))
        priority = self.page.locator('select[name="priority"]')
        if priority.count() > 0:
            priority.select_option(value="medium")

        self.dismiss_ai_chat_panel()
        self.page.click('button[type="submit"]')
        self.page.wait_for_load_state("networkidle")

        self.page.goto(f"{self.base_url}/tasks")
        self.page.wait_for_load_state("networkidle")
        assert self.task_subject in self._body_text(), (
            f"Task '{self.task_subject}' not found in task list"
        )

    def step_logout_and_relogin(self):
        self.page.goto(f"{self.base_url}/logout")
        self.page.wait_for_load_state("networkidle")
        assert "/login" in self.page.url, "Logout did not redirect to login"

        self.page.goto(f"{self.base_url}/login")
        self.page.wait_for_selector('input[name="username"]', state="visible")
        self.page.fill('input[name="username"]', self.email)
        self.page.fill('input[name="password"]', self.password)
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard**", timeout=20000)
        assert "Dashboard" in self._body_text(), "Dashboard missing after re-login"

    def step_data_persisted(self):
        self.page.goto(f"{self.base_url}/contacts?q={self.contact_last}")
        self.page.wait_for_load_state("networkidle")
        assert self.contact_last in self._body_text(), (
            "Contact did not persist across logout/login"
        )
        self.page.goto(f"{self.base_url}/dashboard")
        self.page.wait_for_load_state("networkidle")
        body = self._body_text()
        assert self.quick_add_last in body, (
            "Activation follow-up did not persist across logout/login"
        )

    # ---- runner -------------------------------------------------------------

    def run(self) -> int:
        self.log("New-User Activation E2E Test", "header")
        self.log(f"Base URL : {self.base_url}")
        self.log(f"Signup   : {self.email}")
        self.log(f"Mode     : {'headed' if not self.headless else 'headless'}")

        steps = [
            ("Sign up a brand-new account", self.step_signup),
            ("Dashboard shows the quick-add for a new user", self.step_dashboard_empty_state),
            ("Add the first contact via dashboard quick-add", self.step_dashboard_quick_add),
            ("Create another contact via the full form", self.step_create_contact),
            ("View the contact's detail page", self.step_view_contact),
            ("Create a task for that contact", self.step_create_task),
            ("Log out and log back in", self.step_logout_and_relogin),
            ("Confirm the contact persisted", self.step_data_persisted),
        ]

        self.setup()
        failure = None
        try:
            for name, fn in steps:
                self.run_step(name, fn)
        except AssertionError as e:
            failure = e
        finally:
            self.teardown()

        print(f"\n{'=' * 60}")
        total = len(steps)
        if failure is None:
            print(f"{Colors.GREEN}{Colors.BOLD}[RESULT] {self.passed}/{total} steps passed - "
                  f"PASSED{Colors.END}")
            if self.server_errors:
                print(f"{Colors.YELLOW}(note: {len(self.server_errors)} server-side "
                      f"warning(s) observed but did not break the flow){Colors.END}")
            return 0

        print(f"{Colors.RED}{Colors.BOLD}[RESULT] {self.passed}/{total} steps passed - "
              f"FAILED{Colors.END}")
        print(f"{Colors.RED}{failure}{Colors.END}")
        return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Full new-user activation E2E test (signup -> contact -> task -> relogin).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    parser.add_argument("--slow", action="store_true", help="Add 400ms between actions.")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Target an already-running LOCAL server instead of booting one.",
    )
    parser.add_argument(
        "--keep-db", action="store_true",
        help="Keep the throwaway SQLite database and server log for debugging.",
    )
    args = parser.parse_args()

    server = None
    base_url = args.base_url
    try:
        if not base_url:
            server = ManagedAppServer(keep_db=args.keep_db)
            print(f"{Colors.BLUE}Booting throwaway server on {server.base_url} "
                  f"(db: {server.db_path}){Colors.END}")
            base_url = server.start()

        journey = OnboardingJourney(
            base_url=base_url,
            headless=not args.headed,
            slow_mo=400 if args.slow else 0,
            server=server,
        )
        exit_code = journey.run()
    except Exception as e:  # noqa: BLE001 - top-level guard for a clean message + exit code
        print(f"{Colors.RED}Fatal error: {e}{Colors.END}")
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        if server:
            server.stop()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
